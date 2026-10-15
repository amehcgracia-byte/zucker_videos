"""Show Studio's edit on the automatic Show: clocks, framing and one result per effect."""
import pytest

from core import show_bridge

# Three kept shots at 30 fps: source 10-12 s, 15-16 s, 20-23 s (edited 0-2, 2-3, 3-6).
PLAN = {"fps": 30, "edited_duration": 6.0, "shots": [
    {"start_frame": 300, "end_frame": 360, "zoom": 1.0, "focus": [0.5, 0.5]},
    {"start_frame": 450, "end_frame": 480, "zoom": 2.0, "focus": [0.75, 0.25]},
    {"start_frame": 600, "end_frame": 690, "zoom": 1.0, "zoom_end": 1.5, "focus": [0.5, 0.5]}]}


def test_a_source_interval_reaches_only_the_moments_the_edit_kept():
    pieces = show_bridge.source_pieces(PLAN, 11.0, 21.0)
    assert pieces == [(pytest.approx(1.0), pytest.approx(2.0), 11.0, 12.0), (pytest.approx(2.0), pytest.approx(3.0), 15.0, 16.0),
                      (pytest.approx(3.0), pytest.approx(4.0), 20.0, 21.0)]
    assert show_bridge.source_pieces(PLAN, 12.5, 14.5) == []                    # entirely cut out
    assert show_bridge.edited_to_source(PLAN, 2.5) == pytest.approx(15.5)
    assert show_bridge.edited_to_source(PLAN, 7.0) is None


def test_each_frame_shows_the_window_the_render_crops():
    assert show_bridge.window_at(PLAN, 0.5) == pytest.approx((0.0, 0.0, 1.0))
    x0, y0, w = show_bridge.window_at(PLAN, 2.5)                                # zoom 2 on the upper right
    assert w == pytest.approx(0.5) and x0 == pytest.approx(0.5) and y0 == pytest.approx(0.0)
    _, _, start = show_bridge.window_at(PLAN, 3.0)
    _, _, end = show_bridge.window_at(PLAN, 6.0 - 1 / 30)
    assert start == pytest.approx(1.0) and end == pytest.approx(1 / 1.5, abs=1e-3)   # a slow push-in, frame by frame


def test_studio_pixels_follow_the_framing():
    # The centre of the Studio picture, in a window zoomed 2x on its upper right quarter.
    x, y, k = show_bridge.to_output((640, 360), (0.5, 0.0, 0.5), 1920, 1080)
    assert (x, y) == pytest.approx((0.0, 1080.0)) and k == pytest.approx(1920 / 1280 * 2)


def test_every_effect_gets_a_result_and_nothing_is_replaced_silently():
    recipe = {"version": 2, "duration": 44, "events": [
        {"id": "base", "type": "video", "start": 0, "end": 44},
        {"id": "zoom1", "type": "zoom", "start": 4, "end": 6.6, "scale": 1.4},
        {"id": "finger1", "type": "finger", "start": 16, "end": 19.5, "pathMode": "reviewed"},
        {"id": "ink", "type": "finger", "start": 0, "end": 44, "autoHands": True, "enabled": False},
        {"id": "cap", "type": "caption", "start": 1, "end": 2, "text": "hi"},
        {"id": "gates", "type": "image", "start": 26.04, "end": 26.52, "maskAsset": "bill"},
        {"id": "lost", "type": "spiral", "start": 100, "end": 101, "x": 0.5, "y": 0.5}]}
    drawers = {"spiral": lambda event: (None, ""), "finger": lambda event: (None, "")}
    layers, results = show_bridge.plan_effects(recipe, section="workshop", source_start=0.0, plan=PLAN, drawers=drawers)
    report = {item["id"]: item for item in results}
    assert report["zoom1"]["status"] == "superseded"                            # the automatic framing is kept
    assert report["cap"]["status"] == "superseded"                              # the automatic captions are kept
    assert report["ink"]["status"] == "disabled"
    assert report["gates"]["status"] == "unsupported" and "mask" in report["gates"]["detail"]
    assert report["lost"]["status"] == "failed" and "cut" in report["lost"]["detail"]   # its moment was cut from the edit
    assert "base" not in report
    unknown = show_bridge.plan_effects({"events": [{"id": "x", "type": "part", "start": 10, "end": 11}]}, section="workshop",
                                       source_start=0.0, plan=PLAN)[1][0]
    assert unknown["status"] == "unsupported" and "part" in unknown["detail"]


def test_the_drawing_numbers_are_the_preview_s_own():
    from core import show_bridge_draw as draw
    # Reference values from tools/qa/show_paint_tools.js run in Node.
    assert [round(draw.js_hash(n), 12) for n in (0, 1, 7, 123, 4096, 99999)] == [
        0.392125141341, 0.919709953014, 0.822706321487, 0.572265593801, 0.916364998324, 0.657392568653]
    points = draw.resample([(0, 0), (10, 0), (10, 5)], 1.5)
    assert len(points) == 11 and points[3] == pytest.approx((4.5, 0)) and points[-1] == pytest.approx((10, 5))
    runs = [[[0, 100, 200], [5, 110, 210], [10, 120, 230]], [[40, 500, 500], [41, 505, 505]]]
    assert draw.timed_trails(runs, 8, 25, 1280, 720) == [([(100, 200), (110, 210)], 1)]
    assert draw.timed_trails(runs, 150, 25, 1280, 720) == []


def test_ink_strokes_and_steam_paint_only_where_they_should():
    import numpy as np

    from core import show_bridge_draw as draw
    half = (0.0, 0.0, 0.5)                                                     # a 640x360 frame showing all of Studio's picture
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    draw.pigment(frame, [(200, 200), (400, 240)], half, brush="crayon", color="#f2bd52", width=5)
    ys, xs = np.nonzero(frame.sum(axis=2))
    assert len(xs) and 90 <= xs.min() and xs.max() <= 210 and 85 <= ys.min() and ys.max() <= 135
    assert frame[ys, xs].mean(axis=0)[0] > frame[ys, xs].mean(axis=0)[2]          # warm crayon colour
    lines = np.zeros((360, 640, 3), dtype=np.uint8)
    draw.stroke(lines, [(300, 50), (400, 50)], (0.0, 0.0, 1.0), brush="marker", color="#ffffff", width=6)
    assert lines[50, 350].max() > 150 and lines[80, 350].max() == 0
    steam = np.zeros((360, 640, 3), dtype=np.uint8)
    draw.coffee(steam, (640, 600), half, elapsed=2.0, radius=0.035)
    ys, xs = np.nonzero(steam.sum(axis=2))
    assert len(ys) and ys.max() <= 330 and ys.min() < 290                       # rising above the cup
    assert draw.visible(steam, (640, 600), half) and not draw.visible(steam, (640, 600), (700.0, 0.0, 1.0))


# Reference values from the editor's finger() and spiral() run in Node (strokes captured).
TRACKS = [[[400, 824, 180], [401, 830, 188], [402, 828, 191], [410, 800, 200], [430, 780, 260]], [[600, 500, 500], [640, 520, 540]]]


def test_fingertip_paths_and_spirals_match_the_preview():
    from core import show_bridge_draw as draw
    finger = {"start": 16, "end": 19.5, "pathMode": "reviewed", "drawMode": "trail", "contentShift": 0}
    kind, points = draw.finger_drawing(finger, 16.5, TRACKS)
    assert kind == "stroke" and len(points) == 13 and points[3] == pytest.approx((824.5, 192.125)) and points[-1] == pytest.approx((798, 206))
    _, shifted = draw.finger_drawing({**finger, "contentShift": 0.2}, 16.5, TRACKS)
    assert shifted[0] == pytest.approx((817.5, 194.375)) and shifted[-1] == pytest.approx((793, 221))
    _, circle = draw.finger_drawing({**finger, "drawMode": "circle", "radius": 0.08}, 16.9, TRACKS)
    assert len(circle) == 117 and circle[7] == pytest.approx((843.389, 251.4347), abs=1e-3)
    assert draw.finger_drawing({"start": 1, "end": 3, "pathMode": "manual", "points": [[30, 100, 100]]}, 1.5, []) == ("dot", (100, 100))
    assert draw.finger_drawing(finger, 18.0, TRACKS) is None                          # between detections: nothing invented
    spiral = {"start": 29.5, "end": 36.7, "x": 0.6, "y": 0.36, "radius": 0.14, "turns": 3, "direction": "in"}
    points = draw.spiral_points(spiral, 30.4)
    assert len(points) == 111 and points[0] == pytest.approx((858.7651, 303.0445), abs=1e-3) and points[50] == pytest.approx((769.6824, 181.3273), abs=1e-3)
    out = {**spiral, "direction": "out"}
    assert len(draw.spiral_points(out, 36.6)) == 221 and draw.spiral_points(out, 36.6)[220] == pytest.approx((675.4903, 219.1675), abs=1e-3)
    assert draw.spiral_opacity(out, 36.6) == pytest.approx(0.2857, abs=1e-4)


def test_the_detected_finger_ink_hides_hand_placed_drawings_as_in_studio():
    recipe = {"events": [
        {"id": "ink", "type": "finger", "autoHands": True, "start": 0, "end": 15.5},
        {"id": "under", "type": "finger", "pathMode": "reviewed", "start": 10.2, "end": 11.5},
        {"id": "half", "type": "spiral", "start": 15.0, "end": 16.0, "x": 0.5, "y": 0.5},
        {"id": "kept-only-under", "type": "spiral", "start": 11.0, "end": 13.0, "x": 0.5, "y": 0.5},
        {"id": "steam", "type": "coffee", "start": 0, "end": 2, "parent": "pip-1"}]}
    drawers = {kind: (lambda event: (None, "")) for kind in ("finger", "spiral", "coffee")}
    layers, results = show_bridge.plan_effects(recipe, section="workshop", source_start=0.0, plan=PLAN, drawers=drawers)
    report = {item["id"]: item for item in results}
    assert report["under"]["status"] == "disabled" and "automatic finger ink" in report["under"]["detail"]
    assert report["half"]["status"] == "applied" and "hidden for 0.50 s" in report["half"]["detail"]
    assert report["kept-only-under"]["status"] == "disabled"                    # its kept second is all under the ink
    assert report["steam"]["status"] == "unsupported"
    half = next(layer for layer in layers if layer.effect_id == "half")
    assert half.section_time(2.2) is None and half.section_time(2.7) == pytest.approx(15.7)  # source 15.2 under the ink; 15.7 free
    serious = show_bridge.plan_effects({**recipe, "mode": "serious"}, section="workshop", source_start=0.0, plan=PLAN, drawers=drawers)[1]
    assert {item["status"] for item in serious if item["id"] != "steam"} == {"disabled"}


def test_studio_drawings_follow_each_frame_s_framing():
    import numpy as np
    seen = []
    layer = show_bridge.Layer("x", "spiral", [(2.0, 3.0, 15.0)], {}, lambda frame, t, view: seen.append((t, view)))
    drawings = show_bridge.StudioDrawings(PLAN, [layer])
    assert drawings.active(2.5) and not drawings.active(1.0)
    drawings.draw(np.zeros((1080, 1920, 3), dtype=np.uint8), 2.5)
    (t, (ox, oy, k)), = seen
    assert t == pytest.approx(15.5) and (ox, oy) == pytest.approx((640.0, 0.0)) and k == pytest.approx(3.0)


def test_a_missing_resource_is_reported_plainly_and_nothing_is_drawn_instead(tmp_path, monkeypatch):
    from core.project import create_project
    from core.show_studio_store import StudioStore
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Show", str(tmp_path / "Studio.zuckervid"))
    StudioStore(project.folder).save_composition({"version": 2, "mode": "mixed", "duration": 44.0, "events": [
        {"id": "base", "type": "video", "start": 0, "end": 44, "source": "original", "trackId": "base"},
        {"id": "ink", "type": "finger", "name": "Automatic finger ink", "autoHands": True, "start": 0, "end": 44, "parent": "base"},
        {"id": "cup", "type": "coffee", "name": "Coffee", "start": 0, "end": 44, "parent": "base", "visibleKeys": []}]}, revision=0)
    plan = {"fps": 30, "shots": [{"start_frame": 466 * 30, "end_frame": 476 * 30, "zoom": 1.0}]}   # source 466-476 s: the workshop's first 10 s
    drawings, report = show_bridge.studio_layers(project.folder, plan)
    assert not drawings and {item["id"]: item["status"] for item in report} == {"ink": "failed", "cup": "failed"}
    warnings = show_bridge.studio_warnings(report)
    assert any("automatic-hands" in line and "missing" in line for line in warnings)
    assert any("not marked visible" in line for line in warnings)
    before = show_bridge.studio_fingerprint(project.folder)
    StudioStore(project.folder).save_analysis("automatic-hands", {"fps": 25, "width": 1280, "height": 720, "frames": 50,
                                                                 "detected_frames": 2, "runs": [[[0, 10, 10], [1, 20, 20]]]})
    assert show_bridge.studio_fingerprint(project.folder) != before                # previews are made again
    drawings, report = show_bridge.studio_layers(project.folder, plan)
    assert {item["id"]: item["status"] for item in report}["ink"] == "applied" and drawings
