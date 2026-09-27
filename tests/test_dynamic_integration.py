from __future__ import annotations

from core.stages.edit import _apply_dynamic_moves
from core.stages.export import _ken_burns_filter
from server.api import _preview_timestamp_ratio


def _flat_segments(count: int = 8) -> list[dict]:
    return [
        {
            "clip_path": "/tmp/iphone.mov",
            "source_path": "/tmp/iphone.mov",
            "filename": "iphone.mov",
            "clip_start_sec": index * 8.0,
            "duration_sec": 8.0,
        }
        for index in range(count)
    ]


def test_dynamic_moves_are_sparse_and_animate_flat_camera_segments():
    plan = {"platform": "youtube", "segments": _flat_segments()}

    result = _apply_dynamic_moves(plan, {"edit": {"dynamic_moves_ratio": 0.5}})

    applied = result["dynamic_moves"]["applied"]
    indices = [item["segment_index"] for item in applied]
    assert applied
    assert all(right - left > 1 for left, right in zip(indices, indices[1:]))
    assert all(result["segments"][index]["motion"]["type"] == "ken_burns" for index in indices)
    assert all(
        result["segments"][index]["motion"]["pan_x_start"] != result["segments"][index]["motion"]["pan_x_end"]
        or result["segments"][index]["motion"]["pan_y_start"] != result["segments"][index]["motion"]["pan_y_end"]
        or result["segments"][index]["motion"]["zoom_start"] != result["segments"][index]["motion"]["zoom_end"]
        for index in indices
    )


def test_dynamic_moves_never_replace_directed_360_shots():
    segments = _flat_segments()
    directed = {
        "clip_path": "/tmp/360.mp4",
        "source_path": "/tmp/360.mp4",
        "filename": "wide360.mp4",
        "projection": "equirect",
        "clip_start_sec": 0.0,
        "duration_sec": 8.0,
        "spherical_shot": {
            "type": "recorded_move",
            "recorded_take": "Director take",
            "curve": [{"t": 0.0, "yaw": 10.0, "pitch": 0.0, "fov": 100.0}],
        },
    }
    segments[0] = directed
    original_shot = dict(directed["spherical_shot"])
    result = _apply_dynamic_moves({"platform": "youtube", "segments": segments}, {})

    assert result["segments"][0]["spherical_shot"] == original_shot


def test_true_360_passthrough_does_not_receive_reframing_motion():
    plan = {
        "platform": "360",
        "segments": [
            {
                "clip_path": "/tmp/360.mp4",
                "source_path": "/tmp/360.mp4",
                "projection": "equirect",
                "duration_sec": 12.0,
            }
        ],
    }

    result = _apply_dynamic_moves(plan, {})

    assert result["dynamic_moves"]["enabled"] is True
    assert result["dynamic_moves"]["applied"] == []
    assert "motion" not in result["segments"][0]


def test_ken_burns_filter_animates_pan_when_requested():
    graph = _ken_burns_filter(
        {
            "type": "ken_burns",
            "zoom_start": 1.02,
            "zoom_end": 1.07,
            "pan_x_start": 0.42,
            "pan_x_end": 0.58,
            "pan_y_start": 0.45,
            "pan_y_end": 0.55,
            "easing": "smoothstep",
        },
        "youtube",
        8.0,
    )

    assert graph is not None
    assert "0.420000" in graph
    assert "0.580000" in graph
    assert "3-2" in graph


def test_spherical_preview_landmarks_use_distinct_stable_source_frames():
    ratios = [_preview_timestamp_ratio(key) for key in ("full_stage", "singer", "drummer", "left", "right", "audience")]
    assert len(set(ratios)) == len(ratios)
    assert ratios == [_preview_timestamp_ratio(key) for key in ("full_stage", "singer", "drummer", "left", "right", "audience")]
