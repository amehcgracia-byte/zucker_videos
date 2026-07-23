from __future__ import annotations

import json

import pytest

from core.camera_moves import (
    MAX_PLAUSIBLE_YAW_RATE_DEG_PER_SEC,
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


def test_clip_curve_does_not_compress_a_long_take_into_a_short_segment():
    """Regression guard: a segment must only see the samples inside its own
    window, at the take's real rate — not the whole take's motion rescaled to
    fit the segment's short duration (which would look like many fast spins).
    """
    # A 10-minute take that slowly completes ~2 full rotations overall.
    total_duration = 600.0
    sample_rate = 15.0
    count = int(total_duration * sample_rate)
    move = {
        "name": "Long take",
        "smoothed": [
            {"t": index / sample_rate, "yaw": (index / sample_rate) * (720.0 / total_duration) % 360.0, "pitch": 0.0, "fov": 100.0}
            for index in range(count)
        ],
    }

    # A 6-second segment plucked from the middle of the take.
    curve = clip_curve_for_segment(move, 300.0, 306.0)

    assert curve[0]["t"] == pytest.approx(0.0)
    assert curve[-1]["t"] == pytest.approx(6.0)
    unwrapped_travel = abs(curve[-1]["yaw"] - curve[0]["yaw"])
    # At 720deg/600s the segment should show ~7.2 degrees, nowhere near a full
    # rotation (360deg), let alone "5+ rotations".
    assert unwrapped_travel < 30.0


def test_clip_curve_raises_when_yaw_rate_is_implausible():
    """If a bug ever dumps a whole take's rotation into one short segment
    again, this must fail loudly instead of silently shipping a spinning shot.

    Samples are stored wrapped to [0, 360), like real recorded data, so this
    builds many closely-spaced steps (each < 180 deg apart, so unwrap keeps
    following the same direction) that add up to several full rotations in
    2 seconds — exactly the "whole take crammed into one short segment" shape.
    """
    move = {
        "name": "Broken take",
        "smoothed": [{"t": index * 0.01, "yaw": (index * 30.0) % 360.0, "pitch": 0.0, "fov": 100.0} for index in range(201)],
    }

    with pytest.raises(ValueError, match="Implausible yaw rate"):
        clip_curve_for_segment(move, 0.0, 2.0)


def test_clip_curve_raises_on_a_long_body_segment_too_not_just_short_ones():
    """The guard must check LOCAL rate, not total-travel-over-duration: an
    aggregate check's tolerance grows with duration, so for the 360 "body"
    export (one segment spanning the whole multi-minute song) it would
    become meaninglessly large. A local check catches the bug regardless of
    how long the enclosing segment is.
    """
    move = {
        "name": "Broken take",
        "smoothed": [{"t": index * 0.01, "yaw": (index * 30.0) % 360.0, "pitch": 0.0, "fov": 100.0} for index in range(201)],
    }

    # Same implausible burst, but clipped as part of a much longer segment —
    # an aggregate/duration-scaled check would have allowed this.
    with pytest.raises(ValueError, match="Implausible yaw rate"):
        clip_curve_for_segment(move, 0.0, 600.0)


def test_clip_curve_allows_a_fast_but_plausible_pan():
    # A single fast whip-pan just under the local-rate ceiling: 140 degrees
    # in 0.2s = 700 deg/s (real takes have been observed peaking around
    # 300-340 deg/s, so this is already a generous margin above real motion).
    move = {
        "name": "Fast pan",
        "smoothed": [
            {"t": 0.0, "yaw": 0.0, "pitch": 0.0, "fov": 100.0},
            {"t": 0.2, "yaw": 140.0, "pitch": 0.0, "fov": 100.0},
        ],
    }

    curve = clip_curve_for_segment(move, 0.0, 0.2)
    assert curve[-1]["yaw"] == pytest.approx(140.0)
