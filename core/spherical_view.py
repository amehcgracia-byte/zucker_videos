"""Canonical 360 view parameters shared by preview and export."""

from __future__ import annotations

import math

MAX_SPHERICAL_FOV = 300.0
STEREOGRAPHIC_FOV_THRESHOLD = 170.0
# Rectilinear projection becomes visibly distorted as it approaches 180 degrees.
# Keep ordinary shots perspective-safe; only the explicit Planet shot is
# allowed to use stereographic projection.
PERSPECTIVE_FOV_MAX = 165.0
NORMAL_FOV_MIN = 82.0
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
    at export time.  The renderer maps the ordinary named presets to its
    flat
    output and reserves stereographic output for the explicit Tiny Planet shot.
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
    stereographic = shot_type == "planet" or preset == "tiny_planet"
    if projection_hint in {"flat", "sg"}:
        stereographic = projection_hint == "sg"
    if stereographic:
        if shot_type == "planet" or preset == "tiny_planet":
            vertical = max(160.0, min(260.0, horizontal / max(0.1, float(aspect_ratio))))
        else:
            vertical = max(1.0, min(MAX_SPHERICAL_FOV, horizontal / max(0.1, float(aspect_ratio))))
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
