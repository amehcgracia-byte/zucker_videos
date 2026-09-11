"""Camera-operator avoidance: detect a large back-facing person (the camera
operator) in non-Sony sources and propose yaw/crop adjustments to exclude them.

Detection runs once per clip during ingest analysis, sampling the analysis
proxy at ~2fps with OpenCV DNN + MobileNet-SSD, and caches the per-timestamp
results globally under ``~/ZuckerVideos/Cache/operator/{cache_key}.json`` —
the same cache_key scheme used for proxies/color profiles elsewhere. Edit-time
lookups only ever read this cache; they never run detection inline.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from core.normalization import cache_key_for_source, global_cache_root
from core.stages.base import stable_fingerprint

LOGGER = logging.getLogger(__name__)

CACHE_VERSION = 4
OPERATOR_AVOIDANCE_VERSION = 4

# Sample the clip at ~2fps: dense enough to catch the operator stepping into
# frame, cheap enough not to meaningfully slow ingest.
DETECTION_FPS = 4.0

# Minimum fraction of frame area a person-blob must occupy to be considered
# a foreground camera-operator figure worth avoiding.
OPERATOR_AREA_THRESHOLD = 0.08

# Minimum fraction of frame area a secondary person-blob must occupy to be
# treated as a real subject of interest (the singer/band) rather than noise.
SUBJECT_AREA_THRESHOLD = 0.01

# Maximum yaw shift we will apply to a 360 segment to avoid the operator.
MAX_360_YAW_SHIFT_DEG = 20.0

# iPhone/fixed crop defaults when the operator is detected but no distinct
# subject is found: roughly 50% zoom-in, biased toward the right side of the
# frame, and vertically centered (not pushed toward the top).
IPHONE_OPERATOR_ZOOM = 1.5
IPHONE_AVOIDANCE_CX = 0.65
IPHONE_AVOIDANCE_CY = 0.5

PERSON_CLASS = 15  # COCO class index for 'person' in MobileNet-SSD

_net_singleton: Any = None  # None = not yet attempted, False = load failed, else the cv2.dnn.Net


def _model_paths() -> tuple[Path, Path]:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    model_dir = base / "assets" / "models"
    return model_dir / "MobileNetSSD_deploy.prototxt", model_dir / "MobileNetSSD_deploy.caffemodel"


def _detector() -> Any | None:
    """Lazily load the MobileNet-SSD detector, or return None if unavailable."""
    global _net_singleton
    if _net_singleton is False:
        return None
    if _net_singleton is None:
        try:
            import cv2

            proto, model = _model_paths()
            if not proto.exists() or not model.exists():
                LOGGER.warning("Operator-avoidance model files missing at %s; detection disabled", proto.parent)
                _net_singleton = False
                return None
            _net_singleton = cv2.dnn.readNetFromCaffe(str(proto), str(model))
        except Exception:
            LOGGER.warning("Operator-avoidance model failed to load; detection disabled", exc_info=True)
            _net_singleton = False
            return None
    return _net_singleton


def _cache_key_for_path(path: str) -> str:
    resolved = Path(path)
    try:
        if resolved.parent == global_cache_root() / "proxies":
            return resolved.stem
    except OSError:
        pass
    try:
        return cache_key_for_source(path)
    except OSError:
        return stable_fingerprint({"path": str(resolved)})[:24]


def _cache_path_for(path: str) -> Path:
    return global_cache_root() / "operator" / f"{_cache_key_for_path(path)}.json"


def load_cached_operator_presence(analysis_path: str) -> list[dict[str, float]]:
    """Read-only lookup of cached per-timestamp operator presence for a clip.

    Used at edit time; never triggers detection. Returns ``[]`` when nothing
    is cached yet, the cache is stale, or the file can't be read.
    """
    cache_path = _cache_path_for(analysis_path)
    if not cache_path.exists():
        return []
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("cache_version") != CACHE_VERSION:
        return []
    samples = payload.get("samples")
    return samples if isinstance(samples, list) else []


def analyze_and_cache_operator_presence(
    analysis_path: str,
    progress_callback: Any = None,
) -> list[dict[str, float]]:
    """Detect operator presence across an entire clip at ~2fps and cache it globally.

    Called during ingest analysis. Returns the (possibly newly computed) time
    series of ``{"t": seconds, "area_fraction": float, "cx": float, "cy": float}``
    samples — one per sampled frame that contained a person, restricted to the
    single dominant (largest) detection per frame.
    """
    cache_path = _cache_path_for(analysis_path)
    if cache_path.exists():
        cached = load_cached_operator_presence(analysis_path)
        if cached or _cache_marks_no_detections(cache_path):
            return cached
    samples = _detect_clip_presence(analysis_path, progress_callback)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"cache_version": CACHE_VERSION, "samples": samples}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return samples


def _cache_marks_no_detections(cache_path: Path) -> bool:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("cache_version") == CACHE_VERSION and payload.get("samples") == []


def _detect_clip_presence(path: str, progress_callback: Any = None) -> list[dict[str, float]]:
    net = _detector()
    if net is None:
        return []
    try:
        import cv2
    except ImportError:
        return []
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return []
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        frame_total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, round(fps / DETECTION_FPS))
        samples: list[dict[str, float]] = []
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % step == 0:
                blobs = _detect_frame(net, frame)
                if blobs:
                    ranked = sorted(blobs, key=lambda blob: blob["area_fraction"], reverse=True)
                    dominant = ranked[0]
                    sample = {
                        "t": round(index / fps, 2),
                        "area_fraction": round(dominant["area_fraction"], 4),
                        "cx": round(dominant["cx"], 4),
                        "cy": round(dominant["cy"], 4),
                    }
                    subject = _secondary_subject(ranked, dominant)
                    if subject:
                        sample["subject_area_fraction"] = round(subject["area_fraction"], 4)
                        sample["subject_cx"] = round(subject["cx"], 4)
                        sample["subject_cy"] = round(subject["cy"], 4)
                    samples.append(sample)
                if progress_callback and frame_total:
                    progress_callback(min(99, int(index / frame_total * 100)), "Scanning for camera operator")
            index += 1
        return samples
    finally:
        capture.release()


def _detect_frame(net: Any, frame: Any) -> list[dict[str, float]]:
    import cv2

    height, width = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections = net.forward()
    blobs: list[dict[str, float]] = []
    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])
        class_id = int(detections[0, 0, i, 1])
        # Dark live-stage footage produces weak but useful person boxes.
        # Area/overlap filtering below still rejects detector noise.
        if class_id != PERSON_CLASS or confidence <= 0.30:
            continue
        x1 = float(detections[0, 0, i, 3]) * width
        y1 = float(detections[0, 0, i, 4]) * height
        x2 = float(detections[0, 0, i, 5]) * width
        y2 = float(detections[0, 0, i, 6]) * height
        area_fraction = max(0.0, (x2 - x1) * (y2 - y1)) / max(1, width * height)
        blobs.append(
            {
                "area_fraction": area_fraction,
                "cx": (x1 + x2) / 2 / width,
                "cy": (y1 + y2) / 2 / height,
                "confidence": confidence,
            }
        )
    return blobs


def _secondary_subject(ranked_blobs: list[dict[str, float]], dominant: dict[str, float]) -> dict[str, float] | None:
    """Pick the largest detected person that is not the operator, if any.

    ``ranked_blobs`` is sorted largest-first; ``dominant`` (assumed to be the
    operator, since they're typically closest to the lens) is skipped along
    with anything close enough to its centre to be the same detection.
    """
    for blob in ranked_blobs[1:]:
        if blob["area_fraction"] < SUBJECT_AREA_THRESHOLD:
            break
        # MobileNet frequently emits two overlapping boxes for the same
        # foreground person (especially a dark back-facing operator).  The
        # old 8% centre gate let those duplicate boxes through as a fake
        # "subject", causing the iPhone crop to lock back onto the operator.
        if abs(blob["cx"] - dominant["cx"]) < 0.18 and abs(blob["cy"] - dominant["cy"]) < 0.18:
            continue
        return blob
    return None


# ---------------------------------------------------------------------------
# Per-segment lookup + adjustment builders
# ---------------------------------------------------------------------------


def role_for_record(projection: str | None, filename: str, metadata: dict[str, Any] | None = None) -> str:
    """Classify a source into 360 / fixed_rear / handheld from cheap metadata.

    Mirrors ``core.stages.edit._source_role``; kept independent here so ingest
    doesn't need to import the edit stage.
    """
    projection_l = str(projection or "").lower()
    filename_l = str(filename or "").lower()
    metadata = metadata or {}
    # Projection is authoritative. A 360 camera can be mounted statically or
    # be described as a mobile camera by the uploader; neither may demote it
    # out of the spherical candidate pool.
    if projection_l in {"equirect", "raw_insv"} or filename_l.endswith(".insv") or "360" in filename_l:
        return "360"
    explicit_role = str(metadata.get("camera_role") or "").strip().lower()
    if explicit_role in {"360", "fixed_rear", "handheld"}:
        return explicit_role
    if metadata.get("is_static_camera") is True or metadata.get("static_camera") is True:
        return "fixed_rear"
    if str(metadata.get("camera_type") or metadata.get("device_type") or "").lower() in {"iphone", "phone", "mobile", "smartphone", "static"}:
        return "fixed_rear"
    if any(marker in filename_l for marker in ("iphone", "phone", "mobile", "pixel", "samsung", "galaxy", "android")) or filename_l.endswith(".mov"):
        return "fixed_rear"
    return "handheld"


def avoidance_for_segment(
    role: str,
    samples: list[dict[str, float]],
    clip_start_sec: float,
    duration_sec: float,
    current_yaw: float | None = None,
) -> dict[str, Any] | None:
    """Return an avoidance adjustment for one segment's clip-time window, or None.

    Looks up cached per-timestamp detections overlapping
    ``[clip_start_sec, clip_start_sec + duration_sec]`` and, if the operator
    is prominent anywhere in that window, proposes a correction:

    - For 360: ``{"type": "yaw_shift", "yaw_deg": <float>}``
    - For fixed_rear: ``{"type": "zoom_crop", "zoom": <float>, "cx": 0.5, "cy": 0.42}``

    Sony (``"handheld"``) is always skipped — it IS the operator's own camera.
    """
    if role == "handheld" or not samples:
        return None
    window = [
        sample
        for sample in samples
        if clip_start_sec - 0.5 <= float(sample.get("t") or 0.0) <= clip_start_sec + duration_sec + 0.5
    ]
    if not window:
        return None
    dominant = max(window, key=lambda sample: float(sample.get("area_fraction") or 0.0))
    if float(dominant.get("area_fraction") or 0.0) < OPERATOR_AREA_THRESHOLD:
        return None
    LOGGER.info(
        "Operator detected at clip t=%.2fs — area=%.0f%% of frame",
        dominant.get("t") or 0.0,
        float(dominant.get("area_fraction") or 0.0) * 100,
    )
    if role == "360":
        return _avoidance_360(dominant, current_yaw or 0.0)
    if role == "fixed_rear":
        subject_candidates = [s for s in window if float(s.get("subject_area_fraction") or 0.0) >= SUBJECT_AREA_THRESHOLD]
        subject = max(subject_candidates, key=lambda s: float(s.get("subject_area_fraction") or 0.0)) if subject_candidates else None
        return _avoidance_iphone(dominant, subject)
    return None


def _avoidance_360(blob: dict[str, Any], current_yaw: float) -> dict[str, Any] | None:
    """Propose a yaw shift that moves the operator out of the shot centre.

    The operator blob centre (cx in 0-1 flat frame space) maps to a yaw offset.
    We shift the camera away from the operator by up to MAX_360_YAW_SHIFT_DEG.
    """
    cx = float(blob.get("cx", 0.5))
    # Positive cx (right half) → operator is to the right → shift left (negative yaw)
    shift = -(cx - 0.5) * 2.0 * MAX_360_YAW_SHIFT_DEG
    shift = max(-MAX_360_YAW_SHIFT_DEG, min(MAX_360_YAW_SHIFT_DEG, shift))
    if abs(shift) < 2.0:
        return None
    return {"type": "yaw_shift", "yaw_deg": round(shift, 1)}


def _avoidance_iphone(blob: dict[str, Any], subject: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Propose a zoom-to-singer crop that pushes the operator off-frame.

    When a distinct subject (the singer, or whoever isn't the operator) was
    detected in the window, crop toward them directly. Otherwise fall back to
    a right-of-centre, vertically-centred default: the operator in these clips
    is consistently framed toward the left/bottom, so biasing right keeps the
    band in frame without needing a precise subject fix.
    """
    area = float(blob.get("area_fraction", 0.0))
    zoom = IPHONE_OPERATOR_ZOOM + min(0.1, area * 0.5)
    if subject is not None:
        cx = _centered_pan(float(subject.get("subject_cx", 0.5)), zoom)
        cy = _centered_pan(float(subject.get("subject_cy", 0.5)), zoom)
        cx = max(0.15, min(0.85, cx))
        cy = max(0.25, min(0.75, cy))
    else:
        cx, cy = IPHONE_AVOIDANCE_CX, IPHONE_AVOIDANCE_CY
    return {"type": "zoom_crop", "zoom": round(zoom, 2), "cx": round(cx, 3), "cy": round(cy, 3)}


def _centered_pan(source_fraction: float, zoom: float) -> float:
    """Convert a point's fractional position in the *source* frame into the
    ``pan`` value ``_zoom_crop_filter`` needs so that point lands at the
    centre of the zoomed-and-cropped output.

    ``_zoom_crop_filter`` scales the frame by ``zoom`` then crops a window at
    ``x = (scaled_width - width) * pan``. For a source point at fraction ``p``
    to end up centred in that window: ``pan = (zoom*p - 0.5) / (zoom - 1)``
    (the target ``width`` cancels out, so this only needs ``zoom``).
    """
    if zoom <= 1.0:
        return 0.5
    return (zoom * source_fraction - 0.5) / (zoom - 1.0)


# ---------------------------------------------------------------------------
# Per-export reporting
# ---------------------------------------------------------------------------


def count_avoidance_adjustments(segments: list[dict[str, Any]]) -> int:
    """Count how many segments received an operator-avoidance adjustment."""
    return sum(1 for s in segments if s.get("operator_avoidance"))
