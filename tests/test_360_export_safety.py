from __future__ import annotations

from core.stages.export import (
    SPHERICAL_EXPORT_MOTION_MODE,
    _export_source_filter,
    _v360_motion_commands,
)


def test_packaged_360_export_uses_static_projection_safety_mode():
    assert SPHERICAL_EXPORT_MOTION_MODE == "static"


def test_static_export_segment_does_not_emit_runtime_v360_commands():
    shot = {
        "type": "singer",
        "yaw": 120.0,
        "pitch": -18.0,
        "fov": 95.0,
        "drift_yaw_fraction": 0.04,
    }
    # The planner math remains available for a later validated motion path,
    # but the packaged export must not feed dynamic commands to v360.
    assert _v360_motion_commands({**shot, "runtime_motion_enabled": False}, 6.0) == []
    graph = _export_source_filter(
        {"projection": "equirect"},
        {**shot, "runtime_motion_enabled": False},
        duration=6.0,
    )
    assert "sendcmd=f=" not in graph
