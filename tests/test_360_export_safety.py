from __future__ import annotations

from pathlib import Path

from core.stages.export import (
    SPHERICAL_EXPORT_MOTION_MODE,
    _export_source_filter,
    _spherical_motion_cache_recipe,
    _static_export_segment,
)


def test_final_360_export_keeps_authored_pose_but_disables_runtime_motion() -> None:
    segment = {
        "projection": "equirect",
        "spherical_shot": {
            "type": "recorded_move",
            "yaw": 125.0,
            "pitch": -18.0,
            "fov": 92.0,
            "curve": [{"t": 0.0, "yaw": 100.0, "pitch": -18.0, "fov": 92.0}],
        },
    }

    rendered = _static_export_segment(segment)

    assert SPHERICAL_EXPORT_MOTION_MODE == "static"
    assert rendered["spherical_shot"]["yaw"] == 125.0
    assert rendered["spherical_shot"]["pitch"] == -18.0
    assert rendered["spherical_shot"]["fov"] == 92.0
    assert rendered["spherical_shot"]["runtime_motion_enabled"] is False

    graph = _export_source_filter(
        {"projection": "equirect"},
        rendered["spherical_shot"],
        duration=4.0,
        command_path=Path("/tmp/should-not-be-created.sendcmd"),
    )
    assert "sendcmd=" not in graph
    assert "yaw=125.000" in graph
    assert "pitch=-18.000" in graph
    assert "h_fov=92.000" in graph


def test_static_export_mode_invalidates_spherical_segment_cache_recipe() -> None:
    recipe = _spherical_motion_cache_recipe()

    assert recipe["export_motion_mode"] == "static"
    assert recipe["version"] >= 22
