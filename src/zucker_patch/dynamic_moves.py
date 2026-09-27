"""Deterministic, subtle automatic motion plans for Zucker Editor.

This module is intentionally side-effect free. It plans motion for segments but
does not run detection, read video frames, or modify a project. 360 curves use
the same sample shape as core.camera_moves:
    {"t": seconds, "yaw": degrees, "pitch": degrees, "fov": degrees}

The editor can therefore pass a generated curve through the existing sendcmd
and v360 render path after the integration described in INTEGRACION.md.
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any, Mapping, Sequence

try:
    from core.operator_avoidance import load_cached_operator_presence
except ImportError:  # Allows the pure planner to be imported in isolation.
    load_cached_operator_presence = None  # type: ignore[assignment]

MIN_DYNAMIC_SEGMENT_SEC = 6.0
DEFAULT_DYNAMIC_RATIO = 0.25
CURVE_SAMPLES = 9
MAX_DYNAMIC_YAW_RATE_DEG_PER_SEC = 4.5
MAX_DYNAMIC_PITCH_RATE_DEG_PER_SEC = 2.0
MAX_DYNAMIC_FOV_RATE_DEG_PER_SEC = 2.0
MAX_DYNAMIC_YAW_TRAVEL_DEG = 18.0
MAX_DYNAMIC_PITCH_TRAVEL_DEG = 7.0
MAX_DYNAMIC_FOV_TRAVEL_DEG = 8.0

MOVE_KINDS = (
    "pan_left",
    "pan_right",
    "zoom_in",
    "zoom_out",
    "zoom_pan",
    "person_track",
)


def segment_duration(segment: Mapping[str, Any]) -> float:
    """Return a segment duration from the project's common field variants."""

    for key in ("duration_sec", "duration", "length_sec"):
        value = _finite_float(segment.get(key))
        if value is not None:
            return max(0.0, value)
    start = _finite_float(segment.get("start_sec"))
    end = _finite_float(segment.get("end_sec"))
    if start is not None and end is not None:
        return max(0.0, end - start)
    return 0.0


def eligible_segment_indices(
    segments: Sequence[Mapping[str, Any]],
    min_duration_sec: float = MIN_DYNAMIC_SEGMENT_SEC,
) -> list[int]:
    """Return only segments long enough to carry a visible, gentle movement."""

    minimum = max(0.0, float(min_duration_sec))
    return [
        index
        for index, segment in enumerate(segments)
        if segment_duration(segment) >= minimum
    ]


def select_dynamic_move_indices(
    segments: Sequence[Mapping[str, Any]],
    ratio: float = DEFAULT_DYNAMIC_RATIO,
    min_duration_sec: float = MIN_DYNAMIC_SEGMENT_SEC,
    seed: str = "zucker-editor",
) -> list[int]:
    """Choose roughly one segment in four, never two adjacent segments.

    Selection is deterministic for a given seed. If the requested ratio cannot
    be reached without adjacent moves, the largest valid subset is returned.
    """

    eligible = eligible_segment_indices(segments, min_duration_sec)
    if not eligible or ratio <= 0:
        return []
    desired = max(1, int(round(len(eligible) * min(float(ratio), 1.0))))
    ranked = sorted(
        eligible,
        key=lambda index: _stable_number(f"{seed}:segment:{index}"),
    )
    selected: list[int] = []
    selected_set: set[int] = set()
    for index in ranked:
        if index - 1 in selected_set or index + 1 in selected_set:
            continue
        selected.append(index)
        selected_set.add(index)
        if len(selected) >= desired:
            break
    return sorted(selected)


def plan_dynamic_moves(
    segments: Sequence[Mapping[str, Any]],
    ratio: float = DEFAULT_DYNAMIC_RATIO,
    min_duration_sec: float = MIN_DYNAMIC_SEGMENT_SEC,
    seed: str = "zucker-editor",
    person_track_available: bool = False,
) -> list[dict[str, Any]]:
    """Return deterministic assignments without changing the input segments."""

    indices = select_dynamic_move_indices(segments, ratio, min_duration_sec, seed)
    plans: list[dict[str, Any]] = []
    for ordinal, index in enumerate(indices):
        kinds = list(MOVE_KINDS if person_track_available else MOVE_KINDS[:-1])
        kind = kinds[
            _stable_number(f"{seed}:kind:{index}:{ordinal}") % len(kinds)
        ]
        plans.append(
            {
                "segment_index": index,
                "kind": kind,
                "duration_sec": round(segment_duration(segments[index]), 6),
                "seed": f"{seed}:{index}",
                "uses_cached_person_track": kind == "person_track",
            }
        )
    return plans


def load_cached_person_track(analysis_path: str) -> list[dict[str, float]]:
    """Load subject coordinates from the existing MobileNet-SSD cache.

    This is read-only and never starts detection. The cache stores the dominant
    detection as cx/cy and, when available, the secondary subject as
    subject_cx/subject_cy. Dynamic tracking deliberately uses only the
    secondary subject so it does not chase the camera operator.
    """

    if not analysis_path or load_cached_operator_presence is None:
        return []
    try:
        samples = load_cached_operator_presence(analysis_path)
    except (OSError, ValueError, TypeError):
        return []
    output: list[dict[str, float]] = []
    for sample in samples or []:
        t = _finite_float(sample.get("t"))
        cx = _finite_float(sample.get("subject_cx"))
        cy = _finite_float(sample.get("subject_cy"))
        if t is None or cx is None or cy is None:
            continue
        output.append(
            {
                "t": max(0.0, t),
                "cx": _clamp(cx, 0.0, 1.0),
                "cy": _clamp(cy, 0.0, 1.0),
            }
        )
    return sorted(output, key=lambda item: item["t"])


def generate_iphone_motion(
    segment: Mapping[str, Any],
    kind: str,
    seed: str = "zucker-editor",
    person_samples: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a gentle animated crop plan for a normal flat/iPhone segment.

    The current exporter already understands zoom_start/zoom_end. The
    pan_x_start/end and pan_y_start/end fields are consumed by the small
    exporter integration described in INTEGRACION.md; pan_x and pan_y remain
    as backwards-compatible static fallbacks.
    """

    duration = segment_duration(segment)
    if duration < MIN_DYNAMIC_SEGMENT_SEC:
        raise ValueError("Dynamic motion requires a segment of at least 6 seconds")
    if kind not in MOVE_KINDS[:-1]:
        if kind != "person_track":
            raise ValueError(f"Unknown dynamic move kind: {kind}")
        kind = "zoom_pan"

    rng = _rng(seed)
    pan_x_start = pan_x_end = 0.5
    pan_y_start = pan_y_end = 0.5
    zoom_start = 1.02
    zoom_end = 1.02

    if kind == "pan_left":
        pan_x_start, pan_x_end = 0.58, 0.42
    elif kind == "pan_right":
        pan_x_start, pan_x_end = 0.42, 0.58
    elif kind == "zoom_in":
        zoom_end = 1.08
    elif kind == "zoom_out":
        zoom_start, zoom_end = 1.08, 1.02
    elif kind == "zoom_pan":
        pan_x_start, pan_x_end = (0.42, 0.58) if rng.random() < 0.5 else (0.58, 0.42)
        zoom_end = 1.07
    elif kind == "person_track":
        points = _person_points_for_segment(segment, person_samples or [])
        if points:
            first, last = points[0], points[-1]
            pan_x_start = _clamp(first["cx"], 0.30, 0.70)
            pan_x_end = _clamp(last["cx"], 0.30, 0.70)
            pan_y_start = _clamp(first["cy"], 0.30, 0.70)
            pan_y_end = _clamp(last["cy"], 0.30, 0.70)
        else:
            zoom_end = 1.06
            kind = "zoom_in"

    return {
        "type": "ken_burns",
        "dynamic_move": kind,
        "duration_sec": round(duration, 6),
        "easing": "smoothstep",
        "zoom_start": round(zoom_start, 6),
        "zoom_end": round(zoom_end, 6),
        "pan_x": round((pan_x_start + pan_x_end) / 2.0, 6),
        "pan_y": round((pan_y_start + pan_y_end) / 2.0, 6),
        "pan_x_start": round(pan_x_start, 6),
        "pan_x_end": round(pan_x_end, 6),
        "pan_y_start": round(pan_y_start, 6),
        "pan_y_end": round(pan_y_end, 6),
    }


def generate_360_curve(
    shot: Mapping[str, Any],
    duration_sec: float,
    kind: str,
    seed: str = "zucker-editor",
    person_samples: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, float]]:
    """Generate a slow curve in the format consumed by the existing v360 path."""

    duration = float(duration_sec)
    if duration < MIN_DYNAMIC_SEGMENT_SEC:
        raise ValueError("Dynamic motion requires a segment of at least 6 seconds")
    if kind not in MOVE_KINDS:
        raise ValueError(f"Unknown dynamic move kind: {kind}")

    base_yaw = _finite_float(shot.get("yaw")) or 0.0
    base_pitch = _finite_float(shot.get("pitch")) or 0.0
    base_fov = _finite_float(shot.get("fov")) or 100.0
    base_fov = _clamp(base_fov, 1.0, 179.0)
    rng = _rng(seed)

    yaw_start = base_yaw
    yaw_end = base_yaw
    pitch_start = pitch_end = base_pitch
    fov_start = fov_end = base_fov

    max_yaw_travel = min(
        MAX_DYNAMIC_YAW_TRAVEL_DEG,
        MAX_DYNAMIC_YAW_RATE_DEG_PER_SEC * duration * 0.72,
    )
    max_pitch_travel = min(
        MAX_DYNAMIC_PITCH_TRAVEL_DEG,
        MAX_DYNAMIC_PITCH_RATE_DEG_PER_SEC * duration * 0.72,
    )
    max_fov_travel = min(
        MAX_DYNAMIC_FOV_TRAVEL_DEG,
        MAX_DYNAMIC_FOV_RATE_DEG_PER_SEC * duration * 0.72,
    )

    if kind == "pan_left":
        yaw_end -= max_yaw_travel
    elif kind == "pan_right":
        yaw_end += max_yaw_travel
    elif kind == "zoom_in":
        fov_end -= max_fov_travel
    elif kind == "zoom_out":
        fov_end += max_fov_travel
    elif kind == "zoom_pan":
        direction = -1.0 if rng.random() < 0.5 else 1.0
        yaw_end += direction * max_yaw_travel
        fov_end -= max_fov_travel * 0.7
    elif kind == "person_track":
        points = list(person_samples or [])
        if points:
            first = _person_point(points[0])
            last = _person_point(points[-1])
            if first and last:
                yaw_start = base_yaw + _subject_yaw_delta(first["cx"], base_fov)
                yaw_end = base_yaw + _subject_yaw_delta(last["cx"], base_fov)
                pitch_start = base_pitch + _subject_pitch_delta(first["cy"], base_fov)
                pitch_end = base_pitch + _subject_pitch_delta(last["cy"], base_fov)
                yaw_start, yaw_end = _limit_pair(yaw_start, yaw_end, max_yaw_travel, base_yaw)
                pitch_start, pitch_end = _limit_pair(pitch_start, pitch_end, max_pitch_travel, base_pitch)
            else:
                kind = "zoom_pan"
        else:
            kind = "zoom_pan"
        if kind == "zoom_pan":
            yaw_end += max_yaw_travel * 0.5
            fov_end -= max_fov_travel * 0.5

    fov_start = _clamp(fov_start, 1.0, 179.0)
    fov_end = _clamp(fov_end, 1.0, 179.0)
    curve: list[dict[str, float]] = []
    for index in range(CURVE_SAMPLES):
        progress = index / float(CURVE_SAMPLES - 1)
        eased = _smoothstep(progress)
        curve.append(
            {
                "t": round(duration * progress, 6),
                "yaw": round((yaw_start + _shortest_delta(yaw_start, yaw_end) * eased) % 360.0, 6),
                "pitch": round(_lerp(pitch_start, pitch_end, eased), 6),
                "fov": round(_lerp(fov_start, fov_end, eased), 6),
            }
        )
    validate_360_curve(curve, duration)
    return curve


def build_dynamic_360_shot(
    shot: Mapping[str, Any],
    duration_sec: float,
    kind: str,
    seed: str = "zucker-editor",
    person_samples: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a recorded_move-compatible spherical_shot payload."""

    curve = generate_360_curve(shot, duration_sec, kind, seed, person_samples)
    first = curve[0]
    return {
        "type": "recorded_move",
        "label": f"Dynamic move: {kind}",
        "recorded_take": "Dynamic move",
        "dynamic_move": kind,
        "yaw": first["yaw"],
        "pitch": first["pitch"],
        "fov": first["fov"],
        "curve": curve,
    }


def validate_360_curve(curve: Sequence[Mapping[str, Any]], duration_sec: float) -> None:
    """Raise ValueError when a curve has invalid timing or excessive speed."""

    if len(curve) < 2:
        raise ValueError("A 360 curve needs at least two samples")
    previous_t = -1.0
    for sample, next_sample in zip(curve, curve[1:]):
        t = _finite_float(sample.get("t"))
        next_t = _finite_float(next_sample.get("t"))
        if t is None or next_t is None or t <= previous_t or next_t <= t:
            raise ValueError("360 curve timestamps must increase strictly")
        previous_t = t
        dt = next_t - t
        yaw_rate = abs(_shortest_delta(_finite_float(sample.get("yaw")) or 0.0, _finite_float(next_sample.get("yaw")) or 0.0)) / dt
        pitch_rate = abs((_finite_float(next_sample.get("pitch")) or 0.0) - (_finite_float(sample.get("pitch")) or 0.0)) / dt
        fov_rate = abs((_finite_float(next_sample.get("fov")) or 0.0) - (_finite_float(sample.get("fov")) or 0.0)) / dt
        if yaw_rate > MAX_DYNAMIC_YAW_RATE_DEG_PER_SEC + 1e-6:
            raise ValueError(f"360 yaw rate exceeds safe limit: {yaw_rate:.3f} deg/s")
        if pitch_rate > MAX_DYNAMIC_PITCH_RATE_DEG_PER_SEC + 1e-6:
            raise ValueError(f"360 pitch rate exceeds safe limit: {pitch_rate:.3f} deg/s")
        if fov_rate > MAX_DYNAMIC_FOV_RATE_DEG_PER_SEC + 1e-6:
            raise ValueError(f"360 fov rate exceeds safe limit: {fov_rate:.3f} deg/s")
    last_t = _finite_float(curve[-1].get("t"))
    if last_t is None or abs(last_t - float(duration_sec)) > 1e-5:
        raise ValueError("360 curve must finish at the segment duration")


def _person_points_for_segment(segment: Mapping[str, Any], samples: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    start = _finite_float(segment.get("source_start_sec"))
    if start is None:
        start = _finite_float(segment.get("clip_start_sec")) or 0.0
    end = start + segment_duration(segment)
    points: list[dict[str, float]] = []
    for sample in samples:
        point = _person_point(sample)
        t = _finite_float(sample.get("t"))
        if point is not None and t is not None and start <= t <= end:
            points.append({"t": t, **point})
    if points:
        return points
    return [{"t": start, "cx": 0.5, "cy": 0.5}, {"t": end, "cx": 0.5, "cy": 0.5}]


def _person_point(sample: Mapping[str, Any]) -> dict[str, float] | None:
    cx = _finite_float(sample.get("subject_cx", sample.get("cx")))
    cy = _finite_float(sample.get("subject_cy", sample.get("cy")))
    if cx is None or cy is None:
        return None
    return {"cx": _clamp(cx, 0.0, 1.0), "cy": _clamp(cy, 0.0, 1.0)}


def _subject_yaw_delta(cx: float, fov: float) -> float:
    return _clamp((cx - 0.5) * fov * 0.25, -MAX_DYNAMIC_YAW_TRAVEL_DEG, MAX_DYNAMIC_YAW_TRAVEL_DEG)


def _subject_pitch_delta(cy: float, fov: float) -> float:
    return _clamp((0.5 - cy) * fov * 0.12, -MAX_DYNAMIC_PITCH_TRAVEL_DEG, MAX_DYNAMIC_PITCH_TRAVEL_DEG)


def _limit_pair(start: float, end: float, travel: float, anchor: float) -> tuple[float, float]:
    return _clamp(start, anchor - travel, anchor + travel), _clamp(end, anchor - travel, anchor + travel)


def _smoothstep(value: float) -> float:
    value = _clamp(value, 0.0, 1.0)
    return value * value * (3.0 - 2.0 * value)


def _shortest_delta(start: float, end: float) -> float:
    return ((end - start + 540.0) % 360.0) - 180.0


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * _clamp(amount, 0.0, 1.0)


def _rng(seed: str) -> random.Random:
    return random.Random(_stable_number(seed))


def _stable_number(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:16], 16)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))
