"""Canonical 360 view parameters shared by preview and export."""

from __future__ import annotations

import math

MAX_SPHERICAL_FOV = 300.0
STEREOGRAPHIC_FOV_THRESHOLD = 170.0
# Rectilinear projection becomes visibly distorted as it approaches 180 degrees.
# Keep linear portraits perspective-safe. Wide presets use stereographic
# projection, which trades curved straight lines for less edge stretching.
PERSPECTIVE_FOV_MAX = 110.0
NORMAL_FOV_MIN = 30.0
NORMAL_FOV_MAX = PERSPECTIVE_FOV_MAX
# The external 360 editors expose pitches beyond the old +/-25 degree clamp.
# Keep authored pitches wide enough to round-trip those keyframes while the
# automatic planner continues to use its own tighter subject-safe clamp.
NORMAL_PITCH_MIN = -45.0
NORMAL_PITCH_MAX = 45.0
NORMAL_ROLL_MIN = -45.0
NORMAL_ROLL_MAX = 45.0
DEWARP_FOV_MIN = 70.0
DEWARP_FOV_MAX = 100.0
PROJECTION_CONTROL_MIN = 0.0
PROJECTION_CONTROL_MAX = 1.0
PROJECTION_PRESETS = {
    "linear": "linear",
    "dewarp": "dewarp",
    "megaview": "megaview",
    "ultrawide": "ultrawide",
    "crystal_ball": "crystal_ball",
    "tiny_planet": "tiny_planet",
    "planet": "tiny_planet",
}


def normalize_projection_preset(value: object, shot_type: str = "") -> str:
    """Return the stable preset name used by the preview, plan and exporter.

    FFmpeg's v360 filter does not expose Insta360's named DEWARP/MEGAVIEW
    presets.  The name is nevertheless part of the authored pose: retaining it
    prevents a preview keyframe from silently becoming a different projection
    at export time.  Linear/dewarp use rectilinear output; wide presets use stereographic
    output with a paired vertical angle. These are our projection choices,
    not a reproduction of proprietary camera-vendor algorithms.
    """
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"sg", "stereographic"}:
        return "tiny_planet"
    if raw in PROJECTION_PRESETS:
        return PROJECTION_PRESETS[raw]
    return "tiny_planet" if str(shot_type or "") == "planet" else "linear"


def effective_pitch(pitch: float, shot_type: str = "") -> float:
    """Return the canonical vertical angle for preview, review and export."""
    value = float(pitch)
    if str(shot_type or "") == "planet":
        return max(-90.0, min(90.0, value))
    return max(NORMAL_PITCH_MIN, min(NORMAL_PITCH_MAX, value))


def effective_roll(roll: float, shot_type: str = "") -> float:
    """Return the canonical horizon rotation for preview, review and export."""
    value = float(roll)
    if str(shot_type or "") == "planet":
        return max(-180.0, min(180.0, value))
    return max(NORMAL_ROLL_MIN, min(NORMAL_ROLL_MAX, value))


def effective_projection_control(value: object) -> float:
    """Preserve the auxiliary 360 editor control without unsafe values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(PROJECTION_CONTROL_MIN, min(PROJECTION_CONTROL_MAX, number))


def signed_yaw(value: float) -> float:
    """Convert the UI's canonical 0..360 yaw to v360's signed convention."""
    value = float(value) % 360.0
    return value - 360.0 if value > 180.0 else value


def paired_flat_fov(horizontal_fov: float, aspect_ratio: float) -> tuple[float, float]:
    horizontal = max(1.0, min(179.0, float(horizontal_fov)))
    aspect = max(0.1, float(aspect_ratio))
    vertical = math.degrees(2.0 * math.atan(math.tan(math.radians(horizontal) / 2.0) / aspect))
    return horizontal, max(1.0, min(179.0, vertical))


def effective_fov(fov: float, shot_type: str = "", projection_preset: str | None = None) -> float:
    shot_type = str(shot_type or "")
    preset = normalize_projection_preset(projection_preset, shot_type)
    value = float(fov)
    if shot_type == "planet" or preset == "tiny_planet":
        return max(220.0, min(MAX_SPHERICAL_FOV, value))
    if preset == "dewarp":
        # Insta360 documents DEWARP as a 70–100° viewing-angle preset. Keep
        # that contract instead of allowing a wide value to tear the image.
        return max(DEWARP_FOV_MIN, min(DEWARP_FOV_MAX, value))
    if preset in {"megaview", "ultrawide", "crystal_ball"}:
        return max(NORMAL_FOV_MIN, min(170.0, value))
    if shot_type == "recorded_move":
        return max(NORMAL_FOV_MIN, min(PERSPECTIVE_FOV_MAX, value))
    return max(NORMAL_FOV_MIN, min(NORMAL_FOV_MAX, value))


def view_parameters(
    yaw: float,
    pitch: float,
    fov: float,
    aspect_ratio: float,
    shot_type: str = "",
    projection_hint: str | None = None,
    projection_preset: str | None = None,
    roll: float = 0.0,
    projection_control: object = None,
) -> dict[str, float | str]:
    """Return exactly the projection and v360 fields used by both paths."""
    shot_type = str(shot_type or "")
    preset = normalize_projection_preset(projection_preset, shot_type)
    horizontal = effective_fov(fov, shot_type, preset)
    stereographic = shot_type == "planet" or preset in {"tiny_planet", "megaview", "ultrawide", "crystal_ball"}
    if projection_hint in {"flat", "sg"}:
        stereographic = projection_hint == "sg"
    if stereographic:
        # Stereographic angles cannot be divided by the image aspect ratio.
        # Pair the projected radii so a tiny planet remains circular in 16:9.
        vertical = math.degrees(4 * math.atan(math.tan(math.radians(horizontal) / 4) / max(.1, float(aspect_ratio))))
        projection = "sg"
    else:
        horizontal, vertical = paired_flat_fov(horizontal, aspect_ratio)
        projection = "flat"
    return {
        "yaw": signed_yaw(yaw),
        "pitch": effective_pitch(pitch, shot_type),
        "roll": effective_roll(roll, shot_type),
        "h_fov": horizontal,
        "v_fov": vertical,
        "projection": projection,
        "projection_preset": preset,
        "projection_control": effective_projection_control(projection_control),
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
