"""Short hover previews with compressed master-audio excerpts for Frames review.

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

PREVIEW_VERSION = 6
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
    # A cache name must never measure cameras or wait for the export colour lock.
    identity = json.dumps({
        "version": PREVIEW_VERSION, "source": source, "mtime": mtime,
        "canvas": segment.get("review_canvas"), "layout": segment.get("review_layout"),
        "treatment": segment.get("reel_mix_treatment"), "subject_center": segment.get("reel_subject_center"),
        "clip_start": round(float(segment.get("clip_start_sec") or 0.0), 3),
        "duration": round(float(segment.get("duration_sec") or 0.0), 3),
        "master_audio": _audio_identity(project), "master_start": segment.get("master_start_sec", 0),
        "video_light": segment.get("video_light"), "light_settings": project.data.get("settings", {}).get("edit", {}).get("video_light"),
        "shot": segment.get("spherical_shot") or {}, "motion": segment.get("motion") or {}, "flat_framing":segment.get("flat_framing"),
    }, sort_keys=True, default=str)
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return preview_root(project) / f"preview-{index:04d}-{key}.mp4"


def _master_audio(project):
    value = (project.data.get('inputs', {}).get('master') or {}).get('path')
    return Path(value) if value and Path(value).is_file() else None


def _audio_identity(project):
    path = _master_audio(project)
    if path is None:
        return None
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def _attach_master_audio(project, segment, video, ffmpeg):
    """Mux only this short excerpt as AAC; never use the camera microphone."""
    master = _master_audio(project)
    if master is None:
        return
    duration = min(3., max(.1, float(segment.get('duration_sec') or .1)))
    start = float(segment.get('master_start_sec') or 0.)
    temporary = video.with_name(video.stem + '.audio.mp4')
    filters = ([f'adelay={round(-start*1000)}:all=1'] if start < 0 else [])
    filters += ['apad', f'atrim=duration={duration:.6f}']
    try:
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', str(video), '-ss', str(max(0., start)), '-i', str(master),
            '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-af', ','.join(filters),
            '-c:a', 'aac', '-b:a', '96k', '-ac', '2', '-ar', '48000',
            '-t', str(duration), '-movflags', '+faststart', str(temporary)],
            check=True, capture_output=True, timeout=30)
        os.replace(temporary, video)
    finally:
        temporary.unlink(missing_ok=True)


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


def _reel_graph(project, segment, color, *, source_filter=None):
    """Use the export's crop/subject/mix graph at a smaller review resolution."""
    from core.stages import export
    duration = max(.1, float(segment.get('duration_sec') or .1))
    graph = export._segment_filtergraph('reel', duration, {}, color,
        has_watermark=False, text_enabled=False, segment=segment,
        source_filter=source_filter, motion_filter=export._motion_filter(segment, 'reel', duration),
        reel_letterbox_filter=export._reel_letterbox_filter(project, segment, 'reel'))
    for before, after in [('2160:3840','540:960'),('3840:2160','960:540'),
                          ('1080:1920','270:480'),('1080x1920','270x480'),
                          ('1920:1080','480:270'),('1920x1080','480x270'),('w=1920:h=1080','w=480:h=270')]:
        graph=graph.replace(before,after)
    return graph[:-3]+f'[review_full];[review_full]fps={PREVIEW_FPS}[v]'


def _render_reel(project, segment, output, ffmpeg):
    from core.shot_review import _source_for
    from core.stages import export
    from core.video_light import resolved_profile
    source_filter=None
    source=_source_for(segment)
    if segment.get('spherical_shot'):
        source, _ = _existing_sphere_source(project, segment)
        source_filter=export._export_source_filter({'projection':segment.get('projection') or 'equirect'}, segment['spherical_shot'])
    from core.video_light import resolved_reel_profile
    graph=_reel_graph(project,segment,resolved_reel_profile(project,segment),source_filter=source_filter)
    duration=min(3.,max(.1,float(segment.get('duration_sec') or .1)))
    command=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y',
             '-ss',str(segment.get('clip_start_sec') or 0),'-t',str(duration),'-i',source,
             '-filter_complex',graph,'-map','[v]',*_ENCODE,str(output)]
    subprocess.run(command,check=True,capture_output=True,timeout=120)


def _render_flat(project: Project, segment: dict[str, Any], output: Path, ffmpeg: str) -> None:
    from core.shot_review import _source_for, _thumbnail_filter
    from core.stages import export
    from core.video_light import resolved_profile
    color = resolved_profile(project, segment)
    source = _source_for(segment)
    duration = min(3.0, max(0.1, float(segment.get("duration_sec") or 0.1)))
    if segment.get("spherical_shot"):
        # 360 hold: the review photo's framing, now in motion.
        sphere, _ = _existing_sphere_source(project, segment)
        render_segment = dict(segment, source_path=sphere, clip_path=sphere, projection="equirect")
        video_filter = _thumbnail_filter(render_segment, color).replace("w=360:h=202", f"w={PREVIEW_WIDTH}:h={PREVIEW_HEIGHT}")
        video_filter = f"{video_filter},fps={PREVIEW_FPS}"
        source = sphere
    else:
        # Fixed cameras: the export's base framing and its exact zoom/pan.
        motion = export._motion_filter(segment, "youtube", duration)
        base = export._base_video_filter("youtube")
        if motion and "zoompan=" in motion and not segment.get("flat_framing"):
            base = base.replace("1920:1080", "3840:2160")
        from core.flat_framing import filtergraph
        steps = [export._color_filter(color) if color else None, filtergraph(segment["flat_framing"]) if segment.get("flat_framing") else None, base, f"fps={int(export.TARGET_EXPORT_FPS)}", motion,
                 f"scale={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}", f"fps={PREVIEW_FPS}"]
        video_filter = ",".join(step for step in steps if step)
        # Preserve the export movement, at preview geometry rather than 4K.
        video_filter = (video_filter.replace("3840:2160", "960:540")
                        .replace("1920:1080", "480:270").replace("1920x1080", "480x270"))
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
    duration = min(3.0, max(0.1, float(segment.get("duration_sec") or 0.1)))
    frames = max(1, int(round(duration * PREVIEW_FPS)))
    # Same pose sampler choice as the export renderer.
    sampler = ((lambda seconds: _v360_motion_at(shot, duration, seconds))
               if (shot.get("type") in {"recorded_move", "planet"} and shot.get("movement") != "planet_to_stage")
               or not shot.get("movement") else None)
    from core.video_light import resolved_profile
    from core.stages.export import _color_filter
    color = resolved_profile(project, segment)
    if segment.get("review_canvas") == "reel":
        from core.video_light import resolved_reel_profile
        color = resolved_reel_profile(project, segment)
    reel_graph = _reel_graph(project, segment, color) if segment.get("review_canvas") == "reel" else None
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}", "-r", str(PREVIEW_FPS), "-i", "pipe:0", *(["-filter_complex", reel_graph, "-map", "[v]"] if reel_graph else ["-vf", _color_filter(color)] if color else []), *_ENCODE, str(output)]
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
    if output.is_file() and output.stat().st_size > 0:
        return output
    lock = _LOCKS[int(hashlib.sha256(str(output).encode()).hexdigest()[:8], 16) % len(_LOCKS)]
    with lock:
        if output.is_file() and output.stat().st_size > 0:
            return output
        ffmpeg = locate_executable("ffmpeg") or "ffmpeg"
        temporary = output.with_name(f"{output.stem}.{threading.get_ident()}.tmp.mp4")
        try:
            if _native_motion(segment):
                _render_sphere_motion(project, segment, temporary, ffmpeg)
            elif segment.get("review_canvas") == "reel":
                _render_reel(project, segment, temporary, ffmpeg)
            else:
                _render_flat(project, segment, temporary, ffmpeg)
            _attach_master_audio(project, segment, temporary, ffmpeg)
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("FFmpeg completed without producing a preview")
            os.replace(temporary, output)
            output.with_suffix(".error.json").unlink(missing_ok=True)
        except Exception as exc:
            output.with_suffix(".error.json").write_text(json.dumps({"error": str(exc)[-300:]}))
            raise
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
            from core.shot_review import _review_segments, _review_signature
            folder = str(project.folder) + ":" + _review_signature(_review_segments(project))
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
        # Finish each group before starting the next. No all-photo barrier and
        # no second producer racing the hover requests.
        from core.shot_review import render_review_thumbnails
        for first in range(0, len(segments), 3):
            if stop.is_set():
                return
            indices = set(range(first, min(first + 3, len(segments))))
            render_review_thumbnails(project, indices, batch_only=True)
            for index in sorted(indices):
                if stop.is_set():
                    return
                try:
                    render_preview(project, index, segments[index])
                except Exception as exc:
                    LOGGER.info("Review preview %s not prefetched: %s", index, exc)



PREFETCH = _Prefetcher()
