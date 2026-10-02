"""Canonical 360 view parameters shared by preview and export."""

from __future__ import annotations

import math

MAX_SPHERICAL_FOV = 300.0
STEREOGRAPHIC_FOV_THRESHOLD = 170.0
NORMAL_FOV_MIN = 1.0
NORMAL_FOV_MAX = MAX_SPHERICAL_FOV


def signed_yaw(value: float) -> float:
    """Convert the UI's canonical 0..360 yaw to v360's signed convention."""
    value = float(value) % 360.0
    return value - 360.0 if value > 180.0 else value


def paired_flat_fov(horizontal_fov: float, aspect_ratio: float) -> tuple[float, float]:
    horizontal = max(1.0, min(179.0, float(horizontal_fov)))
    aspect = max(0.1, float(aspect_ratio))
    vertical = math.degrees(2.0 * math.atan(math.tan(math.radians(horizontal) / 2.0) / aspect))
    return horizontal, max(1.0, min(179.0, vertical))


def effective_fov(fov: float, shot_type: str = "") -> float:
    shot_type = str(shot_type or "")
    value = float(fov)
    if shot_type == "planet":
        return max(220.0, min(MAX_SPHERICAL_FOV, value))
    if shot_type == "recorded_move":
        return max(1.0, min(MAX_SPHERICAL_FOV, value))
    return max(NORMAL_FOV_MIN, min(NORMAL_FOV_MAX, value))


def view_parameters(yaw: float, pitch: float, fov: float, aspect_ratio: float, shot_type: str = "", projection_hint: str | None = None) -> dict[str, float | str]:
    """Return exactly the projection and v360 fields used by both paths."""
    shot_type = str(shot_type or "")
    horizontal = effective_fov(fov, shot_type)
    stereographic = shot_type == "planet" or horizontal > STEREOGRAPHIC_FOV_THRESHOLD
    if projection_hint in {"flat", "sg"}:
        stereographic = projection_hint == "sg"
    if stereographic:
        if shot_type == "planet":
            vertical = max(160.0, min(260.0, horizontal / max(0.1, float(aspect_ratio))))
        else:
            vertical = max(1.0, min(MAX_SPHERICAL_FOV, horizontal / max(0.1, float(aspect_ratio))))
        projection = "sg"
    else:
        horizontal, vertical = paired_flat_fov(horizontal, aspect_ratio)
        projection = "flat"
    return {
        "yaw": signed_yaw(yaw),
        "pitch": float(pitch),
        "h_fov": horizontal,
        "v_fov": vertical,
        "projection": projection,
    }


def spherical_view_filter(
    projection: str,
    yaw: float,
    pitch: float,
    fov: float,
    shot_type: str = "",
    *,
    insv_fov: float = 190.0,
    aspect_ratio: float = 16.0 / 9.0,
    width: int = 360,
    height: int = 202,
) -> str:
    """Build the canonical per-shot v360 filter used by views and audits.

    The analysis proxy deliberately remains a fixed flat view. Any authored
    view of a spherical shot must call this helper with the saved pose so the
    thumbnail, interactive preview and export agree on yaw/pitch/FOV and on
    the flat-vs-stereographic projection choice.
    """
    source_projection = str(projection or "").lower()
    view = view_parameters(yaw, pitch, fov, aspect_ratio, shot_type)
    if source_projection == "raw_insv":
        prefix = (
            f"v360=input=dfisheye:output=e:ih_fov={float(insv_fov):.3f}:"
            f"iv_fov={float(insv_fov):.3f}:interp=lanczos,"
        )
    elif source_projection == "equirect":
        prefix = ""
    else:
        raise ValueError(f"Unsupported spherical source projection: {projection!r}")
    return (
        f"{prefix}v360=input=equirect:output={view['projection']}:"
        f"yaw={float(view['yaw']):.3f}:pitch={float(view['pitch']):.3f}:"
        f"h_fov={float(view['h_fov']):.3f}:v_fov={float(view['v_fov']):.3f}:"
        f"w={int(width)}:h={int(height)}:interp=lanczos"
    )
