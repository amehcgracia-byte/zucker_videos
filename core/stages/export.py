"""ffmpeg export stage for wizard edit plans."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import json
import logging
import math
from pathlib import Path
from typing import Any

from core.ffmpeg import FFmpegError, tool_status
from core.messages import t
from core.media_validation import record_is_usable_camera_video, record_media_path
from core.normalization import EVEN_SDR_FILTER, NORMALIZATION_VERSION, SDR_TONEMAP_FILTER, global_cache_root, global_segment_path, source_cache_key
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage
from core.stages.edit import load_edit_plan

LOGGER = logging.getLogger(__name__)
MAX_EXPORT_BYTES = int(1.9 * 1024 * 1024 * 1024)
AUDIO_BITRATE = 192_000
MIN_ACCEPTABLE_VIDEO_BITRATE = 2_500_000
MAX_VIDEO_BITRATE = 18_000_000
TARGET_EXPORT_FPS = 30.0
TARGET_EXPORT_TIMESCALE = 30_000
EXPORT_SEGMENT_RECIPE_VERSION = 14
INTRO_DURATION = 10.2
OUTRO_DURATION = 10.2
CONTENT_FADE_DURATION = 1.5
FRAME_INTERVAL_TOLERANCE = 0.50


class ExportStage(Stage):
    """Render the wizard edit plan to MP4."""

    name = "export"
    dependencies = ["edit"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint edit output and export settings."""
        return stable_fingerprint(
            {
                "edit": project.data["stages"]["edit"].get("fingerprint"),
                "wizard": project.data["settings"].get("wizard", {}),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the export manifest artifact path."""
        return {"export_manifest": str(artifact_path(project, "export_manifest.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Render the wizard export with streamed ffmpeg progress."""
        progress_callback(5, t("preparing_export"))
        try:
            plan = load_edit_plan(project)
        except FileNotFoundError:
            plan = load_coverage(project)
        segments = _frame_normalized_segments(plan.get("segments") or [])
        if not segments:
            raise ValueError(t("missing_segments"))
        master = project.data["inputs"].get("master")
        if not master:
            raise ValueError(t("missing_master_for_export"))
        platform = plan.get("platform") or project.data["settings"].get("wizard", {}).get("platform") or "youtube"
        output_path = _output_path(project, platform)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content_duration = _plan_duration(segments)
        duration = content_duration + INTRO_DURATION + OUTRO_DURATION
        bitrate_info = _bitrate_for_duration(duration)
        warnings = list(plan.get("warnings") or [])
        if bitrate_info["warning"]:
            progress_callback(6, bitrate_info["warning"])
        if platform == "360":
            _render_360_plan(project, segments, master["path"], output_path, bitrate_info["video_bitrate"], warnings, progress_callback)
        else:
            _render_plan(project, segments, master["path"], output_path, platform, bitrate_info["video_bitrate"], warnings, progress_callback)
        progress_callback(95, t("saving_result"))
        path = artifact_path(project, "export_manifest.json")
        if bitrate_info["warning"]:
            warnings.append(bitrate_info["warning"])
        clip_fates = _clip_fates(project, plan, segments, content_duration)
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "render_logic": (
                    "360 video-only passthrough with one continuous final master-audio mux"
                    if platform == "360"
                    else "video-only edit-plan segments plus intro/outro clips; concat; one continuous final master-audio mux"
                ),
                "warnings": warnings,
                "max_export_bytes": MAX_EXPORT_BYTES,
                "target_video_bitrate": bitrate_info["video_bitrate"],
                "spherical_shot_usage": plan.get("spherical_shot_usage") or _spherical_shot_usage(segments),
                "exports": [
                    {
                        "platform": platform,
                        "path": str(output_path),
                        "filename": output_path.name,
                        "duration_sec": duration,
                        "warnings": warnings,
                        "cut_count": int(plan.get("cut_count") or max(0, len(segments) - 1)),
                        "camera_usage": plan.get("camera_usage") or _camera_usage(segments),
                        "spherical_shot_usage": plan.get("spherical_shot_usage") or _spherical_shot_usage(segments),
                        "excluded_clips": plan.get("excluded_clips") or [],
                        "clip_fates": clip_fates,
                    }
                ],
            },
        )
        progress_callback(100, t("export_ready"))
        return self.outputs(project)


def _output_path(project: Project, platform: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in project.data["name"]).strip() or "video"
    return project.exports_dir / f"{safe_name}-{platform}.mp4"


def _frame_normalized_segments(segments: list[dict[str, Any]], fps: float = TARGET_EXPORT_FPS) -> list[dict[str, Any]]:
    """Return segments whose boundaries and durations sit on the target frame grid."""
    normalized: list[dict[str, Any]] = []
    if not segments:
        return normalized
    first_master = _round_to_frame(float(segments[0].get("master_start_sec") or 0.0), fps)
    cursor = 0.0
    for segment in segments:
        original_master = float(segment.get("master_start_sec") or first_master + cursor)
        original_clip = float(segment.get("clip_start_sec") or 0.0)
        frames = max(1, int(round(max(0.0, float(segment.get("duration_sec") or 0.0)) * fps)))
        duration = frames / fps
        master_start = _round_to_frame(first_master + cursor, fps)
        clip_start = max(0.0, original_clip + (master_start - original_master))
        normalized.append(
            {
                **segment,
                "clip_start_sec": _round_to_frame(clip_start, fps),
                "master_start_sec": master_start,
                "duration_sec": round(duration, 6),
                "frame_count": frames,
            }
        )
        cursor += duration
    return normalized


def _round_to_frame(seconds: float, fps: float = TARGET_EXPORT_FPS) -> float:
    return round(round(float(seconds) * fps) / fps, 6)


def _render_plan(
    project: Project,
    segments: list[dict[str, Any]],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    warnings: list[str],
    progress_callback: ProgressCallback,
) -> None:
    ffmpeg = _ffmpeg_path()
    temp_dir = output_path.parent / f".{output_path.stem}-segments"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    segment_paths: list[Path] = []
    total_duration = _plan_duration(segments)
    rendered_duration = 0.0
    color_profiles = _color_profiles_for_segments(project, segments, warnings)
    overlay_config = _overlay_config(platform, segments[0])
    verify_motion = bool(project.data.get("settings", {}).get("export", {}).get("verify_motion", True))
    try:
        intro_path = temp_dir / "intro.mp4"
        _render_logo_clip(
            intro_path,
            platform,
            "intro",
            INTRO_DURATION,
            video_bitrate,
            lambda percent, detail: progress_callback(8 + int(percent * 2 / 100), detail),
        )
        segment_paths.append(intro_path)
        render_segments = _continuous_spherical_render_segments(segments)
        for index, segment in enumerate(render_segments, start=1):
            segment_duration = max(0.1, float(segment["duration_sec"]))
            base_percent = 10 + int(70 * rendered_duration / max(total_duration, 0.1))

            def segment_progress(local_percent: int, detail: str, *, index: int = index) -> None:
                segment_share = 70 * segment_duration / max(total_duration, 0.1)
                percent = base_percent + int(segment_share * local_percent / 100)
                progress_callback(min(84, percent), f"Rendering segment {index}/{len(render_segments)}: {detail}")

            color_profile = color_profiles.get(str(segment.get("clip_path")), {})
            intro_fade = index == 1
            outro_fade = index == len(render_segments)
            intro_logo = False
            outro_logo = False
            segment_path = cached_segment_path(project, segment, platform, video_bitrate, overlay_config, color_profile, intro_fade, outro_fade, intro_logo, outro_logo)
            source_info = _segment_source_info(project, segment)
            command_line = "cached segment"
            if not segment_path.exists():
                tmp_segment = temp_dir / f"segment-{index:04d}.mp4"
                commands: list[list[str]] = []
                rendered_from = _render_segment(
                    project,
                    segment,
                    master_path,
                    tmp_segment,
                    platform,
                    video_bitrate,
                    overlay_config,
                    color_profile,
                    segment_progress,
                    intro_fade=intro_fade,
                    outro_fade=outro_fade,
                    intro_logo=intro_logo,
                    outro_logo=outro_logo,
                    warnings=warnings,
                    command_recorder=commands,
                )
                if commands:
                    command_line = " ".join(commands[-1])
                segment_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(tmp_segment, segment_path)
                if rendered_from == "proxy":
                    warnings.append(f"Used proxy fallback for {Path(str(segment.get('source_path') or segment.get('clip_path'))).name}")
            _verify_segment_frame_duration(segment_path, _segment_frame_count(segment), Path(str(source_info.get("source_path") or segment.get("clip_path"))).name)
            if verify_motion:
                command_line = _verify_or_rebuild_segment(
                    project,
                    segment,
                    master_path,
                    segment_path,
                    temp_dir / f"segment-{index:04d}.mp4",
                    platform,
                    video_bitrate,
                    overlay_config,
                    color_profile,
                    segment_progress,
                    intro_fade,
                    outro_fade,
                    intro_logo,
                    outro_logo,
                    warnings,
                    command_line,
                    segment_duration,
                    Path(str(source_info.get("source_path") or segment.get("clip_path"))).name,
                )
            segment_paths.append(segment_path)
            rendered_duration += segment_duration
        outro_path = temp_dir / "outro.mp4"
        _render_logo_clip(
            outro_path,
            platform,
            "outro",
            OUTRO_DURATION,
            video_bitrate,
            lambda percent, detail: progress_callback(82 + int(percent * 2 / 100), detail),
        )
        segment_paths.append(outro_path)
        concat_path = temp_dir / "concat.txt"
        joined_video = temp_dir / "joined-video.mp4"
        concat_path.write_text("".join(_concat_file_line(path) for path in segment_paths), encoding="utf-8")
        _run_ffmpeg_progress(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-progress",
                "pipe:1",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(joined_video),
            ],
            total_duration + INTRO_DURATION + OUTRO_DURATION,
            t("joining_segments"),
            lambda percent, detail: progress_callback(84 + int(percent * 6 / 100), detail),
        )
        cfr_video = _cadence_checked_joined_video(
            joined_video,
            temp_dir / "joined-video-cfr.mp4",
            video_bitrate,
            lambda percent, detail: progress_callback(89 + int(percent * 1 / 100), detail),
        )
        real_duration = _media_duration(str(cfr_video))
        audio_start, audio_delay = _audio_mux_start_and_delay(segments)
        _mux_continuous_master_audio(
            cfr_video,
            master_path,
            output_path,
            audio_start,
            real_duration,
            video_bitrate,
            lambda percent, detail: progress_callback(90 + int(percent * 5 / 100), detail),
            content_start=INTRO_DURATION,
            content_end=max(INTRO_DURATION, real_duration - OUTRO_DURATION),
            audio_delay=audio_delay,
        )
        if verify_motion:
            _verify_joined_output(output_path, segments, timeline_offset=INTRO_DURATION)
            _verify_final_audio(
                output_path,
                segments,
                master_path,
                audio_start,
                timeline_offset=INTRO_DURATION,
                audio_delay=audio_delay,
                duration=real_duration,
                content_start=INTRO_DURATION,
                content_end=max(INTRO_DURATION, real_duration - OUTRO_DURATION),
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _render_360_plan(
    project: Project,
    segments: list[dict[str, Any]],
    master_path: str,
    output_path: Path,
    video_bitrate: int,
    warnings: list[str],
    progress_callback: ProgressCallback,
) -> None:
    """Render a full equirectangular 360 clip with master audio and spherical metadata."""
    ffmpeg = _ffmpeg_path()
    segment = _select_360_segment(project, segments)
    duration = max(1.0, float(segment.get("duration_sec") or 1.0))
    temp_dir = output_path.parent / f".{output_path.stem}-360"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    try:
        intro = temp_dir / "intro.mp4"
        body = temp_dir / "body.mp4"
        outro = temp_dir / "outro.mp4"
        joined = temp_dir / "joined.mp4"
        _render_logo_clip(
            intro,
            "360",
            "intro",
            INTRO_DURATION,
            video_bitrate,
            lambda percent, detail: progress_callback(8 + int(percent * 4 / 100), detail),
        )
        body_paths: list[Path] = []
        rendered_duration = 0.0
        body_segments = _continuous_spherical_render_segments(segments if any(item.get("spherical_shot") for item in segments) else [segment])
        for index, body_segment in enumerate(body_segments, start=1):
            segment_path = temp_dir / f"body-{index:04d}.mp4"
            segment_duration = max(0.1, float(body_segment.get("duration_sec") or 0.1))
            base_percent = 12 + int(72 * rendered_duration / max(duration, 0.1))
            _render_360_body(
                project,
                body_segment,
                segment_path,
                video_bitrate,
                lambda percent, detail, base_percent=base_percent, segment_duration=segment_duration: progress_callback(
                    min(83, base_percent + int(72 * segment_duration / max(duration, 0.1) * percent / 100)),
                    f"Rendering 360: {detail}",
                ),
                intro_fade=index == 1,
                outro_fade=index == len(body_segments),
            )
            body_paths.append(segment_path)
            rendered_duration += segment_duration
        _render_logo_clip(
            outro,
            "360",
            "outro",
            OUTRO_DURATION,
            video_bitrate,
            lambda percent, detail: progress_callback(84 + int(percent * 4 / 100), detail),
        )
        concat_path = temp_dir / "concat.txt"
        concat_path.write_text("".join(_concat_file_line(path) for path in [intro, *body_paths, outro]), encoding="utf-8")
        _run_ffmpeg_progress(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-progress",
                "pipe:1",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                *_spherical_metadata_args(),
                str(joined),
            ],
            duration + INTRO_DURATION + OUTRO_DURATION,
            t("joining_segments"),
            lambda percent, detail: progress_callback(88 + int(percent * 4 / 100), detail),
        )
        cfr_joined = _cadence_checked_joined_video(
            joined,
            temp_dir / "joined-cfr.mp4",
            video_bitrate,
            lambda percent, detail: progress_callback(91 + int(percent * 1 / 100), detail),
            extra_args=_spherical_metadata_args(),
        )
        real_duration = _media_duration(str(cfr_joined))
        audio_start, audio_delay = _audio_mux_start_and_delay([segment])
        _mux_continuous_master_audio(
            cfr_joined,
            master_path,
            output_path,
            audio_start,
            real_duration,
            video_bitrate,
            lambda percent, detail: progress_callback(92 + int(percent * 3 / 100), detail),
            extra_args=_spherical_metadata_args(),
            content_start=INTRO_DURATION,
            content_end=max(INTRO_DURATION, real_duration - OUTRO_DURATION),
            audio_delay=audio_delay,
        )
        warnings.append("360 export uses the selected 360 clip full length with synced master audio; no multicam cuts.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _select_360_segment(project: Project, segments: list[dict[str, Any]]) -> dict[str, Any]:
    for segment in segments:
        if segment.get("projection") in {"equirect", "raw_insv"}:
            return segment
    records = project.data.get("inputs", {}).get("videos", [])
    candidates = [record for record in records if (record.get("probe") or {}).get("projection") in {"equirect", "raw_insv"} or record.get("projection") in {"equirect", "raw_insv"}]
    if not candidates:
        raise ValueError("No 360 clip is registered")
    preferred = sorted(candidates, key=lambda record: 0 if (record.get("probe") or {}).get("projection") == "equirect" or record.get("projection") == "equirect" else 1)[0]
    normalized = preferred.get("normalized") or {}
    return {
        "title": Path(str(preferred.get("path"))).stem,
        "clip_path": normalized.get("path") or preferred.get("path"),
        "source_path": preferred.get("path"),
        "clip_start_sec": 0.0,
        "master_start_sec": 0.0,
        "duration_sec": float((preferred.get("probe") or {}).get("duration") or preferred.get("duration") or 1.0),
        "projection": preferred.get("projection") or (preferred.get("probe") or {}).get("projection"),
        "filename": Path(str(preferred.get("path"))).name,
    }


def _expand_spherical_render_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    previous_shot: dict[str, Any] | None = None
    for segment in segments:
        shot = _spherical_shot(segment)
        if not shot:
            expanded.append(segment)
            previous_shot = None
            continue
        parts = _spherical_segment_parts(segment, previous_shot)
        expanded.extend(parts)
        previous_shot = shot
    return expanded


def _continuous_spherical_render_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    previous_shot: dict[str, Any] | None = None
    for segment in segments:
        shot = _spherical_shot(segment)
        if shot and previous_shot and previous_shot.get("type") != shot.get("type"):
            shot = {**shot, "previous_shot": previous_shot}
            output.append({**segment, "spherical_shot": shot})
        else:
            output.append(segment)
        previous_shot = _spherical_shot(segment)
    return output


def _spherical_segment_parts(segment: dict[str, Any], previous_shot: dict[str, Any] | None) -> list[dict[str, Any]]:
    shot = _spherical_shot(segment) or {}
    duration = max(0.0, float(segment.get("duration_sec") or 0.0))
    if duration <= 0.001:
        return []
    parts: list[dict[str, Any]] = []
    current = 0.0
    start_yaw = _shot_float(previous_shot, "yaw", _shot_float(shot, "yaw", 0.0))
    target_yaw = _shot_float(shot, "yaw", 0.0)
    if previous_shot and previous_shot.get("type") != shot.get("type") and duration > 1.0:
        pan_duration = min(_shot_float(shot, "transition_sec", 0.45), duration / 3.0)
        pan_steps = max(3, min(12, int(round(pan_duration / 0.05))))
        for index in range(pan_steps):
            part_duration = pan_duration / pan_steps
            amount = (index + 1) / pan_steps
            parts.append(_spherical_part(segment, current, part_duration, {**shot, "type": "pan", "label": shot.get("label"), "yaw": _lerp_angle(start_yaw, target_yaw, amount)}))
            current += part_duration
    remaining = max(0.0, duration - current)
    if shot.get("type") == "planet" and remaining > 0.001:
        spin = _shot_float(shot, "spin_deg_per_sec", 22.0)
        step = min(0.5, remaining)
        while remaining > 0.001:
            part_duration = min(step, remaining)
            yaw = target_yaw + spin * (current + part_duration / 2.0)
            parts.append(_spherical_part(segment, current, part_duration, {**shot, "yaw": yaw % 360.0}))
            current += part_duration
            remaining -= part_duration
    elif remaining > 0.001:
        step = min(1.0, remaining)
        static_start = current
        static_duration = remaining
        while remaining > 0.001:
            part_duration = min(step, remaining)
            midpoint = (current - static_start + part_duration / 2.0) / max(static_duration, 0.001)
            parts.append(_spherical_part(segment, current, part_duration, _drifted_spherical_shot(shot, midpoint)))
            current += part_duration
            remaining -= part_duration
    return parts


def _spherical_part(segment: dict[str, Any], offset: float, duration: float, shot: dict[str, Any]) -> dict[str, Any]:
    return {
        **segment,
        "clip_start_sec": round(float(segment.get("clip_start_sec") or 0.0) + offset, 6),
        "master_start_sec": round(float(segment.get("master_start_sec") or 0.0) + offset, 6),
        "duration_sec": round(duration, 6),
        "spherical_shot": shot,
    }


def _lerp_angle(start: float, end: float, amount: float) -> float:
    delta = ((end - start + 540.0) % 360.0) - 180.0
    return (start + delta * max(0.0, min(1.0, amount))) % 360.0


def _drifted_spherical_shot(shot: dict[str, Any], amount: float) -> dict[str, Any]:
    amount = max(0.0, min(1.0, amount))
    yaw_delta = _shot_float(shot, "drift_yaw_deg", 0.0) * (amount - 0.5)
    pitch_delta = _shot_float(shot, "drift_pitch_deg", 0.0) * (amount - 0.5)
    return {
        **shot,
        "yaw": (_shot_float(shot, "yaw", 0.0) + yaw_delta) % 360.0,
        "pitch": _shot_float(shot, "pitch", 0.0) + pitch_delta,
    }


def _render_360_body(
    project: Project,
    segment: dict[str, Any],
    output_path: Path,
    video_bitrate: int,
    progress_callback: ProgressCallback | None,
    intro_fade: bool = False,
    outro_fade: bool = False,
) -> None:
    ffmpeg = _ffmpeg_path()
    duration = max(0.1, float(segment["duration_sec"]))
    frame_count = _segment_frame_count(segment)
    source = _segment_source_info(project, segment)
    watermark = _watermark_path()
    shot = _spherical_shot(segment)
    filter_complex = _equirect_filtergraph(
        source.get("probe") or {},
        duration,
        frame_count,
        bool(watermark),
        shot=shot,
        intro_fade=intro_fade,
        outro_fade=outro_fade,
        command_path=output_path.with_suffix(".sendcmd.txt"),
    )
    command = _segment_video_command_base(ffmpeg, source["source_path"], segment, duration)
    if watermark:
        command.extend(["-loop", "1", "-i", str(watermark)])
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{TARGET_EXPORT_FPS:.3f}",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(TARGET_EXPORT_TIMESCALE),
            "-an",
            "-frames:v",
            str(frame_count),
            "-movflags",
            "+faststart+use_metadata_tags",
            *_spherical_metadata_args(),
        ]
    )
    command.extend(_video_encode_args("h264_videotoolbox", video_bitrate))
    command.append(str(output_path))
    try:
        _run_ffmpeg_progress(command, duration, Path(str(source["source_path"])).name, progress_callback)
    except FFmpegError:
        if output_path.exists():
            output_path.unlink()
        command = command[: command.index("-c:v")] + _video_encode_args("libx264", video_bitrate) + [str(output_path)]
        _run_ffmpeg_progress(command, duration, Path(str(source["source_path"])).name, progress_callback)


def _equirect_filtergraph(
    probe: dict[str, Any],
    duration: float,
    frame_count: int | None,
    has_watermark: bool,
    shot: dict[str, Any] | None = None,
    intro_fade: bool = False,
    outro_fade: bool = False,
    command_path: Path | None = None,
) -> str:
    yaw = _shot_yaw(shot)
    pitch = _shot_float(shot, "pitch", 0.0)
    command_prefix = _v360_sendcmd_filter(shot, duration, command_path)
    if probe.get("projection") == "raw_insv":
        fov = int(probe.get("insv_fov") or 190)
        base_filter = f"v360=input=dfisheye:output=e:ih_fov={fov}:iv_fov={fov}:interp=lanczos,{command_prefix}v360@sphere=input=equirect:output=equirect:yaw={yaw:.3f}:pitch={pitch:.3f}:interp=lanczos,scale=3840:1920:force_original_aspect_ratio=decrease,pad=3840:1920:(ow-iw)/2:(oh-ih)/2"
    else:
        base_filter = f"{command_prefix}v360@sphere=input=equirect:output=equirect:yaw={yaw:.3f}:pitch={pitch:.3f}:interp=lanczos,scale=3840:1920:force_original_aspect_ratio=decrease,pad=3840:1920:(ow-iw)/2:(oh-ih)/2"
    timing = _exact_cadence_filter(frame_count) if frame_count else _constant_cadence_filter()
    filters = f"{base_filter},{timing},tpad=stop_mode=clone:stop_duration={1.0 / TARGET_EXPORT_FPS:.6f},format=yuv420p"
    if intro_fade:
        filters += f",fade=t=in:st=0:d={CONTENT_FADE_DURATION:.3f}"
    if outro_fade:
        filters += f",fade=t=out:st={max(0.0, duration - CONTENT_FADE_DURATION):.3f}:d={CONTENT_FADE_DURATION:.3f}"
    graph = f"[0:v]{filters}[base]"
    if not has_watermark:
        return f"{graph};[base]copy[v]"
    return (
        f"{graph};"
        "[1:v]format=rgba,scale=-1:96,colorchannelmixer=aa=0.70[wm];"
        "[base][wm]overlay=W-w-80:H-h-80:format=auto[v]"
    )


def _spherical_metadata_args() -> list[str]:
    return [
        "-metadata",
        "projection=equirectangular",
        "-metadata",
        "spherical_video=true",
        "-metadata:s:v:0",
        "projection=equirectangular",
        "-metadata:s:v:0",
        "stereo_mode=mono",
        "-metadata:s:v:0",
        "spherical_video=true",
    ]


def _render_logo_clip(
    output_path: Path,
    platform: str,
    kind: str,
    duration: float,
    video_bitrate: int,
    progress_callback: ProgressCallback | None,
) -> None:
    """Render a standalone video-only intro/outro logo clip in the concat house format."""
    ffmpeg = _ffmpeg_path()
    logo = _intro_logo_path() or _logo_path()
    width, height = _target_size(platform)
    bg = "0x000000"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-f",
        "lavfi",
        "-i",
        f"color=c={bg}:s={width}x{height}:r={TARGET_EXPORT_FPS:.3f}:d={duration:.3f}",
    ]
    input_count = 1
    if logo:
        command.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(logo)])
        input_count += 1
    filter_complex = _logo_filtergraph(platform, kind, duration, bool(logo))
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{TARGET_EXPORT_FPS:.3f}",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(TARGET_EXPORT_TIMESCALE),
            "-an",
            "-movflags",
            "+faststart",
        ]
    )
    command.extend(_video_encode_args("libx264", video_bitrate))
    command.append(str(output_path))
    _run_ffmpeg_progress(command, duration, f"{kind.title()} logo", progress_callback)


def _logo_filtergraph(platform: str, kind: str, duration: float, has_logo: bool) -> str:
    video = f"[0:v]format=yuv420p,{_constant_cadence_filter()}[bg]"
    if not has_logo:
        return f"{video};[bg]copy[v]"
    logo_height = int(_target_size(platform)[1] * 0.72)
    fade_in = 3.45
    fade_out = 3.00
    black_hold = 1.50
    fade_out_start = max(0.0, duration - black_hold - fade_out)
    fade = f"fade=t=in:st={black_hold:.2f}:d={fade_in:.2f}:alpha=1,fade=t=out:st={fade_out_start:.2f}:d={fade_out:.2f}:alpha=1"
    return (
        f"{video};"
        f"[1:v]format=rgba,scale=-1:{logo_height},{fade}[logo];"
        f"[bg][logo]overlay=(W-w)/2:(H-h)/2:format=auto[v]"
    )


def _intro_master_start(segments: list[dict[str, Any]]) -> float:
    first = float(segments[0].get("master_start_sec") or 0.0) if segments else 0.0
    return max(0.0, first - INTRO_DURATION)


def _audio_mux_start_and_delay(segments: list[dict[str, Any]]) -> tuple[float, float]:
    first = float(segments[0].get("master_start_sec") or 0.0) if segments else 0.0
    desired_start = first - INTRO_DURATION
    return max(0.0, desired_start), max(0.0, -desired_start)


def _outro_master_start(segments: list[dict[str, Any]]) -> float:
    if not segments:
        return 0.0
    last = segments[-1]
    return max(0.0, float(last.get("master_start_sec") or 0.0) + float(last.get("duration_sec") or 0.0))


def _target_size(platform: str) -> tuple[int, int]:
    if platform in {"instagram", "tiktok"}:
        return 608, 1080
    if platform == "360":
        return 3840, 1920
    return 1920, 1080


def _render_segment(
    project: Project,
    segment: dict[str, Any],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    overlay_config: dict[str, Any] | None = None,
    color_profile: dict[str, Any] | None = None,
    progress_callback: ProgressCallback | None = None,
    intro_fade: bool = False,
    outro_fade: bool = False,
    intro_logo: bool = False,
    outro_logo: bool = False,
    warnings: list[str] | None = None,
    command_recorder: list[list[str]] | None = None,
    force_proxy: bool = False,
) -> str:
    ffmpeg = _ffmpeg_path()
    duration = max(0.1, float(segment["duration_sec"]))
    frame_count = _segment_frame_count(segment)
    source = _segment_source_info(project, segment)
    watermark = _watermark_path()
    if source.get("paired_path") and not force_proxy:
        force_proxy = True
    if force_proxy:
        proxy_path = source.get("proxy_path")
        if not proxy_path or proxy_path == source["source_path"]:
            raise FFmpegError(f"No proxy fallback is available for {Path(str(source['source_path'])).name}")
        if warnings is not None:
            warnings.append(f"Original segment looked static for {Path(str(source['source_path'])).name}; using proxy fallback")
        return _render_proxy_segment(
            ffmpeg,
            proxy_path,
            master_path,
            segment,
            output_path,
            platform,
            video_bitrate,
            overlay_config or {},
            color_profile or {},
            duration,
            frame_count,
            watermark,
            progress_callback,
            intro_fade,
            outro_fade,
            intro_logo,
            outro_logo,
            command_recorder,
        )
    sendcmd_path = output_path.with_suffix(".sendcmd.txt")
    source_filter = _export_source_filter(source.get("probe") or {}, _spherical_shot(segment), duration=duration, command_path=sendcmd_path)
    filter_complex = _segment_filtergraph(
        platform,
        duration,
        overlay_config or {},
        color_profile or {},
        bool(watermark),
        _ffmpeg_supports_filter("drawtext"),
        intro_fade=intro_fade,
        outro_fade=outro_fade,
        intro_logo=intro_logo,
        outro_logo=outro_logo,
        source_filter=source_filter,
        motion_filter=_motion_filter(segment, platform, duration),
        frame_count=frame_count,
    )
    command_base = _segment_video_command_base(
        ffmpeg,
        source["source_path"],
        segment,
        duration,
    )
    if watermark:
        command_base.extend(["-loop", "1", "-i", str(watermark)])
    command_base.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{TARGET_EXPORT_FPS:.3f}",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(TARGET_EXPORT_TIMESCALE),
            "-an",
            "-frames:v",
            str(frame_count),
            "-movflags",
            "+faststart",
        ]
    )
    hardware = _video_encode_args("h264_videotoolbox", video_bitrate)
    software = _video_encode_args("libx264", video_bitrate)
    try:
        command = command_base + hardware + [str(output_path)]
        if command_recorder is not None:
            command_recorder.append(command)
        _run_ffmpeg_progress(command, duration, Path(str(source["source_path"])).name, progress_callback)
        return "original"
    except FFmpegError as exc:
        if output_path.exists():
            output_path.unlink()
        if progress_callback:
            progress_callback(0, t("hardware_fallback"))
        try:
            command = command_base + software + [str(output_path)]
            if command_recorder is not None:
                command_recorder.append(command)
            _run_ffmpeg_progress(command, duration, Path(str(source["source_path"])).name, progress_callback)
            return "original"
        except FFmpegError as fallback_exc:
            if output_path.exists():
                output_path.unlink()
            proxy_path = source.get("proxy_path")
            if not proxy_path or proxy_path == source["source_path"]:
                raise FFmpegError(f"{fallback_exc}\nFallback after h264_videotoolbox failed with: {exc}") from fallback_exc
            if warnings is not None:
                warnings.append(f"Original render failed for {Path(str(source['source_path'])).name}; using proxy fallback")
            return _render_proxy_segment(
                ffmpeg,
                proxy_path,
                master_path,
                segment,
                output_path,
                platform,
                video_bitrate,
                overlay_config or {},
                color_profile or {},
                duration,
                frame_count,
                watermark,
                progress_callback,
                intro_fade,
                outro_fade,
                intro_logo,
                outro_logo,
                command_recorder,
            )


def _render_proxy_segment(
    ffmpeg: str,
    proxy_path: str,
    master_path: str,
    segment: dict[str, Any],
    output_path: Path,
    platform: str,
    video_bitrate: int,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    duration: float,
    frame_count: int,
    watermark: Path | None,
    progress_callback: ProgressCallback | None,
    intro_fade: bool,
    outro_fade: bool,
    intro_logo: bool,
    outro_logo: bool,
    command_recorder: list[list[str]] | None,
) -> str:
    proxy_filter = _segment_filtergraph(
        platform,
        duration,
        overlay_config,
        color_profile,
        bool(watermark),
        _ffmpeg_supports_filter("drawtext"),
        intro_fade=intro_fade,
        outro_fade=outro_fade,
        intro_logo=intro_logo,
        outro_logo=outro_logo,
        frame_count=frame_count,
    )
    proxy_command = _segment_video_command_base(ffmpeg, proxy_path, segment, duration)
    if watermark:
        proxy_command.extend(["-loop", "1", "-i", str(watermark)])
    proxy_command.extend(
        [
            "-filter_complex",
            proxy_filter,
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{TARGET_EXPORT_FPS:.3f}",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(TARGET_EXPORT_TIMESCALE),
            "-an",
            "-frames:v",
            str(frame_count),
            "-movflags",
            "+faststart",
        ]
    )
    command = proxy_command + _video_encode_args("libx264", video_bitrate) + [str(output_path)]
    if command_recorder is not None:
        command_recorder.append(command)
    _run_ffmpeg_progress(command, duration, Path(str(proxy_path)).name, progress_callback)
    return "proxy"


def _segment_frame_count(segment: dict[str, Any]) -> int:
    try:
        return max(1, int(segment.get("frame_count") or round(float(segment.get("duration_sec") or 0.0) * TARGET_EXPORT_FPS)))
    except (TypeError, ValueError):
        return 1


def _verify_or_rebuild_segment(
    project: Project,
    segment: dict[str, Any],
    master_path: str,
    segment_path: Path,
    tmp_segment: Path,
    platform: str,
    video_bitrate: int,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    progress_callback: ProgressCallback | None,
    intro_fade: bool,
    outro_fade: bool,
    intro_logo: bool,
    outro_logo: bool,
    warnings: list[str],
    command_line: str,
    segment_duration: float,
    label: str,
) -> str:
    """Verify one rendered segment, rebuilding/falling back before final concat."""
    try:
        _verify_moving_segment(segment_path, segment_duration, label, command_line)
        return command_line
    except FFmpegError as first_error:
        segment_path.unlink(missing_ok=True)
        commands: list[list[str]] = []
        _render_segment(
            project,
            segment,
            master_path,
            tmp_segment,
            platform,
            video_bitrate,
            overlay_config,
            color_profile,
            progress_callback,
            intro_fade=intro_fade,
            outro_fade=outro_fade,
            intro_logo=intro_logo,
            outro_logo=outro_logo,
            warnings=warnings,
            command_recorder=commands,
        )
        command_line = " ".join(commands[-1]) if commands else "rerender command unavailable"
        shutil.copy2(tmp_segment, segment_path)
        try:
            _verify_moving_segment(segment_path, segment_duration, label, command_line)
            return command_line
        except FFmpegError as rerender_error:
            source_info = _segment_source_info(project, segment)
            proxy_path = source_info.get("proxy_path")
            if not proxy_path or proxy_path == source_info.get("source_path"):
                raise FFmpegError(f"{rerender_error}\nInitial verification failure: {first_error}") from rerender_error
            tmp_segment.unlink(missing_ok=True)
            commands = []
            _render_segment(
                project,
                segment,
                master_path,
                tmp_segment,
                platform,
                video_bitrate,
                overlay_config,
                color_profile,
                progress_callback,
                intro_fade=intro_fade,
                outro_fade=outro_fade,
                intro_logo=intro_logo,
                outro_logo=outro_logo,
                warnings=warnings,
                command_recorder=commands,
                force_proxy=True,
            )
            command_line = " ".join(commands[-1]) if commands else "proxy fallback command unavailable"
            shutil.copy2(tmp_segment, segment_path)
            _verify_moving_segment(segment_path, segment_duration, label, command_line)
            return command_line


def _segment_video_command_base(ffmpeg: str, clip_path: str, segment: dict[str, Any], duration: float) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-ss",
        f"{float(segment['clip_start_sec']):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(clip_path)),
    ]


def _run_ffmpeg_progress(command: list[str], duration: float, label: str, progress: ProgressCallback | None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    current = -1
    for line in process.stdout:
        match = re.match(r"out_time_ms=(\d+)", line.strip())
        if not match or duration <= 0:
            continue
        seconds = int(match.group(1)) / 1_000_000
        percent = max(current, min(99, int(seconds / duration * 100)))
        if progress and percent > current:
            current = percent
            progress(percent, f"{label} — {percent}%")
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError((stderr or "").strip() or "ffmpeg export failed")
    if progress:
        progress(100, f"{label} — 100%")


def _constant_cadence_filter(fps: float = TARGET_EXPORT_FPS) -> str:
    return f"{_target_fps_filter(fps)},setpts=N/({fps:.3f}*TB)"


def _target_fps_filter(fps: float = TARGET_EXPORT_FPS) -> str:
    return f"fps=fps={fps:.3f}:round=near:start_time=0"


def _exact_cadence_filter(frame_count: int, fps: float = TARGET_EXPORT_FPS) -> str:
    return f"{_target_fps_filter(fps)},trim=start_frame=0:end_frame={max(1, int(frame_count))},setpts=N/({fps:.3f}*TB)"


def _normalize_joined_video_cadence(
    input_path: Path,
    output_path: Path,
    video_bitrate: int,
    progress_callback: ProgressCallback | None,
    extra_args: list[str] | None = None,
) -> None:
    """Rewrite the concatenated video onto one exact CFR timeline before audio mux."""
    duration = _media_duration(str(input_path))
    movflags = "+faststart+use_metadata_tags" if extra_args else "+faststart"
    command = [
        _ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-i",
        str(input_path),
        "-vf",
        f"{_constant_cadence_filter()},format=yuv420p",
        "-an",
        "-r",
        f"{TARGET_EXPORT_FPS:.3f}",
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        str(TARGET_EXPORT_TIMESCALE),
        "-movflags",
        movflags,
        *(extra_args or []),
    ]
    command.extend(_video_encode_args("libx264", video_bitrate))
    command.append(str(output_path))
    _run_ffmpeg_progress(command, duration, t("joining_segments"), progress_callback)


def _cadence_checked_joined_video(
    input_path: Path,
    fallback_output_path: Path,
    video_bitrate: int,
    progress_callback: ProgressCallback | None,
    extra_args: list[str] | None = None,
) -> Path:
    """Use stream-copy concat output when cadence is already valid; rewrite only as fallback."""
    try:
        _verify_video_cadence(input_path, f"stream-copy joined video {input_path}", duration=_media_duration(str(input_path)))
        return input_path
    except FFmpegError as exc:
        LOGGER.warning("Joined video cadence rewrite required for %s: %s", input_path, exc)
        _normalize_joined_video_cadence(input_path, fallback_output_path, video_bitrate, progress_callback, extra_args=extra_args)
        return fallback_output_path


def _mux_continuous_master_audio(
    video_path: Path,
    master_path: str,
    output_path: Path,
    audio_start: float,
    duration: float,
    video_bitrate: int,
    progress_callback: ProgressCallback | None,
    extra_args: list[str] | None = None,
    content_start: float | None = None,
    content_end: float | None = None,
    audio_delay: float = 0.0,
) -> None:
    """Mux one continuous master-audio span over the already-concatenated video."""
    ffmpeg = _ffmpeg_path()
    movflags = "+faststart+use_metadata_tags" if extra_args else "+faststart"
    filters = []
    if audio_delay > 0:
        delay_ms = int(round(audio_delay * 1000))
        filters.append(f"adelay={delay_ms}:all=1")
    filters.extend(["apad", f"atrim=0:{duration:.3f}", "asetpts=PTS-STARTPTS"])
    if content_start is not None:
        filters.append(f"afade=t=in:st={max(0.0, content_start):.3f}:d={CONTENT_FADE_DURATION:.3f}")
    if content_end is not None:
        filters.append(f"afade=t=out:st={max(0.0, content_end - CONTENT_FADE_DURATION):.3f}:d={CONTENT_FADE_DURATION:.3f}")
    audio_filter = ",".join(filters) + "[a]"
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-i",
        str(video_path),
        "-ss",
        f"{max(0.0, audio_start):.3f}",
        "-i",
        str(Path(master_path)),
        "-filter_complex",
        audio_filter,
        "-map",
        "0:v:0",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        f"{duration:.3f}",
        "-movflags",
        movflags,
        *(extra_args or []),
        str(output_path),
    ]
    _run_ffmpeg_progress(command, duration, t("joining_segments"), progress_callback)


def _base_video_filter(platform: str) -> str:
    if platform in {"instagram", "tiktok"}:
        return "scale=608:1080:force_original_aspect_ratio=increase,crop=608:1080,setsar=1,format=yuv420p"
    if platform == "360":
        return "scale=3840:1920:force_original_aspect_ratio=decrease,pad=3840:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"


def _motion_filter(segment: dict[str, Any], platform: str, duration: float) -> str | None:
    motion = segment.get("motion") or {}
    if motion.get("type") != "ken_burns":
        return None
    width, height = _target_size(platform)
    try:
        zoom_start = float(motion.get("zoom_start", 1.0))
        zoom_end = float(motion.get("zoom_end", 1.06))
        pan_x = float(motion.get("pan_x", 0.5))
        pan_y = float(motion.get("pan_y", 0.5))
    except (TypeError, ValueError):
        return None
    zoom_start = max(1.0, min(1.12, zoom_start))
    zoom_end = max(zoom_start, min(1.12, zoom_end))
    pan_x = max(0.0, min(1.0, pan_x))
    pan_y = max(0.0, min(1.0, pan_y))
    frame_count = max(1, int(round(max(0.1, float(duration)) * TARGET_EXPORT_FPS)))
    progress = f"min(1,n/{max(1, frame_count - 1)})"
    zoom_expr = f"({zoom_start:.6f}+({zoom_end:.6f}-{zoom_start:.6f})*{progress})"
    scaled_width = f"ceil({width}*{zoom_expr}/2)*2"
    scaled_height = f"ceil({height}*{zoom_expr}/2)*2"
    return (
        f"scale=w='{scaled_width}':h='{scaled_height}':eval=frame,"
        f"crop={width}:{height}:x='(iw-{width})*{pan_x:.4f}':y='(ih-{height})*{pan_y:.4f}'"
    )


def _v360_sendcmd_filter(shot: dict[str, Any] | None, duration: float | None, command_path: Path | None, fov_command: str | None = "h_fov") -> str:
    if not shot or command_path is None or duration is None or duration <= 0:
        return ""
    commands = _v360_motion_commands(shot, float(duration), fov_command=fov_command)
    if not commands:
        return ""
    command_path.write_text("".join(commands), encoding="utf-8")
    return f"sendcmd=f={_escape_filter_path(command_path)},"


def _v360_motion_commands(shot: dict[str, Any], duration: float, fov_command: str | None = "h_fov") -> list[str]:
    step = 1.0 / TARGET_EXPORT_FPS
    count = max(1, int(math.ceil(duration / step)))
    commands: list[str] = []
    for index in range(count + 1):
        t = min(duration, index * step)
        yaw, pitch, fov = _v360_motion_at(shot, duration, t)
        commands.append(f"{t:.6f} sphere yaw {yaw:.6f};\n")
        commands.append(f"{t:.6f} sphere pitch {pitch:.6f};\n")
        if fov_command:
            commands.append(f"{t:.6f} sphere {fov_command} {fov:.6f};\n")
    return commands


def _v360_motion_at(shot: dict[str, Any], duration: float, t: float) -> tuple[float, float, float]:
    duration = max(0.001, duration)
    target_yaw = _shot_yaw(shot)
    target_pitch = _shot_float(shot, "pitch", 0.0)
    target_fov = _effective_flat_fov(shot)
    if shot.get("type") == "planet":
        yaw = target_yaw + _shot_float(shot, "spin_deg_per_sec", 18.0) * max(0.0, t)
        return _signed_yaw(yaw), target_pitch, target_fov

    previous = shot.get("previous_shot") if isinstance(shot.get("previous_shot"), dict) else None
    pan_duration = 0.0
    yaw = target_yaw
    pitch = target_pitch
    fov = target_fov
    if previous:
        pan_duration = min(_shot_float(shot, "transition_sec", 0.45), duration / 3.0)
        if pan_duration > 0 and t < pan_duration:
            amount = max(0.0, min(1.0, t / pan_duration))
            yaw = _lerp_signed_yaw(_shot_yaw(previous), target_yaw, amount)
            pitch = _lerp_float(_shot_float(previous, "pitch", target_pitch), target_pitch, amount)
            fov = _lerp_float(_shot_float(previous, "fov", target_fov), target_fov, amount)
            return _signed_yaw(yaw), pitch, fov

    hold_duration = max(0.001, duration - pan_duration)
    hold_amount = max(0.0, min(1.0, (t - pan_duration) / hold_duration))
    yaw += _shot_float(shot, "drift_yaw_deg", 0.0) * (hold_amount - 0.5)
    pitch += _shot_float(shot, "drift_pitch_deg", 0.0) * (hold_amount - 0.5)
    fov += _shot_float(shot, "fov_delta_deg", 0.0) * (hold_amount - 0.5)
    return _signed_yaw(yaw), pitch, fov


def _lerp_float(start: float, end: float, amount: float) -> float:
    return start + (end - start) * max(0.0, min(1.0, amount))


def _lerp_signed_yaw(start: float, end: float, amount: float) -> float:
    delta = ((end - start + 540.0) % 360.0) - 180.0
    return start + delta * max(0.0, min(1.0, amount))


def _signed_yaw(value: float) -> float:
    value = float(value) % 360.0
    if value > 180.0:
        value -= 360.0
    return value


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _export_source_filter(probe: dict[str, Any], shot: dict[str, Any] | None = None, duration: float | None = None, command_path: Path | None = None) -> str:
    """Prepare source pixels for export while leaving fps conversion to the segment timing filter."""
    yaw = _shot_yaw(shot)
    pitch = _shot_float(shot, "pitch", 0.0)
    fov = _effective_flat_fov(shot)
    command_prefix = _v360_sendcmd_filter(shot, duration, command_path, fov_command="v_fov")
    if probe.get("projection") == "raw_insv":
        insv_fov = int(probe.get("insv_fov") or 190)
        spatial = f"v360=input=dfisheye:output=e:ih_fov={insv_fov}:iv_fov={insv_fov}:interp=lanczos,{command_prefix}v360@sphere=input=equirect:output=flat:yaw={yaw:.3f}:pitch={pitch:.3f}:v_fov={fov:.3f}:w=1920:h=1080:interp=lanczos"
    elif probe.get("projection") == "equirect":
        spatial = f"{command_prefix}v360@sphere=input=equirect:output=flat:yaw={yaw:.3f}:pitch={pitch:.3f}:v_fov={fov:.3f}:w=1920:h=1080:interp=lanczos"
    elif probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
        spatial = f"{SDR_TONEMAP_FILTER},scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        spatial = EVEN_SDR_FILTER
    return spatial


def _spherical_shot(segment: dict[str, Any]) -> dict[str, Any] | None:
    shot = segment.get("spherical_shot")
    return shot if isinstance(shot, dict) else None


def _effective_flat_fov(shot: dict[str, Any] | None) -> float:
    fov = _shot_float(shot, "fov", 100.0)
    shot_type = str((shot or {}).get("type") or "")
    if shot_type == "planet":
        minimum = 140.0
    elif shot_type in {"full_stage", "audience_stage_wide"}:
        minimum = 115.0
    elif shot_type:
        minimum = 95.0
    else:
        minimum = 100.0
    return max(minimum, min(190.0, fov))


def _shot_float(shot: dict[str, Any] | None, key: str, fallback: float) -> float:
    try:
        return float((shot or {}).get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _shot_yaw(shot: dict[str, Any] | None) -> float:
    yaw = _shot_float(shot, "yaw", 0.0) % 360.0
    if yaw > 180.0:
        yaw -= 360.0
    return yaw


def _spherical_shot_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        shot = _spherical_shot(segment) or {}
        label = str(shot.get("label") or shot.get("type") or "").strip()
        if label:
            usage[label] = usage.get(label, 0) + 1
    return usage


def _segment_filtergraph(
    platform: str,
    duration: float,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    has_watermark: bool = True,
    text_enabled: bool = True,
    intro_fade: bool = False,
    outro_fade: bool = False,
    intro_logo: bool = False,
    outro_logo: bool = False,
    source_filter: str | None = None,
    motion_filter: str | None = None,
    frame_count: int | None = None,
) -> str:
    filters = []
    if source_filter:
        filters.append(source_filter)
    filters.extend([_base_video_filter(platform), motion_filter, _color_filter(color_profile)])
    filters.append(_exact_cadence_filter(frame_count) if frame_count else _constant_cadence_filter())
    if intro_fade:
        filters.append(f"fade=t=in:st=0:d={CONTENT_FADE_DURATION:.3f}")
    if outro_fade:
        filters.append(f"fade=t=out:st={max(0.0, duration - CONTENT_FADE_DURATION):.3f}:d={CONTENT_FADE_DURATION:.3f}")
    if text_enabled:
        filters.extend(_text_filters(platform, duration, overlay_config))
    filters.append(f"tpad=stop_mode=clone:stop_duration={1.0 / TARGET_EXPORT_FPS:.6f}")
    filters.append("format=yuv420p")
    graph = f"[0:v]{','.join(filter for filter in filters if filter)}[base]"
    if not has_watermark:
        return f"{graph};[base]copy[v]"
    margin = 40 if platform == "youtube" else 28
    wm_height = 65 if platform == "youtube" else 58
    if not intro_logo and not outro_logo:
        return (
            f"{graph};"
            f"[1:v]format=rgba,scale=-1:{wm_height},colorchannelmixer=aa=0.70[wm];"
            f"[base][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto[v]"
        )
    return _logo_overlay_filtergraph(graph, platform, duration, intro_logo, outro_logo, margin=margin, wm_height=wm_height)


def _logo_overlay_filtergraph(graph: str, platform: str, duration: float, intro_logo: bool, outro_logo: bool, margin: int, wm_height: int) -> str:
    """Overlay transparent watermark and optional large intro/outro logo from input 1."""
    logo_height = int(_target_size(platform)[1] * 0.70)
    logo_fade_filters = []
    logo_labels = []
    split_outputs = ["wm_src"]
    if intro_logo:
        split_outputs.append("intro_src")
    if outro_logo:
        split_outputs.append("outro_src")
    split_count = len(split_outputs)
    split = f"[1:v]format=rgba,split={split_count}" + "".join(f"[{label}]" for label in split_outputs)
    if intro_logo:
        logo_fade_filters.append(
            f"[intro_src]scale=-1:{logo_height},fade=t=in:st=1.05:d=3.30:alpha=1,fade=t=out:st=6.00:d=3.30:alpha=1[intro]"
        )
        logo_labels.append(("intro", "enable='lt(t,9.6)'"))
    if outro_logo:
        logo_fade_filters.append(
            f"[outro_src]scale=-1:{logo_height},fade=t=in:st=0:d=3.30:alpha=1,fade=t=out:st=6.30:d=3.00:alpha=1[outro]"
        )
        logo_labels.append(("outro", f"enable='gte(t,{max(0.0, duration - 9.6):.3f})'"))
    wm = f"[wm_src]scale=-1:{wm_height},colorchannelmixer=aa=0.70[wm]"
    chain_input = "base"
    chain_filters = []
    for index, (label, enable) in enumerate(logo_labels):
        output = "with_logo" if index == len(logo_labels) - 1 else f"with_logo_{index}"
        chain_filters.append(f"[{chain_input}][{label}]overlay=(W-w)/2:(H-h)/2:format=auto:{enable}[{output}]")
        chain_input = output
    return (
        f"{graph};"
        f"{split};"
        f"{wm};"
        + ";".join(logo_fade_filters + chain_filters)
        + ";"
        f"[{chain_input}][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto[v]"
    )


def _segment_source_info(project: Project, segment: dict[str, Any]) -> dict[str, Any]:
    """Resolve the original source and analysis proxy for an edit segment."""
    segment_source = str(segment.get("source_path") or "")
    segment_proxy = str(segment.get("clip_path") or "")
    for record in project.data.get("inputs", {}).get("videos", []):
        normalized = record.get("normalized") or {}
        candidates = {str(record.get("path") or ""), str(normalized.get("path") or "")}
        if segment_source in candidates or segment_proxy in candidates:
            return {
                "source_path": str(record.get("path") or segment_source or segment_proxy),
                "proxy_path": str(normalized.get("path") or segment_proxy),
                "probe": record.get("probe") or {},
                "cache_key": record.get("cache_key") or normalized.get("cache_key") or source_cache_key(record),
                "paired_path": record.get("paired_path") or (record.get("probe") or {}).get("paired_path"),
            }
    return {
        "source_path": segment_source or segment_proxy,
        "proxy_path": segment_proxy,
        "probe": segment.get("probe") or {},
        "cache_key": stable_fingerprint({"source": segment_source, "proxy": segment_proxy})[:24],
    }


def cached_segment_path(
    project: Project,
    segment: dict[str, Any],
    platform: str,
    video_bitrate: int,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    intro_fade: bool,
    outro_fade: bool,
    intro_logo: bool = False,
    outro_logo: bool = False,
) -> Path:
    """Return the global cache path for a rendered segment recipe."""
    source = _segment_source_info(project, segment)
    recipe = stable_fingerprint(
        {
            "cache_key": source["cache_key"],
            "source_path": source["source_path"],
            "clip_start_sec": round(float(segment.get("clip_start_sec") or 0.0), 3),
            "duration_sec": round(float(segment.get("duration_sec") or 0.0), 3),
            "master_start_sec": round(float(segment.get("master_start_sec") or 0.0), 3),
            "platform": platform,
            "video_bitrate": video_bitrate,
            "overlay": overlay_config,
            "color": color_profile,
            "intro_fade": intro_fade,
            "outro_fade": outro_fade,
            "intro_logo": intro_logo,
            "outro_logo": outro_logo,
            "spherical_shot": _spherical_shot(segment) or {},
            "motion": segment.get("motion") or {},
            "normalization_version": NORMALIZATION_VERSION,
            "export_segment_recipe": EXPORT_SEGMENT_RECIPE_VERSION,
        }
    )[:24]
    return global_segment_path(recipe)


def _verify_moving_segment(path: Path, duration: float, label: str, command_line: str) -> None:
    """Fail when a rendered segment decodes as identical frames at two timestamps."""
    if duration < 1.0:
        return
    first_at = max(0.10, min(duration * 0.20, max(0.10, duration - 0.90)))
    second_at = min(duration - 0.10, max(duration * 0.80, first_at + min(1.0, duration * 0.40)))
    _verify_moving_frames(path, first_at, second_at, label, f"segment={path} command={command_line}")


def _verify_segment_frame_duration(path: Path, frame_count: int, label: str) -> None:
    expected = frame_count / TARGET_EXPORT_FPS
    frames = _video_frame_count(path)
    actual = frames / TARGET_EXPORT_FPS
    if frames != frame_count:
        raise FFmpegError(f"Segment duration is not frame-exact for {label}: expected {expected:.6f}s ({frame_count} frames), got {actual:.6f}s ({frames} frames)")


def _video_frame_count(path: Path) -> int:
    result = subprocess.run(
        [
            _ffprobe_path(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise FFmpegError((result.stderr or "").strip() or f"Could not count segment frames: {path}")
    try:
        return int((result.stdout or "0").strip().splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise FFmpegError(f"Could not parse segment frame count for {path}: {result.stdout!r}") from exc


def _verify_moving_window(path: Path, start: float, duration: float, label: str, context: str) -> None:
    """Fail when a window inside a rendered video decodes as identical frames."""
    if duration < 1.0:
        return
    first_at = start + max(0.10, min(duration * 0.25, duration - 0.75))
    second_at = start + min(duration - 0.10, max(duration * 0.75, (first_at - start) + min(1.0, duration * 0.40)))
    _verify_moving_frames(path, first_at, second_at, label, context)


def _verify_moving_frames(path: Path, first_at: float, second_at: float, label: str, context: str) -> None:
    """Compare two decoded frames from one file."""
    if second_at <= first_at:
        return
    first = _frame_md5(path, first_at)
    second = _frame_md5(path, second_at)
    if first and second and first == second:
        raise FFmpegError(
            f"Rendered static segment for {label}: frame hashes were identical at {first_at:.2f}s and {second_at:.2f}s. "
            f"{context}"
        )


def _verify_joined_output(path: Path, segments: list[dict[str, Any]], timeline_offset: float = 0.0) -> None:
    """Verify final concat output timing and per-source motion after the join."""
    if len(segments) < 2:
        return
    timeline: list[tuple[float, dict[str, Any]]] = []
    cursor = timeline_offset
    for segment in segments:
        timeline.append((cursor, segment))
        cursor += max(0.0, float(segment.get("duration_sec") or 0.0))

    checked_sources: set[str] = set()
    for start, segment in timeline:
        source = str(segment.get("source_path") or segment.get("clip_path") or "")
        if not source or source in checked_sources:
            continue
        duration = max(0.0, float(segment.get("duration_sec") or 0.0))
        if duration < 1.0:
            continue
        checked_sources.add(source)
        _verify_moving_window(
            path,
            start,
            duration,
            Path(source).name,
            f"final={path} window_start={start:.3f} window_duration={duration:.3f}",
        )

    boundaries: list[float] = []
    cursor = timeline_offset
    for segment in segments[:-1]:
        cursor += max(0.0, float(segment.get("duration_sec") or 0.0))
        boundaries.append(cursor)
        if len(boundaries) >= 3:
            break
    expected_delta = 1.0 / TARGET_EXPORT_FPS
    for boundary in boundaries:
        start = max(0.0, boundary - expected_delta * 5)
        pts = _frame_pts_times(path, start, expected_delta * 12)
        if len(pts) < 3:
            raise FFmpegError(f"Could not verify final output PTS near join boundary {boundary:.3f}s in {path}")
        deltas = [later - earlier for earlier, later in zip(pts, pts[1:])]
        if any(delta <= 0 for delta in deltas):
            raise FFmpegError(f"Non-monotonic final output PTS near join boundary {boundary:.3f}s in {path}: {pts[:12]}")
        tiny = [delta for delta in deltas if delta < expected_delta * 0.50]
        if tiny:
            raise FFmpegError(
                f"Collapsed final output PTS near join boundary {boundary:.3f}s in {path}: "
                f"min_delta={min(tiny):.6f}, expected~{expected_delta:.6f}, pts={pts[:12]}"
            )
        long = [delta for delta in deltas if delta > expected_delta * (1.0 + FRAME_INTERVAL_TOLERANCE)]
        if long:
            raise FFmpegError(
                f"Stretched final output PTS near join boundary {boundary:.3f}s in {path}: "
                f"max_delta={max(long):.6f}, expected~{expected_delta:.6f}, pts={pts[:12]}"
            )
    _verify_video_cadence(path, f"final output {path}", start=0.0, duration=_media_duration(str(path)))


def _verify_final_audio(
    path: Path,
    segments: list[dict[str, Any]],
    master_path: str,
    audio_start: float,
    timeline_offset: float,
    audio_delay: float = 0.0,
    duration: float | None = None,
    content_start: float | None = None,
    content_end: float | None = None,
) -> None:
    """Verify final file has one audio stream and no obvious gaps at early joins."""
    metadata = _probe_streams(path)
    audio_streams = [stream for stream in metadata.get("streams") or [] if stream.get("codec_type") == "audio"]
    if len(audio_streams) != 1:
        raise FFmpegError(f"Expected exactly one continuous final audio stream in {path}, found {len(audio_streams)}")
    try:
        source_audio_duration = _media_duration(master_path)
    except Exception:
        source_audio_duration = 0.0
    boundaries: list[float] = []
    cursor = timeline_offset
    for segment in segments[:-1]:
        cursor += max(0.0, float(segment.get("duration_sec") or 0.0))
        boundaries.append(cursor)
        if len(boundaries) >= 3:
            break
    for boundary in boundaries:
        if boundary < audio_delay:
            continue
        if _time_overlaps_audio_fade(boundary - 0.080, 0.160, duration, content_start, content_end):
            LOGGER.info("Skipping final audio join RMS jump check inside intentional fade window at %.3fs", boundary)
            continue
        source_time = audio_start + boundary - audio_delay
        if source_audio_duration and source_time + 0.080 >= source_audio_duration:
            continue
        before = _audio_rms(path, max(0.0, boundary - 0.080), 0.060)
        after = _audio_rms(path, boundary + 0.020, 0.060)
        expected_before = _expected_master_rms(master_path, source_time - 0.080, 0.060, boundary - 0.080, duration, content_start, content_end)
        expected_after = _expected_master_rms(master_path, source_time + 0.020, 0.060, boundary + 0.020, duration, content_start, content_end)
        if before <= 0.0005 or after <= 0.0005:
            if min(expected_before, expected_after) <= 0.0005:
                continue
            raise FFmpegError(f"Possible audio silence gap near segment join {boundary:.3f}s in {path}: before={before:.6f} after={after:.6f}")
        ratio = max(before, after) / max(0.000001, min(before, after))
        expected_ratio = max(expected_before, expected_after) / max(0.000001, min(expected_before, expected_after))
        if ratio > 8.0 and ratio > expected_ratio * 2.0:
            raise FFmpegError(f"Possible audio level jump near segment join {boundary:.3f}s in {path}: before={before:.6f} after={after:.6f}")


def _probe_streams(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            _ffprobe_path(),
            "-v",
            "error",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise FFmpegError((result.stderr or "").strip() or f"Could not inspect streams: {path}")
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"Could not parse stream probe for {path}: {exc}") from exc


def _audio_rms(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            _ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "-",
        ],
        check=False,
        capture_output=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise FFmpegError((result.stderr.decode("utf-8", "ignore") if isinstance(result.stderr, bytes) else result.stderr or "").strip() or f"Could not decode audio: {path}")
    data = result.stdout or b""
    if len(data) < 2:
        return 0.0
    sample_count = len(data) // 2
    total = 0.0
    for index in range(0, sample_count * 2, 2):
        sample = int.from_bytes(data[index : index + 2], byteorder="little", signed=True) / 32768.0
        total += sample * sample
    return (total / max(1, sample_count)) ** 0.5


def _expected_master_rms(
    master_path: str,
    source_start: float,
    window_duration: float,
    timeline_start: float,
    total_duration: float | None,
    content_start: float | None,
    content_end: float | None,
) -> float:
    if source_start < 0:
        return 0.0
    rms = _audio_rms(Path(master_path), source_start, window_duration)
    gain = _audio_gain_at(timeline_start + window_duration / 2.0, total_duration, content_start, content_end)
    return rms * gain


def _audio_gain_at(timestamp: float, total_duration: float | None, content_start: float | None, content_end: float | None) -> float:
    gain = 1.0
    if content_start is not None and timestamp < content_start:
        gain = 1.0
    if content_start is not None and content_start <= timestamp < content_start + CONTENT_FADE_DURATION:
        gain *= max(0.0, min(1.0, (timestamp - content_start) / CONTENT_FADE_DURATION))
    if content_end is not None and content_end - CONTENT_FADE_DURATION < timestamp <= content_end:
        gain *= max(0.0, min(1.0, (content_end - timestamp) / CONTENT_FADE_DURATION))
    if content_end is not None and timestamp > content_end:
        return 0.0
    if total_duration is not None and timestamp > total_duration:
        return 0.0
    return gain


def _time_overlaps_audio_fade(
    start: float,
    duration: float,
    total_duration: float | None,
    content_start: float | None,
    content_end: float | None,
) -> bool:
    end = start + duration
    for fade_start, fade_end in _audio_fade_windows(total_duration, content_start, content_end):
        if start < fade_end and end > fade_start:
            return True
    return False


def _audio_fade_windows(total_duration: float | None, content_start: float | None, content_end: float | None) -> list[tuple[float, float]]:
    windows: list[tuple[float, float]] = []
    if content_start is not None:
        windows.append((max(0.0, content_start), max(0.0, content_start + CONTENT_FADE_DURATION)))
    if content_end is not None:
        windows.append((max(0.0, content_end - CONTENT_FADE_DURATION), max(0.0, content_end)))
    if total_duration is not None:
        windows = [(max(0.0, start), min(total_duration, end)) for start, end in windows if end > 0.0 and start < total_duration]
    return [(start, end) for start, end in windows if end > start]


def _audio_gain_curve_samples(
    total_duration: float,
    content_start: float,
    content_end: float,
    step: float = 1.0 / TARGET_EXPORT_FPS,
) -> list[tuple[float, float]]:
    samples: list[tuple[float, float]] = []
    count = int(round(total_duration / step)) + 1
    for index in range(count):
        timestamp = min(total_duration, index * step)
        samples.append((round(timestamp, 6), _audio_gain_at(timestamp, total_duration, content_start, content_end)))
    return samples


def _frame_md5(path: Path, timestamp: float) -> str | None:
    ffmpeg = _ffmpeg_path()
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "md5",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise FFmpegError((result.stderr or "").strip() or f"Could not verify rendered segment frames: {path}")
    output = (result.stdout or "").strip()
    if "=" in output:
        return output.split("=", 1)[1].strip()
    return output or None


def _frame_pts_times(path: Path, start: float, duration: float) -> list[float]:
    ffprobe = _ffprobe_path()
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-read_intervals",
            f"{start:.6f}%+{duration:.6f}",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise FFmpegError((result.stderr or "").strip() or f"Could not inspect frame PTS: {path}")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"Could not parse frame PTS for {path}: {exc}") from exc
    pts: list[float] = []
    for frame in payload.get("frames") or []:
        value = frame.get("best_effort_timestamp_time")
        try:
            pts.append(float(value))
        except (TypeError, ValueError):
            continue
    return pts


def _verify_video_cadence(path: Path, label: str, start: float = 0.0, duration: float | None = None) -> None:
    expected_delta = 1.0 / TARGET_EXPORT_FPS
    window = duration if duration is not None else max(0.0, _media_duration(str(path)) - start)
    if window <= expected_delta * 3:
        return
    sample_window = min(2.0, window)
    if window <= sample_window + expected_delta:
        sample_starts = [start]
    else:
        span = max(0.0, window - sample_window)
        sample_starts = [start + span * fraction for fraction in (0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0)]
    pts: list[float] = []
    for sample_start in sample_starts:
        pts.extend(_frame_pts_times(path, sample_start, sample_window))
    if len(pts) < 3:
        raise FFmpegError(f"Could not verify video cadence for {label}: only {len(pts)} frame timestamps")
    deltas = [
        later - earlier
        for earlier, later in zip(pts, pts[1:])
        if later > earlier and later - earlier < sample_window
    ]
    if not deltas:
        raise FFmpegError(f"Could not verify video cadence for {label}: no usable frame timestamp deltas")
    low = expected_delta * (1.0 - FRAME_INTERVAL_TOLERANCE)
    high = expected_delta * (1.0 + FRAME_INTERVAL_TOLERANCE)
    outliers = [delta for delta in deltas if delta < low or delta > high]
    if outliers:
        raise FFmpegError(
            f"Irregular video cadence in {label}: min_delta={min(deltas):.6f}, max_delta={max(deltas):.6f}, "
            f"expected~{expected_delta:.6f}, outliers={len(outliers)}"
        )


def _color_filter(color_profile: dict[str, Any]) -> str:
    brightness = max(-0.08, min(0.08, float(color_profile.get("brightness_adjust") or 0.0)))
    saturation = max(0.90, min(1.10, float(color_profile.get("saturation_adjust") or 1.0)))
    return f"eq=brightness={brightness:.4f}:saturation={saturation:.4f}"


def _clip_fates(project: Project, plan: dict[str, Any], segments: list[dict[str, Any]], total_duration: float) -> list[dict[str, Any]]:
    """Summarize every registered clip as used, excluded, or not covering the edit."""
    used_seconds: dict[str, float] = {}
    names_by_path: dict[str, str] = {}
    for segment in segments:
        path = str(segment.get("clip_path") or "")
        if not path:
            continue
        used_seconds[path] = used_seconds.get(path, 0.0) + float(segment.get("duration_sec") or 0.0)
        names_by_path[path] = str(segment.get("filename") or Path(path).name)

    excluded_by_path: dict[str, dict[str, Any]] = {}
    excluded_by_name: dict[str, dict[str, Any]] = {}
    for item in plan.get("excluded_clips") or []:
        diagnostic = item.get("diagnostic") or {}
        for key in (diagnostic.get("path"), diagnostic.get("source_path")):
            if key:
                excluded_by_path[str(key)] = item
        excluded_by_name[str(item.get("filename") or diagnostic.get("filename") or "")] = item

    diagnostics = list(plan.get("clip_diagnostics") or [])
    selection_by_path: dict[str, dict[str, Any]] = {}
    selection_by_name: dict[str, dict[str, Any]] = {}
    for item in plan.get("selection_diagnostics") or []:
        for key in (item.get("path"), item.get("source_path")):
            if key:
                selection_by_path[str(key)] = item
        if item.get("filename"):
            selection_by_name[str(item["filename"])] = item
    diagnostic_keys = {
        str(value)
        for diagnostic in diagnostics
        for value in (diagnostic.get("path"), diagnostic.get("source_path"))
        if value
    }
    for record in project.data.get("inputs", {}).get("videos", []):
        media_path = record_media_path(record)
        if str(media_path) in diagnostic_keys or str(record.get("path")) in diagnostic_keys:
            continue
        reason = record.get("not_a_video_reason") or "not covering this song"
        diagnostics.append(
            {
                "clip_id": None,
                "filename": Path(str(record.get("path") or media_path)).name,
                "path": media_path,
                "source_path": record.get("path"),
                "valid_video": record_is_usable_camera_video(record),
                "reason": reason,
            }
        )

    fates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for diagnostic in diagnostics:
        path = str(diagnostic.get("path") or diagnostic.get("source_path") or "")
        filename = str(diagnostic.get("filename") or names_by_path.get(path) or Path(path).name or "clip")
        excluded = excluded_by_path.get(path) or excluded_by_name.get(filename)
        selection = selection_by_path.get(path) or selection_by_path.get(str(diagnostic.get("source_path") or "")) or selection_by_name.get(filename)
        if excluded:
            status = "excluded"
            used_percent = 0.0
            reason = str(excluded.get("reason") or "excluded")
        elif path in used_seconds:
            status = "used"
            used_percent = used_seconds[path] / max(total_duration, 0.1) * 100
            reason = "using full 360 clip" if plan.get("platform") == "360" else _selection_reason("used in final edit", selection)
        elif diagnostic.get("valid_video") is False:
            status = "excluded"
            used_percent = 0.0
            reason = str(diagnostic.get("reason") or "not a usable camera video")
        else:
            status = "not_covering"
            used_percent = 0.0
            reason = _selection_reason("not covering this song", selection)
        seen.add(path or filename)
        fates.append(
            {
                "clip_id": diagnostic.get("clip_id"),
                "filename": filename,
                "status": status,
                "reason": reason,
                "used_percent": round(used_percent, 1),
                "confidence": diagnostic.get("confidence"),
                "threshold": diagnostic.get("threshold"),
                "offset_sec": diagnostic.get("offset_sec"),
                "verification": diagnostic.get("verification"),
                "manual_override": diagnostic.get("manual_override"),
                "projection": diagnostic.get("projection"),
                "eligible_segments": selection.get("eligible_segments") if selection else None,
                "chosen_segments": selection.get("chosen_segments") if selection else None,
                "covered_seconds": selection.get("covered_seconds") if selection else None,
                "eligible_seconds": selection.get("eligible_seconds") if selection else None,
                "chosen_seconds": selection.get("chosen_seconds") if selection else None,
            }
        )
    for path, seconds in used_seconds.items():
        key = path or names_by_path.get(path, "")
        if key in seen:
            continue
        fates.append(
            {
                "filename": names_by_path.get(path) or Path(path).name,
                "status": "used",
                "reason": "used in final edit",
                "used_percent": round(seconds / max(total_duration, 0.1) * 100, 1),
            }
        )
    return fates


def _selection_reason(base: str, selection: dict[str, Any] | None) -> str:
    if not selection:
        return base
    eligible = int(selection.get("eligible_segments") or 0)
    chosen = int(selection.get("chosen_segments") or 0)
    covered = float(selection.get("covered_seconds") or 0.0)
    chosen_seconds = float(selection.get("chosen_seconds") or 0.0)
    confidence = selection.get("confidence")
    if eligible and chosen:
        return f"{base}; chosen {chosen}/{eligible} eligible segments ({chosen_seconds:.1f}s of {covered:.1f}s covered, confidence {float(confidence or 0.0):.1f})"
    if eligible and not chosen:
        return f"eligible for {eligible} segments but not selected by camera rotation ({covered:.1f}s covered, confidence {float(confidence or 0.0):.1f})"
    return f"{base}; {covered:.1f}s overlaps the requested window"


def _text_filters(platform: str, duration: float, config: dict[str, Any]) -> list[str]:
    title = str(config.get("title") or "").strip()
    band = str(config.get("band_name") or "").strip()
    handle = str(config.get("handle") or "").strip()
    filters: list[str] = []
    if title:
        if platform == "youtube":
            text = title if not band else f"{title} • {band}"
            filters.append(_drawtext(text, "x=64:y=h-th-92:fontsize=46:enable='between(t,0,4)'"))
        else:
            filters.append(_drawtext(title, "x=(w-tw)/2:y=(h-th)/2:fontsize=54:enable='between(t,0,4)'"))
    if platform in {"instagram", "tiktok"} and handle:
        filters.append(_drawtext(handle, f"x=(w-tw)/2:y=h-th-130:fontsize=38:enable='gte(t,{max(0.0, duration - 4):.3f})'"))
    return filters


def _drawtext(text: str, placement: str) -> str:
    font = _font_path()
    font_part = f"fontfile='{_escape_filter_value(str(font))}':" if font else ""
    return (
        "drawtext="
        f"{font_part}text='{_escape_filter_value(text)}':"
        "fontcolor=white:shadowcolor=black@0.60:shadowx=2:shadowy=2:"
        f"{placement}"
    )


def _overlay_config(platform: str, first_segment: dict[str, Any]) -> dict[str, Any]:
    config = _global_config()
    return {
        "platform": platform,
        "title": first_segment.get("title") or config.get("project_name"),
        "band_name": config.get("band_name") or "",
        "handle": config.get("handle") or "",
    }


def _global_config() -> dict[str, Any]:
    path = Path.home() / "ZuckerVideos" / "config.json"
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _watermark_path() -> Path | None:
    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "logo_watermark.png",
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "watermark.png",
        Path(__file__).resolve().parents[2] / "assets" / "logo_watermark.png",
        Path(__file__).resolve().parents[2] / "assets" / "watermark.png",
    ]
    for candidate in candidates:
        if candidate.exists() and _has_real_alpha(candidate):
            return candidate
    return None


def _logo_path() -> Path | None:
    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "logo_editor_green.png",
        Path(__file__).resolve().parents[2] / "assets" / "logo_editor_green.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return _watermark_path()


def _intro_logo_path() -> Path | None:
    candidates = [
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "logo_watermark.png",
        Path(__file__).resolve().parents[2] / "assets" / "logo_watermark.png",
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "logo_intro_black.png",
        Path(__file__).resolve().parents[2] / "assets" / "logo_intro_black.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _can_blend_intro_outro() -> bool:
    return _watermark_path() is not None


def _has_real_alpha(path: Path) -> bool:
    try:
        from PIL import Image

        image = Image.open(path).convert("RGBA")
        alpha = set(image.getchannel("A").getdata())
        if len(alpha) >= 2 and min(alpha) < 250:
            return True
        LOGGER.error("Refusing watermark without real alpha transparency: %s", path)
        return False
    except Exception as exc:
        LOGGER.error("Could not validate watermark alpha for %s: %s", path, exc)
        return False


def _font_path() -> Path | None:
    for candidate in (
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/SFNS.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ):
        if candidate.exists():
            return candidate
    return None


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")


def _concat_file_line(path: Path) -> str:
    return "file '{}'\n".format(path.as_posix().replace("'", "'\\''"))


def _color_profiles_for_segments(project: Project, segments: list[dict[str, Any]], warnings: list[str] | None = None) -> dict[str, dict[str, Any]]:
    raw: dict[str, dict[str, float]] = {}
    warnings = warnings if warnings is not None else []
    for segment in segments:
        clip_path = str(segment.get("clip_path"))
        if clip_path and clip_path not in raw:
            profile, warning = cached_or_measure_clip_color(project, clip_path)
            if warning:
                warnings.append(warning)
            raw[clip_path] = profile
    valid = [profile for profile in raw.values() if profile]
    if not valid:
        return {path: {} for path in raw}
    target_luma = sum(profile["luma"] for profile in valid) / len(valid)
    target_sat = sum(profile["saturation"] for profile in valid) / len(valid)
    corrected: dict[str, dict[str, Any]] = {}
    for path, profile in raw.items():
        if not profile:
            corrected[path] = {}
            continue
        corrected[path] = color_correction_for_profile(profile, target_luma, target_sat)
    return corrected


def cached_or_measure_clip_color(project: Project, path: str) -> tuple[dict[str, float], str | None]:
    """Return cached color profile or measure bounded samples."""
    key = _color_cache_key(project, path)
    cache_path = global_cache_root() / "color" / f"{key}.json"
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and "luma" in cached and "saturation" in cached:
                return {"luma": float(cached["luma"]), "saturation": float(cached["saturation"])}, None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    profile, warning = measure_clip_color(path)
    if profile:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return profile, warning


def _color_cache_key(project: Project, path: str) -> str:
    for record in project.data.get("inputs", {}).get("videos", []):
        normalized = record.get("normalized") or {}
        if path in {record.get("path"), normalized.get("path")}:
            return str(record.get("cache_key") or normalized.get("cache_key") or source_cache_key(record))
    return stable_fingerprint({"path": str(Path(path).expanduser().resolve())})[:24]


def measure_clip_color(path: str) -> tuple[dict[str, float], str | None]:
    """Measure sampled luma/saturation using short ffmpeg signalstats windows."""
    try:
        duration = _media_duration(path)
    except Exception as exc:
        return {}, t("color_skipped", filename=Path(path).name, reason=str(exc))
    y_values: list[float] = []
    sat_values: list[float] = []
    for command in color_sample_commands(path, duration):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        except subprocess.TimeoutExpired:
            return {}, t("color_skipped", filename=Path(path).name, reason="sample timed out")
        if result.returncode != 0:
            return {}, t("color_skipped", filename=Path(path).name, reason=(result.stderr or "sample failed").strip()[:180])
        text = f"{result.stdout}\n{result.stderr}"
        y_values.extend(float(value) for value in re.findall(r"lavfi.signalstats.YAVG=([0-9.]+)", text))
        sat_values.extend(float(value) for value in re.findall(r"lavfi.signalstats.SATAVG=([0-9.]+)", text))
    if not y_values or not sat_values:
        return {}, t("color_skipped", filename=Path(path).name, reason="no signalstats samples")
    return {"luma": sum(y_values) / len(y_values), "saturation": sum(sat_values) / len(sat_values)}, None


def color_sample_commands(path: str, duration: float) -> list[list[str]]:
    """Build bounded color-sampling ffmpeg commands."""
    window = min(2.0, max(0.25, duration / 5.0))
    commands: list[list[str]] = []
    for fraction in (0.10, 0.30, 0.50, 0.70, 0.90):
        start = max(0.0, min(duration - window, duration * fraction))
        commands.append(
            [
                _ffmpeg_path(),
                "-hide_banner",
                "-nostdin",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{window:.3f}",
                "-i",
                path,
                "-vf",
                "signalstats,metadata=print",
                "-an",
                "-f",
                "null",
                "-",
            ]
        )
    return commands


def _media_duration(path: str) -> float:
    status = tool_status()
    ffprobe = status.get("ffprobe_path")
    if not ffprobe:
        raise FFmpegError("ffprobe is missing. Install it with: brew install ffmpeg")
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "ffprobe failed")
    return max(0.1, float((result.stdout or "0").strip() or 0.0))


def color_correction_for_profile(profile: dict[str, float], target_luma: float, target_sat: float) -> dict[str, float]:
    """Return subtle eq corrections toward a shared target."""
    luma = max(1.0, float(profile.get("luma") or target_luma or 128.0))
    sat = max(1.0, float(profile.get("saturation") or target_sat or 64.0))
    brightness = max(-0.08, min(0.08, (target_luma - luma) / 255.0 * 0.35))
    saturation = max(0.90, min(1.10, 1.0 + (target_sat - sat) / 255.0 * 0.60))
    return {"brightness_adjust": brightness, "saturation_adjust": saturation}


def _video_encode_args(codec: str, video_bitrate: int) -> list[str]:
    if codec == "h264_videotoolbox":
        return [
            "-c:v",
            codec,
            "-b:v",
            str(video_bitrate),
            "-maxrate",
            str(int(video_bitrate * 1.2)),
            "-bufsize",
            str(int(video_bitrate * 2)),
            "-profile:v",
            "high",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(video_bitrate),
        "-maxrate",
        str(int(video_bitrate * 1.2)),
        "-bufsize",
        str(int(video_bitrate * 2)),
    ]


def _bitrate_for_duration(duration: float) -> dict[str, Any]:
    seconds = max(1.0, duration)
    budget_bits = MAX_EXPORT_BYTES * 8 * 0.94
    target = int(max(300_000, budget_bits / seconds - AUDIO_BITRATE))
    warning = None
    if target < MIN_ACCEPTABLE_VIDEO_BITRATE:
        warning = t("bitrate_cap_warning")
    return {"video_bitrate": min(target, MAX_VIDEO_BITRATE), "warning": warning}


def _plan_duration(segments: list[dict[str, Any]]) -> float:
    return sum(max(0.0, float(segment.get("duration_sec") or 0.0)) for segment in segments)


def _camera_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        name = Path(str(segment.get("clip_path"))).name
        usage[name] = usage.get(name, 0) + 1
    return usage


def _ffmpeg_path() -> str:
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    return str(ffmpeg)


def _ffprobe_path() -> str:
    status = tool_status()
    ffprobe = status.get("ffprobe_path")
    if not ffprobe:
        raise FFmpegError("ffprobe is missing. Install it with: brew install ffmpeg")
    return str(ffprobe)


_FILTER_SUPPORT_CACHE: dict[str, bool] = {}


def _ffmpeg_supports_filter(name: str) -> bool:
    if name in _FILTER_SUPPORT_CACHE:
        return _FILTER_SUPPORT_CACHE[name]
    try:
        result = subprocess.run([_ffmpeg_path(), "-hide_banner", "-filters"], capture_output=True, text=True, check=False)
        supported = result.returncode == 0 and re.search(rf"\b{name}\b", result.stdout) is not None
    except Exception:
        supported = False
    _FILTER_SUPPORT_CACHE[name] = supported
    return supported
