"""Show framing: whole-picture reframes centred on the host's face, and camera moves."""
import subprocess

import numpy as np
import pytest

from core.ffmpeg import tool_status
from core.show_edit import CHOE, plan_show
from core.show_framing import apply_focus, focus_for, frame_centre
from core.show_render import render_show

FPS = 30


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def test_a_close_shot_centres_the_face_with_headroom_and_stays_in_the_picture():
    cx, cy = frame_centre((0.7, 0.3), 2.0)       # window = half the picture
    assert cx == pytest.approx(0.7)
    assert cy == pytest.approx(0.3 + 0.12 * 0.5)  # face at 38 % of the window height
    cx, cy = frame_centre((0.95, 0.05), 2.0)     # near a corner: the window stays inside
    assert (cx, cy) == (pytest.approx(0.75), pytest.approx(0.25))
    assert frame_centre((0.9, 0.2), 1.0) == (0.5, 0.42)  # a wide shot shows everything


def test_shots_take_the_face_of_their_own_seconds_or_the_nearest_one():
    track = [(1.0, 0.6, 0.4, 0.1), (1.5, 0.62, 0.4, 0.1), (2.0, 0.64, 0.4, 0.1), (9.0, 0.2, 0.3, 0.1)]
    assert focus_for(0.9, 2.2, track) == (0.62, 0.4)
    assert focus_for(3.0, 4.0, track) == (0.64, 0.4)    # no face inside: nearest within 4 s
    assert focus_for(20.0, 21.0, track) is None
    plan = {"fps": FPS, "shots": [{"start_frame": 30, "end_frame": 66, "zoom": 2.0}, {"start_frame": 600, "end_frame": 630, "zoom": 1.0}]}
    apply_focus(plan, track)
    assert plan["shots"][0]["focus"][0] == pytest.approx(0.62, abs=0.01)
    assert plan["framing"]["shots_with_face"] == 1


def test_long_shots_move_the_camera_short_ones_do_not():
    # Phrases of six words (2.4 s) with pauses between them, like real talk.
    words = [{"word": f"w{i}", "start_sec": i * 0.4 + (i // 6) * 0.8, "end_sec": i * 0.4 + (i // 6) * 0.8 + 0.32} for i in range(150)]
    plan = plan_show({"segments": [{"words": words}]}, duration=82.0, style=CHOE)
    moving = [shot for shot in plan["shots"] if shot.get("zoom_end")]
    assert moving and all(shot["end"] - shot["start"] >= CHOE.move_min_sec - 0.05 for shot in moving)
    assert any(shot["zoom"] > shot["zoom_end"] for shot in moving)     # a reveal opens up
    assert any(shot["zoom"] < shot["zoom_end"] for shot in moving)     # a push goes in


def _source_with_square(ffmpeg: str, path, seconds: float = 2.0):
    # Grey picture with a white square centred at (0.75, 0.3).
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x404040:s=640x360:r={FPS}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
                    "-vf", "drawbox=x=456:y=84:w=48:h=48:color=white:t=fill", "-c:v", "libx264", "-crf", "10",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def _frames(ffmpeg: str, path, w: int, h: int) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w)


def test_the_render_centres_its_zoom_on_the_focus(tmp_path):
    ffmpeg = _ffmpeg()
    _source_with_square(ffmpeg, tmp_path / "in.mp4")
    plan = {"fps": FPS, "shots": [{"start_frame": 0, "end_frame": 30, "zoom": 2.0, "focus": [0.75, 0.3]}]}
    render_show(tmp_path / "in.mp4", plan, tmp_path / "out.mp4", ffmpeg=ffmpeg, width=320, height=180)
    frame = _frames(ffmpeg, tmp_path / "out.mp4", 320, 180)[15]
    assert frame[90, 160] > 200                 # the square is in the middle of the shot
    bright = np.argwhere(frame > 200)
    assert 40 <= bright[:, 1].ptp() <= 56       # 48 px square at 320 px wide after a 2x zoom of 640 px: ~48


def test_a_camera_move_changes_the_framing_during_the_shot(tmp_path):
    ffmpeg = _ffmpeg()
    _source_with_square(ffmpeg, tmp_path / "in.mp4")
    plan = {"fps": FPS, "shots": [{"start_frame": 0, "end_frame": 45, "zoom": 1.0, "zoom_end": 2.0, "focus": [0.75, 0.3]}]}
    render_show(tmp_path / "in.mp4", plan, tmp_path / "out.mp4", ffmpeg=ffmpeg, width=320, height=180)
    frames = _frames(ffmpeg, tmp_path / "out.mp4", 320, 180)
    first, last = (np.argwhere(frames[i] > 200)[:, 1].ptp() for i in (0, len(frames) - 1))
    assert last > first * 1.6                   # the square grows as the camera pushes in


def test_faces_in_photos_on_the_wall_are_not_the_host():
    from core.show_framing import steady
    host = [(t * 0.5, 0.6 + 0.01 * (t % 3), 0.3, 0.14) for t in range(40)]
    photos = [(t * 0.5 + 0.25, 0.9, 0.2, 0.06) for t in range(10)]          # small faces on the fridge
    stray = [(10.0, 0.1, 0.5, 0.15)]                                          # one false detection far away
    kept = steady(sorted(host + photos + stray))
    assert len(kept) == len(host)
    assert all(abs(x - 0.6) < 0.05 for _, x, _, _ in kept)


def test_the_face_position_on_screen_follows_the_reframe():
    from core.show_framing import face_on_screen
    plan = {"fps": 30, "shots": [{"start_frame": 0, "end_frame": 30, "zoom": 1.0, "focus": [0.5, 0.42], "face": [0.7, 0.3]},
                                 {"start_frame": 30, "end_frame": 60, "zoom": 2.0, "focus": [0.7, 0.36], "face": [0.7, 0.3]},
                                 {"start_frame": 60, "end_frame": 90, "zoom": 1.0}]}
    assert face_on_screen(plan, 0.5) == pytest.approx((0.7, 0.3))
    x, y = face_on_screen(plan, 1.5)                    # close shot: the face is near the middle, high
    assert x == pytest.approx(0.5) and y == pytest.approx(0.38)
    assert face_on_screen(plan, 2.5) is None             # no face found in that shot


def test_steam_placed_on_the_picture_follows_each_shot_s_framing():
    from core.show_framing import steam_on_screen
    plan = {"fps": 30, "shots": [{"start_frame": 0, "end_frame": 60, "zoom": 1.0},
                                 {"start_frame": 60, "end_frame": 120, "zoom": 2.0, "focus": [0.4, 0.7]},
                                 {"start_frame": 120, "end_frame": 180, "zoom": 2.0, "focus": [0.8, 0.2]}]}
    item = {"steam": [0.35, 0.75], "anchor": [0.3, 0.75, 0.1, 0.1], "at": 1.0, "duration": 4.5, "scale": 1.0}
    pieces = steam_on_screen(plan, item)
    assert [(p["at"], p["duration"]) for p in pieces] == [(1.0, 1.0), (2.0, 2.0)]     # the third shot does not show the mug
    assert pieces[0]["steam"] == [0.35, 0.75] and pieces[0]["scale"] == 1.0
    assert pieces[1]["steam"] == [pytest.approx(0.4), pytest.approx(0.6)] and pieces[1]["scale"] == 2.0
    assert pieces[1]["anchor"][2] == pytest.approx(0.2)                               # the mug looks twice as big


def test_no_steam_when_the_mug_is_cut_off_by_the_frame():
    # A close shot that keeps the rim at its bottom edge but not the mug: the steam
    # would float over whatever is there.
    from core.show_framing import steam_on_screen
    plan = {"fps": 30, "shots": [{"start_frame": 0, "end_frame": 60, "zoom": 2.0, "focus": [0.4, 0.47]},
                                 {"start_frame": 60, "end_frame": 120, "zoom": 1.0}]}
    item = {"steam": [0.35, 0.70], "anchor": [0.3, 0.70, 0.1, 0.2], "at": 0.0, "duration": 4.0, "scale": 1.0}
    assert [(p["at"], p["duration"]) for p in steam_on_screen(plan, item)] == [(2.0, 2.0)]


def test_without_opencv_face_models_the_show_is_framed_without_faces(tmp_path, monkeypatch):
    # A packaged app once lacked OpenCV's data: every render failed in detectMultiScale.
    import cv2

    from core import gags
    from core.show_framing import face_detectors, face_track
    from core.show_proposals import face_intervals
    monkeypatch.setattr(cv2.data, "haarcascades", str(tmp_path) + "/")
    assert face_detectors() == []
    assert face_track(tmp_path / "missing.mp4") == []                         # no crash, no faces
    resources = [{"kind": "video", "intervals": [{"kind": "still", "thumb": str(tmp_path / "t.jpg"), "start": 0, "end": 9}]}]
    assert face_intervals(resources) == []
    assert gags.face_side(tmp_path / "missing.mp4") == "right"
