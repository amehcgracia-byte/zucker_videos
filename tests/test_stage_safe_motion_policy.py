"""Regression tests for per-shot motion without changing allowed zoom ranges."""
from pathlib import Path


def test_selected_shot_movement_is_boolean_and_scoped():
    edit = Path("server/edit_timeline.py").read_text()
    assert "if not isinstance(movement,bool)" in edit
    assert "s['motion_enabled']=movement" in edit
    assert "pose['runtime_motion_enabled'] = movement" in edit
    assert "'motion_enabled','flat_framing'" in edit


def test_export_respects_movement_off_but_keeps_existing_zoom_ranges():
    render = Path("core/stages/export.py").read_text()
    assert "if segment.get('motion_enabled') is False:" in render
    assert "zoom = max(1.0, min(1.6, zoom))" in render


def test_individual_360_move_stops_after_the_authored_pan():
    from core.stages.export import _v360_motion_at
    pose = {"yaw": 30, "pitch": 0, "fov": 90, "manual_yaw_delta_deg": 8}
    assert _v360_motion_at(pose, 5, 0)[0] == 30
    assert _v360_motion_at(pose, 5, 3)[0] == 38
    assert _v360_motion_at(pose, 5, 5)[0] == 38
