"""ffmpeg export stage for wizard edit plans."""

from __future__ import annotations

import bisect
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import re
import shutil
import subprocess
import sys
import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

from core.build_info import build_info
from core.camera_moves import clip_curve_for_segment, interpolate_curve, limit_yaw_velocity, load_camera_moves, normalize_recorded_samples, recorded_move_covering, recorded_shot_for_segment
from core.operator_avoidance import count_avoidance_adjustments
from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.messages import t
from core.media_validation import record_is_usable_camera_video, record_media_path
from core.normalization import EVEN_SDR_FILTER, NORMALIZATION_VERSION, SDR_TONEMAP_FILTER, global_cache_root, global_segment_path, source_cache_key
from core.project import Project
from core.spherical_metadata import SphericalMetadataError, inject_spherical_metadata
from core.spherical_view import (
    MAX_SPHERICAL_FOV,
    NORMAL_FOV_MAX,
    NORMAL_FOV_MIN,
    STEREOGRAPHIC_FOV_THRESHOLD,
    effective_fov,
    paired_flat_fov,
    signed_yaw,
    view_parameters,
)
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage
from core.stages.edit import (
    PLANET_SPIN_DEG_PER_SEC,
    SPHERICAL_MAX_MOTION_FRACTION_PER_SEC,
    SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC,
    SPHERICAL_MIN_SWEEP_SPEED_DEG_PER_SEC,
    SPHERICAL_PRIMARY_DRIFT_FRACTION,
    SPHERICAL_SWEEP_SPEED_DEG_PER_SEC,
    load_edit_plan,
)

LOGGER = logging.getLogger(__name__)
MAX_EXPORT_BYTES = int(1.9 * 1024 * 1024 * 1024)
AUDIO_BITRATE = 192_000
MIN_ACCEPTABLE_VIDEO_BITRATE = 2_500_000
MAX_VIDEO_BITRATE = 18_000_000
TARGET_EXPORT_FPS = 30.0
TARGET_EXPORT_TIMESCALE = 30_000
# The reframing v360 instance is labelled so sendcmd can drive it per frame.
# The sendcmd target MUST be this exact label: ffmpeg matches the command target
# against the filter's instance name ("v360@sphere"), NOT the bare "@id" suffix.
# Targeting just "sphere" silently matches nothing, freezing all 360 motion.
SPHERE_V360_LABEL = "v360@sphere"
EXPORT_SEGMENT_RECIPE_VERSION = 21
# v17 adds byte-level and full-shot attestation to segment sidecars.  A file
# with a copied/reused sidecar is no longer accepted if its bytes or authored
# motion fields differ from the current render.
SPHERICAL_MOTION_RECIPE_VERSION = 21
# Emergency diagnostic switch; normal exports use the bounded motion path.
FORCE_STATIC_360_ISOLATION = False
SPHERICAL_HOLD_COMMAND_COUNT = 2
SPHERICAL_SHORT_SEGMENT_STATIC_SEC = 2.0
SPHERICAL_NORMAL_FOV_MIN = NORMAL_FOV_MIN
SPHERICAL_NORMAL_FOV_MAX = NORMAL_FOV_MAX
SPHERICAL_MAX_HOLD_YAW_DEG = 10.0
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
        run_id = _export_run_id()
        output_path = _output_path(project, platform, run_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content_duration = _plan_duration(segments)
        duration = content_duration + INTRO_DURATION + OUTRO_DURATION
        bitrate_info = _bitrate_for_duration(duration)
        warnings = list(plan.get("warnings") or [])
        if bitrate_info["warning"]:
            progress_callback(6, bitrate_info["warning"])
        if platform == "360":
            # The End trim the user actually set, so the outro can let the rest
            # of the song play out when the camera stopped recording early --
            # without spilling into audio the user trimmed away.
            window = plan.get("window") or {}
            song_end_sec = window.get("trim_end_sec")
            if song_end_sec is None and window.get("start_sec") is not None:
                song_end_sec = float(window.get("start_sec") or 0.0) + float(window.get("duration_sec") or 0.0)
            _render_360_plan(
                project,
                segments,
                master["path"],
                output_path,
                bitrate_info["video_bitrate"],
                warnings,
                progress_callback,
                song_end_sec=float(song_end_sec) if song_end_sec is not None else None,
            )
        else:
            _render_plan(project, segments, master["path"], output_path, platform, bitrate_info["video_bitrate"], warnings, progress_callback)
        progress_callback(95, t("saving_result"))
        path = artifact_path(project, "export_manifest.json")
        if bitrate_info["warning"]:
            warnings.append(bitrate_info["warning"])
        clip_fates = [] if platform == "360" else _clip_fates(project, plan, segments, content_duration)
        if platform != "360":
            _warn_unused_cameras(clip_fates, plan, warnings)
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
                "spherical_recording_usage": plan.get("spherical_recording_usage") or _spherical_recording_usage(segments),
                "operator_avoidance_segments": 0 if platform == "360" else count_avoidance_adjustments(segments),
                "performance": project.data.pop("_export_performance", None),
                "exports": [
                    {
                        "platform": platform,
                        "run_id": run_id,
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                        "path": str(output_path),
                        "filename": output_path.name,
                        "duration_sec": duration,
                        "warnings": warnings,
                        "cut_count": int(plan.get("cut_count") or max(0, len(segments) - 1)),
                        "camera_usage": {} if platform == "360" else (plan.get("camera_usage") or _camera_usage(segments)),
                        "spherical_shot_usage": plan.get("spherical_shot_usage") or _spherical_shot_usage(segments),
                        "spherical_recording_usage": plan.get("spherical_recording_usage") or _spherical_recording_usage(segments),
                        "operator_avoidance_segments": 0 if platform == "360" else count_avoidance_adjustments(segments),
                        "excluded_clips": plan.get("excluded_clips") or [],
                        "clip_fates": clip_fates,
                    }
                ],
            },
        )
        progress_callback(100, t("export_ready"))
        return self.outputs(project)


def _export_run_id() -> str:
    """Return a collision-proof, human-sortable ID for one export run."""
    return time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000_000:09d}"


def _output_path(project: Project, platform: str, run_id: str | None = None) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in project.data["name"]).strip() or "video"
    suffix = f"-{run_id}" if run_id else ""
    return project.exports_dir / f"{safe_name}-{platform}{suffix}.mp4"


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
        segment_workers = max(1, min(4, int(project.data.get("settings", {}).get("export", {}).get("segment_workers", 2))))
        render_started = time.perf_counter()
        render_stats: list[dict[str, Any]] = []
        progress_lock = threading.Lock()
        futures = []
        with ThreadPoolExecutor(max_workers=segment_workers) as executor:
            for index, segment in enumerate(render_segments, start=1):
                futures.append(
                    executor.submit(
                        _render_segment_job,
                        project, index, segment, len(render_segments), temp_dir, master_path,
                        platform, video_bitrate, overlay_config,
                        color_profiles.get(str(segment.get("clip_path")), {}), verify_motion,
                        progress_callback, progress_lock,
                    )
                )
            results = [future.result() for future in as_completed(futures)]
        results.sort(key=lambda item: int(item["index"]))
        expected_indices = list(range(1, len(render_segments) + 1))
        actual_indices = [int(item["index"]) for item in results]
        if actual_indices != expected_indices:
            raise FFmpegError(f"Rendered segment order is not contiguous: {actual_indices}")
        cache_owners: dict[str, dict[str, Any]] = {}
        for result in results:
            path_key = str(result.get("cache_path") or result["path"])
            identity = result.get("spherical_identity")
            previous_identity = cache_owners.get(path_key)
            if previous_identity is not None and previous_identity != identity:
                raise FFmpegError(
                    f"360 cache collision: {path_key} was produced for two different views: "
                    f"{previous_identity} vs {identity}"
                )
            cache_owners[path_key] = identity
            segment_paths.append(result["path"])
            warnings.extend(result["warnings"])
            render_stats.append({key: result[key] for key in ("index", "cached", "ffmpeg_sec", "verify_sec", "total_sec")})
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
        _write_export_link_audit(
            project,
            output_path,
            render_segments,
            results,
            concat_path,
            final_path=None,
        )
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
        _write_export_link_audit(
            project,
            output_path,
            render_segments,
            results,
            concat_path,
            final_path=output_path,
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
        project.data["_export_performance"] = {
            "segment_count": len(render_segments),
            "segment_workers": segment_workers,
            "wall_sec": round(time.perf_counter() - render_started, 3),
            "segments": sorted(render_stats, key=lambda item: item["index"]),
        }
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
    song_end_sec: float | None = None,
) -> None:
    """Direct passthrough export: the original 360 clip already works fine in
    YouTube/VLC, so this mode does nothing but trim it to the song's Start/End
    range (stream copy, no body re-encode), replace its audio with the synced
    master track, and bookend it with intro/outro logos re-encoded to match
    the source's own codec/resolution/fps so the concat needs no re-encode
    either. No reprojection, motion, beat cuts, camera selection, or operator
    avoidance -- and no watermark, since compositing one would require
    re-encoding the whole body.
    """
    del project  # kept in the signature to match the other _render_*_plan functions
    if not segments:
        raise FFmpegError("No 360 segment to export")
    segment = segments[0]
    source_path = str(segment.get("source_path") or segment.get("clip_path") or "")
    if not source_path or not Path(source_path).exists():
        raise FFmpegError(f"360 source file is missing: {source_path or '(none)'}")
    profile = _probe_video_profile(source_path)
    clip_start = max(0.0, float(segment.get("clip_start_sec") or 0.0))
    duration = max(0.1, float(segment.get("duration_sec") or 0.0))

    temp_dir = output_path.parent / f".{output_path.stem}-360"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    try:
        intro = temp_dir / "intro.mp4"
        body = temp_dir / "body.mp4"
        outro = temp_dir / "outro.mp4"
        joined = temp_dir / "joined.mp4"
        _render_matched_logo_clip(
            intro,
            profile,
            "intro",
            INTRO_DURATION,
            video_bitrate,
            lambda percent, detail: progress_callback(8 + int(percent * 4 / 100), detail),
        )
        _copy_trim_video(
            source_path,
            body,
            clip_start,
            duration,
            lambda percent, detail: progress_callback(12 + int(percent * 60 / 100), detail),
            codec_name=profile.get("codec_name"),
        )
        audio_start, audio_delay = _audio_mux_start_and_delay([segment])
        outro_duration = _outro_duration_for_remaining_music(
            master_path, audio_start, audio_delay, INTRO_DURATION + duration, song_end_sec
        )
        _render_matched_logo_clip(
            outro,
            profile,
            "outro",
            outro_duration,
            video_bitrate,
            lambda percent, detail: progress_callback(72 + int(percent * 4 / 100), detail),
        )
        concat_reencoded = _concat_360_segments(
            intro,
            body,
            outro,
            joined,
            profile,
            video_bitrate,
            duration + INTRO_DURATION + outro_duration,
            temp_dir,
            lambda percent, detail: progress_callback(76 + int(percent * 8 / 100), detail),
        )
        # No cadence-normalization fallback here: that path assumes the fixed
        # TARGET_EXPORT_FPS and would re-encode (and resample) the body to
        # force it, which is exactly the re-encode this mode exists to avoid.
        # The source's own native fps is preserved as-is.
        real_duration = _media_duration(str(joined))
        muxed = temp_dir / "muxed.mp4"
        _mux_continuous_master_audio(
            joined,
            master_path,
            muxed,
            audio_start,
            real_duration,
            video_bitrate,
            lambda percent, detail: progress_callback(84 + int(percent * 10 / 100), detail),
            extra_args=_spherical_metadata_args(),
            content_start=INTRO_DURATION,
            # L-cut at BOTH ends: the music is already playing under the intro
            # logo (content_start fades it in from t=0), and it keeps playing
            # under the outro logo, fading out only at the very end of the
            # timeline. Fading at the end of the BODY instead left the outro
            # silent and chopped the tail off the song.
            content_end=real_duration,
            audio_delay=audio_delay,
        )
        # ffmpeg's `-metadata` flags above (belt-and-braces, plus `-strict
        # unofficial` on every stream-copy step) only ever produce cosmetic
        # udta string tags -- confirmed empirically: even with
        # +use_metadata_tags a stream-copied/remuxed file has zero real
        # uuid/sv3d/st3d spherical box structures, so VLC/YouTube see it as a
        # flat rectangle. This final injection step writes the actual Google
        # Spherical Video V2 boxes (vendored, pure Python, no external
        # install) and hard-fails the export if they don't verifiably land.
        try:
            inject_spherical_metadata(str(muxed), str(output_path))
        except SphericalMetadataError as exc:
            raise FFmpegError(f"360 export failed spherical metadata verification: {exc}") from exc
        passthrough_note = (
            "360 export is a direct passthrough: the original clip is trimmed via stream copy "
            "(no re-encode of the body) and only the synced master audio plus intro/outro logos "
            "are added. "
            if not concat_reencoded
            else "360 export trims the original clip via stream copy, but the body had to be "
            "re-encoded once when joining the intro/outro logos: ffmpeg's HEVC decoder cannot "
            "reliably play back a stream-copied concatenation of independently-encoded HEVC "
            "segments (confirmed empirically -- it produces a black frame with sound, playable "
            "audio but broken video references), so this mode re-encodes only at the join step, "
            "at a bitrate matched to the source, to guarantee the exported file actually plays. "
        )
        warnings.append(
            passthrough_note
            + "The watermark is skipped in this mode -- overlaying it would require "
            "re-encoding the whole body, and preserving the source's original quality and 360 "
            "metadata matters more here. Spherical metadata (sv3d/st3d boxes) is injected fresh "
            "into the final file and verified via ffprobe so YouTube/VLC recognize it as 360."
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _concat_360_segments(
    intro: Path,
    body: Path,
    outro: Path,
    joined: Path,
    profile: dict[str, Any],
    video_bitrate: int,
    total_duration: float,
    temp_dir: Path,
    progress_callback: ProgressCallback,
) -> bool:
    """Join intro+body+outro into `joined`. Returns True if the body had to be re-encoded.

    For H.264 sources this is a genuine stream-copy passthrough (`-c copy`
    via the concat demuxer) -- verified to produce clean, correctly
    decoding output even when the intro/outro were encoded by a different
    encoder than the body.

    For HEVC sources, stream-copy concatenation of independently-encoded
    segments is NOT safe: empirically confirmed (real hevc_videotoolbox
    intro/outro + libx265/HEVC body, joined via `-f concat -c copy`) to
    produce a file whose HEVC decoder throws "Could not find ref with POC
    N" / "Error constructing the frame RPS" throughout the body -- it plays
    as a black frame with audio, because the concatenated bitstream's
    reference picture sets don't line up across the encoder boundary even
    though each segment decodes perfectly on its own. Decoding each segment
    and re-joining with the `concat` FILTER (not the demuxer) sidesteps
    this entirely, since the filter operates on already-decoded frames, at
    the cost of one re-encode of the joined result (still never touches
    per-frame content, only the container-level stream-copy boundary).
    """
    codec_name = str(profile.get("codec_name") or "h264").lower()
    ffmpeg = _ffmpeg_path()
    if codec_name not in {"hevc", "h265"}:
        concat_path = temp_dir / "concat.txt"
        concat_path.write_text("".join(_concat_file_line(path) for path in [intro, body, outro]), encoding="utf-8")
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
                "-strict",
                "unofficial",
                *_spherical_metadata_args(),
                str(joined),
            ],
            total_duration,
            t("joining_segments"),
            progress_callback,
        )
        return False

    filter_complex = "[0:v:0][1:v:0][2:v:0]concat=n=3:v=1:a=0[v]"
    base_command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-i",
        str(intro),
        "-i",
        str(body),
        "-i",
        str(outro),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-pix_fmt",
        profile["pix_fmt"],
    ]
    tail = ["-strict", "unofficial", *_spherical_metadata_args(), str(joined)]
    try:
        _run_ffmpeg_progress(
            base_command + _video_encode_args("hevc_videotoolbox", video_bitrate) + tail,
            total_duration,
            t("joining_segments"),
            progress_callback,
        )
    except FFmpegError:
        if joined.exists():
            joined.unlink()
        _run_ffmpeg_progress(
            base_command + _video_encode_args("libx265", video_bitrate) + tail,
            total_duration,
            t("joining_segments"),
            progress_callback,
        )
    return True


def _probe_video_profile(path: str) -> dict[str, Any]:
    """Probe the exact codec/resolution/fps of a source file, so intro/outro
    logos can be re-encoded to match it closely enough for a stream-copy
    concat to work without touching the body.
    """
    metadata = ffprobe(path)
    streams = metadata.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    return {
        "codec_name": str(video.get("codec_name") or "h264").lower(),
        "width": int(video.get("width") or 1920),
        "height": int(video.get("height") or 1080),
        "fps": _parse_frame_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")) or 30.0,
        "pix_fmt": str(video.get("pix_fmt") or "yuv420p"),
    }


def _parse_frame_rate(value: Any) -> float | None:
    text = str(value or "")
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denom = float(right)
            return round(float(left) / denom, 6) if denom else None
        except (TypeError, ValueError):
            return None
    try:
        return round(float(text), 6)
    except (TypeError, ValueError):
        return None


def _matching_encoders(codec_name: str) -> tuple[str, str]:
    """Return (hardware_codec, software_codec) matching the source's own
    codec family, so intro/outro concat cleanly with a stream-copied body.
    """
    if codec_name in {"hevc", "h265"}:
        return "hevc_videotoolbox", "libx265"
    return "h264_videotoolbox", "libx264"


def _copy_trim_video(
    source_path: str,
    output_path: Path,
    start: float,
    duration: float,
    progress_callback: ProgressCallback | None,
    codec_name: str | None = None,
) -> None:
    """Trim the source to [start, start+duration) via stream copy -- no re-encode.

    -ss is given BEFORE -i (input-side seeking): ffmpeg snaps to the nearest
    preceding keyframe, so the body may start up to one GOP earlier than the
    exact requested instant, but decodes cleanly from its very first frame in
    any player. Post-input -ss (with -c copy) gives frame-accurate output but
    starts mid-GOP, which some strict players show as a brief garbled frame --
    a worse tradeoff than a small, sync-compensated timing offset. Note:
    -avoid_negative_ts make_zero must NOT be added here -- combined with
    pre-input -ss it made ffmpeg (observed on ffmpeg 7.x) misinterpret -t as
    an absolute input cutoff instead of an output duration, silently
    truncating the trim.

    HEVC sources: many non-Apple 360 cameras tag their HEVC video 'hev1'
    rather than 'hvc1'. WebKit's <video> element (the packaged app's Result
    player) silently refuses to decode 'hev1' -- it demuxes and plays the
    audio track fine but never produces a video frame, showing black with
    sound. `-tag:v hvc1` rewrites just that four-character-code box tag
    (verified bit-identical to the source via frame MD5 -- this is not a
    re-encode), so a stream-copied HEVC body plays back correctly.

    End boundary: because the input-side seek lands on the keyframe at or
    BEFORE `start`, asking for exactly `duration` seconds from there ends the
    output up to one GOP EARLIER than the requested end -- which is precisely
    the reported "the 360 export cuts the song early, the End trim is
    ignored". `-t` is therefore measured from the keyframe ffmpeg actually
    snapped to, so the requested end instant is always included.
    """
    snapped_start = _preceding_keyframe(source_path, start)
    # Measure the requested end from the keyframe ffmpeg will actually land on,
    # not from the requested start, so the snap eats into the head (already
    # sync-compensated) instead of truncating the tail.
    copy_duration = max(0.1, (start + duration) - snapped_start)
    command = [
        _ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{copy_duration:.3f}",
        "-map",
        "0:v:0",
        "-c",
        "copy",
        "-strict",
        "unofficial",
        *(["-tag:v", "hvc1"] if codec_name in {"hevc", "h265"} else []),
        "-an",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run_ffmpeg_progress(command, copy_duration, "Trimming 360 source", progress_callback)


MAX_OUTRO_DURATION = 45.0


def _outro_duration_for_remaining_music(
    master_path: str,
    audio_start: float,
    audio_delay: float,
    content_end: float,
    song_end_sec: float | None = None,
) -> float:
    """Length of the outro logo, extended so the song can finish underneath it.

    The outro is normally OUTRO_DURATION long. When the picture ends before
    the song does -- the common 360 case, where the camera stopped recording
    partway through the chosen range -- the outro is stretched (up to
    MAX_OUTRO_DURATION) so the rest of the song plays out under the logo
    rather than being chopped off mid-phrase.

    "The song" means the user's chosen Start/End range, NOT the whole master
    file: `song_end_sec` is the End trim. Falling back to the file's full
    length would happily play minutes of audio the user explicitly trimmed
    away. Shorter than the default is never returned -- the outro logo still
    needs its own time to read.
    """
    del master_path  # only the chosen range matters, never the file's full length
    if song_end_sec is None:
        # No End trim known for this plan: keep the default outro rather than
        # guessing from the master file's length, which would play back audio
        # the user may have deliberately trimmed away.
        return OUTRO_DURATION
    end_master_sec = float(song_end_sec)
    if end_master_sec <= 0:
        return OUTRO_DURATION
    # Master-track position playing at the instant the picture ends.
    master_at_content_end = max(0.0, audio_start + max(0.0, content_end - audio_delay))
    remaining = float(end_master_sec) - master_at_content_end
    if remaining <= OUTRO_DURATION:
        return OUTRO_DURATION
    return round(min(MAX_OUTRO_DURATION, remaining), 3)


def _preceding_keyframe(source_path: str, start: float) -> float:
    """Return the keyframe timestamp at or just before `start` (or `start`).

    ffmpeg's input-side seek lands here, so this is what the trim's duration
    has to be measured from. Probing is limited to a window around `start` so
    a multi-hour camera file costs a fraction of a second to inspect.
    """
    if start <= 0.0:
        return 0.0
    status = tool_status()
    if not status.get("ffprobe_path"):
        return start
    window_start = max(0.0, start - 30.0)
    command = [
        str(status["ffprobe_path"]),
        "-v", "error",
        "-read_intervals", f"{window_start:.3f}%{start + 0.5:.3f}",
        "-select_streams", "v:0",
        "-skip_frame", "nokey",
        "-show_entries", "frame=best_effort_timestamp_time",
        "-of", "csv=p=0",
        str(Path(source_path)),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return start
    times: list[float] = []
    for line in result.stdout.splitlines():
        text = line.strip().rstrip(",")
        if not text or text == "N/A":
            continue
        try:
            times.append(float(text))
        except ValueError:
            continue
    candidates = [value for value in times if value <= start + 0.001]
    return max(candidates) if candidates else start


def _render_matched_logo_clip(
    output_path: Path,
    profile: dict[str, Any],
    kind: str,
    duration: float,
    video_bitrate: int,
    progress_callback: ProgressCallback | None,
) -> None:
    """Render a video-only intro/outro logo clip matching the source's own
    codec family, resolution, fps, and pixel format, so it can be joined to
    the stream-copied body via a plain concat (no re-encode of the body).
    """
    ffmpeg = _ffmpeg_path()
    logo = _intro_logo_path() or _logo_path()
    width, height, fps = profile["width"], profile["height"], profile["fps"]
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
        f"color=c=0x000000:s={width}x{height}:r={fps:.3f}:d={duration:.3f}",
    ]
    if logo:
        command.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(logo)])
    filter_complex = _logo_filtergraph_matched(kind, duration, bool(logo), height)
    command.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-pix_fmt",
            profile["pix_fmt"],
            "-r",
            f"{fps:.3f}",
            "-fps_mode",
            "cfr",
            "-an",
            "-movflags",
            "+faststart",
        ]
    )
    hw_codec, sw_codec = _matching_encoders(profile["codec_name"])
    try:
        _run_ffmpeg_progress(command + _video_encode_args(hw_codec, video_bitrate) + [str(output_path)], duration, f"{kind.title()} logo", progress_callback)
    except FFmpegError:
        if output_path.exists():
            output_path.unlink()
        _run_ffmpeg_progress(command + _video_encode_args(sw_codec, video_bitrate) + [str(output_path)], duration, f"{kind.title()} logo", progress_callback)


def _logo_filtergraph_matched(kind: str, duration: float, has_logo: bool, height: int) -> str:
    video = f"[0:v]format=yuv420p,{_constant_cadence_filter()}[bg]"
    if not has_logo:
        return f"{video};[bg]copy[v]"
    logo_height = int(height * 0.72)
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
        if shot and previous_shot and previous_shot.get("type") != shot.get("type") and shot.get("type") != "recorded_move":
            shot = {**shot, "previous_shot": previous_shot}
            output.append({**segment, "spherical_shot": shot})
        else:
            output.append(segment)
        # Keep the last 360 view through intervening non-360 cuts. When the
        # edit returns to the equirectangular camera, the return sweep should
        # still start from the prior 360 view rather than silently resetting
        # the transition state.
        if shot:
            previous_shot = _spherical_shot(segment)
    return output


def _spherical_segment_parts(segment: dict[str, Any], previous_shot: dict[str, Any] | None) -> list[dict[str, Any]]:
    shot = _spherical_shot(segment) or {}
    duration = max(0.0, float(segment.get("duration_sec") or 0.0))
    if duration <= 0.001:
        return []
    if FORCE_STATIC_360_ISOLATION:
        return [_spherical_part(segment, 0.0, duration, shot)]
    if duration < SPHERICAL_SHORT_SEGMENT_STATIC_SEC:
        return [_spherical_part(segment, 0.0, duration, shot)]
    parts: list[dict[str, Any]] = []
    current = 0.0
    start_yaw = _shot_float(previous_shot, "yaw", _shot_float(shot, "yaw", 0.0))
    target_yaw = _shot_float(shot, "yaw", 0.0)
    if previous_shot and previous_shot.get("type") != shot.get("type") and duration >= 2.0 and bool(shot.get("sweep_enabled", False)):
        distance = abs(_shortest_yaw_delta(start_yaw, target_yaw))
        speed = max(_sweep_speed(shot), min(SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC, distance / max(duration, 0.001)))
        full_pan_duration = distance / speed if speed > 0 else 0.0
        pan_duration = min(duration, full_pan_duration)
        pan_steps = max(3, min(12, int(round(pan_duration / 0.05))))
        signed_delta = _shortest_yaw_delta(start_yaw, target_yaw)
        for index in range(pan_steps):
            part_duration = pan_duration / pan_steps
            advance = degrees_per_second_to_step(speed, current + part_duration)
            amount = min(1.0, advance / max(0.001, distance))
            parts.append(_spherical_part(segment, current, part_duration, {**shot, "type": "pan", "label": shot.get("label"), "yaw": (start_yaw + signed_delta * amount) % 360.0}))
            current += part_duration
    remaining = max(0.0, duration - current)
    if shot.get("type") == "planet" and remaining > 0.001:
        spin = _shot_float(shot, "spin_deg_per_sec", 22.0)
        step = min(0.5, remaining)
        while remaining > 0.001:
            part_duration = min(step, remaining)
            yaw = target_yaw + degrees_per_second_to_step(spin, current + part_duration / 2.0)
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
    return {
        **shot,
        "yaw": (_shot_float(shot, "yaw", 0.0) + yaw_delta) % 360.0,
        "pitch": _shot_float(shot, "pitch", 0.0),
    }


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
    manual_offset = float(segments[0].get("audio_offset_sec") or 0.0) if segments else 0.0
    desired_start = first - INTRO_DURATION + manual_offset
    return max(0.0, desired_start), max(0.0, -desired_start)


def _outro_master_start(segments: list[dict[str, Any]]) -> float:
    if not segments:
        return 0.0
    last = segments[-1]
    return max(0.0, float(last.get("master_start_sec") or 0.0) + float(last.get("duration_sec") or 0.0))


def _target_size(platform: str) -> tuple[int, int]:
    if platform in {"instagram", "tiktok"}:
        return 608, 1080
    if platform == "reel":
        return 1080, 1920
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
            source_filter=_export_source_filter(
                source.get("probe") or {},
                _spherical_shot(segment),
                duration=duration,
                command_path=output_path.with_suffix(".sendcmd.txt"),
            ) if _spherical_shot(segment) else None,
        )
    sendcmd_path = output_path.with_suffix(".sendcmd.txt")
    segment_probe = source.get("probe") or {}
    _warn_if_spherical_framing_was_dropped(segment, segment_probe, warnings)
    source_filter = _export_source_filter(segment_probe, _spherical_shot(segment), duration=duration, command_path=sendcmd_path)
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
                source_filter=_export_source_filter(
                    source.get("probe") or {},
                    _spherical_shot(segment),
                    duration=duration,
                    command_path=output_path.with_suffix(".sendcmd.txt"),
                ) if _spherical_shot(segment) else None,
            )


def _render_segment_job(
    project: Project,
    index: int,
    segment: dict[str, Any],
    render_count: int,
    temp_dir: Path,
    master_path: str,
    platform: str,
    video_bitrate: int,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    verify_motion: bool,
    progress_callback: ProgressCallback,
    progress_lock: threading.Lock,
) -> dict[str, Any]:
    """Render and verify one segment; safe to run in an export worker."""
    started = time.perf_counter()
    segment_duration = max(0.1, float(segment["duration_sec"]))
    base_percent = 10 + int(70 * (index - 1) / max(1, render_count))

    def segment_progress(local_percent: int, detail: str) -> None:
        percent = base_percent + int((70 / max(1, render_count)) * local_percent / 100)
        with progress_lock:
            progress_callback(min(84, percent), f"Rendering segment {index}/{render_count}: {detail}")

    intro_fade = index == 1
    outro_fade = index == render_count
    color_profile = color_profile or {}
    segment_path = cached_segment_path(project, segment, platform, video_bitrate, overlay_config, color_profile, intro_fade, outro_fade, False, False)
    source_info = _segment_source_info(project, segment)
    label = Path(str(source_info.get("source_path") or segment.get("clip_path"))).name
    command_line = "cached segment"
    local_warnings: list[str] = []
    force_rerender = bool(project.data.get("settings", {}).get("export", {}).get("force_rerender_segments", False))
    cached = (not force_rerender) and segment_path.exists() and _segment_cache_stamp_matches(segment_path, segment)
    if force_rerender and segment_path.exists():
        LOGGER.warning("FORCE RERENDER: bypassing segment cache %s", segment_path)
    if segment_path.exists() and not cached:
        LOGGER.warning(
            "Rejecting stale or unverifiable segment cache %s; recipe=%s commit=%s",
            segment_path,
            SPHERICAL_MOTION_RECIPE_VERSION,
            build_info().get("git_commit", "unknown"),
        )
        segment_path.unlink(missing_ok=True)
        _segment_cache_stamp_path(segment_path).unlink(missing_ok=True)
    ffmpeg_started = time.perf_counter()
    commands: list[list[str]] = []
    if not cached:
        tmp_segment = temp_dir / f"segment-{index:04d}.mp4"
        rendered_from = _render_segment(
            project, segment, master_path, tmp_segment, platform, video_bitrate,
            overlay_config, color_profile, segment_progress,
            intro_fade=intro_fade, outro_fade=outro_fade,
            warnings=local_warnings, command_recorder=commands,
        )
        if _spherical_shot(segment):
            LOGGER.info(
                "360 segment write index=%s path=%s rendered_from=%s commands=%s",
                index, tmp_segment, rendered_from, len(commands),
            )
        if commands:
            command_line = " ".join(commands[-1])
        segment_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp_segment, segment_path)
        if rendered_from == "proxy":
            local_warnings.append(f"Used proxy fallback for {label}")
    ffmpeg_sec = time.perf_counter() - ffmpeg_started
    verify_started = time.perf_counter()
    _verify_segment_frame_duration(segment_path, _segment_frame_count(segment), label)
    if verify_motion:
        command_line = _verify_or_rebuild_segment(
            project, segment, master_path, segment_path,
            temp_dir / f"segment-{index:04d}.mp4", platform, video_bitrate,
            overlay_config, color_profile, segment_progress,
            intro_fade, outro_fade, False, False, local_warnings,
            command_line, segment_duration, label,
            command_recorder=commands,
        )
    _write_segment_cache_stamp(segment_path, segment)
    _require_segment_cache_stamp(segment_path, segment)
    # The global cache is the reusable store; concat receives a per-export
    # copy so two timeline entries can never alias the same pathname, even if
    # their recipes are identical. This makes ordering and boundary auditing
    # unambiguous and prevents a future cache-key regression from producing a
    # mixed concat input.
    concat_path = temp_dir / f"segment-{index:04d}-concat.mp4"
    shutil.copy2(segment_path, concat_path)
    shutil.copy2(_segment_cache_stamp_path(segment_path), _segment_cache_stamp_path(concat_path))
    _require_segment_cache_stamp(concat_path, segment)
    segment_progress(100, "complete")
    return {
        "index": index,
        "path": concat_path,
        "cache_path": segment_path,
        "cache_stamp": _segment_cache_stamp_path(segment_path),
        "sendcmd_path": temp_dir / f"segment-{index:04d}.sendcmd.txt",
        "ffmpeg_command": commands[-1] if commands else [],
        "ffmpeg_commands": commands,
        "spherical_identity": _spherical_cache_identity(segment),
        "warnings": local_warnings,
        "cached": cached,
        "ffmpeg_sec": round(ffmpeg_sec, 3),
        "verify_sec": round(time.perf_counter() - verify_started, 3),
        "total_sec": round(time.perf_counter() - started, 3),
    }


def _write_export_link_audit(
    project: Project,
    output_path: Path,
    render_segments: list[dict[str, Any]],
    results: list[dict[str, Any]],
    concat_path: Path,
    final_path: Path | None,
) -> None:
    """Persist every 360 render link so a delivered export is bisectable."""
    result_by_index = {int(item["index"]): item for item in results}
    entries: list[dict[str, Any]] = []
    for index, segment in enumerate(render_segments, start=1):
        if not _spherical_shot(segment):
            continue
        result = result_by_index.get(index) or {}
        sendcmd_path = Path(str(result.get("sendcmd_path") or ""))
        stamp_path = Path(str(result.get("cache_stamp") or ""))
        sendcmd = ""
        if sendcmd_path.exists():
            sendcmd = sendcmd_path.read_text(encoding="utf-8")
        stamp: dict[str, Any] | None = None
        if stamp_path.exists():
            try:
                candidate = json.loads(stamp_path.read_text(encoding="utf-8"))
                stamp = candidate if isinstance(candidate, dict) else None
            except (OSError, json.JSONDecodeError):
                stamp = None
        entries.append(
            {
                "index": index,
                "master_start_sec": segment.get("master_start_sec"),
                "duration_sec": segment.get("duration_sec"),
                "spherical_shot": _spherical_shot(segment),
                "sendcmd_path": str(sendcmd_path),
                "sendcmd": sendcmd,
                "cache_path": str(result.get("cache_path") or ""),
                "concat_path": str(result.get("path") or ""),
                "cache_stamp_path": str(stamp_path),
                "cache_stamp": stamp,
                "cache_mtime": _path_mtime(result.get("cache_path")),
                "concat_mtime": _path_mtime(result.get("path")),
                "ffmpeg_command": result.get("ffmpeg_command") or [],
                "ffmpeg_commands": result.get("ffmpeg_commands") or [],
                "cached": bool(result.get("cached")),
            }
        )
    concat_entries = []
    if concat_path.exists():
        concat_entries = concat_path.read_text(encoding="utf-8").splitlines()
    write_artifact_json(
        artifact_path(project, "export_link_audit.json"),
        {
            "schema": 1,
            "build": build_info(),
            "spherical_motion_recipe": _spherical_motion_cache_recipe(),
            "output_path": str(output_path),
            "final_path": str(final_path) if final_path else None,
            "concat_path": str(concat_path),
            "concat_entries": concat_entries,
            "segments": entries,
        },
    )


def _path_mtime(value: Any) -> str | None:
    """Return an ISO mtime while a temporary concat copy still exists."""
    if not value:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(Path(str(value)).stat().st_mtime))
    except (OSError, ValueError):
        return None


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
    source_filter: str | None = None,
) -> str:
    proxy_filter = _segment_filtergraph(
        platform,
        duration,
        overlay_config,
        color_profile,
        bool(watermark),
        _ffmpeg_supports_filter("drawtext"),
        source_filter=source_filter,
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
    command_recorder: list[list[str]] | None = None,
) -> str:
    """Verify one rendered segment, rebuilding/falling back before final concat."""
    try:
        _verify_moving_segment(segment_path, segment_duration, label, command_line)
        return command_line
    except FFmpegError as first_error:
        segment_path.unlink(missing_ok=True)
        commands = command_recorder if command_recorder is not None else []
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
        if _spherical_shot(segment):
            LOGGER.info("360 segment repair write path=%s command=%s", tmp_segment, commands[-1] if commands else "missing")
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
            commands = command_recorder if command_recorder is not None else []
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
            if _spherical_shot(segment):
                LOGGER.info("360 segment proxy-repair write path=%s command=%s", tmp_segment, commands[-1] if commands else "missing")
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
    try:
        for line in process.stdout:
            match = re.match(r"out_time_ms=(\d+)", line.strip())
            if not match or duration <= 0:
                continue
            seconds = int(match.group(1)) / 1_000_000
            percent = max(current, min(99, int(seconds / duration * 100)))
            if progress and percent > current:
                current = percent
                progress(percent, f"{label} — {percent}%")
    except BaseException:
        # progress() can raise (e.g. a user cancellation) — don't leave the
        # ffmpeg process running in the background when that happens.
        process.kill()
        process.wait()
        raise
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
        # L-cut: audio fades in from the very start of the timeline (under
        # the intro logo), not once the intro finishes -- ffmpeg's afade is
        # silent before its own start point, so fading in at content_start
        # meant the intro played with no sound at all until the video content
        # began. By the time content_start arrives the fade is long done
        # (CONTENT_FADE_DURATION << INTRO_DURATION), so this only affects the
        # first ~1.5s of the intro, not the cut point itself.
        filters.append(f"afade=t=in:st=0.000:d={CONTENT_FADE_DURATION:.3f}")
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
        *(["-strict", "unofficial"] if extra_args else []),
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
    if platform == "reel":
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p"
    if platform == "360":
        return "scale=3840:1920:force_original_aspect_ratio=decrease,pad=3840:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"


def _motion_filter(segment: dict[str, Any], platform: str, duration: float) -> str | None:
    motion = segment.get("motion") or {}
    motion_type = motion.get("type")
    if motion_type == "ken_burns":
        return _ken_burns_filter(motion, platform, duration)
    if motion_type == "zoom_crop":
        return _zoom_crop_filter(motion, platform)
    return None


def _ken_burns_filter(motion: dict[str, Any], platform: str, duration: float) -> str | None:
    width, height = _target_size(platform)
    try:
        zoom_start = float(motion.get("zoom_start", 1.0))
        zoom_end = float(motion.get("zoom_end", 1.06))
        pan_x = float(motion.get("pan_x", 0.5))
        pan_y = float(motion.get("pan_y", 0.5))
    except (TypeError, ValueError):
        return None
    zoom_start = max(1.0, min(1.14, zoom_start))
    zoom_end = max(1.0, min(1.14, zoom_end))
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


def _zoom_crop_filter(motion: dict[str, Any], platform: str) -> str | None:
    """Static (non-animated) zoom+crop, used by operator-avoidance adjustments.

    Wider zoom range than ken_burns (up to 1.6x) since this needs to push a
    prominent foreground operator fully off-frame, not just add subtle motion.
    """
    width, height = _target_size(platform)
    try:
        zoom = float(motion.get("zoom", 1.35))
        pan_x = float(motion.get("cx", 0.5))
        pan_y = float(motion.get("cy", 0.5))
    except (TypeError, ValueError):
        return None
    zoom = max(1.0, min(1.6, zoom))
    pan_x = max(0.0, min(1.0, pan_x))
    pan_y = max(0.0, min(1.0, pan_y))
    scaled_width = f"ceil({width}*{zoom:.6f}/2)*2"
    scaled_height = f"ceil({height}*{zoom:.6f}/2)*2"
    return (
        f"scale=w='{scaled_width}':h='{scaled_height}',"
        f"crop={width}:{height}:x='(iw-{width})*{pan_x:.4f}':y='(ih-{height})*{pan_y:.4f}'"
    )


def _v360_sendcmd_filter(shot: dict[str, Any] | None, duration: float | None, command_path: Path | None, aspect_ratio: float = 16.0 / 9.0) -> str:
    if not shot or command_path is None or duration is None or duration <= 0:
        return ""
    commands = _v360_motion_commands(shot, float(duration), aspect_ratio=aspect_ratio)
    if not commands:
        return ""
    command_path.write_text("".join(commands), encoding="utf-8")
    return f"sendcmd=f={_escape_filter_path(command_path)},"


def _v360_motion_commands(shot: dict[str, Any], duration: float, aspect_ratio: float = 16.0 / 9.0) -> list[str]:
    if not _shot_requires_runtime_motion(shot):
        return []
    if FORCE_STATIC_360_ISOLATION:
        yaw, pitch, fov = _static_360_pose(shot)
        h_fov, v_fov = _paired_motion_fov(shot, fov, aspect_ratio)
        return [
            f"0.000000 {SPHERE_V360_LABEL} yaw {yaw:.6f};\n",
            f"0.000000 {SPHERE_V360_LABEL} pitch {pitch:.6f};\n",
            f"0.000000 {SPHERE_V360_LABEL} h_fov {h_fov:.6f};\n",
            f"0.000000 {SPHERE_V360_LABEL} v_fov {v_fov:.6f};\n",
        ]
    commands: list[str] = []
    # A recorded-move curve can hold thousands of samples and is sampled once per frame.
    # Pre-normalise it a single time and interpolate with a bisect lookup so sendcmd
    # generation is O(frames * log N) instead of O(frames * N); the previous per-frame
    # re-normalisation made long 360 exports take minutes just to emit the command file.
    curve_sampler = _recorded_curve_sampler(shot)
    # FFmpeg reconfigures v360 at every sendcmd event and corrupts the event
    # frame on the shipped build. Keep the experiment to the minimum: opening
    # pose plus one midpoint pose. Static holds return above and emit none.
    event_count = max(2, int(SPHERICAL_HOLD_COMMAND_COUNT))
    if shot.get("type") in {"planet", "recorded_move"}:
        event_times = [0.0, duration]
    else:
        event_times = [0.0, duration / 2.0]
        if event_count > 2:
            event_times = [duration * index / (event_count - 1) for index in range(event_count)]
    for t in event_times:
        if curve_sampler is not None:
            yaw, pitch, fov = curve_sampler(max(0.0, min(duration, t)))
            yaw = _signed_yaw(yaw)
        else:
            yaw, pitch, fov = _v360_motion_at(shot, duration, t)
        h_fov, v_fov = _paired_motion_fov(shot, fov, aspect_ratio)
        commands.append(f"{t:.6f} {SPHERE_V360_LABEL} yaw {yaw:.6f};\n")
        commands.append(f"{t:.6f} {SPHERE_V360_LABEL} pitch {pitch:.6f};\n")
        commands.append(f"{t:.6f} {SPHERE_V360_LABEL} h_fov {h_fov:.6f};\n")
        commands.append(f"{t:.6f} {SPHERE_V360_LABEL} v_fov {v_fov:.6f};\n")
    if shot.get("type") != "recorded_move":
        first_yaw = _v360_motion_at(shot, duration, 0.0)[0]
        next_yaw = _v360_motion_at(shot, duration, min(duration / 2.0, duration))[0]
        last_yaw = _v360_motion_at(shot, duration, duration)[0]
        configured_rate = float(shot.get("hold_motion_rate_deg_per_sec") or 0.0)
        LOGGER.info(
            "360 motion emit path=%s shot=%s duration=%.3f fps=%.3f hold=%s "
            "configured_deg_per_sec=%.6f yaw_start=%.6f yaw_step=%.6f yaw_end=%.6f",
            __name__,
            shot.get("label") or shot.get("type") or "360",
            duration,
            TARGET_EXPORT_FPS,
            shot.get("hold_motion") or "none",
            configured_rate,
            _shortest_yaw_delta(first_yaw, next_yaw),
            _shortest_yaw_delta(first_yaw, last_yaw),
        )
    return commands


def _shot_requires_runtime_motion(shot: dict[str, Any] | None) -> bool:
    """Whether this shot needs the unsafe runtime v360 command path.

    A static pose must stay a plain v360 filter. Even constant sendcmd events
    reconfigure v360 and corrupt the event frame on the FFmpeg build we ship.
    """
    if not shot:
        return False
    if shot.get("type") == "recorded_move":
        return bool(shot.get("curve"))
    if shot.get("type") == "planet":
        return float(shot.get("spin_deg_per_sec") or 0.0) > 0.0
    if float(shot.get("hold_motion_rate_deg_per_sec") or 0.0) > 0.0:
        return True
    previous = shot.get("previous_shot") if isinstance(shot.get("previous_shot"), dict) else None
    if previous and bool(shot.get("sweep_enabled", False)):
        return abs(_shortest_yaw_delta(_shot_yaw(previous), _shot_yaw(shot))) > 1e-6
    return any(float(shot.get(key) or 0.0) != 0.0 for key in ("drift_yaw_fraction", "drift_pitch_fraction", "fov_delta_fraction", "drift_yaw_deg", "drift_pitch_deg", "fov_delta_deg"))


def _recorded_curve_sampler(shot: dict[str, Any]):
    """Return a fast per-frame interpolator for a recorded-move curve, or None.

    Normalises the curve once and interpolates by bisect.  The returned callable
    reproduces the exact math of ``camera_moves.interpolate_curve`` (endpoint clamp,
    shortest-arc yaw lerp, linear pitch/fov) without re-normalising on every frame.
    """
    if not shot or shot.get("type") != "recorded_move":
        return None
    samples = limit_yaw_velocity(shot.get("curve") or [])
    if not samples:
        return None
    times = [float(s["t"]) for s in samples]
    first, last = samples[0], samples[-1]

    def sample(t: float) -> tuple[float, float, float]:
        if t <= times[0]:
            return first["yaw"], first["pitch"], first["fov"]
        if t >= times[-1]:
            return last["yaw"], last["pitch"], last["fov"]
        index = bisect.bisect_right(times, t) - 1
        left = samples[index]
        right = samples[index + 1]
        span = max(0.000001, right["t"] - left["t"])
        amount = (t - left["t"]) / span
        yaw = _lerp_angle(left["yaw"], right["yaw"], amount)
        pitch = left["pitch"] + (right["pitch"] - left["pitch"]) * amount
        fov = left["fov"] + (right["fov"] - left["fov"]) * amount
        return yaw, pitch, fov

    return sample


def _automatic_drift_degrees(shot: dict[str, Any], axis: str, visible_fov: float, duration: float) -> float:
    """Total travel (degrees) for one automatic-motion axis over the segment.

    Motion is authored as a fraction of the shot's visible field (see
    ``core.stages.edit._spherical_motion_profile``); here it is resolved
    against the FOV the segment is actually rendered at, then clamped so the
    resulting rate can never exceed SPHERICAL_MAX_MOTION_FRACTION_PER_SEC of
    that field per second. The clamp matters because the fraction describes
    travel across the WHOLE segment: without it, a short segment would turn
    the same budget into a fast pan.

    Legacy plans (cached before this rework) carry absolute ``*_deg`` values
    instead. Those are honoured but pushed through the identical clamp, so an
    already-cached edit plan cannot resurrect the old runaway motion.
    """
    hold_rate = shot.get("hold_motion_rate_deg_per_sec") if axis == "drift_yaw" else None
    if hold_rate is not None:
        travel = degrees_per_second_to_step(
            _shot_float(shot, "hold_motion_rate_deg_per_sec", 0.0),
            max(0.001, duration),
        )
    else:
        fraction = shot.get(f"{axis}_fraction")
        if fraction is None:
            legacy = _shot_float(shot, f"{axis}_deg", 0.0)
            travel = legacy
        else:
            travel = _shot_float(shot, f"{axis}_fraction", 0.0) * visible_fov
    ceiling = SPHERICAL_MAX_MOTION_FRACTION_PER_SEC * visible_fov * max(0.001, duration)
    travel = max(-ceiling, min(ceiling, travel))
    if axis == "drift_yaw":
        travel = max(-SPHERICAL_MAX_HOLD_YAW_DEG, min(SPHERICAL_MAX_HOLD_YAW_DEG, travel))
    return travel


def _v360_motion_at(shot: dict[str, Any], duration: float, t: float) -> tuple[float, float, float]:
    duration = max(0.001, duration)
    if FORCE_STATIC_360_ISOLATION:
        return _static_360_pose(shot)
    if shot.get("type") == "recorded_move":
        safe_curve = limit_yaw_velocity(shot.get("curve") or [])
        interpolated = interpolate_curve(safe_curve, max(0.0, min(duration, t)))
        if interpolated:
            yaw, pitch, fov = interpolated
            return _signed_yaw(yaw), pitch, fov
    target_yaw = _shot_yaw(shot)
    target_pitch = _shot_float(shot, "pitch", 0.0)
    target_fov = _effective_flat_fov(shot)
    # A sub-two-second cut is a hold. There is not enough screen time for a
    # graceful move, so do not let a sweep, drift, or planet spin leak into it.
    if duration < SPHERICAL_SHORT_SEGMENT_STATIC_SEC:
        return _signed_yaw(target_yaw), target_pitch, target_fov
    if shot.get("type") == "planet":
        authored_fraction = shot.get("spin_fov_fraction_per_sec")
        if authored_fraction is None:
            spin_per_sec = _shot_float(shot, "spin_deg_per_sec", PLANET_SPIN_DEG_PER_SEC)
        else:
            spin_per_sec = _shot_float(shot, "spin_fov_fraction_per_sec", 0.0) * target_fov
        spin_per_sec = min(max(0.0, spin_per_sec), PLANET_SPIN_DEG_PER_SEC)
        yaw = target_yaw + degrees_per_second_to_step(spin_per_sec, max(0.0, t))
        return _signed_yaw(yaw), target_pitch, target_fov

    # Landmark changes are intentional sweeps. Their duration is determined by
    # angular distance and a project-level degrees/second setting, never by the
    # legacy fixed 0.45s transition. If a sweep cannot fit in this segment we
    # use the bounded max speed so it remains a sweep, not a whip.
    previous = shot.get("previous_shot") if isinstance(shot.get("previous_shot"), dict) else None
    pan_duration = 0.0
    yaw = target_yaw
    pitch = target_pitch
    fov = target_fov
    if previous:
        previous_yaw = _shot_yaw(previous)
        distance = abs(_shortest_yaw_delta(previous_yaw, target_yaw))
        if not bool(shot.get("sweep_enabled", False)):
            requested = 0.0
        else:
            # Normal case: duration is distance / requested angular speed.
            # If that does not fit this short segment, use the bounded speed
            # needed to fit it. This never consults the legacy transition_sec.
            requested_speed = _sweep_speed(shot)
            available_speed = distance / duration if duration > 0 else 0.0
            speed = min(
                SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC,
                max(requested_speed, available_speed),
            )
            requested = distance / speed if speed > 0 else 0.0
        if requested > 0 and distance > 0:
            pan_duration = requested
            if t <= min(duration, pan_duration):
                delta = _shortest_yaw_delta(previous_yaw, target_yaw)
                advance = min(abs(delta), degrees_per_second_to_step(speed, max(0.0, t)))
                yaw = previous_yaw + (1.0 if delta >= 0.0 else -1.0) * advance
                # Set pitch/FOV at the shot boundary and animate yaw alone.
                # Interpolating all three axes is what made otherwise gentle
                # pans read as agitated.
                pitch = target_pitch
                fov = target_fov
                return _signed_yaw(yaw), pitch, fov

    # A hold rate is an angular velocity, not a per-command increment.  Resolve
    # it against elapsed seconds at the command timestamp; this keeps the
    # conversion explicit in the active sendcmd path and prevents a 0.4 deg/s
    # setting from becoming 0.4 deg per 30-fps frame.
    hold_elapsed = max(0.0, min(duration - pan_duration, t - pan_duration))
    hold_rate = shot.get("hold_motion_rate_deg_per_sec")
    if hold_rate is not None:
        yaw += degrees_per_second_to_step(
            _shot_float(shot, "hold_motion_rate_deg_per_sec", 0.0),
            hold_elapsed,
        )
    else:
        hold_duration = max(0.001, duration - pan_duration)
        hold_amount = max(0.0, min(1.0, (t - pan_duration) / hold_duration))
        yaw += _automatic_drift_degrees(shot, "drift_yaw", target_fov, hold_duration) * hold_amount
    # Automatic landmark motion is yaw-only. Pitch and FOV are framing
    # choices, not simultaneous animated axes; recorded Director takes are
    # the sole exception because their curve is explicitly user-authored.
    return _signed_yaw(yaw), pitch, fov


def _static_360_pose(shot: dict[str, Any] | None) -> tuple[float, float, float]:
    """Return the fixed pose used when diagnostic isolation is explicitly on."""
    return (
        _signed_yaw(_shot_yaw(shot)),
        _shot_float(shot, "pitch", 0.0),
        _effective_flat_fov(shot),
    )


def _lerp_signed_yaw(start: float, end: float, amount: float) -> float:
    delta = _shortest_yaw_delta(start, end)
    return start + delta * max(0.0, min(1.0, amount))


def _shortest_yaw_delta(start: float, end: float) -> float:
    return ((end - start + 540.0) % 360.0) - 180.0


def _sweep_speed(shot: dict[str, Any] | None) -> float:
    value = _shot_float(shot, "sweep_speed_deg_per_sec", SPHERICAL_SWEEP_SPEED_DEG_PER_SEC)
    return max(SPHERICAL_MIN_SWEEP_SPEED_DEG_PER_SEC, min(SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC, value))


def degrees_per_second_to_step(rate_deg_per_sec: float, step_seconds: float) -> float:
    """Convert an angular velocity into the advance for one time step."""
    return float(rate_deg_per_sec) * max(0.0, float(step_seconds))


def _signed_yaw(value: float) -> float:
    value = float(value) % 360.0
    if value > 180.0:
        value -= 360.0
    return value


def _escape_filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _export_source_filter(probe: dict[str, Any], shot: dict[str, Any] | None = None, duration: float | None = None, command_path: Path | None = None) -> str:
    """Prepare source pixels for export while leaving fps conversion to the segment timing filter."""
    view = view_parameters(
        _shot_yaw(shot),
        _shot_float(shot, "pitch", 0.0),
        _shot_float(shot, "fov", 100.0),
        16.0 / 9.0,
        str((shot or {}).get("type") or ""),
    )
    yaw = float(view["yaw"])
    pitch = float(view["pitch"])
    h_fov = float(view["h_fov"])
    v_fov = float(view["v_fov"])
    command_prefix = _v360_sendcmd_filter(shot, duration, command_path, aspect_ratio=16.0 / 9.0)
    # A rectilinear ("flat") view degenerates as it approaches 180° -- the edges
    # stretch to infinity -- so anything genuinely wide has to be stereographic
    # ("sg", the tiny-planet projection), which stays sane out past 300° and is
    # what gives the "see the whole sphere" look. Planet is always sg; ordinary
    # wide shots switch over once flat would start tearing.
    output_projection = str(view["projection"])
    if probe.get("projection") == "raw_insv":
        insv_fov = int(probe.get("insv_fov") or 190)
        spatial = f"v360=input=dfisheye:output=e:ih_fov={insv_fov}:iv_fov={insv_fov}:interp=lanczos,{command_prefix}{SPHERE_V360_LABEL}=input=equirect:output={output_projection}:yaw={yaw:.3f}:pitch={pitch:.3f}:h_fov={h_fov:.3f}:v_fov={v_fov:.3f}:w=1920:h=1080:interp=lanczos"
    elif probe.get("projection") == "equirect":
        spatial = f"{command_prefix}{SPHERE_V360_LABEL}=input=equirect:output={output_projection}:yaw={yaw:.3f}:pitch={pitch:.3f}:h_fov={h_fov:.3f}:v_fov={v_fov:.3f}:w=1920:h=1080:interp=lanczos"
    elif probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
        spatial = f"{SDR_TONEMAP_FILTER},scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        spatial = EVEN_SDR_FILTER
    return spatial


def _spherical_shot(segment: dict[str, Any]) -> dict[str, Any] | None:
    shot = segment.get("spherical_shot")
    return shot if isinstance(shot, dict) else None


def _warn_if_spherical_framing_was_dropped(segment: dict[str, Any], probe: dict[str, Any], warnings: list[str]) -> None:
    """Warn loudly when a 360 segment is about to render as a flat passthrough.

    A segment that declares a spherical_shot (or an equirect/raw_insv
    projection) but whose resolved probe reports neither projection will NOT get
    the v360 reframing -- it renders as a flat, letterboxed passthrough of the
    raw equirect, with every landmark identical and no motion. That used to
    happen silently (e.g. when a segment's source path didn't resolve to its
    input record, so the probe came back empty). This surfaces it instead of
    shipping a broken 360 render with no indication anything went wrong.
    """
    wants_spherical = bool(_spherical_shot(segment)) or str(segment.get("projection") or "") in {"equirect", "raw_insv"}
    if not wants_spherical:
        return
    if str(probe.get("projection") or "") in {"equirect", "raw_insv"}:
        return
    name = Path(str(segment.get("source_path") or segment.get("clip_path") or "")).name or "(unknown)"
    warnings.append(
        f"360 framing was dropped for {name}: the segment asked for a spherical shot but its "
        "source probe reported no equirect/raw_insv projection, so it rendered as a flat "
        "passthrough. This usually means the segment's source did not match a prepared input record."
    )


# Widest horizontal field the stereographic path accepts. v360 stays coherent
# well past this, but ~300° already shows essentially the whole sphere and is a
# sane ceiling. Above STEREOGRAPHIC_FOV_THRESHOLD a shot renders stereographic
# (tiny-planet) rather than rectilinear.
MAX_SPHERICAL_FOV = 300.0
STEREOGRAPHIC_FOV_THRESHOLD = 170.0


def _shot_peak_fov(shot: dict[str, Any] | None) -> float:
    """The widest horizontal field the shot ever reaches, over its whole duration.

    A recorded take can zoom during the move, so its authored ``fov`` is only
    the starting field; the curve is what says how wide it actually gets.
    """
    fov = _effective_flat_fov(shot)
    if str((shot or {}).get("type") or "") == "recorded_move":
        curve = (shot or {}).get("curve") or []
        widest = [_shot_float(sample, "fov", fov) for sample in curve if isinstance(sample, dict)]
        if widest:
            fov = max(fov, min(MAX_SPHERICAL_FOV, max(widest)))
    return fov


def _use_stereographic(shot: dict[str, Any] | None) -> bool:
    """Decide flat vs stereographic ONCE per segment, from the shot alone.

    Deliberately independent of the instantaneous per-frame FOV. The output
    projection is baked into the filtergraph when the segment is built, while
    h_fov/v_fov are driven per frame through sendcmd -- so if the two disagreed
    about which projection is in play they would pair the vertical field by
    different rules mid-shot. A shot sitting near the threshold with a little
    fov drift did exactly that: v_fov jumped ~162 deg to 120 deg partway
    through, a visible pop in an otherwise still hold. Keying off the widest
    field the shot ever reaches keeps one projection for the whole segment.
    """
    shot_type = str((shot or {}).get("type") or "")
    if shot_type == "planet":
        return True
    if shot_type not in {"recorded_move", ""}:
        return False
    return _shot_peak_fov(shot) > STEREOGRAPHIC_FOV_THRESHOLD


def _effective_flat_fov(shot: dict[str, Any] | None) -> float:
    return effective_fov(_shot_float(shot, "fov", 100.0), str((shot or {}).get("type") or ""))


def _paired_motion_fov(shot: dict[str, Any] | None, fov: float, aspect_ratio: float) -> tuple[float, float]:
    params = view_parameters(
        _shot_float(shot, "yaw", 0.0),
        _shot_float(shot, "pitch", 0.0),
        fov,
        aspect_ratio,
        str((shot or {}).get("type") or ""),
        projection_hint="sg" if _use_stereographic(shot) else "flat",
    )
    return float(params["h_fov"]), float(params["v_fov"])


def _paired_flat_fov(horizontal_fov: float, aspect_ratio: float) -> tuple[float, float]:
    return paired_flat_fov(horizontal_fov, aspect_ratio)


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


def _spherical_recording_usage(segments: list[dict[str, Any]]) -> dict[str, Any]:
    recorded = 0
    landmark = 0
    takes: dict[str, int] = {}
    for segment in segments:
        shot = _spherical_shot(segment) or {}
        if not shot:
            continue
        if shot.get("type") == "recorded_move":
            recorded += 1
            take = str(shot.get("recorded_take") or "Take")
            takes[take] = takes.get(take, 0) + 1
        else:
            landmark += 1
    return {"recorded_segments": recorded, "landmark_segments": landmark, "takes": takes}


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
    spherical_motion_recipe = _spherical_motion_cache_recipe()
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
            "spherical_view_identity": _spherical_cache_identity(segment),
            "motion": segment.get("motion") or {},
            "normalization_version": NORMALIZATION_VERSION,
            "export_segment_recipe": EXPORT_SEGMENT_RECIPE_VERSION,
            # Authored shot JSON alone is not enough: renderer-side motion
            # semantics can change while the plan stays byte-for-byte equal.
            "spherical_motion_recipe": spherical_motion_recipe,
            "spherical_motion_recipe_hash": stable_fingerprint(spherical_motion_recipe),
        }
    )[:24]
    return global_segment_path(recipe)


def _segment_cache_stamp_path(segment_path: Path) -> Path:
    """Return the attestation sidecar for a rendered segment."""
    return segment_path.with_suffix(segment_path.suffix + ".json")


def _segment_cache_stamp_payload(segment: dict[str, Any]) -> dict[str, Any]:
    recipe = _spherical_motion_cache_recipe()
    return {
        "schema": 1,
        "export_segment_recipe": EXPORT_SEGMENT_RECIPE_VERSION,
        "spherical_motion_recipe_version": SPHERICAL_MOTION_RECIPE_VERSION,
        "spherical_motion_recipe_hash": stable_fingerprint(recipe),
        "git_commit": build_info().get("git_commit", "unknown"),
        "spherical_identity": _spherical_cache_identity(segment),
        "spherical_shot_fingerprint": stable_fingerprint(_spherical_shot(segment) or {}),
    }


def _segment_sha256(segment_path: Path) -> str:
    digest = hashlib.sha256()
    with segment_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _segment_cache_stamp_matches(segment_path: Path, segment: dict[str, Any]) -> bool:
    """Accept a cache file only when its render attestation matches this run."""
    stamp_path = _segment_cache_stamp_path(segment_path)
    if not segment_path.exists() or not stamp_path.exists():
        return False
    try:
        actual = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    expected = _segment_cache_stamp_payload(segment)
    try:
        expected["segment_sha256"] = _segment_sha256(segment_path)
    except OSError:
        return False
    return all(actual.get(key) == value for key, value in expected.items())


def _write_segment_cache_stamp(segment_path: Path, segment: dict[str, Any]) -> None:
    stamp_path = _segment_cache_stamp_path(segment_path)
    temporary = stamp_path.with_suffix(stamp_path.suffix + ".tmp")
    payload = _segment_cache_stamp_payload(segment)
    payload["segment_sha256"] = _segment_sha256(segment_path)
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(stamp_path)


def _require_segment_cache_stamp(segment_path: Path, segment: dict[str, Any]) -> None:
    if not _segment_cache_stamp_matches(segment_path, segment):
        raise FFmpegError(f"Segment cache attestation mismatch; refusing to assemble {segment_path}")


def _spherical_cache_identity(segment: dict[str, Any]) -> dict[str, Any] | None:
    """Stable human-auditable identity for a rendered spherical viewpoint."""
    shot = _spherical_shot(segment)
    if not shot:
        return None
    return {
        "shot_id": str(shot.get("shot_id") or shot.get("label") or shot.get("type") or "360"),
        "type": str(shot.get("type") or ""),
        "yaw": round(float(shot.get("yaw") or 0.0) % 360.0, 6),
        "pitch": round(float(shot.get("pitch") or 0.0), 6),
        "fov": round(float(shot.get("fov") or 0.0), 6),
        "curve": stable_fingerprint(shot.get("curve") or []) if shot.get("type") == "recorded_move" else None,
    }


def _spherical_motion_cache_recipe() -> dict[str, Any]:
    """Describe every renderer rule whose change must invalidate 360 caches."""
    return {
        "version": SPHERICAL_MOTION_RECIPE_VERSION,
        "force_static_360_isolation": FORCE_STATIC_360_ISOLATION,
        "sweep_speed_default": SPHERICAL_SWEEP_SPEED_DEG_PER_SEC,
        "sweep_speed_min": SPHERICAL_MIN_SWEEP_SPEED_DEG_PER_SEC,
        "sweep_speed_max": SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC,
        "transition_policy": "shortest_yaw_delta_at_angular_speed_v3_cross_cut_returns",
        "axis_policy": "yaw_only_sweep_and_yaw_hold_v2",
        "hold_step_policy": "deg_per_sec_times_elapsed_seconds_v1",
        "hold_motion_rate_deg_per_sec": 0.01,
        "hold_motion_default": "subtle",
        "sendcmd_event_policy": "two_absolute_poses_start_midpoint_v1",
        "landmark_hold_min_sec": 6.0,
        "landmark_hold_target_sec": 8.0,
        "landmark_hold_max_sec": 12.0,
        "landmark_selection_policy": "weighted_deficit_primary_recency_penalty_yaw_tiebreak_v2",
        "fixed_camera_motion_policy": "half_of_fixed_rear_cuts_gentle_ken_burns_v2",
        "recorded_yaw_max_rate_deg_per_sec": 40.0,
        "short_segment_static_sec": SPHERICAL_SHORT_SEGMENT_STATIC_SEC,
        "normal_fov_min": SPHERICAL_NORMAL_FOV_MIN,
        "normal_fov_max": SPHERICAL_NORMAL_FOV_MAX,
        "max_hold_yaw_deg": SPHERICAL_MAX_HOLD_YAW_DEG,
        "planet_spin_deg_per_sec": PLANET_SPIN_DEG_PER_SEC,
        "shared_view_parameters_version": 1,
        "cache_view_identity_version": 1,
        "operator_avoidance_360": "disabled_for_preview_render_coordinate_parity",
        "automatic_yaw_drift_fraction": SPHERICAL_PRIMARY_DRIFT_FRACTION,
        "automatic_motion_fraction_per_sec": SPHERICAL_MAX_MOTION_FRACTION_PER_SEC,
        "v360_target": SPHERE_V360_LABEL,
        "target_fps": TARGET_EXPORT_FPS,
    }


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
    # L-cut: the intro fade-in runs from t=0 (see _mux_continuous_master_audio),
    # not from content_start -- audio plays under the intro logo, already at
    # full volume well before video content begins.
    gain = 1.0
    if content_start is not None and timestamp < CONTENT_FADE_DURATION:
        gain *= max(0.0, min(1.0, timestamp / CONTENT_FADE_DURATION))
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
        windows.append((0.0, CONTENT_FADE_DURATION))
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


def _warn_unused_cameras(clip_fates: list[dict[str, Any]], plan: dict[str, Any], warnings: list[str]) -> None:
    """Add a warning to the warnings list when an entire synced camera is absent from the edit."""
    excluded_names = {str(item.get("filename") or "") for item in (plan.get("excluded_clips") or [])}
    for fate in clip_fates:
        filename = str(fate.get("filename") or "")
        status = str(fate.get("status") or "")
        if status in {"excluded", "not_covering"} and filename and filename not in excluded_names:
            reason = str(fate.get("reason") or "")
            if "quality rejected" in reason or "director" in reason.lower():
                warnings.append(
                    f"Camera {filename} was completely excluded by quality gating: {reason}"
                )
            elif status == "not_covering" and fate.get("covered_seconds", 0):
                covered = float(fate.get("covered_seconds") or 0.0)
                if covered > 30.0:
                    warnings.append(
                        f"Camera {filename} covered {covered:.0f}s of the edit window but was not selected for any segment."
                    )


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
    if platform in {"instagram", "tiktok", "reel"} and handle:
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
    if codec == "hevc_videotoolbox":
        return [
            "-c:v",
            codec,
            "-b:v",
            str(video_bitrate),
            "-maxrate",
            str(int(video_bitrate * 1.2)),
            "-bufsize",
            str(int(video_bitrate * 2)),
            "-tag:v",
            "hvc1",
        ]
    if codec == "libx265":
        return [
            "-c:v",
            "libx265",
            "-preset",
            "veryfast",
            "-b:v",
            str(video_bitrate),
            "-maxrate",
            str(int(video_bitrate * 1.2)),
            "-bufsize",
            str(int(video_bitrate * 2)),
            "-tag:v",
            "hvc1",
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


# 360 segments run every frame through a v360 equirect reprojection plus a
# per-frame sendcmd motion track (and a frame-diff motion verification pass),
# which is substantially more ffmpeg work per second of output than a plain
# flat crop/scale segment. Weighting the progress bar by this instead of raw
# duration keeps "N% done" honest instead of racing ahead on cheap segments
# and then stalling through the expensive ones.
SPHERICAL_SEGMENT_COST_MULTIPLIER = 3.5


def _segment_render_weight(segment: dict[str, Any]) -> float:
    duration = max(0.0, float(segment.get("duration_sec") or 0.0))
    if _spherical_shot(segment):
        return duration * SPHERICAL_SEGMENT_COST_MULTIPLIER
    return duration


def _plan_render_weight(segments: list[dict[str, Any]]) -> float:
    return sum(_segment_render_weight(segment) for segment in segments)


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
