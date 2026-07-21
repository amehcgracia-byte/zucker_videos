"""ffmpeg export stage for wizard edit plans."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import json
from pathlib import Path
from typing import Any

from core.ffmpeg import FFmpegError, tool_status
from core.messages import t
from core.media_validation import record_is_usable_camera_video, record_media_path
from core.normalization import NORMALIZATION_VERSION, global_cache_root, global_segment_path, normalization_filter, source_cache_key
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage
from core.stages.edit import load_edit_plan

MAX_EXPORT_BYTES = int(1.9 * 1024 * 1024 * 1024)
AUDIO_BITRATE = 192_000
MIN_ACCEPTABLE_VIDEO_BITRATE = 2_500_000
MAX_VIDEO_BITRATE = 18_000_000
TARGET_EXPORT_FPS = 30.0
TARGET_EXPORT_TIMESCALE = 30_000
EXPORT_SEGMENT_RECIPE_VERSION = 3


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
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError(t("missing_segments"))
        master = project.data["inputs"].get("master")
        if not master:
            raise ValueError(t("missing_master_for_export"))
        platform = plan.get("platform") or project.data["settings"].get("wizard", {}).get("platform") or "youtube"
        output_path = _output_path(project, platform)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = _plan_duration(segments)
        bitrate_info = _bitrate_for_duration(duration)
        warnings = list(plan.get("warnings") or [])
        if bitrate_info["warning"]:
            progress_callback(6, bitrate_info["warning"])
        _render_plan(project, segments, master["path"], output_path, platform, bitrate_info["video_bitrate"], warnings, progress_callback)
        progress_callback(95, t("saving_result"))
        path = artifact_path(project, "export_manifest.json")
        if bitrate_info["warning"]:
            warnings.append(bitrate_info["warning"])
        clip_fates = _clip_fates(project, plan, segments, duration)
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "render_logic": "edit-plan segments with master-audio slices; concat",
                "warnings": warnings,
                "max_export_bytes": MAX_EXPORT_BYTES,
                "target_video_bitrate": bitrate_info["video_bitrate"],
                "exports": [
                    {
                        "platform": platform,
                        "path": str(output_path),
                        "filename": output_path.name,
                        "duration_sec": duration,
                        "warnings": warnings,
                        "cut_count": int(plan.get("cut_count") or max(0, len(segments) - 1)),
                        "camera_usage": plan.get("camera_usage") or _camera_usage(segments),
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
        for index, segment in enumerate(segments, start=1):
            segment_duration = max(0.1, float(segment["duration_sec"]))
            base_percent = 10 + int(70 * rendered_duration / max(total_duration, 0.1))

            def segment_progress(local_percent: int, detail: str, *, index: int = index) -> None:
                segment_share = 70 * segment_duration / max(total_duration, 0.1)
                percent = base_percent + int(segment_share * local_percent / 100)
                progress_callback(min(84, percent), f"Rendering segment {index}/{len(segments)}: {detail}")

            color_profile = color_profiles.get(str(segment.get("clip_path")), {})
            intro_fade = index == 1
            outro_fade = index == len(segments)
            segment_path = cached_segment_path(project, segment, platform, video_bitrate, overlay_config, color_profile, intro_fade, outro_fade)
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
                    warnings=warnings,
                    command_recorder=commands,
                )
                if commands:
                    command_line = " ".join(commands[-1])
                segment_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(tmp_segment, segment_path)
                if rendered_from == "proxy":
                    warnings.append(f"Used proxy fallback for {Path(str(segment.get('source_path') or segment.get('clip_path'))).name}")
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
                    warnings,
                    command_line,
                    segment_duration,
                    Path(str(source_info.get("source_path") or segment.get("clip_path"))).name,
                )
            segment_paths.append(segment_path)
            rendered_duration += segment_duration
        concat_path = temp_dir / "concat.txt"
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
                str(output_path),
            ],
            total_duration,
            t("joining_segments"),
            lambda percent, detail: progress_callback(84 + int(percent * 11 / 100), detail),
        )
        if verify_motion:
            _verify_joined_output(output_path, segments)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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
    warnings: list[str] | None = None,
    command_recorder: list[list[str]] | None = None,
    force_proxy: bool = False,
) -> str:
    ffmpeg = _ffmpeg_path()
    duration = max(0.1, float(segment["duration_sec"]))
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
            watermark,
            progress_callback,
            intro_fade,
            outro_fade,
            command_recorder,
        )
    filter_complex = _segment_filtergraph(
        platform,
        duration,
        overlay_config or {},
        color_profile or {},
        bool(watermark),
        _ffmpeg_supports_filter("drawtext"),
        intro_fade=intro_fade,
        outro_fade=outro_fade,
        source_filter=normalization_filter(source.get("probe") or {}, fps=TARGET_EXPORT_FPS, proxy=False),
    )
    command_base = _segment_command_base(
        ffmpeg,
        source["source_path"],
        master_path,
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
        "-map",
            "1:a:0",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{TARGET_EXPORT_FPS:.3f}",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(TARGET_EXPORT_TIMESCALE),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
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
                watermark,
                progress_callback,
                intro_fade,
                outro_fade,
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
    watermark: Path | None,
    progress_callback: ProgressCallback | None,
    intro_fade: bool,
    outro_fade: bool,
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
    )
    proxy_command = _segment_command_base(ffmpeg, proxy_path, master_path, segment, duration)
    if watermark:
        proxy_command.extend(["-loop", "1", "-i", str(watermark)])
    proxy_command.extend(
        [
            "-filter_complex",
            proxy_filter,
            "-map",
            "[v]",
            "-map",
            "1:a:0",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{TARGET_EXPORT_FPS:.3f}",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            str(TARGET_EXPORT_TIMESCALE),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
        ]
    )
    command = proxy_command + _video_encode_args("libx264", video_bitrate) + [str(output_path)]
    if command_recorder is not None:
        command_recorder.append(command)
    _run_ffmpeg_progress(command, duration, Path(str(proxy_path)).name, progress_callback)
    return "proxy"


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
                warnings=warnings,
                command_recorder=commands,
                force_proxy=True,
            )
            command_line = " ".join(commands[-1]) if commands else "proxy fallback command unavailable"
            shutil.copy2(tmp_segment, segment_path)
            _verify_moving_segment(segment_path, segment_duration, label, command_line)
            return command_line


def _segment_command_base(ffmpeg: str, clip_path: str, master_path: str, segment: dict[str, Any], duration: float) -> list[str]:
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
        "-ss",
        f"{float(segment.get('master_start_sec') or 0.0):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(master_path)),
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


def _base_video_filter(platform: str) -> str:
    if platform in {"instagram", "tiktok"}:
        return "scale=608:1080:force_original_aspect_ratio=increase,crop=608:1080,setsar=1,format=yuv420p"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"


def _segment_filtergraph(
    platform: str,
    duration: float,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    has_watermark: bool = True,
    text_enabled: bool = True,
    intro_fade: bool = False,
    outro_fade: bool = False,
    source_filter: str | None = None,
) -> str:
    filters = []
    if source_filter:
        filters.append(source_filter)
    filters.extend([_base_video_filter(platform), _color_filter(color_profile)])
    filters.append(f"fps={TARGET_EXPORT_FPS:.3f},setpts=PTS-STARTPTS")
    if intro_fade:
        filters.append("fade=t=in:st=0:d=0.5")
    if outro_fade:
        filters.append(f"fade=t=out:st={max(0.0, duration - 0.5):.3f}:d=0.5")
    if text_enabled:
        filters.extend(_text_filters(platform, duration, overlay_config))
    filters.append("format=yuv420p")
    graph = f"[0:v]{','.join(filter for filter in filters if filter)}[base]"
    if not has_watermark:
        return f"{graph};[base]copy[v]"
    margin = 40 if platform == "youtube" else 28
    wm_height = 65 if platform == "youtube" else 58
    intro_height = 280 if platform == "youtube" else 220
    return (
        f"{graph};"
        f"[2:v]format=rgba,split=2[wm_src][intro_src];"
        f"[wm_src]scale=-1:{wm_height},colorchannelmixer=aa=0.70,fade=t=in:st=1.35:d=0.65:alpha=1[wm];"
        f"[intro_src]scale=-1:{intro_height},fade=t=in:st=0:d=0.35:alpha=1,fade=t=out:st=1.35:d=0.65:alpha=1[intro];"
        f"[base][intro]overlay=(W-w)/2:(H-h)/2:format=auto:enable='lt(t,2)'[with_intro];"
        f"[with_intro][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto:enable='gte(t,1.35)'[v]"
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


def _verify_joined_output(path: Path, segments: list[dict[str, Any]]) -> None:
    """Verify final concat output timing and per-source motion after the join."""
    if len(segments) < 2:
        return
    timeline: list[tuple[float, dict[str, Any]]] = []
    cursor = 0.0
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
    cursor = 0.0
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
            reason = _selection_reason("used in final edit", selection)
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
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "watermark_white.png",
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "logo_editor_white.png",
        Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web" / "watermark.png",
        Path(__file__).resolve().parents[2] / "assets" / "watermark_white.png",
        Path(__file__).resolve().parents[2] / "assets" / "logo_editor_white.png",
        Path(__file__).resolve().parents[2] / "assets" / "watermark.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


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
