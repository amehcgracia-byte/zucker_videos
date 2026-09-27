"""Regression tests for Zucker Editor's dynamic motion planner."""

from __future__ import annotations

try:
    from .dynamic_moves import (
        MAX_DYNAMIC_YAW_RATE_DEG_PER_SEC,
        build_dynamic_360_shot,
        generate_360_curve,
        plan_dynamic_moves,
        select_dynamic_move_indices,
        validate_360_curve,
    )
except ImportError:
    from dynamic_moves import (  # type: ignore
        MAX_DYNAMIC_YAW_RATE_DEG_PER_SEC,
        build_dynamic_360_shot,
        generate_360_curve,
        plan_dynamic_moves,
        select_dynamic_move_indices,
        validate_360_curve,
    )


def test_only_long_segments_are_eligible() -> None:
    segments = [
        {"duration_sec": 5.99},
        {"duration_sec": 6.0},
        {"duration_sec": 10.0},
        {"duration_sec": 3.0},
        {"duration_sec": 8.0},
    ]
    selected = select_dynamic_move_indices(segments, ratio=1.0, seed="eligible")
    assert selected
    assert 0 not in selected
    assert 3 not in selected


def test_selected_segments_are_never_consecutive() -> None:
    segments = [{"duration_sec": 8.0} for _ in range(12)]
    selected = select_dynamic_move_indices(segments, ratio=1.0, seed="spacing")
    assert all(right - left > 1 for left, right in zip(selected, selected[1:]))


def test_selection_is_deterministic_and_roughly_quarter() -> None:
    segments = [{"duration_sec": 8.0} for _ in range(16)]
    first = select_dynamic_move_indices(segments, ratio=0.25, seed="same")
    second = select_dynamic_move_indices(segments, ratio=0.25, seed="same")
    assert first == second
    assert 2 <= len(first) <= 4


def test_plan_can_include_cached_person_tracking() -> None:
    segments = [{"duration_sec": 8.0} for _ in range(8)]
    plans = plan_dynamic_moves(segments, ratio=1.0, seed="person", person_track_available=True)
    assert plans
    assert all(item["segment_index"] in range(8) for item in plans)
    assert all(item["kind"] in {"pan_left", "pan_right", "zoom_in", "zoom_out", "zoom_pan", "person_track"} for item in plans)


def test_360_curves_are_slow_and_plausible() -> None:
    curve = generate_360_curve({"yaw": 30.0, "pitch": 0.0, "fov": 100.0}, 8.0, "zoom_pan", "slow")
    validate_360_curve(curve, 8.0)
    assert curve[0]["t"] == 0.0
    assert curve[-1]["t"] == 8.0
    assert curve[0]["yaw"] != curve[-1]["yaw"]
    for left, right in zip(curve, curve[1:]):
        delta = abs(((right["yaw"] - left["yaw"] + 180.0) % 360.0) - 180.0)
        assert delta / (right["t"] - left["t"]) <= MAX_DYNAMIC_YAW_RATE_DEG_PER_SEC


def test_curve_has_smooth_endpoints() -> None:
    curve = generate_360_curve({"yaw": 0.0, "pitch": 0.0, "fov": 100.0}, 10.0, "pan_right", "ease")
    first_step = abs(curve[1]["yaw"] - curve[0]["yaw"])
    middle_step = abs(curve[4]["yaw"] - curve[3]["yaw"])
    last_step = abs(curve[-1]["yaw"] - curve[-2]["yaw"])
    assert first_step < middle_step
    assert last_step < middle_step


def test_360_shot_matches_existing_recorded_curve_shape() -> None:
    shot = build_dynamic_360_shot({"yaw": 180.0, "pitch": -2.0, "fov": 95.0}, 7.0, "pan_left")
    assert shot["type"] == "recorded_move"
    assert {"t", "yaw", "pitch", "fov"} <= set(shot["curve"][0])
    assert shot["curve"][-1]["t"] == 7.0
