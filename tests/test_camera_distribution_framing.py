from __future__ import annotations

import pytest

from core.stages.edit import (
    FIXED_CAMERA_SAFE_ZOOM_MAX,
    _camera_id,
    _ken_burns_motion,
    _youtube_multicam_plan,
)
from core.stages.export import _ken_burns_filter


def test_camera_identity_does_not_collapse_shared_edited_folder() -> None:
    root = "/Volumes/RAWVideos/ZZSessions/2026/August/24.08.26/Edited"
    assert _camera_id({"source_path": f"{root}/C0185.MP4", "filename": "C0185.MP4"}) == "c"
    assert _camera_id({"source_path": f"{root}/IMG_0043.MOV", "filename": "IMG_0043.MOV"}) == "img"
    assert _camera_id({"source_path": f"{root}/VID_20260824_203343_00_003.mp4", "filename": "VID_20260824_203343_00_003.mp4", "projection": "equirect"}) == "360"


def test_camera_allocation_matches_explicit_percentages() -> None:
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 60.0},
        "sources": [
            {"path": "/tmp/nikon.mp4", "filename": "nikon.mp4", "camera_id": "nikon", "offset_sec": 0.0, "duration_sec": 60.0},
            {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "camera_id": "iphone", "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 60.0},
            {"path": "/tmp/360.mp4", "filename": "360.mp4", "camera_id": "360", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 60.0},
        ],
    }
    beats = {"bars_sec": list(range(0, 61, 2)), "sections_sec": []}

    plan = _youtube_multicam_plan(
        coverage,
        beats,
        {"edit": {"camera_weights": {"nikon": 50, "iphone": 30, "360": 20}}},
    )

    distribution = {row["camera_id"]: row for row in plan["camera_distribution"]}
    assert distribution["nikon"]["configured_percent"] == 50.0
    assert distribution["iphone"]["configured_percent"] == 30.0
    assert distribution["360"]["configured_percent"] == 20.0
    assert distribution["nikon"]["actual_percent"] == pytest.approx(50.0, abs=4.0)
    assert distribution["iphone"]["actual_percent"] == pytest.approx(30.0, abs=4.0)
    assert distribution["360"]["actual_percent"] == pytest.approx(20.0, abs=4.0)
    assert sum(row["actual_percent"] for row in distribution.values()) == pytest.approx(100.0)


def test_fixed_camera_recipe_is_subject_anchored_and_low_aggression() -> None:
    motion = _ken_burns_motion(7, target_x=0.68, target_y=0.41, allow_static=False, force_close=True)

    assert motion["target_x"] == pytest.approx(0.68)
    assert motion["target_y"] == pytest.approx(0.41)
    assert max(motion["zoom_start"], motion["zoom_end"]) <= FIXED_CAMERA_SAFE_ZOOM_MAX
    assert motion["enforce_top_edge"] is False


def test_renderer_handles_full_frame_start_without_dividing_by_zero() -> None:
    graph = _ken_burns_filter(
        {
            "type": "ken_burns",
            "movement": "zoom_in_center",
            "lock_target": True,
            "target_x": 0.68,
            "target_y": 0.41,
            "zoom_start": 1.0,
            "zoom_end": 1.2,
            "pan_x_start": 0.5,
            "pan_x_end": 0.5,
            "pan_y_start": 0.5,
            "pan_y_end": 0.5,
            "enforce_top_edge": False,
        },
        "youtube",
        4.0,
    )

    assert "if(gt(" in graph
    assert "nan" not in graph.lower()
