"""Regression tests for per-shot motion without changing allowed zoom ranges."""
from pathlib import Path


def test_selected_shot_movement_is_boolean_and_scoped():
    edit = Path("server/edit_timeline.py").read_text()
    assert "if not isinstance(movement,bool)" in edit
    assert "s['motion_enabled']=movement" in edit
    assert "runtime_motion_enabled=movement" in edit
    assert "'motion_enabled','flat_framing'" in edit


def test_export_respects_movement_off_but_keeps_existing_zoom_ranges():
    render = Path("core/stages/export.py").read_text()
    assert "if segment.get('motion_enabled') is False:" in render
    assert "zoom = max(1.0, min(1.6, zoom))" in render


def test_individual_360_move_is_bounded():
    edit = Path("server/edit_timeline.py").read_text()
    assert "pose['hold_motion_rate_deg_per_sec'] = (0.3 if i % 2 else -0.3)" in edit
    assert "pose.pop('hold_motion_rate_deg_per_sec',None)" in edit
