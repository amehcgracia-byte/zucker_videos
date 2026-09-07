"""ffmpeg export stage for wizard edit plans."""

from __future__ import annotations

import bisect
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import os
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
from core.normalization import EVEN_SDR_FILTER, NORMALIZATION_VERSION, SDR_TONEMAP_FILTER, ensure_global_cache_dirs, global_cache_root, global_segment_path, source_cache_key
from core.project import Project, atomic_write_json
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
    IPHONE_CROP_TOP_LIMIT,
    _valid_motion_recipe,
    load_edit_plan,
)

LOGGER = logging.getLogger(__name__)
_LOGO_CACHE_LOCK = threading.RLock()
LOGO_CACHE_RECIPE_VERSION = 2
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
INTRO_DURATION = 10.0
COLOR_PROFILE_VERSION = 4
REEL_LETTERBOX_CACHE_VERSION = 1
REEL_LETTERBOX_BLUR_SIGMA = 18.0
OUTRO_DURATION = 10.2
CONTENT_FADE_DURATION = 1.5
TRANSITION_PROFILES = {
    "youtube": {"duration": 0.12, "sections_only": True},
    "reel": {"duration": 0.08, "sections_only": False, "every": 3},
    "reel_horizontal": {"duration": 0.08, "sections_only": False, "every": 3},
    # No crossfade between equirectangular cuts: the 360 path is a direct
    # projection-safe passthrough. Its logo clips already fade from/to black.
    "360": {"duration": 0.0, "sections_only": True},
}


def _transition_profile(project: Project, platform: str) -> dict[str, Any]:
    """Return only this mode's transition settings."""
    profile = dict(TRANSITION_PROFILES.get(platform, {"duration": 0.0}))
    configured = (((project.data.get("settings") or {}).get("export") or {}).get("transitions") or {}).get(platform)
    if isinstance(configured, dict):
        profile.update(configured)
    profile["duration"] = max(0.0, float(profile.get("duration") or 0.0))
    return profile


def _transition_boundaries(segments: list[dict[str, Any]], profile: dict[str, Any]) -> list[int]:
    """Indices after which a flat-video crossfade is allowed."""
    if len(segments) < 2 or float(profile.get("duration") or 0.0) <= 0:
        return []
    boundaries: list[int] = []
    for index in range(len(segments) - 1):
        if profile.get("sections_only"):
            left = segments[index].get("section") or segments[index].get("section_id")
            right = segments[index + 1].get("section") or segments[index + 1].get("section_id")
            if left and right and left != right:
                boundaries.append(index)
        else:
            every = max(1, int(profile.get("every") or 1))
            if (index + 1) % every == 0:
                boundaries.append(index)
    return boundaries


def _render_flat_video_transitions(
    paths: list[Path], durations: list[float], boundaries: list[int], output_path: Path,
    video_bitrate: int, progress_callback: ProgressCallback, fade_duration: float,
) -> Path:
    """Encode selected flat-video xfade joins, leaving the other cuts hard."""
    if not boundaries:
        return paths[0] if len(paths) == 1 else _concat_flat_paths(paths, durations, output_path, video_bitrate, progress_callback)
    ffmpeg = _ffmpeg_path()
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, path in enumerate(paths):
        inputs += ["-i", str(path)]
        label = f"v{index}"
        labels.append(label)
        filters.append(f"[{index}:v]settb=AVTB,setpts=PTS-STARTPTS,fps=30,format=yuv420p[{label}]")
    chunks: list[tuple[str, float]] = []
    start = 0
    for boundary in [*boundaries, len(paths) - 1]:
        end = boundary + 1
        chunk_labels = labels[start:end]
        chunk_duration = sum(float(value) for value in durations[start:end])
        if len(chunk_labels) == 1:
            chunk_label = chunk_labels[0]
        else:
            chunk_label = f"chunk{len(chunks)}"
            filters.append("".join(f"[{label}]" for label in chunk_labels) + f"concat=n={len(chunk_labels)}:v=1:a=0[{chunk_label}]")
        chunks.append((chunk_label, chunk_duration))
        start = end
    current, current_duration = chunks[0]
    fade_duration = max(0.001, float(fade_duration))
    for index, (next_label, next_duration) in enumerate(chunks[1:], start=1):
        out = f"xf{index}"
        offset = max(0.0, current_duration - fade_duration)
        filters.append(f"[{current}][{next_label}]xfade=transition=fade:duration={fade_duration:.3f}:offset={offset:.3f}[{out}]")
        current = out
        current_duration += next_duration - fade_duration
    filters.append(f"[{current}]format=yuv420p[vout]")
    command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", *inputs,
               "-filter_complex", ";".join(filters), "-map", "[vout]", "-an", "-c:v", "libx264",
               "-preset", "veryfast", "-b:v", str(video_bitrate), "-r", "30", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", str(output_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "Video transition render failed")
    return output_path


def _concat_flat_paths(paths: list[Path], durations: list[float], output_path: Path, video_bitrate: int, progress_callback: ProgressCallback) -> Path:
    """Small fallback for a caller that has no selected transitions."""
    concat = output_path.with_suffix(".txt")
    concat.write_text("".join(_concat_file_line(path) for path in paths), encoding="utf-8")
    result = subprocess.run([str(_ffmpeg_path()), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(output_path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "Video segment join failed")
    return output_path
FRAME_INTERVAL_TOLERANCE = 0.50
FRAME_COUNT_METADATA_TIMEOUT_SEC = 10
FRAME_COUNT_DECODE_TIMEOUT_SEC = 120
FRAME_COUNT_DECODE_RETRY_TIMEOUT_SEC = 300
MIN_EXPORT_FREE_SPACE_BYTES = 512 * 1024 * 1024


class SegmentRenderError(FFmpegError):
    """A render failure with enough context to be actionable in the UI."""

    def __init__(self, index: int, source_path: str, cause: BaseException, required_bytes: int = 0) -> None:
        self.segment_index = int(index)
        self.source_path = str(source_path)
        self.exit_code = getattr(cause, "exit_code", None)
        self.stderr_tail = list(getattr(cause, "stderr_tail", []) or [])
        self.required_bytes = int(required_bytes or 0)
        super().__init__(_segment_failure_message(self.segment_index, self.source_path, cause, self.required_bytes))


def _format_gib(value: int | float) -> str:
    return f"{max(0.1, float(value) / (1024 ** 3)):.1f} GB"


def _segment_failure_message(index: int, source_path: str, cause: BaseException, required_bytes: int = 0) -> str:
    source = Path(source_path).expanduser()
    stderr = "\n".join(getattr(cause, "stderr_tail", []) or [])
    lowered = f"{cause}\n{stderr}".lower()
    if "no space left" in lowered or "disk full" in lowered or "enospc" in lowered:
        estimate = _format_gib(required_bytes) if required_bytes else "the estimated export space"
        return f"Not enough free space to continue (needs ~{estimate}). Export stopped at segment {index}."
    if not source.exists():
        return f"The drive containing {source} is no longer available. Export stopped at segment {index}."
    if "ffmpeg is missing" in lowered or isinstance(cause, FileNotFoundError):
        return f"ffmpeg is missing. Export stopped at segment {index}."
    exit_code = getattr(cause, "exit_code", None)
    code = f" (exit code {exit_code})" if exit_code is not None else ""
    detail = "\n".join(getattr(cause, "stderr_tail", []) or [])[-2000:]
    if not detail:
        detail = str(cause) or "ffmpeg returned an error"
    return f"Export failed at segment {index} ({source}){code}. ffmpeg reported:\n{detail}"


def _required_export_space_bytes(duration: float, video_bitrate: int, segment_count: int) -> int:
    # Account for the final file, per-segment global cache, per-export copies,
    # concat intermediates, and a modest working margin. This is deliberately
    # conservative because a segment can temporarily exist in three places.
    encoded = max(0.0, float(duration)) * (max(300_000, int(video_bitrate)) + AUDIO_BITRATE) / 8.0
    return max(MIN_EXPORT_FREE_SPACE_BYTES, int(encoded * 2.5 + 256 * 1024 * 1024))


def _check_export_disk_space(output_path: Path, required_bytes: int) -> None:
    ensure_global_cache_dirs()
    locations = {output_path.parent, global_cache_root()}
    free_values = []
    details = []
    for location in locations:
        location.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(location)
        free_values.append(usage.free)
        details.append(f"{location.resolve()}: {_format_gib(usage.free)} free")
    free = min(free_values) if free_values else 0
    if free < required_bytes:
        raise FFmpegError(
            f"Not enough free space to continue (needs ~{_format_gib(required_bytes)}; "
            f"the limiting volume has {_format_gib(free)} available). Checked: {'; '.join(details)}"
        )


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
        missing_sources = _missing_project_sources(project, plan)
        if missing_sources:
            listed = "\n".join(f"• {path}" for path in missing_sources[:12])
            extra = f"\n…and {len(missing_sources) - 12} more." if len(missing_sources) > 12 else ""
            if any(Path(path).parts[:2] == ("/", "Volumes") or str(path).startswith("/Volumes/") for path in missing_sources):
                raise FFmpegError(
                    "The drive containing the following source file(s) is no longer available:\n"
                    f"{listed}{extra}\n\nReconnect the drive and retry; completed segment caches will be reused."
                )
            raise FFmpegError(
                "This project references source files that are no longer available:\n"
                f"{listed}{extra}\n\n"
                "Re-link the files in the Inbox or choose Rebuild project to run analysis again."
            )
        master = project.data["inputs"].get("master")
        if not master:
            raise ValueError(t("missing_master_for_export"))
        platform = plan.get("platform") or project.data["settings"].get("wizard", {}).get("platform") or "youtube"
        run_id = _export_run_id()
        output_path = _output_path(project, platform, run_id)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        content_duration = _plan_duration(segments)
        duration = content_duration if platform == "reel" else content_duration + INTRO_DURATION + OUTRO_DURATION
        bitrate_info = _bitrate_for_duration(duration)
        required_space = _required_export_space_bytes(duration, bitrate_info["video_bitrate"], len(segments))
        _check_export_disk_space(output_path, required_space)
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
            # The container duration is authoritative for 360 exports.  In
            # particular, a concat stream-copy can complete successfully
            # while carrying an incorrect duration/timestamp timeline.
            duration = _media_duration(str(output_path))
        else:
            render_platform = "reel_horizontal" if platform == "reel" and str(plan.get("reel_aspect") or project.data.get("settings", {}).get("wizard", {}).get("reel_aspect") or "9:16") == "16:9" else platform
            _render_plan(
                project, segments, master["path"], output_path, render_platform,
                bitrate_info["video_bitrate"], warnings, progress_callback, required_space,
            )
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
                        "camera_sequence": [] if platform == "360" else [
                            str(segment.get("camera_id") or Path(str(segment.get("clip_path"))).name)
                            for segment in segments
                        ],
                        "camera_alternatives": [] if platform == "360" else [
                            {
                                "camera_id": str(segment.get("camera_id") or Path(str(segment.get("clip_path"))).name),
                                "available_camera_ids": list(segment.get("available_camera_ids") or []),
                                "alternative_available": bool(segment.get("camera_alternative_available")),
                            }
                            for segment in segments
                        ],
                        "camera_runs": [] if platform == "360" else _camera_runs(segments),
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


def _missing_project_sources(project: Project, plan: dict[str, Any]) -> list[str]:
    """Return unique source paths referenced by the project that disappeared."""
    candidates: list[str] = []
    master = project.data.get("inputs", {}).get("master") or {}
    if master.get("path"):
        candidates.append(str(master["path"]))
    for record in project.data.get("inputs", {}).get("videos", []):
        path = record.get("path") or record.get("source_path")
        if path:
            candidates.append(str(path))
    for segment in plan.get("segments") or []:
        path = segment.get("source_path") or segment.get("clip_path")
        if path:
            candidates.append(str(path))
    missing: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        path = str(Path(raw).expanduser())
        if path in seen or Path(path).exists():
            continue
        seen.add(path)
        missing.append(path)
    return missing


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


def _segment_worker_count(project: Project, segment_count: int) -> int:
    """Choose a bounded worker count without turning every FFmpeg into a fork bomb."""
    configured = int(project.data.get("settings", {}).get("export", {}).get("segment_workers", 2) or 0)
    if configured > 2:
        return max(1, min(configured, segment_count or 1, 8))
    cpu_count = max(2, int(os.cpu_count() or 2))
    # FFmpeg itself uses several threads. Half the logical CPUs is a useful
    # ceiling for concurrent encodes; the hard cap keeps memory and thermal
    # pressure predictable on high-core machines.
    workers = max(2, min(8, cpu_count // 2))
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        physical_pages = int(os.sysconf("SC_PHYS_PAGES"))
        memory_gib = (page_size * physical_pages) / (1024 ** 3)
        workers = min(workers, max(2, int(memory_gib // 2)))
    except (AttributeError, OSError, ValueError):
        pass
    return max(1, min(workers, segment_count or 1))


def _render_plan(
    project: Project,
    segments: list[dict[str, Any]],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    warnings: list[str],
    progress_callback: ProgressCallback,
    required_space: int | None = None,
) -> None:
    required_space = required_space or _required_export_space_bytes(
        _plan_duration(segments) + INTRO_DURATION + OUTRO_DURATION,
        video_bitrate,
        len(segments),
    )
    temp_dir = output_path.parent / f".{output_path.stem}-segments"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _check_export_disk_space(output_path, required_space)
    ffmpeg = _ffmpeg_path()
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    segment_paths: list[Path] = []
    total_duration = _plan_duration(segments)
    # Reel does not use the multicam colour-normalisation pass. Do not even
    # measure its sources: aside from being wasted work, that made Reel
    # exports pay the YouTube/360 colour-analysis cost before discarding it.
    color_profiles = {} if platform in {"reel", "reel_horizontal"} else _color_profiles_for_segments(project, segments, warnings)
    if platform in {"reel", "reel_horizontal"}:
        # Reel overlays are composited as RGBA images below. Do not apply the
        # multicam colour-normalisation profile to them: it was the source of
        # the strong green cast reported on promo exports.
        color_profiles = {}
    overlay_config = _overlay_config(platform, segments[0])
    if platform in {"reel", "reel_horizontal"}:
        overlay_config["reel_texts"] = list(project.data.get("settings", {}).get("wizard", {}).get("reel_text_overlays") or [])
        overlay_config["reel_images"] = list(project.data.get("settings", {}).get("wizard", {}).get("reel_image_overlays") or [])
        overlay_config["reel_videos"] = list(project.data.get("settings", {}).get("wizard", {}).get("reel_video_overlays") or [])
        # Reel overlay times are already expressed on the final 0-based Reel
        # timeline. They are composited once after segment assembly.
        overlay_config["reel_origin_sec"] = 0.0
    segment_overlay_config = overlay_config
    if platform in {"reel", "reel_horizontal"}:
        segment_overlay_config = dict(overlay_config)
        segment_overlay_config["reel_texts"] = []
        segment_overlay_config["reel_images"] = []
        segment_overlay_config["reel_videos"] = []
    verify_motion = bool(project.data.get("settings", {}).get("export", {}).get("verify_motion", True))
    phase_times: dict[str, float] = {}
    export_started = time.perf_counter()
    try:
        include_bookends = platform not in {"reel", "reel_horizontal"}
        if include_bookends:
            phase_started = time.perf_counter()
            intro_path = temp_dir / "intro.mp4"
            _render_logo_clip(
                intro_path, platform, "intro", INTRO_DURATION, video_bitrate,
                lambda percent, detail: progress_callback(8 + int(percent * 2 / 100), detail),
            )
            segment_paths.append(intro_path)
            phase_times["intro_sec"] = round(time.perf_counter() - phase_started, 3)
        render_segments = _continuous_spherical_render_segments(segments)
        segment_workers = _segment_worker_count(project, len(render_segments))
        render_started = time.perf_counter()
        render_stats: list[dict[str, Any]] = []
        progress_lock = threading.Lock()
        futures: dict[Any, tuple[int, dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=segment_workers) as executor:
            for index, segment in enumerate(render_segments, start=1):
                future = executor.submit(
                        _render_segment_job,
                        project, index, segment, len(render_segments), temp_dir, master_path,
                        platform, video_bitrate, segment_overlay_config,
            color_profiles.get(str(segment.get("clip_path")), {}), verify_motion,
                        progress_callback, progress_lock,
                    )
                futures[future] = (index, segment)
            results = []
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except BaseException as exc:
                    index, failed_segment = futures[future]
                    source_path = _segment_source_info(project, failed_segment).get("source_path") or failed_segment.get("source_path") or failed_segment.get("clip_path") or "unknown source"
                    if isinstance(exc, SegmentRenderError):
                        raise
                    raise SegmentRenderError(index, str(source_path), exc, required_space) from exc
        results.sort(key=lambda item: int(item["index"]))
        expected_indices = list(range(1, len(render_segments) + 1))
        actual_indices = [int(item["index"]) for item in results]
        if actual_indices != expected_indices:
            raise FFmpegError(f"Rendered segment order is not contiguous: {actual_indices}")
        cache_owners: dict[str, dict[str, Any]] = {}
        body_paths: list[Path] = []
        body_durations: list[float] = []
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
            body_paths.append(result["path"])
            body_durations.append(float(render_segments[int(result["index"]) - 1].get("duration_sec") or 0.0))
            warnings.extend(result["warnings"])
            render_stats.append({key: result[key] for key in ("index", "cached", "rendered_from", "ffmpeg_sec", "verify_sec", "total_sec")})
        transition_profile = _transition_profile(project, platform)
        boundaries = _transition_boundaries(render_segments, transition_profile)
        if boundaries:
            transitioned_body = temp_dir / "body-transitions.mp4"
            _render_flat_video_transitions(
                body_paths, body_durations, boundaries, transitioned_body, video_bitrate,
                lambda percent, detail: progress_callback(82 + int(percent * 2 / 100), detail),
                float(transition_profile["duration"]),
            )
            segment_paths.append(transitioned_body)
        else:
            segment_paths.extend(body_paths)
        if include_bookends:
            phase_started = time.perf_counter()
            outro_path = temp_dir / "outro.mp4"
            _render_logo_clip(
                outro_path, platform, "outro", OUTRO_DURATION, video_bitrate,
                lambda percent, detail: progress_callback(82 + int(percent * 2 / 100), detail),
            )
            segment_paths.append(outro_path)
            phase_times["outro_sec"] = round(time.perf_counter() - phase_started, 3)
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
        phase_started = time.perf_counter()
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
            total_duration + (INTRO_DURATION + OUTRO_DURATION if include_bookends else 0.0),
            t("joining_segments"),
            lambda percent, detail: progress_callback(84 + int(percent * 6 / 100), detail),
        )
        phase_times["concat_sec"] = round(time.perf_counter() - phase_started, 3)
        phase_started = time.perf_counter()
        cfr_video = _cadence_checked_joined_video(
            joined_video,
            temp_dir / "joined-video-cfr.mp4",
            video_bitrate,
            lambda percent, detail: progress_callback(89 + int(percent * 1 / 100), detail),
        )
        phase_times["cadence_sec"] = round(time.perf_counter() - phase_started, 3)
        real_duration = _media_duration(str(cfr_video))
        video_for_mux = cfr_video
        if platform in {"reel", "reel_horizontal"} and (overlay_config.get("reel_texts") or overlay_config.get("reel_images")):
            video_for_mux = temp_dir / "reel-overlays.mp4"
            _render_reel_overlays(
                cfr_video,
                video_for_mux,
                overlay_config,
                real_duration,
                video_bitrate,
                lambda percent, detail: progress_callback(89 + int(percent * 1 / 100), detail),
            )
            real_duration = _media_duration(str(video_for_mux))
        audio_start, audio_delay = _audio_mux_start_and_delay(segments) if include_bookends else (float(segments[0].get("master_start_sec") or 0.0), 0.0)
        phase_started = time.perf_counter()
        _mux_continuous_master_audio(
            video_for_mux,
            master_path,
            output_path,
            audio_start,
            real_duration,
            video_bitrate,
            lambda percent, detail: progress_callback(90 + int(percent * 5 / 100), detail),
            content_start=INTRO_DURATION if include_bookends else 0.0,
            content_end=max(INTRO_DURATION, real_duration - OUTRO_DURATION) if include_bookends else real_duration,
            audio_delay=audio_delay,
        )
        phase_times["mux_sec"] = round(time.perf_counter() - phase_started, 3)
        _write_export_link_audit(
            project,
            output_path,
            render_segments,
            results,
            concat_path,
            final_path=output_path,
        )
        if verify_motion and include_bookends:
            _verify_joined_output(output_path, segments, timeline_offset=INTRO_DURATION)
            _verify_final_audio(
                output_path,
                segments,
                master_path,
                audio_start,
                timeline_offset=INTRO_DURATION if include_bookends else 0.0,
                audio_delay=audio_delay,
                duration=real_duration,
                content_start=INTRO_DURATION if include_bookends else 0.0,
                content_end=max(INTRO_DURATION, real_duration - OUTRO_DURATION) if include_bookends else real_duration,
            )
        project.data["_export_performance"] = {
            "segment_count": len(render_segments),
            "segment_workers": segment_workers,
            "stream_copy_count": sum(1 for item in render_stats if item.get("rendered_from") == "stream_copy"),
            "wall_sec": round(time.perf_counter() - render_started, 3),
            "total_wall_sec": round(time.perf_counter() - export_started, 3),
            "phases": phase_times,
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
    phase_times: dict[str, float] = {}
    export_started = time.perf_counter()
    try:
        intro = temp_dir / "intro.mp4"
        body = temp_dir / "body.mp4"
        outro = temp_dir / "outro.mp4"
        joined = temp_dir / "joined.mp4"
        phase_started = time.perf_counter()
        _render_matched_logo_clip(
            intro,
            profile,
            "intro",
            INTRO_DURATION,
            video_bitrate,
            lambda percent, detail: progress_callback(8 + int(percent * 4 / 100), detail),
        )
        phase_times["intro_sec"] = round(time.perf_counter() - phase_started, 3)
        phase_started = time.perf_counter()
        _copy_trim_video(
            source_path,
            body,
            clip_start,
            duration,
            lambda percent, detail: progress_callback(12 + int(percent * 60 / 100), detail),
            codec_name=profile.get("codec_name"),
        )
        phase_times["body_trim_sec"] = round(time.perf_counter() - phase_started, 3)
        audio_start, audio_delay = _audio_mux_start_and_delay([segment])
        outro_duration = _outro_duration_for_remaining_music(
            master_path, audio_start, audio_delay, INTRO_DURATION + duration, song_end_sec
        )
        phase_started = time.perf_counter()
        _render_matched_logo_clip(
            outro,
            profile,
            "outro",
            outro_duration,
            video_bitrate,
            lambda percent, detail: progress_callback(72 + int(percent * 4 / 100), detail),
        )
        phase_times["outro_sec"] = round(time.perf_counter() - phase_started, 3)
        phase_started = time.perf_counter()
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
        phase_times["concat_sec"] = round(time.perf_counter() - phase_started, 3)
        real_duration = _media_duration(str(joined))
        muxed = temp_dir / "muxed.mp4"
        phase_started = time.perf_counter()
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
        phase_times["mux_sec"] = round(time.perf_counter() - phase_started, 3)
        # ffmpeg's `-metadata` flags above (belt-and-braces, plus `-strict
        # unofficial` on every stream-copy step) only ever produce cosmetic
        # udta string tags -- confirmed empirically: even with
        # +use_metadata_tags a stream-copied/remuxed file has zero real
        # uuid/sv3d/st3d spherical box structures, so VLC/YouTube see it as a
        # flat rectangle. This final injection step writes the actual Google
        # Spherical Video V2 boxes (vendored, pure Python, no external
        # install) and hard-fails the export if they don't verifiably land.
        try:
            phase_started = time.perf_counter()
            inject_spherical_metadata(str(muxed), str(output_path))
            phase_times["metadata_sec"] = round(time.perf_counter() - phase_started, 3)
        except SphericalMetadataError as exc:
            raise FFmpegError(f"360 export failed spherical metadata verification: {exc}") from exc
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise FFmpegError(f"360 export completed without a durable output file: {output_path}")
        LOGGER.info("360 export output written: %s (%d bytes)", output_path, output_path.stat().st_size)
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
        LOGGER.info("360 export performance total_sec=%.3f phases=%s", time.perf_counter() - export_started, phase_times)
        project.data["_export_performance"] = {
            "segment_count": 1,
            "segment_workers": 1,
            "total_wall_sec": round(time.perf_counter() - export_started, 3),
            "phases": phase_times,
            "concat_reencoded": bool(concat_reencoded),
        }
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

    # Some HEVC encoders do produce a valid stream-copy concat when the
    # parameter sets and tags match. Try it first, then decode-check the
    # assembled file before accepting it. Problematic camera/encoder pairs
    # fall through to the existing safe re-encode path.
    concat_path = temp_dir / "concat.txt"
    concat_path.write_text("".join(_concat_file_line(path) for path in [intro, body, outro]), encoding="utf-8")
    try:
        _run_ffmpeg_progress(
            [
                ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1",
                "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy",
                "-tag:v", "hvc1", "-strict", "unofficial", *_spherical_metadata_args(), str(joined),
            ],
            total_duration,
            t("joining_segments"),
            progress_callback,
        )
        _verify_video_decodes(joined)
        actual_duration = _media_duration(str(joined))
        duration_tolerance = max(2.0, 4.0 / max(1.0, float(profile.get("fps") or 30.0)))
        if abs(actual_duration - total_duration) > duration_tolerance:
            raise FFmpegError(
                f"HEVC stream-copy concat duration mismatch: expected {total_duration:.3f}s, "
                f"got {actual_duration:.3f}s"
            )
        # Cadence irregularity is not, by itself, a stream-copy failure.  A
        # native CFR/VFR camera stream can legitimately have timestamp
        # boundaries at a concat join; rejecting it here forced a full HEVC
        # re-encode and changed the timeline.  Decode failure is the actual
        # last-resort signal: only that falls through to the safe re-encode.
        LOGGER.info("HEVC 360 concat accepted stream-copy path=%s", joined)
        return False
    except (FFmpegError, OSError) as exc:
        LOGGER.info("HEVC 360 stream-copy concat rejected; re-encoding join: %s", exc)
        joined.unlink(missing_ok=True)

    fps = max(1.0, float(profile.get("fps") or 30.0))
    filter_complex = (
        f"[0:v:0][1:v:0][2:v:0]concat=n=3:v=1:a=0[joined];"
        f"[joined]fps=fps={fps:.6f}:round=near:start_time=0,"
        f"setpts=N/({fps:.6f}*TB)[v]"
    )
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
        "-r",
        f"{fps:.6f}",
        "-fps_mode",
        "cfr",
        "-video_track_timescale",
        str(max(1000, int(round(fps * 1000)))),
        "-t",
        f"{total_duration:.3f}",
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


def _verify_video_decodes(path: Path) -> None:
    """Decode a video to null to catch broken HEVC reference chains."""
    # A fixed 120s budget is fine for short clips but incorrectly rejects a
    # valid long 360 join on slower HEVC decoders. Scale the guard to the
    # amount of data that must actually be decoded, while keeping a hard cap
    # so a genuinely stuck decoder still fails loudly.
    try:
        timeout = max(120, min(900, int(path.stat().st_size / (25 * 1024 * 1024)) + 60))
    except OSError:
        timeout = 120
    try:
        result = subprocess.run(
            [_ffmpeg_path(), "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"Video decode verification timed out after {timeout}s for {path}") from exc
    diagnostics = (result.stderr or "").strip()
    lowered = diagnostics.lower()
    decoder_failure_markers = (
        "could not find ref",
        "error constructing the frame rps",
        "invalid undecodable nalu",
        "error while decoding",
    )
    if result.returncode != 0 or any(marker in lowered for marker in decoder_failure_markers):
        raise FFmpegError(diagnostics or f"Video decode verification failed for {path}")


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
    logo = _bookend_asset_path(kind)
    is_intro_card = _is_intro_card(logo, kind)
    width, height, fps = profile["width"], profile["height"], profile["fps"]
    cache_path = _logo_clip_cache_path(
        kind,
        duration,
        video_bitrate,
        logo,
        {
            "width": width,
            "height": height,
            "fps": fps,
            "codec": profile.get("codec_name"),
            "profile": profile.get("profile"),
            "level": profile.get("level"),
            "pix_fmt": profile.get("pix_fmt"),
        },
        mode="matched",
    )
    with _LOGO_CACHE_LOCK:
        if _reuse_cached_logo(cache_path, output_path):
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        render_path = cache_path.with_name(f".{cache_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.mp4")
        render_path.unlink(missing_ok=True)
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
    filter_complex = _logo_filtergraph_matched(kind, duration, bool(logo), height, is_intro_card)
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
    matched_options = ["-g", "1", "-bf", "0"] if profile["codec_name"] in {"hevc", "h265"} else []
    try:
        _run_ffmpeg_progress(command + _video_encode_args(hw_codec, video_bitrate) + matched_options + [str(render_path)], duration, f"{kind.title()} logo", progress_callback)
    except FFmpegError:
        render_path.unlink(missing_ok=True)
        _run_ffmpeg_progress(command + _video_encode_args(sw_codec, video_bitrate) + matched_options + [str(render_path)], duration, f"{kind.title()} logo", progress_callback)
    try:
        os.replace(render_path, cache_path)
        shutil.copy2(cache_path, output_path)
    finally:
        render_path.unlink(missing_ok=True)


def _logo_clip_cache_path(
    kind: str,
    duration: float,
    video_bitrate: int,
    logo: Path | None,
    parameters: dict[str, Any],
    mode: str,
) -> Path:
    """Return a global cache path for one exact logo animation encoding."""
    asset = {"path": None, "size": None, "mtime": None}
    if logo:
        try:
            stat = logo.stat()
            asset = {"path": str(logo.resolve()), "size": stat.st_size, "mtime": stat.st_mtime_ns}
        except OSError:
            asset = {"path": str(logo), "missing": True}
    key = stable_fingerprint({
        "recipe": LOGO_CACHE_RECIPE_VERSION,
        "kind": kind,
        "duration": round(float(duration), 6),
        "bitrate": int(video_bitrate),
        "mode": mode,
        "asset": asset,
        "parameters": parameters,
    })[:32]
    return global_cache_root() / "logo_clips" / f"{key}.mp4"


def _reuse_cached_logo(cache_path: Path, output_path: Path) -> bool:
    """Copy a complete cached logo to the export temp directory."""
    try:
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_path, output_path)
            LOGGER.info("Reusing cached logo clip %s", cache_path.name)
            return True
    except OSError:
        cache_path.unlink(missing_ok=True)
    return False


def _logo_filtergraph_matched(kind: str, duration: float, has_logo: bool, height: int, is_intro_card: bool = False) -> str:
    video = f"[0:v]format=yuv420p,{_constant_cadence_filter()}[bg]"
    if not has_logo:
        return f"{video};[bg]copy[v]"
    if is_intro_card:
        # The 16:9 card is contained in the 360 2:1 canvas so none of its
        # text is cropped or stretched. The surrounding pixels stay black.
        card_width = int(round(height * 16 / 9))
        return (
            f"{video};"
            f"[1:v]format=rgba,scale={card_width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={card_width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[card];"
            f"[bg][card]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"fade=t=in:st=0:d={CONTENT_FADE_DURATION:.3f},"
            f"fade=t=out:st={max(0.0, duration-CONTENT_FADE_DURATION):.3f}:d={CONTENT_FADE_DURATION:.3f}[v]"
        )
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
    logo = _bookend_asset_path(kind)
    is_intro_card = _is_intro_card(logo, kind)
    width, height = _target_size(platform)
    cache_path = _logo_clip_cache_path(
        kind,
        duration,
        video_bitrate,
        logo,
        {"width": width, "height": height, "fps": TARGET_EXPORT_FPS, "codec": "libx264", "profile": "high", "pix_fmt": "yuv420p"},
        mode=f"platform:{platform}",
    )
    with _LOGO_CACHE_LOCK:
        if _reuse_cached_logo(cache_path, output_path):
            return
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        render_path = cache_path.with_name(f".{cache_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.mp4")
        render_path.unlink(missing_ok=True)
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
    filter_complex = _logo_filtergraph(platform, kind, duration, bool(logo), is_intro_card)
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
    command.append(str(render_path))
    try:
        _run_ffmpeg_progress(command, duration, f"{kind.title()} logo", progress_callback)
        os.replace(render_path, cache_path)
        shutil.copy2(cache_path, output_path)
    finally:
        render_path.unlink(missing_ok=True)


def _logo_filtergraph(platform: str, kind: str, duration: float, has_logo: bool, is_intro_card: bool = False) -> str:
    video = f"[0:v]format=yuv420p,{_constant_cadence_filter()}[bg]"
    if not has_logo:
        return f"{video};[bg]copy[v]"
    if is_intro_card:
        width, height = _target_size(platform)
        return (
            f"{video};"
            f"[1:v]format=rgba,scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[card];"
            f"[bg][card]overlay=(W-w)/2:(H-h)/2:format=auto,"
            f"fade=t=in:st=0:d={CONTENT_FADE_DURATION:.3f},"
            f"fade=t=out:st={max(0.0, duration-CONTENT_FADE_DURATION):.3f}:d={CONTENT_FADE_DURATION:.3f}[v]"
        )
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
    if platform == "reel":
        return 1080, 1920
    if platform == "reel_horizontal":
        return 1920, 1080
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
    reel_letterbox_filter = _reel_letterbox_filter(project, segment, platform)
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
            reel_letterbox_filter=reel_letterbox_filter,
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
    reel_overlay_items = _reel_overlay_items(segment, overlay_config or {}, platform, output_path.parent)
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
        segment=segment,
        reel_overlay_items=reel_overlay_items,
        reel_letterbox_filter=reel_letterbox_filter,
    )
    command_base = _segment_video_command_base(
        ffmpeg,
        source["source_path"],
        segment,
        duration,
    )
    if watermark:
        command_base.extend(["-loop", "1", "-i", str(watermark)])
    for item in reel_overlay_items:
        command_base.extend(["-loop", "1", "-i", str(item["path"])])
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
    if reel_overlay_items:
        # FFmpeg 8.1.2 has an intermittent libavfilter crash when the
        # frame-evaluated scale/pad overlay graph is scheduled across filter
        # worker threads. Serialising this small graph is safer than making a
        # whole export single-threaded; the encoder remains threaded.
        command_base[command_base.index("-filter_complex"):command_base.index("-filter_complex")] = [
            "-filter_threads", "1", "-filter_complex_threads", "1",
        ]
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
                reel_letterbox_filter=reel_letterbox_filter,
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
        rendered_from = "original"
        if _stream_copy_eligible(project, segment, platform, overlay_config, color_profile, intro_fade, outro_fade):
            try:
                _copy_trim_video(
                    source_info["source_path"], tmp_segment,
                    float(segment.get("clip_start_sec") or 0.0), segment_duration,
                    segment_progress, codec_name=(source_info.get("probe") or {}).get("video_codec"),
                )
                _verify_segment_frame_duration(tmp_segment, _segment_frame_count(segment), label)
                rendered_from = "stream_copy"
            except (FFmpegError, OSError) as exc:
                LOGGER.info("Stream-copy fast path rejected for %s: %s", label, exc)
                tmp_segment.unlink(missing_ok=True)
                rendered_from = _render_segment(
                    project, segment, master_path, tmp_segment, platform, video_bitrate,
                    overlay_config, color_profile, segment_progress,
                    intro_fade=intro_fade, outro_fade=outro_fade,
                    warnings=local_warnings, command_recorder=commands,
                )
        else:
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
        "rendered_from": rendered_from if not cached else "cache",
        "ffmpeg_sec": round(ffmpeg_sec, 3),
        "verify_sec": round(time.perf_counter() - verify_started, 3),
        "total_sec": round(time.perf_counter() - started, 3),
    }


def _stream_copy_eligible(
    project: Project,
    segment: dict[str, Any],
    platform: str,
    overlay_config: dict[str, Any],
    color_profile: dict[str, Any],
    intro_fade: bool,
    outro_fade: bool,
) -> bool:
    """Return true only where stream copy cannot change visible frames or cadence."""
    if platform != "youtube" or intro_fade or outro_fade or color_profile:
        return False
    if _watermark_path() is not None or _spherical_shot(segment) or segment.get("motion"):
        return False
    if overlay_config.get("title") or overlay_config.get("band_name") or overlay_config.get("handle"):
        return False
    source = _segment_source_info(project, segment)
    probe = source.get("probe") or {}
    codec = str(probe.get("video_codec") or probe.get("codec_name") or "").lower()
    fps = float(probe.get("fps") or 0.0)
    width = int(probe.get("width") or 0)
    height = int(probe.get("height") or 0)
    if codec not in {"h264", "avc1"} or not bool(probe.get("cfr", True)):
        return False
    if abs(fps - TARGET_EXPORT_FPS) > 0.01 or (width, height) != _target_size(platform):
        return False
    start = float(segment.get("clip_start_sec") or 0.0)
    if start > 0.001 and abs(_preceding_keyframe(source["source_path"], start) - start) > (1.0 / TARGET_EXPORT_FPS):
        return False
    return True


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
    reel_letterbox_filter: str | None = None,
) -> str:
    reel_overlay_items = _reel_overlay_items(segment, overlay_config, platform, output_path.parent)
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
        segment=segment,
        reel_overlay_items=reel_overlay_items,
        reel_letterbox_filter=reel_letterbox_filter,
    )
    proxy_command = _segment_video_command_base(ffmpeg, proxy_path, segment, duration)
    if watermark:
        proxy_command.extend(["-loop", "1", "-i", str(watermark)])
    for item in reel_overlay_items:
        proxy_command.extend(["-loop", "1", "-i", str(item["path"])])
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
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        error = FFmpegError("ffmpeg is missing")
        error.exit_code = None
        error.stderr_tail = []
        raise error from exc
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
        error = FFmpegError((stderr or "").strip() or "ffmpeg export failed")
        error.exit_code = process.returncode
        error.stderr_tail = (stderr or "").strip().splitlines()[-12:]
        raise error
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
    if platform == "reel_horizontal":
        return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    if platform == "360":
        return "scale=3840:1920:force_original_aspect_ratio=decrease,pad=3840:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"


def _reel_letterbox_cache_path(project: Project, fingerprint: str) -> Path:
    """Return the per-project cache path for one source's native geometry."""
    return project.cache_dir / "reel_letterbox" / f"{fingerprint}.json"


def _native_clip_geometry(project: Project, segment: dict[str, Any]) -> dict[str, Any]:
    """Read and cache the original source dimensions used by Reel letterbox."""
    source = _segment_source_info(project, segment)
    fingerprint = str(source.get("cache_key") or "")
    if not fingerprint:
        fingerprint = stable_fingerprint({"source": source.get("source_path"), "probe": source.get("probe")})[:24]
    cache_path = _reel_letterbox_cache_path(project, fingerprint)
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("version") == REEL_LETTERBOX_CACHE_VERSION and cached.get("fingerprint") == fingerprint:
            return cached
    except (OSError, json.JSONDecodeError):
        pass

    probe = dict(source.get("probe") or {})
    width = int(probe.get("width") or 0)
    height = int(probe.get("height") or 0)
    if width <= 0 or height <= 0:
        metadata = ffprobe(str(source.get("source_path") or source.get("proxy_path") or ""))
        stream = next((item for item in metadata.get("streams") or [] if item.get("codec_type") == "video"), {})
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        probe = stream
    if width <= 0 or height <= 0:
        width, height = 16, 9
    sar = str(probe.get("sample_aspect_ratio") or "1:1")
    try:
        sar_num, sar_den = (int(part) for part in sar.split(":", 1))
        sample_aspect = sar_num / max(1, sar_den)
    except (ValueError, TypeError):
        sample_aspect = 1.0
    geometry = {
        "version": REEL_LETTERBOX_CACHE_VERSION,
        "fingerprint": fingerprint,
        "source_path": str(source.get("source_path") or ""),
        "width": width,
        "height": height,
        "sample_aspect": round(sample_aspect, 8),
        "aspect": round(width * sample_aspect / height, 8),
    }
    try:
        atomic_write_json(cache_path, geometry)
    except OSError:
        LOGGER.warning("Could not cache Reel native geometry at %s", cache_path, exc_info=True)
    return geometry


def _reel_letterbox_filter(project: Project, segment: dict[str, Any], platform: str) -> str | None:
    """Build a per-source Reel letterbox graph only for the explicit Mix mode."""
    if platform != "reel":
        return None
    reel_aspect = str(project.data.get("settings", {}).get("wizard", {}).get("reel_aspect") or "9:16")
    if reel_aspect != "mix":
        return None
    geometry = _native_clip_geometry(project, segment)
    aspect = float(geometry.get("aspect") or (16.0 / 9.0))
    LOGGER.info(
        "Reel per-clip letterbox source=%s native=%sx%s aspect=%.6f cache=%s",
        Path(str(geometry.get("source_path") or "")).name,
        geometry.get("width"), geometry.get("height"), aspect,
        _reel_letterbox_cache_path(project, str(geometry.get("fingerprint") or "")),
    )
    # Contain the real clip in the 9:16 canvas while using the same clip as a
    # blurred cover behind it.  The geometry is deliberately not inferred from
    # the output canvas, so every cut can change its visible native proportion.
    return (
        "split=2[reel_bg][reel_fg];"
        f"[reel_bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma={REEL_LETTERBOX_BLUR_SIGMA:.1f}[reel_blur];"
        "[reel_fg]format=rgba,scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black@0[reel_main];"
        "[reel_blur][reel_main]overlay=(W-w)/2:(H-h)/2:format=auto"
    )


def _motion_filter(segment: dict[str, Any], platform: str, duration: float) -> str | None:
    motion = segment.get("motion") or {}
    motion_type = motion.get("type")
    if motion_type == "ken_burns":
        return _ken_burns_filter(motion, platform, duration)
    if motion_type == "zoom_crop":
        return _zoom_crop_filter(motion, platform)
    return None


def _ken_burns_filter(motion: dict[str, Any], platform: str, duration: float) -> str | None:
    # Renderer-side gate: malformed/cached recipes with more than one motion
    # family are rejected instead of silently composing zoom and pan.
    if not _valid_motion_recipe(motion):
        return None
    width, height = _target_size(platform)
    try:
        zoom_start = float(motion.get("zoom_start", 1.0))
        zoom_end = float(motion.get("zoom_end", 1.06))
        pan_x_start = float(motion.get("pan_x_start", motion.get("pan_x", 0.5)))
        pan_x_end = float(motion.get("pan_x_end", motion.get("pan_x", 0.5)))
        pan_y = 0.5
    except (TypeError, ValueError):
        return None
    zoom_start = max(1.0, min(4.0, zoom_start))
    zoom_end = max(1.0, min(4.0, zoom_end))
    pan_x_start = max(0.0, min(1.0, pan_x_start))
    pan_x_end = max(0.0, min(1.0, pan_x_end))
    pan_y_start = max(0.0, min(1.0, float(motion.get("pan_y_start", pan_y))))
    pan_y_end = max(0.0, min(1.0, float(motion.get("pan_y_end", pan_y))))
    frame_count = max(1, int(round(max(0.1, float(duration)) * TARGET_EXPORT_FPS)))
    try:
        speed_factor = max(0.25, min(2.0, float(motion.get("speed_factor", 1.0))))
    except (TypeError, ValueError):
        speed_factor = 1.0
    # Saturation is intentional: once the endpoint is reached, hold there.
    # It prevents an out-of-bounds crop from wrapping/reversing into a second
    # apparent movement.
    progress = f"min(1,n/{max(1, frame_count - 1)}*{speed_factor:.6f})"
    zoom_expr = f"({zoom_start:.6f}+({zoom_end:.6f}-{zoom_start:.6f})*{progress})"
    if motion.get("lock_target"):
        target_x = max(0.05, min(0.95, float(motion.get("target_x", 0.5))))
        target_y = max(0.05, min(0.95, float(motion.get("target_y", 0.5))))
        pan_x_expr = f"min(1,max(0,(({zoom_expr})*{target_x:.6f}-0.5)/(({zoom_expr})-1)))"
        if motion.get("vertical_motion") in {"down", "up"}:
            pan_y_expr = f"max(({IPHONE_CROP_TOP_LIMIT:.6f}+0.5/({zoom_expr})),({pan_y_start:.6f}+({pan_y_end:.6f}-{pan_y_start:.6f})*{progress}))"
        else:
            pan_y_expr = f"max(({IPHONE_CROP_TOP_LIMIT:.6f}+0.5/({zoom_expr})),min(1,max(0,(({zoom_expr})*{target_y:.6f}-0.5)/(({zoom_expr})-1))))"
    else:
        pan_x_expr = f"({pan_x_start:.6f}+({pan_x_end:.6f}-{pan_x_start:.6f})*{progress})"
        pan_y_expr = f"max(({IPHONE_CROP_TOP_LIMIT:.6f}+0.5/({zoom_expr})),({pan_y_start:.6f}+({pan_y_end:.6f}-{pan_y_start:.6f})*{progress}))"
    scaled_width = f"ceil({width}*{zoom_expr}/2)*2"
    scaled_height = f"ceil({height}*{zoom_expr}/2)*2"
    return (
        f"scale=w='{scaled_width}':h='{scaled_height}':eval=frame,"
        f"crop={width}:{height}:x='(iw-{width})*{pan_x_expr}':y='(ih-{height})*{pan_y_expr}'"
    )


def _zoom_crop_filter(motion: dict[str, Any], platform: str) -> str | None:
    """Static (non-animated) zoom+crop, used by operator-avoidance adjustments.

    Wider zoom range than ken_burns (up to 1.6x) since this needs to push a
    prominent foreground operator fully off-frame, not just add subtle motion.
    """
    width, height = _target_size(platform)
    try:
        zoom = float(motion.get("zoom", 1.35))
        pan_x = 0.5
        pan_y = 0.5
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
    if "runtime_motion_enabled" in shot and not bool(shot.get("runtime_motion_enabled")):
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
    if "runtime_motion_enabled" in shot and not bool(shot.get("runtime_motion_enabled")):
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
    segment: dict[str, Any] | None = None,
    reel_overlay_items: list[dict[str, Any]] | None = None,
    reel_letterbox_filter: str | None = None,
) -> str:
    filters = []
    if source_filter:
        filters.append(source_filter)
    base_filter = _base_video_filter(platform)
    if platform == "reel" and not reel_letterbox_filter and segment and segment.get("reel_subject_center") and not motion_filter:
        center = segment["reel_subject_center"]
        cx = max(0.0, min(1.0, float(center.get("x") or 0.5)))
        cy = max(0.0, min(1.0, float(center.get("y") or 0.5)))
        base_filter = f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920:x='(iw-ow)*{cx:.4f}':y='(ih-oh)*{cy:.4f}',setsar=1,format=yuv420p"
    post_filters = [motion_filter, _exact_cadence_filter(frame_count) if frame_count else _constant_cadence_filter()]
    if intro_fade:
        post_filters.append(f"fade=t=in:st=0:d={CONTENT_FADE_DURATION:.3f}")
    if outro_fade:
        post_filters.append(f"fade=t=out:st={max(0.0, duration - CONTENT_FADE_DURATION):.3f}:d={CONTENT_FADE_DURATION:.3f}")
    if text_enabled:
        post_filters.extend(_text_filters(platform, duration, overlay_config))
    post_filters.extend([f"tpad=stop_mode=clone:stop_duration={1.0 / TARGET_EXPORT_FPS:.6f}", "format=yuv420p"])
    if reel_letterbox_filter:
        pre_filters = [source_filter, _color_filter(color_profile)]
        prefix = ",".join(item for item in pre_filters if item)
        graph = f"[0:v]{prefix + ',' if prefix else ''}{reel_letterbox_filter}[reel_letterboxed];"
        graph += f"[reel_letterboxed]{','.join(item for item in post_filters if item)}[base]"
    else:
        filters.extend([base_filter, motion_filter, _color_filter(color_profile)])
        filters.extend(post_filters)
        graph = f"[0:v]{','.join(filter for filter in filters if filter)}[base]"
    overlay_items = reel_overlay_items or []
    current_label = "base"
    overlay_graph = []
    overlay_start = 2 if has_watermark else 1
    for index, item in enumerate(overlay_items):
        output_label = f"reel_overlay_{index}"
        input_index = overlay_start + index
        fade = str(item.get("animation") or "fade").lower()
        fade_in = "fade=t=in:st=0:d=0.25:alpha=1," if fade in {"fade", "slide", "scale"} else ""
        fade_out = f"fade=t=out:st={max(0.0, float(item.get('end_sec') or duration) - 0.25):.3f}:d=0.25:alpha=1," if fade in {"fade", "slide", "scale"} else ""
        start = float(item.get("start_sec") or 0.0)
        enter_end = start + 0.25
        source_transform = ""
        overlay_x = "0"
        if fade == "slide":
            # Full-frame transparent overlays can still slide cleanly: the
            # transparent canvas moves as one layer and settles at x=0.
            overlay_x = f"if(lt(t\\,{enter_end:.3f})\\,-overlay_w+overlay_w*(t-{start:.3f})/0.25\\,0)"
        elif fade == "scale":
            source_transform = f"scale=w='trunc(iw*if(lt(t,{enter_end:.3f}),0.75+0.25*(t-{start:.3f})/0.25,1)/2)*2':h='trunc(ih*if(lt(t,{enter_end:.3f}),0.75+0.25*(t-{start:.3f})/0.25,1)/2)*2':eval=frame,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        overlay_graph.append(
            f"[{input_index}:v]format=rgba,{source_transform}{fade_in}{fade_out}setpts=PTS-STARTPTS[reel_src_{index}];"
            f"[{current_label}][reel_src_{index}]overlay=x='{overlay_x}':y=0:enable='between(t,{start:.3f},{float(item.get('end_sec') or duration):.3f})':format=auto[{output_label}]"
        )
        current_label = output_label
    if overlay_graph:
        graph += ";" + ";".join(overlay_graph)
        graph += f";[{current_label}]copy[composited]"
        current_label = "composited"
    if not has_watermark:
        return f"{graph};[{current_label}]copy[v]"
    margin = 40 if platform == "youtube" else 28
    wm_height = 65 if platform == "youtube" else 58
    if not intro_logo and not outro_logo:
        if overlay_items:
            return (
                f"{graph};"
                f"[1:v]format=rgba,scale=-1:{wm_height},colorchannelmixer=aa=0.70[wm];"
                f"[{current_label}][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto[v]"
            )
        return (
            f"{graph};"
            f"[1:v]format=rgba,scale=-1:{wm_height},colorchannelmixer=aa=0.70[wm];"
            f"[{current_label}][wm]overlay=W-w-{margin}:H-h-{margin}:format=auto[v]"
        )
    return _logo_overlay_filtergraph(graph, platform, duration, intro_logo, outro_logo, margin=margin, wm_height=wm_height)


def _render_reel_overlays(
    video_path: Path,
    output_path: Path,
    overlay_config: dict[str, Any],
    duration: float,
    video_bitrate: int,
    progress_callback: ProgressCallback | None = None,
) -> None:
    """Composite Reel overlays once on the assembled, final-timeline video."""
    ffmpeg = _ffmpeg_path()
    items = _reel_overlay_items(
        {"master_start_sec": 0.0, "duration_sec": duration},
        {**overlay_config, "reel_origin_sec": 0.0},
        str(overlay_config.get("platform") or "reel"),
        output_path.parent,
    )
    if not items:
        shutil.copy2(video_path, output_path)
        return
    graph = "[0:v]format=yuv420p[reel_base]"
    current = "reel_base"
    graph_parts: list[str] = []
    for index, item in enumerate(items):
        input_index = index + 1
        label = f"reel_final_{index}"
        animation = str(item.get("animation") or "fade").lower()
        start = float(item.get("start_sec") or 0.0)
        end = float(item.get("end_sec") or duration)
        fade_in = "fade=t=in:st=0:d=0.25:alpha=1," if animation in {"fade", "slide", "scale"} else ""
        fade_out = f"fade=t=out:st={max(0.0, end - 0.25):.3f}:d=0.25:alpha=1," if animation in {"fade", "slide", "scale"} else ""
        source_transform = ""
        overlay_x = "0"
        overlay_y = "0"
        if item.get("kind") == "video":
            target_width = max(40, int(1080 * max(0.05, min(1.0, float(item.get("width") or 0.35)))))
            source_transform = f"scale={target_width}:-2:force_original_aspect_ratio=decrease,colorchannelmixer=aa={max(0.05, min(1.0, float(item.get('opacity') or 1.0))):.3f},"
            overlay_x = f"(W-w)*{max(0.0, min(1.0, float(item.get('x') or 0.5))):.4f}"
            overlay_y = f"(H-h)*{max(0.0, min(1.0, float(item.get('y') or 0.5))):.4f}"
        elif animation == "slide":
            overlay_x = f"if(lt(t\\,{start + 0.25:.3f})\\,-overlay_w+overlay_w*(t-{start:.3f})/0.25\\,0)"
        elif animation == "scale":
            source_transform = f"scale=w='trunc(iw*if(lt(t,{start + 0.25:.3f}),0.75+0.25*(t-{start:.3f})/0.25,1)/2)*2':h='trunc(ih*if(lt(t,{start + 0.25:.3f}),0.75+0.25*(t-{start:.3f})/0.25,1)/2)*2':eval=frame,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        graph_parts.append(
            f"[{input_index}:v]format=rgba,{source_transform}{fade_in}{fade_out}setpts=PTS-STARTPTS[reel_src_{index}];"
            f"[{current}][reel_src_{index}]overlay=x='{overlay_x}':y='{overlay_y}':enable='between(t,{start:.3f},{end:.3f})':format=auto[{label}]"
        )
        current = label
    graph += ";" + ";".join(graph_parts) + f";[{current}]format=yuv420p[v]"
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1", "-i", str(video_path)]
    for item in items:
        if item.get("kind") == "video":
            command.extend(["-i", str(item["path"])])
        else:
            command.extend(["-loop", "1", "-i", str(item["path"])])
    command.extend(["-filter_threads", "1", "-filter_complex_threads", "1", "-filter_complex", graph, "-map", "[v]", "-an", "-t", f"{duration:.3f}", "-pix_fmt", "yuv420p", "-r", f"{TARGET_EXPORT_FPS:.3f}", "-fps_mode", "cfr"])
    command.extend(_video_encode_args("libx264", video_bitrate))
    command.append(str(output_path))
    _run_ffmpeg_progress(command, duration, "Rendering Reel overlays", progress_callback)


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
            "reel_letterbox_version": REEL_LETTERBOX_CACHE_VERSION if platform == "reel" else None,
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
    frames = _video_frame_count(path, expected_frames=frame_count)
    actual = frames / TARGET_EXPORT_FPS
    if frames != frame_count:
        raise FFmpegError(f"Segment duration is not frame-exact for {label}: expected {expected:.6f}s ({frame_count} frames), got {actual:.6f}s ({frames} frames)")


def _video_frame_count(path: Path, expected_frames: int | None = None) -> int:
    """Return a frame count without making normal 360 verification decode twice.

    Encoders commonly write ``nb_frames`` into the container. Reading that
    metadata is cheap; only a missing or suspicious value falls back to the
    expensive full decode. The fallback has a realistic timeout for 360
    segments and one longer retry for a machine under parallel CPU load.
    """
    started = time.perf_counter()
    metadata_result = subprocess.run(
        [
            _ffprobe_path(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=nb_frames,duration,avg_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=FRAME_COUNT_METADATA_TIMEOUT_SEC,
    )
    metadata_count: int | None = None
    if metadata_result.returncode == 0:
        for raw in (metadata_result.stdout or "").splitlines():
            raw = raw.strip()
            if raw.isdigit() and int(raw) > 0:
                metadata_count = int(raw)
                break
    if metadata_count is not None and (expected_frames is None or metadata_count == expected_frames):
        LOGGER.info("frame count verification path=%s method=metadata frames=%s seconds=%.3f", path, metadata_count, time.perf_counter() - started)
        return metadata_count

    command = [
        _ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    last_timeout: subprocess.TimeoutExpired | None = None
    for timeout in (FRAME_COUNT_DECODE_TIMEOUT_SEC, FRAME_COUNT_DECODE_RETRY_TIMEOUT_SEC):
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            last_timeout = exc
            LOGGER.warning("frame count verification timed out path=%s timeout=%ss; retrying=%s", path, timeout, timeout != FRAME_COUNT_DECODE_RETRY_TIMEOUT_SEC)
            continue
        if result.returncode != 0:
            raise FFmpegError((result.stderr or "").strip() or f"Could not count segment frames: {path}")
        try:
            frames = int((result.stdout or "0").strip().splitlines()[0])
        except (IndexError, ValueError) as exc:
            raise FFmpegError(f"Could not parse segment frame count for {path}: {result.stdout!r}") from exc
        LOGGER.info("frame count verification path=%s method=full_decode frames=%s seconds=%.3f", path, frames, time.perf_counter() - started)
        return frames
    raise FFmpegError(
        f"Frame-count verification timed out twice for {path} "
        f"(tried {FRAME_COUNT_DECODE_TIMEOUT_SEC}s and {FRAME_COUNT_DECODE_RETRY_TIMEOUT_SEC}s)"
    ) from last_timeout


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


def _verify_video_cadence(
    path: Path,
    label: str,
    start: float = 0.0,
    duration: float | None = None,
    fps: float = TARGET_EXPORT_FPS,
) -> None:
    expected_delta = 1.0 / max(1.0, float(fps))
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
    brightness = max(-0.12, min(0.12, float(color_profile.get("brightness_adjust") or 0.0)))
    saturation = max(0.86, min(1.16, float(color_profile.get("saturation_adjust") or 1.0)))
    red = max(-0.12, min(0.12, float(color_profile.get("red_balance") or 0.0)))
    blue = max(-0.12, min(0.12, float(color_profile.get("blue_balance") or 0.0)))
    # Keep chroma changes deliberately small; the profile is a bridge between
    # cameras, not a replacement for the recorded look.
    return (
        f"eq=brightness={brightness:.4f}:saturation={saturation:.4f},"
        f"colorbalance=rs={red:.4f}:gs={-red * 0.35:.4f}:bs={-blue:.4f}:"
        f"rm={red:.4f}:gm={-red * 0.35:.4f}:bm={-blue:.4f}"
    )


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


def _reel_overlay_items(
    segment: dict[str, Any],
    config: dict[str, Any],
    platform: str,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Rasterise Reel overlays so builds without libavfilter drawtext still work."""
    if platform not in {"reel", "reel_horizontal"}:
        return []
    width, height = _target_size(platform)
    items: list[dict[str, Any]] = []
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except Exception:
        LOGGER.exception("Pillow is required for Reel text overlays")
        return []
    overlay_specs = [("text", item) for item in config.get("reel_texts") or []]
    overlay_specs += [("image", item) for item in config.get("reel_images") or []]
    overlay_specs += [("video", item) for item in config.get("reel_videos") or []]
    segment_start = float(segment.get("master_start_sec") or 0.0) - float(config.get("reel_origin_sec") or 0.0)
    segment_duration = max(0.0, float(segment.get("duration_sec") or 0.0))
    segment_end = segment_start + segment_duration

    def local_interval(raw: dict[str, Any]) -> tuple[float, float] | None:
        """Convert Reel-global overlay time to this segment's filter clock."""
        raw_start = max(0.0, float(raw.get("start_sec") or 0.0))
        raw_end = raw_start + max(0.1, float(raw.get("duration_sec") or 3.0))
        if raw_end <= segment_start or raw_start >= segment_end:
            return None
        return max(0.0, raw_start - segment_start), min(segment_duration, raw_end - segment_start)

    def hex_rgba(value: Any, alpha: int) -> tuple[int, int, int, int]:
        value = str(value or "#ffffff")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            value = "#ffffff"
        return tuple(int(value[offset:offset + 2], 16) for offset in (1, 3, 5)) + (max(0, min(255, alpha)),)

    for index, (kind, raw) in enumerate(overlay_specs):
        interval = local_interval(raw)
        if interval is None:
            continue
        start, end = interval
        if kind == "video":
            video_path = Path(str(raw.get("path") or "")).expanduser().resolve()
            if video_path.exists() and video_path.is_file():
                items.append({"kind": "video", "path": video_path, "start_sec": start, "end_sec": end, "animation": raw.get("animation") or "fade", "x": float(raw.get("x") if raw.get("x") is not None else 0.5), "y": float(raw.get("y") if raw.get("y") is not None else 0.5), "width": float(raw.get("width") or 0.35), "opacity": float(raw.get("opacity") or 1.0)})
            continue
        if kind == "image":
            try:
                image = Image.open(str(raw.get("path"))).convert("RGBA")
                max_width = max(40, int(width * max(0.05, min(1.0, float(raw.get("width") or 0.35)))))
                image.thumbnail((max_width, height), Image.Resampling.LANCZOS)
                alpha = image.getchannel("A").point(lambda value: round(value * max(0.05, min(1.0, float(raw.get("opacity") or 1.0)))))
                image.putalpha(alpha)
                canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                x = int(float(raw.get("x") or 0.5) * width - image.width / 2)
                y = int(float(raw.get("y") or 0.5) * height - image.height / 2)
                canvas.alpha_composite(image, (x, y))
                image_path = output_dir / f"reel-overlay-image-{index}.png"
                canvas.save(image_path)
                items.append({"path": image_path, "start_sec": start, "end_sec": end, "animation": raw.get("animation") or "fade"})
            except Exception:
                LOGGER.warning("Skipping unreadable Reel image overlay %s", raw.get("path"), exc_info=True)
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        opacity = max(0.05, min(1.0, float(raw.get("opacity", 1.0))))
        rgba = hex_rgba(raw.get("color"), round(opacity * 255))
        size = max(18, min(160, int(float(raw.get("size") or 54))))
        font = ImageFont.truetype(str(_font_path(raw.get("font"), raw.get("font_weight"))), size) if _font_path(raw.get("font"), raw.get("font_weight")) else ImageFont.load_default()
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        outline = max(0, min(12, int(float(raw.get("outline_width") or 2))))
        bbox = draw.multiline_textbbox((0, 0), text, font=font, stroke_width=outline)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = raw.get("x")
        y = raw.get("y")
        if x is None or y is None:
            position = str(raw.get("position") or "middle-center")
            vertical, horizontal = position.split("-", 1) if "-" in position else ("middle", "center")
            x = {"left": 0.08, "center": 0.5, "right": 0.92}.get(horizontal, 0.5)
            y = {"top": 0.08, "middle": 0.5, "bottom": 0.92}.get(vertical, 0.5)
        px = int(float(x) * width - (text_width / 2 if float(x) <= 1 else 0))
        py = int(float(y) * height - (text_height / 2 if float(y) <= 1 else 0))
        if float(x) <= 1 and str(raw.get("position") or "").endswith("left"):
            px = int(float(x) * width)
        if float(x) <= 1 and str(raw.get("position") or "").endswith("right"):
            px = int(float(x) * width - text_width)
        if float(y) <= 1 and str(raw.get("position") or "").startswith("top"):
            py = int(float(y) * height)
        if float(y) <= 1 and str(raw.get("position") or "").startswith("bottom"):
            py = int(float(y) * height - text_height)
        bg_alpha = round(opacity * max(0.0, min(1.0, float(raw.get("background_opacity") or 0.0))) * 255)
        if bg_alpha:
            padding = max(4, int(size * 0.18))
            radius = max(0, min(80, int(float(raw.get("background_radius") or 0))))
            draw.rounded_rectangle((px - padding, py - padding, px + text_width + padding, py + text_height + padding), radius=radius, fill=hex_rgba(raw.get("background_color"), bg_alpha))
        shadow_color = hex_rgba(raw.get("shadow_color"), round(opacity * 220))
        shadow_x = int(float(raw.get("shadow_offset_x") or 3))
        shadow_y = int(float(raw.get("shadow_offset_y") or 3))
        shadow_blur = max(0, min(30, int(float(raw.get("shadow_blur") or 4))))
        if shadow_blur or shadow_x or shadow_y:
            shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_layer)
            shadow_draw.multiline_text((px + shadow_x, py + shadow_y), text, font=font, fill=shadow_color, stroke_width=outline, stroke_fill=shadow_color)
            if shadow_blur:
                shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))
            image.alpha_composite(shadow_layer)
            draw = ImageDraw.Draw(image)
        draw.multiline_text((px, py), text, font=font, fill=rgba, stroke_width=outline, stroke_fill=hex_rgba(raw.get("outline_color"), round(opacity * 255)))
        image_path = output_dir / f"reel-overlay-text-{index}.png"
        image.save(image_path)
        items.append({"path": image_path, "start_sec": start, "end_sec": end, "animation": raw.get("animation") or "fade"})
    return items


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
    if platform in {"instagram", "tiktok", "reel", "reel_horizontal"} and handle:
        filters.append(_drawtext(handle, f"x=(w-tw)/2:y=h-th-130:fontsize=38:enable='gte(t,{max(0.0, duration - 4):.3f})'"))
    for item in config.get("reel_texts") or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        color = str(item.get("color") or "#ffffff").replace("#", "0x")
        size = max(18.0, min(160.0, float(item.get("size") or 54.0)))
        start = max(0.0, float(item.get("start_sec") or 0.0))
        end = min(duration, start + max(0.1, float(item.get("duration_sec") or 3.0)))
        position = str(item.get("position") or "middle-center")
        x_map = {"left": "48", "center": "(w-tw)/2", "right": "w-tw-48"}
        y_map = {"top": "48", "middle": "(h-th)/2", "bottom": "h-th-48"}
        vertical, horizontal = (position.split("-", 1) if "-" in position else ("middle", "center"))
        filters.append(_drawtext(text, f"x={x_map.get(horizontal, x_map['center'])}:y={y_map.get(vertical, y_map['middle'])}:fontsize={size:.1f}:fontcolor={color}:enable='between(t,{start:.3f},{end:.3f})'"))
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
    title = str(first_segment.get("title") or config.get("project_name") or "").strip()
    # Generic source-window labels are metadata, not user-authored Reel copy.
    # In particular, older projects persisted "Full video" and the Reel
    # filtergraph then painted it over the live/exported picture.
    if title.casefold() in {"full video", "video"}:
        title = ""
    return {
        "platform": platform,
        "title": title,
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
    personal = _global_config().get("personal_logo_path")
    if personal:
        candidate = Path(str(personal)).expanduser()
        if candidate.exists():
            return candidate
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


def _personal_logo_path() -> Path | None:
    personal = _global_config().get("personal_logo_path")
    if not personal:
        return None
    candidate = Path(str(personal)).expanduser()
    return candidate if candidate.exists() else None


def _intro_card_path() -> Path | None:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    candidates = [
        bundle_root / "assets" / "intro_card_watermark.png",
        Path(__file__).resolve().parents[2] / "assets" / "intro_card_watermark.png",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _bookend_asset_path(kind: str) -> Path | None:
    """Select the standalone intro/outro asset.

    A personal logo always wins. The designed card replaces only the default
    intro; the existing outro logo remains unchanged.
    """
    personal = _personal_logo_path()
    if personal:
        return personal
    if kind == "intro":
        return _intro_card_path() or _intro_logo_path() or _logo_path()
    return _intro_logo_path() or _logo_path()


def _is_intro_card(path: Path | None, kind: str) -> bool:
    card = _intro_card_path()
    return bool(path and card and kind == "intro" and path.resolve() == card.resolve())


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


def _font_path(preferred: Any = None, weight: Any = None) -> Path | None:
    names = {
        "verdana": "Verdana Bold.ttf",
        "verdana bold": "Verdana Bold.ttf",
        "arial": "Arial.ttf",
        "bundled": "Verdana Bold.ttf",
    }
    preferred_name = names.get(str(preferred or "").strip().lower())
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "assets" / "fonts"
    preferred_candidates = [root / preferred_name] if preferred_name else []
    for candidate in (
        *preferred_candidates,
        root / "ReelSans.ttf",
        root / "Verdana Bold.ttf",
        root / "Arial.ttf",
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


COLOR_REFERENCE_PRIORITY = ("sony", "360", "iphone")


def _color_camera_kind(record: dict[str, Any]) -> str:
    """Classify a source for the approved, deterministic reference order."""
    text = " ".join(str(record.get(key) or "") for key in (
        "camera_id", "camera_name", "camera", "camera_label", "filename", "path", "source_path"
    )).lower()
    projection = str(record.get("projection") or (record.get("probe") or {}).get("projection") or "").lower()
    if "sony" in text:
        return "sony"
    if projection in {"equirect", "raw_insv"} or "360" in text or "insv" in text:
        return "360"
    if "iphone" in text or "img_" in text or any(token.endswith(".mov") for token in text.split()):
        return "iphone"
    return "other"


def _color_reference_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    present = {kind: record for record in records if (kind := _color_camera_kind(record)) != "other"}
    for kind in COLOR_REFERENCE_PRIORITY:
        if kind in present:
            return present[kind]
    return records[0] if records else None


def _color_profile_artifact(project: Project) -> Path:
    return project.cache_dir / "color_profiles.json"


def _color_source_key(project: Project, record: dict[str, Any], path: str) -> str:
    fingerprint = str(record.get("cache_key") or (record.get("normalized") or {}).get("cache_key") or source_cache_key(record))
    return stable_fingerprint({
        "project": str(project.folder.resolve()),
        "camera": str(record.get("camera_id") or record.get("camera_name") or Path(path).stem).lower(),
        "fingerprint": fingerprint,
    })[:32]


def _color_profiles_for_segments(project: Project, segments: list[dict[str, Any]], warnings: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """Measure each camera once and correct toward the fixed camera priority reference."""
    warnings = warnings if warnings is not None else []
    records = list(project.data.get("inputs", {}).get("videos", []))
    by_clip: dict[str, tuple[dict[str, Any], str]] = {}
    for segment in segments:
        clip_path = str(segment.get("clip_path") or "")
        if not clip_path or clip_path in by_clip:
            continue
        info = _segment_source_info(project, segment)
        record = next((item for item in records if str((item.get("normalized") or {}).get("path") or item.get("path") or "") in {clip_path, str(info.get("source_path") or "")}), None)
        by_clip[clip_path] = (record or info, str(info.get("source_path") or clip_path))
    measured: dict[str, dict[str, Any]] = {}
    for clip_path, (record, source_path) in by_clip.items():
        profile, warning = cached_or_measure_clip_color(project, source_path, record=record)
        if warning:
            warnings.append(warning)
        profile = dict(profile)
        profile["camera_kind"] = _color_camera_kind(record)
        profile["camera_id"] = str(record.get("camera_id") or record.get("camera_name") or Path(source_path).stem).lower()
        measured[clip_path] = profile
    valid = [profile for profile in measured.values() if profile.get("luma") is not None]
    if not valid:
        return {path: {} for path in measured}
    ref_record = _color_reference_record([record for record, _ in by_clip.values()])
    ref_kind = _color_camera_kind(ref_record or {})
    ref_profile = next((profile for profile in valid if profile.get("camera_kind") == ref_kind), valid[0])
    if float(ref_profile.get("white_clip_ratio") or 0) > 0.05 or float(ref_profile.get("black_clip_ratio") or 0) > 0.05 or not 35 <= float(ref_profile.get("luma") or 0) <= 220:
        warnings.append(
            f"La cámara de referencia de color ({ref_profile.get('camera_id')}) presenta exposición potencialmente defectuosa; se usa por prioridad fija ({ref_kind}), sin sustituirla automáticamente."
        )
    corrected: dict[str, dict[str, Any]] = {}
    for path, profile in measured.items():
        corrected[path] = color_correction_for_profile(profile, ref_profile)
    artifact = {
        "version": COLOR_PROFILE_VERSION,
        "reference_priority": list(COLOR_REFERENCE_PRIORITY),
        "reference_camera": ref_profile.get("camera_id"),
        "profiles": {path: profile for path, profile in measured.items()},
        "corrections": corrected,
    }
    try:
        _color_profile_artifact(project).parent.mkdir(parents=True, exist_ok=True)
        _color_profile_artifact(project).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        warnings.append(f"No se pudo guardar el perfil de color del proyecto: {exc}")
    return corrected


def cached_or_measure_clip_color(project: Project, path: str, record: dict[str, Any] | None = None) -> tuple[dict[str, float], str | None]:
    """Return cached color profile or measure bounded samples."""
    record = record or next((item for item in project.data.get("inputs", {}).get("videos", []) if path in {item.get("path"), (item.get("normalized") or {}).get("path")}), {})
    key = _color_source_key(project, record, path)
    cache_path = global_cache_root() / "color" / f"{key}.json"
    if cache_path.exists():
        try:
            with cache_path.open("r", encoding="utf-8") as fh:
                cached = json.load(fh)
            if isinstance(cached, dict) and cached.get("color_profile_version") == COLOR_PROFILE_VERSION and "luma" in cached and "saturation" in cached:
                return {key: float(value) for key, value in cached.items() if isinstance(value, (int, float))}, None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    profile, warning = measure_clip_color(path)
    if profile:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump({"color_profile_version": COLOR_PROFILE_VERSION, **profile}, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return profile, warning


def _color_cache_key(project: Project, path: str) -> str:
    for record in project.data.get("inputs", {}).get("videos", []):
        normalized = record.get("normalized") or {}
        if path in {record.get("path"), normalized.get("path")}:
            return str(record.get("cache_key") or normalized.get("cache_key") or source_cache_key(record))
    return stable_fingerprint({"path": str(Path(path).expanduser().resolve())})[:24]


def measure_clip_color(path: str) -> tuple[dict[str, float], str | None]:
    """Measure luma, contrast and chroma over five fixed samples."""
    try:
        duration = _media_duration(path)
    except Exception as exc:
        return {}, t("color_skipped", filename=Path(path).name, reason=str(exc))
    fields: dict[str, list[float]] = {key: [] for key in ("YAVG", "YMIN", "YMAX", "SATAVG", "UAVG", "VAVG")}
    for command in color_sample_commands(path, duration):
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
        except subprocess.TimeoutExpired:
            return {}, t("color_skipped", filename=Path(path).name, reason="sample timed out")
        if result.returncode != 0:
            return {}, t("color_skipped", filename=Path(path).name, reason=(result.stderr or "sample failed").strip()[:180])
        text = f"{result.stdout}\n{result.stderr}"
        for key in fields:
            fields[key].extend(float(value) for value in re.findall(rf"lavfi\.signalstats\.{key}=([0-9.]+)", text))
    if not fields["YAVG"] or not fields["SATAVG"]:
        return {}, t("color_skipped", filename=Path(path).name, reason="no signalstats samples")
    mean = lambda key, fallback=0.0: sum(fields[key]) / len(fields[key]) if fields[key] else fallback
    return {
        "luma": mean("YAVG"), "saturation": mean("SATAVG"),
        "luma_min": mean("YMIN"), "luma_max": mean("YMAX"),
        "u_mean": mean("UAVG", 128.0), "v_mean": mean("VAVG", 128.0),
        "black_clip_ratio": sum(1 for value in fields["YMIN"] if value <= 2) / max(1, len(fields["YMIN"])),
        "white_clip_ratio": sum(1 for value in fields["YMAX"] if value >= 253) / max(1, len(fields["YMAX"])),
    }, None


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


def color_correction_for_profile(
    profile: dict[str, float],
    reference: dict[str, float] | float | None = None,
    target_sat: float | None = None,
    *,
    target_luma: float | None = None,
) -> dict[str, float]:
    """Return conservative, fixed per-camera correction toward the reference."""
    legacy_target = not isinstance(reference, dict)
    if isinstance(reference, dict):
        target_luma = float(reference.get("luma") or 128.0)
        target_sat = float(reference.get("saturation") or 64.0)
        target_u = float(reference.get("u_mean") or 128.0)
        target_v = float(reference.get("v_mean") or 128.0)
    else:
        target_luma = float(target_luma if target_luma is not None else (reference or 128.0))
        target_sat = float(target_sat or 64.0)
        target_u = target_v = 128.0
    luma = max(1.0, float(profile.get("luma") or target_luma))
    sat = max(1.0, float(profile.get("saturation") or target_sat))
    if legacy_target:
        return {
            "brightness_adjust": max(-0.08, min(0.08, (target_luma - luma) / 255.0 * 0.35)),
            "saturation_adjust": max(0.90, min(1.10, 1.0 + (target_sat - sat) / 255.0 * 0.60)),
            "red_balance": 0.0,
            "blue_balance": 0.0,
        }
    return {
        # Strong enough to be visible across cameras, but still bounded so the
        # source look and dynamic range remain intact.
        "brightness_adjust": max(-0.12, min(0.12, (target_luma - luma) / 255.0 * 0.90)),
        "saturation_adjust": max(0.86, min(1.16, 1.0 + (target_sat - sat) / 255.0 * 1.20)),
        # U/V are the measured chroma axes.  The wider bound and multiplier
        # are intentional: white-balance mismatch was previously imperceptible
        # even when the exposure correction was technically non-zero.
        "red_balance": max(-0.12, min(0.12, (target_v - float(profile.get("v_mean") or 128.0)) / 128.0 * 0.45)),
        "blue_balance": max(-0.12, min(0.12, (target_u - float(profile.get("u_mean") or 128.0)) / 128.0 * 0.45)),
    }


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


def _camera_runs(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize consecutive physical-camera runs for export auditing."""
    runs: list[dict[str, Any]] = []
    for segment in segments:
        camera_id = str(segment.get("camera_id") or Path(str(segment.get("clip_path"))).name)
        start = float(segment.get("master_start_sec") or 0.0)
        end = start + float(segment.get("duration_sec") or 0.0)
        alternative = bool(segment.get("camera_alternative_available"))
        available = list(segment.get("available_camera_ids") or [])
        if runs and runs[-1]["camera_id"] == camera_id:
            runs[-1]["segment_count"] += 1
            runs[-1]["end_sec"] = round(end, 6)
            runs[-1]["duration_sec"] = round(runs[-1]["end_sec"] - runs[-1]["start_sec"], 6)
            runs[-1]["alternative_available"] = runs[-1]["alternative_available"] or alternative
        else:
            runs.append({
                "camera_id": camera_id,
                "segment_count": 1,
                "start_sec": round(start, 6),
                "end_sec": round(end, 6),
                "duration_sec": round(end - start, 6),
                "alternative_available": alternative,
                "available_camera_ids": available,
            })
    return runs


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
