"""Fast, cached shot-review assets and conservative slot replacement."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from core.project import Project
from core.ffmpeg import locate_executable
from core.spherical_view import spherical_view_filter
from core.spherical_view import (
    effective_fov,
    effective_pitch,
    effective_projection_control,
    effective_roll,
    normalize_projection_preset,
    view_parameters,
)
from core.stages.base import ProgressCallback, ProgressDetail, artifact_path
from core.stages.cut import load_coverage
from core.stages.edit import IPHONE_CROP_TOP_LIMIT, SPHERICAL_NORMAL_FOV_MIN, _camera_id
from core.reel_framing import subject_box_for_window


LOGGER = logging.getLogger(__name__)
_RENDER_STATUS_LOCK = threading.RLock()
_SPHERICAL_PROXY_LOCK = threading.RLock()
SPHERICAL_ANALYSIS_PROXY_VERSION = 1
SPHERICAL_ANALYSIS_WIDTH = 960
SPHERICAL_ANALYSIS_HEIGHT = 480

_REVIEW_SHOT_LABELS = {
    "singer": "Cantante",
    "full_stage": "Escenario completo",
    "audience": "Publico",
    "left": "Lado izquierdo",
    "right": "Lado derecho",
    "audience_stage_wide": "Publico y escenario",
    "drummer": "Bateria",
    "pianist": "Pianista",
    "planet": "Planeta",
}


class ThumbnailRenderError(RuntimeError):
    """A review thumbnail could not be rendered by FFmpeg."""


def _plan(project: Project) -> dict[str, Any]:
    path = artifact_path(project, "edit_plan.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _source_for(segment: dict[str, Any]) -> str:
    # A spherical review frame must come from the equirectangular source so it
    # can be reframed with this segment's landmark.  The ordinary proxy is a
    # single default view and therefore makes every landmark thumbnail look
    # identical.
    if segment.get("spherical_shot") and segment.get("source_path"):
        return str(segment["source_path"])
    return str(segment.get("proxy_path") or segment.get("clip_path") or segment.get("source_path") or "")


def _current_spherical_landmarks(project: Project, source_path: str | None = None) -> dict[str, dict[str, Any]]:
    settings = project.data.get("settings", {}) or {}
    profiles = settings.get("spherical_landmarks_by_source") or {}
    candidates = []
    if source_path:
        candidates.extend([str(source_path), str(Path(source_path).expanduser().resolve())])
    for key in candidates:
        raw = profiles.get(key)
        if isinstance(raw, dict) and raw:
            return {str(name): dict(value) for name, value in raw.items() if isinstance(value, dict)}
    raw = settings.get("spherical_landmarks") or {}
    return {str(key): dict(value) for key, value in raw.items() if isinstance(value, dict)}


def _review_segment(project: Project, segment: dict[str, Any]) -> dict[str, Any]:
    """Overlay current saved landmark angles onto a planned review segment.

    Review can be opened after a user edits landmarks but before a stale edit
    plan has been rebuilt.  The card label and its image must then use the same
    current source of truth, rather than silently trusting the old plan pose.
    """
    shot = segment.get("spherical_shot") or {}
    shot_type = str(shot.get("shot_id") or shot.get("type") or "")
    source_path = str(segment.get("source_path") or segment.get("spherical_source_path") or segment.get("clip_path") or "").strip()
    saved = _current_spherical_landmarks(project, source_path).get(shot_type)
    if not saved or not shot:
        return dict(segment)
    effective = dict(shot)
    for field in ("yaw", "pitch", "fov", "roll", "projection_preset", "projection_control", "weight"):
        if field in saved:
            effective[field] = saved[field]
    # Review cards must use the same canonical pose that preview/export use.
    effective["projection_preset"] = normalize_projection_preset(effective.get("projection_preset"), shot_type)
    effective["pitch"] = effective_pitch(effective.get("pitch", 0.0), shot_type)
    effective["fov"] = effective_fov(effective.get("fov", 95.0), shot_type, effective["projection_preset"])
    effective["roll"] = effective_roll(effective.get("roll", 0.0), shot_type)
    effective["projection_control"] = effective_projection_control(effective.get("projection_control"))
    effective["type"] = shot_type
    effective["shot_id"] = shot_type
    effective["label"] = _REVIEW_SHOT_LABELS.get(shot_type, shot.get("label") or shot_type)
    result = dict(segment)
    result["spherical_shot"] = effective
    return result


def _review_segments(project: Project) -> list[dict[str, Any]]:
    return [_review_segment(project, segment) for segment in (_plan(project).get("segments") or [])]



def _spherical_analysis_source(project: Project, segment: dict[str, Any], progress_callback: ProgressCallback | None = None) -> str:
    """Return a cached low-resolution equirectangular source for one 360 clip.

    Decoding a 5K/6K original once per thumbnail is the dominant Review cost.
    This proxy preserves the sphere (it is deliberately *not* flat), so each
    thumbnail still applies its own authored yaw/pitch/FOV. Export continues
    to read the original camera file.
    """
    source = _source_for(segment)
    projection = str(segment.get("projection") or "").lower()
    source_suffix = Path(source).suffix.lower()
    if projection not in {"equirect", "raw_insv"}:
        projection = "raw_insv" if source_suffix in {".insv", ".insp"} else "equirect"
    if not source or projection not in {"equirect", "raw_insv"}:
        return source
    source_path = Path(source).expanduser()
    try:
        stat = source_path.stat()
    except OSError:
        return source
    insv_fov = float(segment.get("insv_fov") or 190.0)
    key = hashlib.sha256(
        f"{source_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{projection}|{insv_fov:.3f}|{SPHERICAL_ANALYSIS_PROXY_VERSION}"
        .encode("utf-8")
    ).hexdigest()[:24]
    target = project.cache_dir / "spherical_analysis" / f"equirect-{key}.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    with _SPHERICAL_PROXY_LOCK:
        if target.exists() and target.stat().st_size > 0:
            return str(target)
        ffmpeg = locate_executable("ffmpeg") or "ffmpeg"
        tmp = target.with_suffix(".tmp.mp4")
        if projection == "raw_insv":
            source_filter = (
                f"v360=input=dfisheye:output=e:ih_fov={insv_fov:.3f}:iv_fov={insv_fov:.3f}:interp=lanczos,"
            )
        else:
            source_filter = ""
        filter_graph = (
            f"{source_filter}scale={SPHERICAL_ANALYSIS_WIDTH}:{SPHERICAL_ANALYSIS_HEIGHT}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={SPHERICAL_ANALYSIS_WIDTH}:{SPHERICAL_ANALYSIS_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
            "format=yuv420p"
        )
        command = [
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-i", str(source_path), "-map", "0:v:0", "-an",
            "-vf", filter_graph,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-progress", "pipe:1", "-nostats", str(tmp),
        ]
        try:
            from core.stages.export import _media_duration, _run_ffmpeg_progress
            def proxy_progress(percent: int, detail: str) -> None:
                if progress_callback:
                    progress_callback(0, ProgressDetail(detail, task_id=f"review-source-{key}",
                        label=f"Preparing 360 review source {source_path.name}", percent=percent))
            _run_ffmpeg_progress(command, _media_duration(str(source_path)),
                                 f"Preparing 360 review source {source_path.name}", proxy_progress)
            if not tmp.exists() or tmp.stat().st_size <= 0:
                raise ThumbnailRenderError("Could not create the 360 analysis proxy")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
    return str(target)


def _review_pose_for_cache(segment: dict[str, Any]) -> dict[str, Any]:
    shot = segment.get("spherical_shot") or {}
    pose = {key: shot.get(key) for key in ("type", "shot_id", "pitch", "fov", "roll", "projection_preset", "projection_control", "projection", "insv_fov")}
    try:
        pose["yaw"] = float(shot.get("yaw") or 0.0) % 360.0
    except (TypeError, ValueError):
        pose["yaw"] = 0.0
    pose["motion"] = segment.get("motion") or {}
    pose["source_projection"] = segment.get("projection")
    return pose


def _review_signature(segments: list[dict[str, Any]]) -> str:
    payload = []
    for segment in segments:
        item = dict(segment)
        pose = _review_pose_for_cache(segment)
        # The plan may retain the authored yaw as either 330° or -30°.  Keep
        # that representation out of the cache namespace: both values are
        # the same spherical direction and must address the same thumbnail.
        shot = dict(item.get("spherical_shot") or {})
        if shot:
            shot["yaw"] = pose["yaw"]
            item["spherical_shot"] = shot
        item["review_pose"] = pose
        payload.append(item)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _thumbnail_filter(segment: dict[str, Any], color_profile: dict[str, Any] | None = None) -> str:
    color_profile = color_profile or {}
    if color_profile:
        # Keep review frames visually consistent with the final render.  The
        # profile is computed once by export and passed through unchanged.
        brightness = max(-0.12, min(0.12, float(color_profile.get("brightness_adjust") or 0.0)))
        saturation = max(0.86, min(1.16, float(color_profile.get("saturation_adjust") or 1.0)))
        red = max(-0.12, min(0.12, float(color_profile.get("red_balance") or 0.0)))
        blue = max(-0.12, min(0.12, float(color_profile.get("blue_balance") or 0.0)))
        correction = (
            f"eq=brightness={brightness:.4f}:saturation={saturation:.4f},"
            f"colorbalance=rs={red:.4f}:gs={-red * 0.35:.4f}:bs={-blue:.4f}:"
            f"rm={red:.4f}:gm={-red * 0.35:.4f}:bm={-blue:.4f},"
        )
    else:
        correction = ""
    shot = segment.get("spherical_shot") or {}
    if not shot:
        motion = segment.get("motion") or {}
        if motion.get("type") == "ken_burns":
            try:
                progress = 0.5
                zoom = float(motion.get("zoom_start", 1.0)) + (
                    float(motion.get("zoom_end", 1.0)) - float(motion.get("zoom_start", 1.0))
                ) * progress
                pan_x = float(motion.get("pan_x_start", motion.get("pan_x", 0.5))) + (
                    float(motion.get("pan_x_end", motion.get("pan_x", 0.5))) - float(motion.get("pan_x_start", motion.get("pan_x", 0.5)))
                ) * progress
                pan_y = float(motion.get("pan_y_start", motion.get("pan_y", 0.5))) + (
                    float(motion.get("pan_y_end", motion.get("pan_y", 0.5))) - float(motion.get("pan_y_start", motion.get("pan_y", 0.5)))
                ) * progress
                return (
                    f"{correction}scale=w='ceil(360*{zoom:.6f}/2)*2':h='ceil(202*{zoom:.6f}/2)*2':eval=frame,"
                    f"crop=360:202:x='(iw-360)*{pan_x:.6f}':y='(ih-202)*max({IPHONE_CROP_TOP_LIMIT:.6f}+0.5/{zoom:.6f},{pan_y:.6f})',format=yuvj420p"
                )
            except (TypeError, ValueError):
                pass
        return f"{correction}scale=360:-2:force_original_aspect_ratio=decrease"
    projection = str(segment.get("projection") or "").lower()
    source_path = str(segment.get("source_path") or segment.get("clip_path") or "")
    if projection not in {"equirect", "raw_insv"}:
        projection = "raw_insv" if Path(source_path).suffix.lower() in {".insv", ".insp"} else "equirect"
    if projection not in {"equirect", "raw_insv"}:
        return f"{correction}scale=360:-2:force_original_aspect_ratio=decrease"
    view = view_parameters(
        float(shot.get("yaw") or 0.0),
        float(shot.get("pitch") or 0.0),
        float(shot.get("fov") or 95.0),
        16.0 / 9.0,
        str(shot.get("type") or ""),
        projection_preset=shot.get("projection_preset"),
        roll=float(shot.get("roll") or 0.0),
        projection_control=shot.get("projection_control"),
    )
    if projection == "raw_insv":
        insv_fov = float(segment.get("insv_fov") or 190.0)
        prefix = f"v360=input=dfisheye:output=e:ih_fov={insv_fov:.3f}:iv_fov={insv_fov:.3f}:interp=lanczos,"
    else:
        prefix = ""
    return (
        f"{correction}{prefix}v360=input=equirect:output={view['projection']}:"
        f"yaw={float(view['yaw']):.3f}:pitch={float(view['pitch']):.3f}:"
        f"roll={float(view['roll']):.3f}:"
        f"h_fov={float(view['h_fov']):.3f}:v_fov={float(view['v_fov']):.3f}:"
        "w=360:h=202:interp=lanczos,format=yuvj420p"
    )


def _spherical_pose_suffix(candidate: dict[str, Any]) -> str:
    """Make different 360 framings distinct review candidates."""
    shot = candidate.get("spherical_shot") or {}
    if not isinstance(shot, dict) or not shot:
        return ""
    try:
        yaw = float(shot.get("yaw") or 0.0) % 360.0
        pitch = float(shot.get("pitch") or 0.0)
        fov = float(shot.get("fov") or 95.0)
    except (TypeError, ValueError):
        return ""
    return (
        f"|yaw={yaw:.3f}|pitch={pitch:.3f}|fov={fov:.3f}"
        f"|roll={float(shot.get('roll') or 0.0):.3f}"
        f"|projection={normalize_projection_preset(shot.get('projection_preset'), str(shot.get('type') or ''))}"
        f"|projection_control={float(shot.get('projection_control') or 0.0):.3f}"
    )


def _candidate_key(candidate: dict[str, Any]) -> str:
    """Return a stable identity for a source moment and its 360 framing."""
    path = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path") or ""
    try:
        start = float(candidate.get("clip_start_sec", candidate.get("offset_sec", 0)) or 0.0)
    except (TypeError, ValueError):
        start = 0.0
    return f"{path}|{start:.6f}{_spherical_pose_suffix(candidate)}"


def _candidate_keys(candidate: dict[str, Any]) -> set[str]:
    """Return current and legacy spellings of a candidate identity."""
    path = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path") or ""
    starts = {
        candidate.get("clip_start_sec"),
        candidate.get("offset_sec"),
        candidate.get("clip_offset_sec"),
    }
    keys = set()
    pose_suffix = _spherical_pose_suffix(candidate)
    for raw_start in starts:
        if raw_start is None:
            continue
        try:
            value = float(raw_start)
        except (TypeError, ValueError):
            continue
        keys.update({
            f"{path}|{value}{pose_suffix}",
            f"{path}|{value:.6f}{pose_suffix}",
        })
    return keys or {_candidate_key(candidate)}


def _candidate_has_subject(candidate: dict[str, Any], segment: dict[str, Any]) -> bool:
    """Reject known empty/corner flat frames from the replacement reserve."""
    projection = str(candidate.get("projection") or segment.get("projection") or "").lower()
    if projection in {"equirect", "raw_insv"} or candidate.get("spherical_shot"):
        return True
    profile = candidate.get("reel_framing") or segment.get("reel_framing")
    if not profile:
        return True
    try:
        start = float(candidate.get("clip_start_sec") or 0.0)
        duration = max(0.1, float(segment.get("duration_sec") or 0.1))
        box = subject_box_for_window(profile, start, duration)
    except (TypeError, ValueError):
        return False
    if not box:
        return False
    width = max(0.0, float(box.get("x2") or 0.0) - float(box.get("x1") or 0.0))
    height = max(0.0, float(box.get("y2") or 0.0) - float(box.get("y1") or 0.0))
    area = width * height
    center_x = (float(box.get("x1") or 0.0) + float(box.get("x2") or 0.0)) / 2.0
    center_y = (float(box.get("y1") or 0.0) + float(box.get("y2") or 0.0)) / 2.0
    return area >= 0.01 and 0.08 <= center_x <= 0.92 and 0.10 <= center_y <= 0.90


def _candidate_covers_slot(candidate: dict[str, Any], segment: dict[str, Any], platform: str) -> bool:
    """Return whether a replacement is valid for this exact master-time slot."""
    if platform != "youtube":
        return True
    try:
        confidence = float(candidate.get("confidence") or 0.0)
        threshold = float(candidate.get("threshold") or 6.0)
    except (TypeError, ValueError):
        return False
    if confidence < threshold or (candidate.get("low_confidence") and not candidate.get("manual_override")):
        return False
    # A high-confidence source can still have a disagreement between the
    # independent first/last sync checks.  It remains usable for shot review
    # when it covers the complete slot; rejecting it here made the review
    # pool contradict coverage.json (notably the real iPhone source at the
    # end of In Berlin). Low-confidence sources remain excluded above.
    master_start = float(segment.get("master_start_sec") or 0.0)
    duration = max(0.1, float(segment.get("duration_sec") or 0.1))
    coverage_start = float(candidate.get("offset_sec") or 0.0)
    coverage_end = coverage_start + max(0.0, float(candidate.get("duration_sec") or 0.0))
    return coverage_start <= master_start + 0.001 and coverage_end >= master_start + duration - 0.001


def _spherical_review_poses(segment: dict[str, Any], segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return authored landmark poses plus conservative local alternatives.

    A review reserve must remain near a saved landmark. Large synthetic yaw
    jumps made it easy to replace a valid performer shot with an empty corner
    or the audience while still satisfying the old "20 candidates" count.
    """
    current = dict(segment.get("spherical_shot") or {})
    if not current:
        return []
    poses: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(raw: dict[str, Any]) -> None:
        if not raw:
            return
        try:
            yaw = float(raw.get("yaw") or 0.0) % 360.0
            pose_type = str(raw.get("shot_id") or raw.get("type") or current.get("type") or "")
            pitch = effective_pitch(float(raw.get("pitch") or 0.0), pose_type)
            projection_preset = normalize_projection_preset(raw.get("projection_preset"), pose_type)
            fov = effective_fov(float(raw.get("fov") or 95.0), pose_type, projection_preset)
            roll = effective_roll(float(raw.get("roll") or 0.0), pose_type)
        except (TypeError, ValueError):
            return
        pose = dict(raw)
        pose["yaw"], pose["pitch"], pose["fov"] = round(yaw, 3), round(pitch, 3), round(fov, 3)
        pose["roll"] = round(roll, 3)
        pose["projection_preset"] = projection_preset
        pose["projection_control"] = effective_projection_control(raw.get("projection_control"))
        key = _spherical_pose_suffix({"spherical_shot": pose})
        if key in seen:
            return
        seen.add(key)
        poses.append(pose)

    # Authored poses are always first. They are the source of truth for the
    # landmark labels and therefore must survive even when the edit plan is
    # stale or a proxy path was used by coverage.
    anchors: list[dict[str, Any]] = []
    add(current)
    anchors.append(current)
    reviewed_identity = {
        "clip_path": segment.get("clip_path"),
        "source_path": segment.get("source_path"),
        "proxy_path": segment.get("proxy_path"),
        "camera_id": segment.get("camera_id"),
    }
    for planned in segments:
        planned_shot = dict(planned.get("spherical_shot") or {})
        if not planned_shot:
            continue
        planned_identity = {
            "clip_path": planned.get("clip_path"),
            "source_path": planned.get("source_path"),
            "proxy_path": planned.get("proxy_path"),
            "camera_id": planned.get("camera_id"),
        }
        if _same_source_identity(reviewed_identity, planned_identity):
            anchors.append(planned_shot)
            add(planned_shot)

    # Eight local yaw choices × three small framing choices gives 24 reserve
    # candidates per authored landmark without ever jumping to another side
    # of the panorama. If the project has singer/drummer/stage landmarks,
    # each remains a nearby, semantically useful alternative.
    yaw_offsets = (-40, -30, -20, -10, 10, 20, 30, 40)
    framing = ((-3.0, -8.0), (0.0, 0.0), (3.0, 8.0))
    for anchor_index, anchor in enumerate(anchors):
        base_yaw = float(anchor.get("yaw") or 0.0)
        base_pitch = float(anchor.get("pitch") or 0.0)
        base_fov = float(anchor.get("fov") or 95.0)
        for variant_index, (yaw_delta, (pitch_delta, fov_delta)) in enumerate(
            ((yaw_delta, option) for yaw_delta in yaw_offsets for option in framing)
        ):
            variant = dict(anchor)
            variant.update({
                "type": f"review_local_{anchor_index + 1:02d}_{variant_index + 1:02d}",
                "shot_id": f"review_local_{anchor_index + 1:02d}_{variant_index + 1:02d}",
                "label": "Encuadre alternativo cercano",
                "yaw": (base_yaw + yaw_delta) % 360.0,
                "pitch": base_pitch + pitch_delta,
                "fov": base_fov + fov_delta,
            })
            add(variant)
    return poses

def _source_record_for_slot(
    source_records: list[dict[str, Any]],
    master_start: float,
    duration: float,
) -> dict[str, Any] | None:
    master_end = master_start + max(0.1, duration)
    ranked: list[tuple[int, float, dict[str, Any]]] = []
    for source in source_records:
        try:
            offset = float(source.get("offset_sec") or 0.0)
            source_end = offset + max(0.0, float(source.get("duration_sec") or 0.0))
        except (TypeError, ValueError):
            continue
        covers = offset <= master_start + 0.001 and source_end >= master_end - 0.001
        overlap = max(0.0, min(source_end, master_end) - max(offset, master_start))
        ranked.append((1 if covers else 0, overlap, source))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2]


def _path_aliases(raw: Any) -> set[str]:
    """Return stable path spellings used by coverage and the edit plan."""
    value = str(raw or "").strip()
    if not value:
        return set()
    path = Path(value).expanduser()
    aliases = {value, str(path)}
    try:
        aliases.add(str(path.resolve()))
    except OSError:
        pass
    return aliases


def _same_source_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Match a camera even when coverage uses its proxy path."""
    left_paths = set()
    right_paths = set()
    for key in ("path", "clip_path", "source_path", "proxy_path"):
        left_paths.update(_path_aliases(left.get(key)))
        right_paths.update(_path_aliases(right.get(key)))
    if left_paths & right_paths:
        return True
    left_camera = str(left.get("camera_id") or _camera_id(left) or "").strip()
    right_camera = str(right.get("camera_id") or _camera_id(right) or "").strip()
    return bool(left_camera and right_camera and left_camera == right_camera)


def _review_candidate_pool(
    coverage: dict[str, Any],
    segments: list[dict[str, Any]],
    segment: dict[str, Any],
    platform: str,
) -> tuple[list[dict[str, Any]], str]:
    """Build per-slot alternatives from source moments and 360 poses."""
    sources = list(coverage.get("sources") or [])
    if not sources:
        sources = list(coverage.get("segments") or [])
    by_path: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        path = str(source.get("path") or source.get("clip_path") or source.get("source_path") or "")
        if path:
            by_path.setdefault(path, []).append(source)
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    master_start = float(segment.get("master_start_sec") or 0.0)
    reviewed_paths = {
        str(segment.get(key) or "")
        for key in ("clip_path", "source_path", "proxy_path")
        if segment.get(key)
    }
    spherical_poses = _spherical_review_poses(segment, segments) if segment.get("spherical_shot") else []

    reviewed_identity = {
        "clip_path": segment.get("clip_path"),
        "source_path": segment.get("source_path"),
        "proxy_path": segment.get("proxy_path"),
        "camera_id": segment.get("camera_id"),
    }
    reviewed_aliases = set().union(*(_path_aliases(value) for value in reviewed_paths)) if reviewed_paths else set()
    for path, source_records in by_path.items():
        source_identity_match = any(_same_source_identity(source, reviewed_identity) for source in source_records)
        source_path_match = bool(_path_aliases(path) & reviewed_aliases)
        is_spherical_review_source = platform == "youtube" and bool(spherical_poses) and (source_identity_match or source_path_match)
        for source in source_records:
            # For a spherical slot, never add a pose-less copy of the same
            # timestamp: it would be selected as a fake alternative and keep
            # the old framing.
            if is_spherical_review_source:
                continue
            candidate = dict(source)
            if platform == "youtube":
                candidate["clip_start_sec"] = max(0.0, master_start - float(source.get("offset_sec") or 0.0))
            key = _candidate_key(candidate)
            if key not in seen:
                pool.append(candidate)
                seen.add(key)

        # YouTube 360 keeps exact master-time alignment while offering many
        # alternate camera framings from the same registered equirect source.
        if is_spherical_review_source:
            source = _source_record_for_slot(
                source_records,
                master_start,
                float(segment.get("duration_sec") or 0.1),
            ) or source_records[0]
            for pose_index, pose in enumerate(spherical_poses):
                candidate = dict(source)
                candidate["clip_start_sec"] = max(0.0, master_start - float(source.get("offset_sec") or 0.0))
                candidate["projection"] = segment.get("projection") or candidate.get("projection") or "equirect"
                candidate["spherical_shot"] = dict(pose)
                candidate["review_candidate_index"] = pose_index
                key = _candidate_key(candidate)
                if key not in seen:
                    pool.append(candidate)
                    seen.add(key)

        # A Reel has no sync constraint, so each source can provide several
        # genuinely different moments for the same review slot.
        if platform == "reel":
            source = source_records[0]
            try:
                duration = max(0.0, float(source.get("duration_sec") or 0.0))
            except (TypeError, ValueError):
                duration = 0.0
            segment_duration = max(0.1, float(segment.get("duration_sec") or 0.1))
            max_start = max(0.0, duration - segment_duration)
            for moment_index in range(6):
                fraction = moment_index / 5.0
                candidate = dict(source)
                candidate["clip_start_sec"] = round(max_start * fraction, 6)
                candidate["review_candidate_index"] = moment_index
                key = _candidate_key(candidate)
                if key not in seen:
                    pool.append(candidate)
                    seen.add(key)
    for planned in segments:
        source = next(
            (record for records in by_path.values() for record in records if _same_source_identity(record, planned)),
            None,
        )
        if source is None:
            continue
        candidate = dict(source)
        for key in ("clip_start_sec", "motion", "spherical_shot", "projection", "shot_quality_score", "motion_score"):
            if planned.get(key) is not None:
                candidate[key] = planned[key]
        if not _candidate_covers_slot(candidate, segment, platform):
            continue
        key = _candidate_key(candidate)
        if key not in seen:
            pool.append(candidate)
            seen.add(key)
    return pool, "coverage.sources+edit_plan.moments"


def _render_status_path(root: Path) -> Path:
    return root / "render_status.json"


def _load_render_status(root: Path) -> dict[str, dict[str, Any]]:
    path = _render_status_path(root)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        LOGGER.warning("Could not read review render status %s", path, exc_info=True)
        return {}


def _save_render_status(root: Path, status: dict[str, dict[str, Any]]) -> None:
    path = _render_status_path(root)
    # Review can be opened/polled concurrently from the browser. A fixed
    # sibling temporary name lets one request replace the other request's
    # temporary file before it calls replace().
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=root, prefix="render_status.", suffix=".tmp", delete=False
    ) as handle:
        handle.write(json.dumps(status, ensure_ascii=False))
        temporary = Path(handle.name)
    temporary.replace(path)


def _set_render_status(root: Path, index: int, payload: dict[str, Any]) -> None:
    """Merge one worker's state without losing another worker's update."""
    with _RENDER_STATUS_LOCK:
        status = _load_render_status(root)
        status[str(index)] = payload
        _save_render_status(root, status)


def mark_review_render_failed(project: Project, indices: set[int], message: str) -> None:
    """Persist a background render failure so the review UI can show it."""
    segments = _review_segments(project)
    signature = _review_signature(segments)
    root = project.cache_dir / "shot_review" / signature
    root.mkdir(parents=True, exist_ok=True)
    with _RENDER_STATUS_LOCK:
        status = _load_render_status(root)
        for index in indices:
            status[str(index)] = {"state": "failed", "error": message}
        _save_render_status(root, status)


def _replacement_candidates(
    pool: list[dict[str, Any]],
    segment: dict[str, Any],
    tried: set[str],
    platform: str,
) -> tuple[list[tuple[float, dict[str, Any], str]], dict[str, int]]:
    """Return eligible candidates and counts used in Replace diagnostics.

    Keeping this calculation synchronous and side-effect free is important:
    Replace must be able to answer even while the thumbnail worker is busy.
    """
    counts = {"pool": len(pool), "with_path": 0, "covers_slot": 0, "unused": 0}
    candidates: list[tuple[float, dict[str, Any], str]] = []
    for candidate in pool:
        path = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path")
        if not path:
            continue
        counts["with_path"] += 1
        if not _candidate_covers_slot(candidate, segment, platform):
            continue
        counts["covers_slot"] += 1
        if not _candidate_has_subject(candidate, segment):
            continue
        if _candidate_keys(candidate) & tried:
            continue
        counts["unused"] += 1
        candidates.append((float(candidate.get("shot_quality_score") or candidate.get("motion_score") or 0.0), candidate, _candidate_key(candidate)))
    return candidates, counts


def review_items(
    project: Project,
    *,
    render_missing: bool = True,
    render_indices: set[int] | None = None,
    apply_color_profile: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    """Return review items, optionally rendering only selected missing thumbnails."""
    segments = _review_segments(project)
    signature = _review_signature(segments)
    root = project.cache_dir / "shot_review" / signature
    root.mkdir(parents=True, exist_ok=True)
    render_status = _load_render_status(root)
    unavailable = {int(value) for value in project.data.get("settings", {}).get("wizard", {}).get("review_unavailable", [])}
    try:
        coverage = load_coverage(project)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        coverage = {}
    wizard = project.data.get("settings", {}).get("wizard", {}) or {}
    platform = str(coverage.get("platform") or wizard.get("platform") or "generic")
    items: list[dict[str, Any]] = []
    ffmpeg = locate_executable("ffmpeg") or "ffmpeg"
    # Keep the default review path cheap: color profiling belongs to final
    # export and is optional for a thumbnail. A status-only request must not
    # launch/inspect the complete plan just to report missing URLs.
    if render_missing and apply_color_profile:
        from core.stages.export import _color_profiles_for_segments
        color_profiles = _color_profiles_for_segments(project, segments, [])
    else:
        color_profiles = {}
    for index, segment in enumerate(segments):
        if progress_callback:
            progress_callback(int(100 * index / max(1, len(segments))),
                              f"Preparing review thumbnail {index + 1}/{len(segments)}")
        source = _source_for(segment)
        duration = max(0.1, float(segment.get("duration_sec") or 0.1))
        clip_start = float(segment.get("clip_start_sec") or 0.0)
        timestamp = clip_start + duration / 2.0
        source_stat = Path(source).stat() if Path(source).exists() else None
        shot = segment.get("spherical_shot") or {}
        pose = json.dumps(_review_pose_for_cache(segment), sort_keys=True)
        key = hashlib.sha256(f"{source}|{source_stat.st_mtime_ns if source_stat else 0}|{timestamp:.4f}|{pose}".encode()).hexdigest()[:20]
        output = root / f"shot-{index:04d}-{key}.jpg"
        should_render = render_missing and (render_indices is None or index in render_indices)
        thumbnail_error = None
        thumbnail_state = "ready" if output.exists() else "missing"
        render_source = source
        render_segment = dict(segment)
        if should_render and segment.get("spherical_shot"):
            render_source = _spherical_analysis_source(project, segment, progress_callback)
            if render_source != source:
                # The cached analysis source is equirectangular even when the
                # original was raw INSV; never apply the dfisheye step twice.
                render_segment["source_path"] = render_source
                render_segment["clip_path"] = render_source
                render_segment["projection"] = "equirect"
        if not output.exists() and source and should_render:
            _set_render_status(root, index, {"state": "rendering"})
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{timestamp:.3f}",
                "-i", render_source, "-frames:v", "1", "-threads", "1",
                "-vf", _thumbnail_filter(render_segment, color_profiles.get(str(segment.get("clip_path")), {})),
                "-q:v", "5", "-y", str(output),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
                if not output.exists():
                    raise ThumbnailRenderError("FFmpeg completed without producing a JPEG")
                _set_render_status(root, index, {"state": "ready"})
                thumbnail_state = "ready"
            except (OSError, subprocess.CalledProcessError, ThumbnailRenderError) as exc:
                output.unlink(missing_ok=True)
                detail = getattr(exc, "stderr", None) or str(exc)
                thumbnail_error = detail.strip() or "FFmpeg failed to render the thumbnail"
                _set_render_status(root, index, {"state": "failed", "error": thumbnail_error})
                if isinstance(exc, ThumbnailRenderError):
                    raise
                raise ThumbnailRenderError(thumbnail_error) from exc
        elif not output.exists():
            saved = render_status.get(str(index)) or {}
            thumbnail_state = str(saved.get("state") or "missing")
            thumbnail_error = saved.get("error")
        shot = segment.get("spherical_shot") or {}
        # Render from the proxy where appropriate, but identify the card by
        # the registered source filename.  Otherwise Review shots displays a
        # cache hash (and hides which camera supplied the shot).
        display_source = str(segment.get("filename") or Path(source).name or "Unknown source")
        pool, pool_origin = _review_candidate_pool(coverage, segments, segment, platform)
        current_keys = _candidate_keys(segment)
        candidate_count = sum(
            1
            for candidate in pool
            if _candidate_covers_slot(candidate, segment, platform)
            and _candidate_has_subject(candidate, segment)
            and not (_candidate_keys(candidate) & current_keys)
        )
        shot_pose = {
            key: shot.get(key)
            for key in ("type", "shot_id", "yaw", "pitch", "fov")
            if shot and shot.get(key) is not None
        }
        items.append({
            "index": index,
            "thumbnail": f"/api/v1/wizard/review/thumbnail/{signature}/{output.name}" if output.name and output.exists() else None,
            "thumbnail_status": thumbnail_state,
            "thumbnail_error": thumbnail_error,
            "source": display_source,
            "camera_id": segment.get("camera_id") or _camera_id(segment),
            "duration_sec": round(duration, 3),
            "master_start_sec": float(segment.get("master_start_sec") or 0.0),
            "landmark": shot.get("label") if shot else None,
            "pose": shot_pose,
            "candidate_count": candidate_count,
            "candidate_pool_origin": pool_origin,
            "transition_type": str(segment.get("transition_type") or "auto"),
            "subject_safe": _candidate_has_subject(segment, segment),
            "keep": True,
            "no_alternative": index in unavailable,
        })
    if progress_callback:
        progress_callback(100, f"Review thumbnails ready: {len(items)}/{len(segments)}")
    return items


def render_review_thumbnails(project: Project, indices: set[int], max_workers: int = 2) -> None:
    """Render selected review thumbnails in parallel without blocking HTTP."""
    selected = sorted({int(index) for index in indices})
    if not selected:
        return
    workers = max(1, min(int(max_workers), len(selected)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="review-thumb") as pool:
        futures = {
            pool.submit(
                review_items,
                project,
                render_indices={index},
                apply_color_profile=False,
            ): index
            for index in selected
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                future.result()
            except Exception as exc:
                LOGGER.exception("Review thumbnail render failed for index %s", index)
                mark_review_render_failed(project, {index}, str(exc))


def set_review_transition_types(project: Project, transitions: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    """Persist the outgoing transition selected on each review card."""
    plan = _plan(project)
    segments = list(plan.get("segments") or [])
    values = transitions if isinstance(transitions, dict) else {str(i): value for i, value in enumerate(transitions or [])}
    from core.stages.export import TRANSITION_LIBRARY
    allowed = {"auto", "none", *TRANSITION_LIBRARY.keys()}
    for index, segment in enumerate(segments):
        raw = values.get(str(index), values.get(index)) if isinstance(values, dict) else None
        if raw is None:
            continue
        value = str(raw or "auto").strip().lower()
        segment["transition_type"] = value if value in allowed else "auto"
    plan["segments"] = segments
    artifact_path(project, "edit_plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    project.mark_all_stale_from("export")
    project.save()
    return plan


def replace_slots(project: Project, rejected: list[int]) -> dict[str, Any]:
    """Replace rejected slots with unused source/moment candidates."""
    plan = _plan(project)
    segments = plan.get("segments") or []
    coverage = load_coverage(project)
    wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
    platform = str(coverage.get("platform") or wizard.get("platform") or plan.get("platform") or "generic")
    attempts = wizard.setdefault("review_attempts", {})
    # Unlike the old single-attempt behaviour, this is a permanent per-slot
    # record of every candidate that has been displayed to the reviewer.
    exclusions = wizard.setdefault("review_exclusions", {})
    unavailable = set(int(value) for value in wizard.setdefault("review_unavailable", []))
    replaced: list[int] = []
    diagnostics: list[dict[str, Any]] = []
    for raw_index in rejected:
        index = int(raw_index)
        if index < 0 or index >= len(segments):
            continue
        segment = segments[index]
        slot = str(index)
        tried = set(exclusions.setdefault(slot, []))
        # JSON object keys are strings after a reopen. Read both the current
        # in-memory integer form and the persisted string form.
        slot_key = str(slot)
        tried.update(attempts.get(slot_key) or attempts.get(slot) or [])
        tried.update(exclusions.get(slot_key) or exclusions.get(slot) or [])
        tried.update(_candidate_keys(segment))
        pool, pool_origin = _review_candidate_pool(coverage, segments, segment, platform)
        candidates, counts = _replacement_candidates(pool, segment, tried, platform)
        if not candidates:
            unavailable.add(index)
            reason = "pool_empty" if counts["pool"] == 0 else "no_unused_candidate_covers_slot"
            LOGGER.info(
                "Review replace slot=%s unavailable current=%s pool_origin=%s counts=%s",
                index, _candidate_key(segment), pool_origin, counts,
            )
            diagnostics.append({
                "index": index, "status": "unavailable", "current": _candidate_key(segment),
                "reason": reason, "pool_origin": pool_origin, **counts,
            })
            continue
        ordered_candidates = sorted(candidates, key=lambda item: (-item[0], item[2]))
        # Rotate deterministic alternatives instead of returning the same
        # highest-scoring frame on every click.
        variation_seed = str(wizard.get("variation_seed") or "")
        rotation_digest = hashlib.sha256(
            f"{variation_seed}|{slot}|{len(tried)}".encode("utf-8")
        ).hexdigest()
        choice_index = int(rotation_digest[:12], 16) % len(ordered_candidates)
        _score, candidate, key = ordered_candidates[choice_index]
        LOGGER.info(
            "Review replace slot=%s current=%s candidate=%s source=%s clip_start=%s master_start=%s",
            index,
            _candidate_key(segment),
            key,
            candidate.get("filename") or candidate.get("path"),
            candidate.get("clip_start_sec"),
            segment.get("master_start_sec"),
        )
        diagnostics.append({
            "index": index,
            "status": "replaced",
            "current": _candidate_key(segment),
            "candidate": key,
            "source": candidate.get("filename") or candidate.get("path"),
            **counts,
        })
        new_segment = dict(segment)
        new_segment["clip_path"] = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path")
        new_segment["source_path"] = candidate.get("source_path") or new_segment["clip_path"]
        new_segment["filename"] = candidate.get("filename") or Path(str(new_segment["clip_path"])).name
        new_segment["camera_id"] = _camera_id({
            "source_path": new_segment["source_path"],
            "filename": new_segment["filename"],
            "projection": new_segment.get("projection") or candidate.get("projection"),
        })
        if candidate.get("motion"):
            new_segment["motion"] = candidate["motion"]
        if platform == "youtube":
            # The candidate's offset is its position on the master timeline;
            # convert the reviewed slot back into that camera's own timeline.
            candidate_offset = float(candidate.get("offset_sec") or 0.0)
            new_segment["clip_offset_sec"] = candidate_offset
            new_segment["clip_start_sec"] = max(
                0.0,
                float(segment.get("master_start_sec") or 0.0) - candidate_offset,
            )
        else:
            new_segment["clip_start_sec"] = float(candidate.get("clip_start_sec") or 0.0)
        if candidate.get("projection"):
            new_segment["projection"] = candidate["projection"]
        if candidate.get("spherical_shot"):
            new_segment["spherical_shot"] = candidate["spherical_shot"]
        tried.add(key)
        exclusions[slot_key] = sorted(tried)
        exclusions.pop(index, None)
        # Keep the legacy field populated for readers of older project data.
        attempts[slot_key] = sorted(tried)
        attempts.pop(index, None)
        segments[index] = new_segment
        unavailable.discard(index)
        replaced.append(index)
    plan["segments"] = segments
    artifact_path(project, "edit_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    project.data["settings"]["wizard"]["review_attempts"] = attempts
    project.data["settings"]["wizard"]["review_exclusions"] = exclusions
    project.data["settings"]["wizard"]["review_unavailable"] = sorted(unavailable)
    project.save()
    return {
        "replaced": replaced,
        "unavailable": sorted(unavailable),
        "replacement_diagnostics": diagnostics,
        # This is deliberately metadata-only. Thumbnail generation happens
        # after the HTTP response in server.api; Replace never waits on it.
        "items": review_items(project, render_missing=False),
    }
