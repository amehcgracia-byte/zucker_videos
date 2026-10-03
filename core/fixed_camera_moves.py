"""Native, deterministic flat-camera choreography in normalized crop space."""
from __future__ import annotations

import math
import random
from typing import Any

from core.stages.base import stable_fingerprint

FIXED_MOTION_VERSION = 2
MAX_ZOOM = 1.38
ZOOM_MOVES = {"full_zoom_in", "zoom_in_center", "zoom_out_center", "zoom_in", "zoom_out",
              "zoom_in_very_slow", "zoom_out_very_slow", "subject_reframe", "subject_release"}
HORIZONTAL_MOVES = {"pan_right_center", "pan_left_center", "left_to_center", "center_to_right",
                    "right_to_center", "center_to_left"}
VERTICAL_MOVES = {"pan_down_center", "pan_up_center"}
DIAGONAL_MOVES = {"diagonal_up_right", "diagonal_down_left"}
CATALOG = ("full_static", "full_zoom_in", "zoom_in_center", "zoom_out_center", "zoom_in", "zoom_out",
           "zoom_in_very_slow", "zoom_out_very_slow", "pan_right_center", "pan_left_center",
           "left_to_center", "center_to_right", "right_to_center", "center_to_left",
           "pan_down_center", "pan_up_center", "diagonal_up_right", "diagonal_down_left",
           "subject_reframe", "subject_release")


def _finite(value: float, default: float) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def fixed_camera_motion(index: int, target_x: float = .5, target_y: float = .5, *,
                        duration: float = 4.0, seed: str = "", previous: str = "",
                        confidence: float = 1.0, allow_static: bool = True,
                        preferred: str = "", gentle: bool = False) -> dict[str, Any]:
    """Select one authored family; bound travel by seconds and subject position.

    Crop endpoints and the complete straight-line path must keep the anchor
    inside the central 80% of the output. Without positive evidence only a
    full-frame hold or a conservative central zoom is allowed. No detector runs here.
    """
    duration = max(.1, _finite(duration, 4.0))
    known = (_finite(confidence, 0.0) >= .5
             and .25 <= _finite(target_x, float("nan")) <= .75
             and .35 <= _finite(target_y, float("nan")) <= .65)
    tx = min(.75, max(.25, _finite(target_x, .5))) if known else .5
    ty = min(.65, max(.35, _finite(target_y, .5))) if known else .5
    rng = random.Random(stable_fingerprint({"version": 1, "seed": seed, "index": index}))
    eligible = list(CATALOG) if known else ["full_static", "zoom_in_very_slow", "zoom_out_very_slow"]
    if not allow_static:
        eligible.remove("full_static")
    eligible = [name for name in eligible if name != previous]
    kind = preferred if preferred in eligible else rng.choice(eligible)
    very_slow = "very_slow" in kind or not known
    rate = .018 if not known else .009 if very_slow else .025
    travel = min(.10 if not known else .04 if very_slow else .16, duration * rate) * rng.uniform(.9, 1.0)
    if gentle:
        travel = min(travel, .10)
    z0 = z1 = 1.0
    x0 = x1 = y0 = y1 = .5
    lock = kind in ZOOM_MOVES
    if lock:
        base = 1.0 if kind in {"full_zoom_in", "subject_reframe", "subject_release"} or not known else 1.06
        z0, z1 = base, min(MAX_ZOOM, base + travel)
        if "out" in kind or kind == "subject_release":
            z0, z1 = z1, z0
    elif kind != "full_static":
        z0 = z1 = 1.12 if gentle else 1.18
        # Pan travel in source space stays below 1% per second, and much
        # smaller vertically/diagonally. Never combine a pan with a zoom.
        span = min(.07, duration * .012) * rng.uniform(.8, 1.0)
        if kind in HORIZONTAL_MOVES:
            x0, x1 = {
                "left_to_center": (.5-span, .5), "center_to_right": (.5, .5+span),
                "right_to_center": (.5+span, .5), "center_to_left": (.5, .5-span),
                "pan_right_center": (.5-span/2, .5+span/2),
                "pan_left_center": (.5+span/2, .5-span/2),
            }[kind]
        elif kind in VERTICAL_MOVES:
            y0, y1 = (.5-span/2, .5+span/2)
            if kind == "pan_up_center":
                y0, y1 = y1, y0
        else:
            x0, x1, y0, y1 = .5-span/3, .5+span/3, .5+span/3, .5-span/3
            if kind == "diagonal_down_left":
                x0, x1, y0, y1 = x1, x0, y1, y0

    def bounded_pan(pan: float, target: float, zoom: float, low: float, high: float) -> float:
        if zoom <= 1.001:
            return .5
        # output anchor = zoom*target - (zoom-1)*pan
        low = max(low, (zoom*target-.90)/(zoom-1))
        high = min(high, (zoom*target-.10)/(zoom-1))
        return min(high, max(low, pan))

    if not lock:
        x0 = bounded_pan(x0, tx, z0, .25, .75)
        x1 = bounded_pan(x1, tx, z1, .25, .75)
        y0 = bounded_pan(y0, ty, z0, .35, .65)
        y1 = bounded_pan(y1, ty, z1, .35, .65)
    return {"type": "ken_burns", "movement": kind, "library_version": FIXED_MOTION_VERSION,
            "seed": seed, "duration_sec": round(duration, 6), "subject_confidence": min(1.0, max(0.0, _finite(confidence, 0.0))),
            "subject_fallback": not known, "speed": "very_slow" if very_slow else "slow",
            "speed_factor": 1.0, "lock_target": lock, "target_x": round(tx, 4), "target_y": round(ty, 4),
            "zoom_start": round(z0, 6), "zoom_end": round(z1, 6),
            "pan_x_start": round(x0, 6), "pan_x_end": round(x1, 6),
            "pan_y_start": round(y0, 6), "pan_y_end": round(y1, 6),
            "pan_x": round(x0, 6), "pan_y": round(y0, 6),
            "vertical_motion": "up" if y1 < y0 else "down" if y1 > y0 else "static",
            "enforce_top_edge": False}
