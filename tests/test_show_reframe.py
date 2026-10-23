"""Zooms aimed at the face found in each shot; no face, no zoom."""
import subprocess

import numpy as np
import pytest

from core import show_reframe
from core.ffmpeg import tool_status
from core.show_framing import frame_centre


def _bright(rgb: np.ndarray):
    """A stand-in face detector: the bright square, if any."""
    ys, xs = np.nonzero(rgb[..., 0] > 200)
    if not len(xs):
        return []
    h, w = rgb.shape[:2]
    return [(xs.mean() / w, ys.mean() / h, (xs.max() - xs.min() + 1) / w)]


def test_the_face_of_a_shot_is_the_middle_of_its_frames_or_none():
    def frame(x):
        rgb = np.zeros((90, 160, 3), np.uint8)
        if x is not None:
            rgb[30:46, x:x + 16] = 255
        return rgb
    face = show_reframe.shot_face([frame(70), frame(72), frame(74)], _bright)
    assert face == pytest.approx((79.5 / 160, 37.5 / 90), abs=0.005)                       # the middle one
    assert show_reframe.shot_face([frame(70), frame(None), frame(72)], _bright) is not None   # one frame missed
    assert show_reframe.shot_face([frame(None), frame(None), frame(72)], _bright) is None     # mostly no face
    assert show_reframe.shot_face([frame(10), frame(80), frame(140)], _bright) is None        # jumps: an old cut


def test_reaim_aims_zooms_at_the_face_and_opens_up_shots_without_one(tmp_path, monkeypatch):
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    # Two seconds with a "face" low on the left, then two seconds of an empty picture.
    source = tmp_path / "talk.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180:r=30:d=4",
                    "-vf", "drawbox=x=60:y=100:w=40:h=40:color=white:t=fill:enable='lt(t,2)'",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", str(source)], check=True)
    monkeypatch.setattr(show_reframe, "detector", lambda: _bright)
    plan = {"fps": 30, "shots": [
        {"start_frame": 0, "end_frame": 30, "zoom": 1.65, "focus": [0.5, 0.42]},
        {"start_frame": 30, "end_frame": 60, "zoom": 1.0, "focus": [0.5, 0.42]},
        {"start_frame": 70, "end_frame": 110, "zoom": 1.3, "zoom_end": 1.45, "focus": [0.5, 0.42], "face": [0.5, 0.3]},
        {"start_frame": 80, "end_frame": 100, "zoom": 1.65, "focus": [0.7, 0.2], "aimed": True},
    ]}
    counts = show_reframe.reaim(plan, source)
    assert counts == {"aimed": 1, "unzoomed": 1, "kept": 1, "wide": 1, "eased": 0}
    first, wide, empty, chosen = plan["shots"]
    assert first["face"] == pytest.approx([80 / 320, 120 / 180], abs=0.01)
    assert first["focus"] == pytest.approx(list(frame_centre(tuple(first["face"]), 1.65)), abs=1e-3)
    assert wide["zoom"] == 1.0 and "face" not in wide
    assert empty["zoom"] == 1.0 and "zoom_end" not in empty and "face" not in empty
    assert chosen["focus"] == [0.7, 0.2]                                  # aimed by hand: left alone
    assert plan["framing"]["reaimed"]["aimed"] == 1


def test_a_face_that_moves_keeps_its_zoom_eased_just_enough():
    leaning = [(0.6, 0.3), (0.62, 0.42), (0.61, 0.5)]                    # the person leans in
    face, zoom = show_reframe.fitted(leaning, 1.65)
    assert zoom == 1.65 and face == pytest.approx((0.61, 0.4))           # aimed between, all of it in frame
    face, zoom = show_reframe.fitted([(0.3, 0.3), (0.3, 0.4), (0.3, 0.8)], 1.65)
    assert zoom == 1.4                                                    # eased off to keep it in
    assert show_reframe.fitted([(0.1, 0.3), (0.9, 0.3), (0.5, 0.3)], 1.65) is None   # an old cut: no zoom follows that
