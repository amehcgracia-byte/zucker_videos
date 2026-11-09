"""Regression tests for optional Stage motion and safe automatic closeups."""
from pathlib import Path

from core.stage_performers import subject_crop


def test_automatic_musician_crop_never_exceeds_50_percent():
    person = {
        "id": "guitarist",
        "head_box": [0.46, 0.24, 0.50, 0.32],
        "body_box": [0.43, 0.30, 0.55, 0.65],
    }
    crop = subject_crop(person, [person])
    assert 1 <= crop["zoom"] <= 1.5
    assert 0 <= crop["x"] <= 1
    assert 0 <= crop["y"] <= 1


def test_shot_save_and_render_have_separate_motion_off_paths():
    edit = Path("server/edit_timeline.py").read_text()
    render = Path("core/stages/export.py").read_text()
    assert "if not isinstance(movement,bool)" in edit
    assert "s['motion_enabled']=movement" in edit
    assert "if segment.get('motion_enabled') is False:" in render
    assert "runtime_motion_enabled=movement" in edit


def test_operator_avoidance_auto_zoom_is_capped():
    render = Path("core/stages/export.py").read_text()
    assert "zoom = max(1.0, min(1.5, zoom))" in render
