"""Lightweight subject framing analysis used only by the Reel pipeline.

The analysis is deliberately separate from operator avoidance and from all
YouTube/360/Backstage stages.  It stores a small, versioned trajectory per
source so Edit can choose a safe crop without opening a video or running a
detector for every cut.
"""

from __future__ import annotations

from core.stages.base import ProgressDetail

import json
import logging
import sys
from pathlib import Path
from typing import Any

from core.normalization import cache_key_for_source, global_cache_root
from core.operator_avoidance import _detect_frame, _detector, load_cached_operator_presence, role_for_record
from core.stages.base import stable_fingerprint

LOGGER = logging.getLogger(__name__)

REEL_FRAMING_VERSION = 1
REEL_FRAMING_FPS = 1.5
# Reel cuts currently draw from the opening material of each source. Bound
# the analysis window so a 10-minute camera file does not add many minutes to
# every Reel export; beyond this window the solver uses its safe fallback.
REEL_ANALYSIS_MAX_SEC = 45.0
REEL_FRAMING_WIDTH = 640
MIN_PERSON_CONFIDENCE = 0.28
MIN_FACE_SIZE = 12


def _cache_path(source_path: str, camera_id: str) -> Path:
    try:
        source_key = cache_key_for_source(source_path)
    except OSError:
        source_key = stable_fingerprint({"path": source_path})[:24]
    key = stable_fingerprint({
        "camera_id": camera_id,
        "source_key": source_key,
        "version": REEL_FRAMING_VERSION,
    })[:32]
    return global_cache_root() / "reel_framing" / f"{key}.json"


def _camera_id(record: dict[str, Any], role: str) -> str:
    return str(
        record.get("camera_id")
        or record.get("camera_name")
        or record.get("camera_label")
        or f"{role}:{Path(str(record.get('path') or '')).stem}"
    ).strip().lower()


def load_cached_reel_framing(source_path: str, camera_id: str) -> dict[str, Any] | None:
    path = _cache_path(source_path, camera_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != REEL_FRAMING_VERSION:
        return None
    return payload


def analyze_reel_framing_record(
    record: dict[str, Any],
    progress_callback: Any = None,
) -> dict[str, Any] | None:
    """Analyze one Reel source, reusing a profile when its fingerprint matches."""
    source_path = str((record.get("normalized") or {}).get("path") or record.get("path") or "")
    if not source_path or not Path(source_path).exists():
        return None
    role = role_for_record(
        str((record.get("probe") or {}).get("projection") or record.get("projection") or ""),
        Path(str(record.get("path") or source_path)).name,
    )
    camera_id = _camera_id(record, role)
    cached = load_cached_reel_framing(source_path, camera_id)
    if cached is not None:
        return cached
    profile = _analyze_video(source_path, role, record, progress_callback)
    if profile is None:
        return None
    profile.update({"version": REEL_FRAMING_VERSION, "camera_id": camera_id, "source_path": source_path})
    path = _cache_path(source_path, camera_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return profile


def analyze_reel_framing_records(records: list[dict[str, Any]], progress_callback: Any = None) -> None:
    """Populate Reel-only framing profiles on ingest records."""
    for index, record in enumerate(records):
        try:
            projection = str((record.get("probe") or {}).get("projection") or record.get("projection") or "")
            role = role_for_record(projection, Path(str(record.get("path") or "")).name)
            if role == "360":
                # Phase 2 owns 360 rectilinear-view analysis. Never spend
                # Reel Phase 1 time or create a Phase 1 profile for it.
                continue
            profile = analyze_reel_framing_record(
                record,
                lambda percent, message: progress_callback(
                    96 + int((index + percent / 100.0) / max(1, len(records)) * 4),
                    ProgressDetail(message, task_id=f"framing-{index}",
                                   label=f"Analysing subjects in {Path(str(record.get('path') or 'clip')).name}", percent=percent),
                ) if progress_callback else None,
            )
            if profile is not None:
                record["reel_framing"] = profile
        except Exception:
            # Framing is a quality improvement, never a reason to make a Reel
            # impossible to export. Edit will use a centered full-frame fallback.
            LOGGER.warning("Reel framing analysis failed for %s", record.get("path"), exc_info=True)


def _box(x1: float, y1: float, x2: float, y2: float, confidence: float, kind: str) -> dict[str, Any]:
    return {
        "x1": max(0.0, min(1.0, x1)), "y1": max(0.0, min(1.0, y1)),
        "x2": max(0.0, min(1.0, x2)), "y2": max(0.0, min(1.0, y2)),
        "confidence": round(float(confidence), 3), "kind": kind,
    }


def _analyze_video(path: str, role: str, record: dict[str, Any], progress_callback: Any = None) -> dict[str, Any] | None:
    try:
        import cv2
    except ImportError:
        return None
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return None
    net = _detector()
    face_cascade = None
    try:
        cascade_path = str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_alt2.xml")
        face_cascade = cv2.CascadeClassifier(cascade_path)
    except Exception:
        face_cascade = None
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0) or 30.0
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = total / fps if total else 0.0
        analysis_duration = min(duration, REEL_ANALYSIS_MAX_SEC) if duration else 0.0
        sample_count = max(1, int(analysis_duration * REEL_FRAMING_FPS)) if analysis_duration else 1
        samples: list[dict[str, Any]] = []
        previous_gray = None
        previous_boxes: list[dict[str, Any]] = []
        existing_subject_samples = load_cached_operator_presence(path) if role == "fixed_rear" else []
        frame_index = 0
        sample_index = 0
        next_sample_frame = 0.0
        while sample_index < sample_count:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index + 0.5 < next_sample_frame:
                frame_index += 1
                continue
            sample_time = sample_index / REEL_FRAMING_FPS
            source_h, source_w = frame.shape[:2]
            scale = min(1.0, REEL_FRAMING_WIDTH / max(1, source_w))
            small = cv2.resize(frame, (max(2, int(source_w * scale)), max(2, int(source_h * scale))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            boxes: list[dict[str, Any]] = []
            # Person DNN inference is the expensive part. Run it on every
            # other sample and use optical-flow propagation between those
            # anchors; the stored trajectory still remains 1.5 samples/sec.
            if net is not None and sample_index % 2 == 0:
                for detection in _detect_frame_boxes(net, small):
                    if detection["confidence"] >= MIN_PERSON_CONFIDENCE:
                        boxes.append(_box(*detection["coords"], detection["confidence"], "person"))
            if face_cascade is not None:
                try:
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(MIN_FACE_SIZE, MIN_FACE_SIZE))
                    for x, y, w, h in faces:
                        boxes.append(_box(x / small.shape[1], y / small.shape[0], (x + w) / small.shape[1], (y + h) / small.shape[0], 0.45, "face"))
                except Exception:
                    pass
            motion = 0.0
            if previous_gray is not None:
                try:
                    flow = cv2.calcOpticalFlowFarneback(previous_gray, gray, None, 0.5, 2, 15, 2, 5, 1.2, 0)
                    motion = float((flow[..., 0] ** 2 + flow[..., 1] ** 2).mean() ** 0.5) / max(1.0, small.shape[1])
                except Exception:
                    motion = 0.0
            if not boxes and previous_boxes:
                # Keep the last reliable subject through a detector miss, but
                # mark it as propagated so the solver can apply a short decay.
                dx, dy = _median_flow_delta(previous_gray, gray, cv2) if previous_gray is not None else (0.0, 0.0)
                boxes = [_shift_box(item, dx / max(1, small.shape[1]), dy / max(1, small.shape[0]), 0.92) for item in previous_boxes]
            if role == "fixed_rear":
                # Preserve the existing iPhone operator-avoidance detector's
                # secondary subject whenever it has a stronger identity than
                # a raw person detection.
                for item in existing_subject_samples:
                    if abs(float(item.get("t") or 0.0) - sample_time) <= max(0.35, 1.0 / REEL_FRAMING_FPS):
                        if item.get("subject_cx") is not None and item.get("subject_cy") is not None:
                            cx, cy = float(item["subject_cx"]), float(item["subject_cy"])
                            area = max(0.025, float(item.get("subject_area_fraction") or 0.04))
                            half_w = min(0.25, area ** 0.5 * 0.8)
                            half_h = min(0.35, half_w * 1.4)
                            boxes.append(_box(cx - half_w, cy - half_h, cx + half_w, cy + half_h, 0.65, "existing_subject"))
                        break
            previous_gray, previous_boxes = gray, boxes
            samples.append({"t": round(sample_time, 3), "boxes": boxes, "motion": round(motion, 6), "detected": bool(boxes)})
            if progress_callback and total:
                progress_callback(min(95, int(sample_index / max(1, sample_count) * 95)), f"Framing {Path(path).name}")
            frame_index += 1
            sample_index += 1
            next_sample_frame += fps / REEL_FRAMING_FPS
        return {
            "duration_sec": round(duration, 3),
            "analysis_end_sec": round(analysis_duration, 3),
            "sample_rate": REEL_FRAMING_FPS,
            "role": role,
            "samples": samples,
        }
    finally:
        capture.release()


def _detect_frame_boxes(net: Any, frame: Any) -> list[dict[str, Any]]:
    import cv2
    height, width = frame.shape[:2]
    blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
    net.setInput(blob)
    detections = net.forward()
    result = []
    for i in range(detections.shape[2]):
        confidence = float(detections[0, 0, i, 2])
        if int(detections[0, 0, i, 1]) != 15 or confidence < MIN_PERSON_CONFIDENCE:
            continue
        result.append({"confidence": confidence, "coords": (
            float(detections[0, 0, i, 3]), float(detections[0, 0, i, 4]),
            float(detections[0, 0, i, 5]), float(detections[0, 0, i, 6]),
        )})
    return result


def _median_flow_delta(previous: Any, current: Any, cv2: Any) -> tuple[float, float]:
    try:
        flow = cv2.calcOpticalFlowFarneback(previous, current, None, 0.5, 2, 15, 2, 5, 1.2, 0)
        import numpy as np
        return float(np.median(flow[..., 0])), float(np.median(flow[..., 1]))
    except Exception:
        return 0.0, 0.0


def _shift_box(item: dict[str, Any], dx: float, dy: float, confidence_scale: float) -> dict[str, Any]:
    return _box(float(item["x1"]) + dx, float(item["y1"]) + dy, float(item["x2"]) + dx, float(item["y2"]) + dy, float(item.get("confidence") or 0.0) * confidence_scale, "flow")


def subject_box_for_window(profile: dict[str, Any] | None, start: float, duration: float) -> dict[str, float] | None:
    """Return a conservative union box for the Reel cut window."""
    if not profile:
        return None
    end = float(start) + max(0.0, float(duration))
    if profile.get("analysis_end_sec") is not None and float(start) > float(profile.get("analysis_end_sec") or 0.0) + 0.5:
        return None
    samples = [s for s in profile.get("samples") or [] if float(s.get("t") or 0.0) <= end + 0.35 and float(s.get("t") or 0.0) >= start - 0.35]
    if not samples:
        samples = list(profile.get("samples") or [])[-2:]
    boxes = [b for sample in samples for b in sample.get("boxes") or [] if float(b.get("confidence") or 0.0) >= 0.20]
    if not boxes:
        return None
    return {
        "x1": min(float(b["x1"]) for b in boxes), "y1": min(float(b["y1"]) for b in boxes),
        "x2": max(float(b["x2"]) for b in boxes), "y2": max(float(b["y2"]) for b in boxes),
    }
