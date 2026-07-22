from __future__ import annotations

import json

import pytest

from core.camera_moves import (
    clip_curve_for_segment,
    interpolate_curve,
    normalize_recorded_samples,
    save_camera_move,
    smooth_camera_curve,
)
from core.project import create_project


def test_recorded_samples_are_monotonic_and_locale_independent(tmp_path):
    raw = [
        {"t": 1.0, "yaw": 358, "pitch": -10, "fov": 90},
        {"t": 1.1, "yaw": 1, "pitch": -8, "fov": 92},
        {"t": 1.05, "yaw": 8, "pitch": -7, "fov": 94},
        {"t": 1.2, "yaw": 4, "pitch": -6, "fov": 96},
    ]

    samples = normalize_recorded_samples(raw)
    smoothed = smooth_camera_curve(samples)

    assert [sample["t"] for sample in samples] == [1.0, 1.1, 1.2]
    assert max(abs(((b["yaw"] - a["yaw"] + 540) % 360) - 180) for a, b in zip(smoothed, smoothed[1:])) < 6


def test_save_camera_move_writes_raw_and_smoothed_curve(tmp_path):
    project = create_project("Move", str(tmp_path / "Move.zuckervid"))
    samples = [{"t": index / 15, "yaw": index, "pitch": 0, "fov": 100} for index in range(20)]

    take = save_camera_move(project, "Main take", samples, "/tmp/360.mp4")
    data = json.loads((project.artifacts_dir / "camera_moves" / "Main take.json").read_text(encoding="utf-8"))

    assert take["sample_count"] == 20
    assert data["raw"][0]["t"] == pytest.approx(0.0)
    assert data["smoothed"][-1]["t"] == pytest.approx(19 / 15)
    assert data["sample_rate_hz"] == pytest.approx(15.0, abs=0.01)


def test_clip_curve_interpolates_segment_boundaries():
    move = {
        "name": "Take",
        "smoothed": [
            {"t": 10.0, "yaw": 10, "pitch": -5, "fov": 90},
            {"t": 11.0, "yaw": 20, "pitch": -4, "fov": 95},
            {"t": 12.0, "yaw": 30, "pitch": -3, "fov": 100},
        ],
    }

    curve = clip_curve_for_segment(move, 10.5, 11.5)
    midpoint = interpolate_curve(curve, 0.5)

    assert curve[0]["t"] == pytest.approx(0.0)
    assert curve[-1]["t"] == pytest.approx(1.0)
    assert midpoint == pytest.approx((20.0, -4.0, 95.0))
