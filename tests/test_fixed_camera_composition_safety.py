"""Composition safety for automatic fixed-camera Ken Burns shots."""
import pytest
from core.fixed_camera_moves import CATALOG, fixed_camera_motion
from core.stages.export import _ken_burns_filter


@pytest.mark.parametrize("kind", CATALOG)
def test_no_person_bounds_never_trigger_aggressive_crop(kind):
    for style in ("tranquilo", "animado", "frenetico"):
        move = fixed_camera_motion(3, .6, .45, preferred=kind, style=style, duration=7)
        assert max(move["zoom_start"],move["zoom_end"]) <= 1.15 + 1e-6
        assert max(move["zoom_start"],move["zoom_end"]) >= 1.


@pytest.mark.parametrize("kind", ["zoom_in_center", "zoom_out_center", "subject_reframe", "subject_release"])
@pytest.mark.parametrize("box", [
    {"x1":.35,"y1":.10,"x2":.60,"y2":.80},
    {"x1":.05,"y1":.25,"x2":.48,"y2":.90},
    {"x1":.42,"y1":.08,"x2":.83,"y2":.89},
    {"x1":.38,"y1":.23,"x2":.62,"y2":.77},
])
def test_zoom_fits_full_person_and_has_headroom_at_every_frame(kind,box):
    tx=(box["x1"]+box["x2"])/2
    ty=(box["y1"]+box["y2"])/2
    move=fixed_camera_motion(7,tx,ty,preferred=kind,subject_box=box,duration=7,confidence=1)
    assert max(move["zoom_start"],move["zoom_end"]) <= 1.25 + 1e-6
    for frame in range(101):
        t=frame/100
        z=move["zoom_start"]+(move["zoom_end"]-move["zoom_start"])*t
        px=max(0.,min(1.,(z*move["target_x"]-.5)/(z-1))) if z>1.001 else .5
        py=max(0.,min(1.,(z*move["target_y"]-.5)/(z-1))) if z>1.001 else .5
        corners=[(z*box["x1"]-(z-1)*px,z*box["x2"]-(z-1)*px),
                 (z*box["y1"]-(z-1)*py,z*box["y2"]-(z-1)*py)]
        assert corners[0][0] >= .045-1e-3 and corners[0][1] <= .955+1e-3
        assert corners[1][0] >= .075-1e-3 and corners[1][1] <= .955+1e-3


def test_fixed_camera_disable_motion_keeps_static_output():
    from core.stages.export import _motion_filter
    move=fixed_camera_motion(2,.5,.5,subject_box={"x1":.3,"y1":.1,"x2":.7,"y2":.8},preferred="zoom_in")
    shot={"motion":move,"motion_enabled":True,"source_path":"flat.mp4"}
    assert _motion_filter(shot,"youtube",4)
    assert _motion_filter({**shot,"motion_enabled":False},"youtube",4) is None


def test_cache_contains_person_rectangle_when_available():
    from pathlib import Path
    source=Path("core/operator_avoidance.py").read_text()
    assert 'sample["subject_" + axis]' in source
    assert '"x1": max(0.0' in source
