import numpy as np
import pytest

from core.spherical_motion import (
    _fast_wrap_x_inplace,
    reproject_maps,
)


@pytest.mark.parametrize("values", [
    [0.0, 100.0, 3839.5],
    [-100.0, 0.0, 100.0],
    [3700.0, 3840.0, 4300.0],
    [-8000.0, 8000.0, 1.0],
    [np.nan, 100.0, 3841.0],
])
def test_wrap_matches_original(values):
    source = np.asarray(values, dtype=np.float32)
    expected = source.copy()
    expected %= 3840

    actual = source.copy()
    _fast_wrap_x_inplace(actual, 3840)

    np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("preset,fov", [
    ("linear", 60),
    ("linear", 82),
    ("linear", 130),
    ("tiny_planet", 220),
    ("tiny_planet", 270),
])
@pytest.mark.parametrize("yaw", [0, 90, 180, 359])
def test_reprojection_exact_equivalence(preset, fov, yaw):
    shot = {
        "yaw": float(yaw),
        "pitch": 0.0,
        "fov": float(fov),
        "type": "stage",
        "projection_preset": preset,
    }
    args = ((3840, 1920), (960, 540), shot)
    pose = (float(yaw), 0.0, float(fov))

    old_x, old_y = reproject_maps(*args, pose=pose)
    new_x, new_y = reproject_maps(
        *args, pose=pose, fast_wrap=True
    )

    np.testing.assert_array_equal(new_x, old_x)
    np.testing.assert_array_equal(new_y, old_y)


def test_default_remains_original():
    shot = {
        "yaw": 180.0,
        "pitch": 0.0,
        "fov": 82.0,
        "projection_preset": "linear",
    }
    args = ((3840, 1920), (960, 540), shot)

    x1, y1 = reproject_maps(*args)
    x2, y2 = reproject_maps(*args, fast_wrap=False)

    np.testing.assert_array_equal(x1, x2)
    np.testing.assert_array_equal(y1, y2)
