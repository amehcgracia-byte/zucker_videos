"""Short silent hover previews for the Frames review.

Each preview shows the planned shot as it will render: fixed cameras through
the export's own zoom filter, 360 camera moves through the export's own
reprojection (at 480x270, 15 fps), and 360 holds with the thumbnail framing.
Clips are small (~100-200 KB), cached by content like the review photos, made
on demand when a card is hovered and prefetched one at a time in the
background. Prefetch stops as soon as the final render starts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from core.ffmpeg import locate_executable
from core.project import Project

LOGGER = logging.getLogger(__name__)

PREVIEW_VERSION = 1
PREVIEW_WIDTH, PREVIEW_HEIGHT = 480, 270
PREVIEW_FPS = 15
PREVIEW_SPHERE_WIDTH = 1920
_ENCODE = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
_LOCKS = [threading.Lock() for _ in range(16)]


def preview_root(project: Project) -> Path:
    root = Path(project.cache_dir) / "shot_review" / "previews-v1"
    root.mkdir(parents=True, exist_ok=True)
    return root


def preview_path(project: Project, index: int, segment: dict[str, Any]) -> Path:
    from core.shot_review import _source_for
    from core.storage import cache_mtime_ns
    source = _source_for(segment)
    mtime = cache_mtime_ns(Path(source)) if source and Path(source).exists() else 0
    identity = json.dumps({
        "version": PREVIEW_VERSION, "source": source, "mtime": mtime,
        "clip_start": round(float(segment.get("clip_start_sec") or 0.0), 3),
        "duration": round(float(segment.get("duration_sec") or 0.0), 3),
        "shot": segment.get("spherical_shot") or {}, "motion": segment.get("motion") or {},
    }, sort_keys=True, default=str)
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return preview_root(project) / f"preview-{index:04d}-{key}.mp4"


def _native_motion(segment: dict[str, Any]) -> bool:
    """The export's own rule for frame-by-frame 360 reprojection."""
    from core.stages.export import _shot_requires_runtime_motion
    shot = segment.get("spherical_shot") or {}
    return bool(shot and str(segment.get("projection") or "equirect").lower() == "equirect"
                and shot.get("runtime_motion_enabled", True)
                and (shot.get("movement") or _shot_requires_runtime_motion(shot)))


def _existing_sphere_source(project: Project, segment: dict[str, Any]) -> tuple[str, tuple[int, int] | None]:
    """A cached equirect proxy when one exists; never transcode a whole clip."""
    from core.ffmpeg import ffprobe
    from core.shot_review import _source_for, _spherical_analysis_source
    source = _spherical_analysis_source(project, segment)
    if source == _source_for(segment):
        proxy = _existing_export_proxy(project, segment)
        source = proxy or source
    try:
        stream = next(item for item in ffprobe(source).get("streams") or [] if item.get("codec_type") == "video")
        return source, (int(stream["width"]), int(stream["height"]))
    except (StopIteration, KeyError, ValueError, Exception):
        return source, None


def _existing_export_proxy(project: Project, segment: dict[str, Any]) -> str | None:
    from core.stages import export
    info = export._segment_source_info(project, segment)
    try:
        target = export._spherical_export_proxy_target(project, info, segment)
    except Exception:
        return None
    return str(target) if target and target.is_file() and target.stat().st_size > 0 else None


def _render_flat(project: Project, segment: dict[str, Any], output: Path, ffmpeg: str) -> None:
    from core.shot_review import _source_for, _thumbnail_filter
    from core.stages import export
    source = _source_for(segment)
    duration = max(0.1, float(segment.get("duration_sec") or 0.1))
    if segment.get("spherical_shot"):
        # 360 hold: the review photo's framing, now in motion.
        sphere, _ = _existing_sphere_source(project, segment)
        render_segment = dict(segment, source_path=sphere, clip_path=sphere, projection="equirect")
        video_filter = _thumbnail_filter(render_segment).replace("w=360:h=202", f"w={PREVIEW_WIDTH}:h={PREVIEW_HEIGHT}")
        video_filter = f"{video_filter},fps={PREVIEW_FPS}"
        source = sphere
    else:
        # Fixed cameras: the export's base framing and its exact zoom/pan.
        motion = export._motion_filter(segment, "youtube", duration)
        base = export._base_video_filter("youtube")
        if motion and "zoompan=" in motion:
            base = base.replace("1920:1080", "3840:2160")
        steps = [base, f"fps={int(export.TARGET_EXPORT_FPS)}", motion,
                 f"scale={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}", f"fps={PREVIEW_FPS}"]
        video_filter = ",".join(step for step in steps if step)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-ss", f"{float(segment.get('clip_start_sec') or 0.0):.3f}", "-t", f"{duration:.3f}",
               "-i", source, "-vf", video_filter, *_ENCODE, str(output)]
    subprocess.run(command, check=True, capture_output=True, timeout=300,
                   preexec_fn=(lambda: os.nice(10)) if os.name == "posix" else None)


def _render_sphere_motion(project: Project, segment: dict[str, Any], output: Path, ffmpeg: str) -> None:
    from core.spherical_motion import run_reprojected_command
    from core.stages.export import _v360_motion_at
    shot = segment.get("spherical_shot") or {}
    source, size = _existing_sphere_source(project, segment)
    if not size:
        raise RuntimeError(f"Could not read the 360 source {source}")
    duration = max(0.1, float(segment.get("duration_sec") or 0.1))
    frames = max(1, int(round(duration * PREVIEW_FPS)))
    # Same pose sampler choice as the export renderer.
    sampler = ((lambda seconds: _v360_motion_at(shot, duration, seconds))
               if (shot.get("type") in {"recorded_move", "planet"} and shot.get("movement") != "planet_to_stage")
               or not shot.get("movement") else None)
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}", "-r", str(PREVIEW_FPS), "-i", "pipe:0", *_ENCODE, str(output)]
    # Decode at most PREVIEW_SPHERE_WIDTH wide: plenty for a 480 px view of a
    # narrow lens, and far cheaper to convert than the 4K/6K sphere.
    width = min(size[0], PREVIEW_SPHERE_WIDTH)
    decode = (width, max(2, round(size[1] * width / size[0] / 2) * 2)) if width < size[0] else None
    run_reprojected_command(command, source, size, float(segment.get("clip_start_sec") or 0.0), frames, shot,
                            pose_sampler=sampler, output_size=(PREVIEW_WIDTH, PREVIEW_HEIGHT), fps=PREVIEW_FPS,
                            decode_size=decode)


def render_preview(project: Project, index: int, segment: dict[str, Any]) -> Path:
    """Render (or reuse) the hover preview for one review slot."""
    output = preview_path(project, index, segment)
    lock = _LOCKS[int(hashlib.sha256(str(output).encode()).hexdigest()[:8], 16) % len(_LOCKS)]
    with lock:
        if output.is_file() and output.stat().st_size > 0:
            return output
        ffmpeg = locate_executable("ffmpeg") or "ffmpeg"
        temporary = output.with_name(f"{output.stem}.{threading.get_ident()}.tmp.mp4")
        try:
            if _native_motion(segment):
                _render_sphere_motion(project, segment, temporary, ffmpeg)
            else:
                _render_flat(project, segment, temporary, ffmpeg)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("FFmpeg completed without producing a preview")
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    return output


class _Prefetcher:
    """One background worker; previews are a convenience, never a competitor."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._project_folder: str | None = None

    def start(self, project: Project) -> None:
        with self._lock:
            folder = str(project.folder)
            if self._thread and self._thread.is_alive() and self._project_folder == folder:
                return
            self.stop_locked()
            self._stop = threading.Event()
            self._project_folder = folder
            self._thread = threading.Thread(target=self._run, args=(project, self._stop), daemon=True,
                                            name="review-preview-prefetch")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self.stop_locked()

    def stop_locked(self) -> None:
        self._stop.set()
        self._thread = None

    @staticmethod
    def _run(project: Project, stop: threading.Event) -> None:
        from core.shot_review import _review_segments, _thumbnail_asset
        try:
            segments = _review_segments(project)
        except Exception:
            return
        # Let the review photos finish first; they are what the user sees.
        thumbnails = Path(project.cache_dir) / "shot_review" / "assets-v2"
        for _ in range(600):
            if stop.is_set():
                return
            if all(_thumbnail_asset(thumbnails, index, segment)[2].exists() for index, segment in enumerate(segments)):
                break
            stop.wait(1.0)
        for index, segment in enumerate(segments):
            if stop.is_set():
                return
            try:
                render_preview(project, index, segment)
            except Exception as exc:  # A missing preview falls back to the photo.
                LOGGER.info("Review preview %s not prefetched: %s", index, exc)


PREFETCH = _Prefetcher()
