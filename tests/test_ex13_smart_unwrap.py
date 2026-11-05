import numpy as np
import pytest

from core.spherical_motion import (
    _smart_unwrap_if_safe,
    reproject_maps,
)


@pytest.mark.parametrize("axis", [0, 1])
def test_smart_unwrap_special_cases(axis):
    rng = np.random.default_rng(13013)
    arrays = [
        np.array([[0.0, 0.1], [0.2, 0.3]]),
        np.array([[0.0, np.pi], [-np.pi, 0.0]]),
        np.array([[0.0, np.pi + 1e-7]]),
        np.array([[0.0, -np.pi - 1e-7]]),
        np.array([[np.nan, 0.0], [1.0, 2.0]]),
        np.array([[np.inf, 0.0], [1.0, 2.0]]),
        np.array([[0.0, -np.inf], [1.0, 2.0]]),
        np.array([[0.0]]),
        rng.uniform(-np.pi, np.pi, size=(8, 12)),
        rng.uniform(-0.1, 0.1, size=(8, 12)),
    ]

    for a in arrays:
        original = a.copy()
        with np.errstate(invalid="ignore"):
            expected = np.unwrap(a, axis=axis)
            actual = _smart_unwrap_if_safe(a, axis=axis)
        np.testing.assert_array_equal(actual, expected)
        np.testing.assert_array_equal(a, original)


@pytest.mark.parametrize(
    "preset,fovs",
    [
        ("linear", (60, 82, 130)),
        ("dewarp", (60, 100)),
        ("tiny_planet", (220, 270, 300)),
    ],
)
def test_smart_maps_match_original(preset, fovs):
    orientations = (
        (0, 0, 0),
        (180, 0, 0),
        (359, 45, 30),
        (90, -90, 0),
        (270, 70, -45),
    )

    for fov in fovs:
        for yaw, pitch, roll in orientations:
            shot = {
                "yaw": float(yaw),
                "pitch": float(pitch),
                "roll": float(roll),
                "fov": float(fov),
                "type": "stage",
                "projection_preset": preset,
            }
            pose = (float(yaw), float(pitch), float(fov))

            for fast_wrap in (False, True):
                original = reproject_maps(
                    (3840, 1920), (960, 540),
                    shot, pose=pose, fast_wrap=fast_wrap
                )
                candidate = reproject_maps(
                    (3840, 1920), (960, 540),
                    shot, pose=pose, fast_wrap=fast_wrap,
                    smart_unwrap=True,
                )
                for a, b in zip(original, candidate):
                    np.testing.assert_array_equal(a, b)


def test_smart_unwrap_is_opt_in():
    import inspect
    from core.spherical_motion import (
        run_reprojected_command,
        _run_reprojected_command,
    )
    for fn in (
        reproject_maps,
        run_reprojected_command,
        _run_reprojected_command,
    ):
        signature = inspect.signature(fn)
        assert signature.parameters["smart_unwrap"].default is False
