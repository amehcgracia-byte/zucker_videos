"""Camera moves between framings: eased, the same in the render, in a scene preview and for stickers."""
import subprocess

import numpy as np
import pytest

from core.ffmpeg import tool_status
from core.show import window_plan
from core.show_bridge import window_at
from core.show_camera import MOVE_SEC, camera_at, default_moves, glide, window
from core.show_render import render_show

FPS = 30


def _plan():
    return {"fps": FPS, "shots": [
        {"start_frame": 0, "end_frame": 40, "zoom": 1.0, "focus": [0.5, 0.42], "cut_before": "start"},
        {"start_frame": 40, "end_frame": 100, "zoom": 1.65, "focus": [0.3, 0.35], "cut_before": "pace", "move": "glide"},
        {"start_frame": 130, "end_frame": 170, "zoom": 1.3, "focus": [0.6, 0.4], "cut_before": "silence"},
    ]}


def test_a_gliding_shot_starts_where_the_last_ended_and_eases_to_its_framing():
    plan = _plan()
    origin, frames, skip = glide(plan, 1)
    assert origin == (1.0, 0.5, 0.42) and frames == round(MOVE_SEC * FPS) and skip == 0
    zooms = [camera_at(plan, n)[0] for n in range(40, 40 + frames + 5)]
    assert zooms[0] == pytest.approx(1.0)
    assert all(b >= a for a, b in zip(zooms, zooms[1:]))                  # one way, no overshoot
    assert zooms[1] - zooms[0] < zooms[frames // 2 + 1] - zooms[frames // 2]  # slow out, fast middle
    assert zooms[frames] == pytest.approx(1.65) and zooms[-1] == pytest.approx(1.65)
    assert camera_at(plan, 39) == (1.0, 0.5, 0.42)
    assert camera_at(plan, 100) == (1.3, 0.6, 0.4)                         # a cut stays a cut


def test_a_scene_cut_out_on_its_own_keeps_the_move_exactly():
    plan = _plan()
    plan["shots"][1]["zoom_end"] = 1.8
    part = window_plan(plan, 1.5, 3.0)                                     # starts inside the move
    first = int(1.5 * FPS)
    for n in range(first, int(3.0 * FPS)):
        assert camera_at(part, n - first) == pytest.approx(camera_at(plan, n), abs=1e-4)
    assert window_at(part, 0.2) == pytest.approx(window_at(plan, 1.7), abs=1e-4)


def test_moves_replace_some_cuts_where_the_picture_runs_on():
    shots = [{"start_frame": i * 45, "end_frame": i * 45 + 45, "zoom": z, "focus": [0.5, 0.4], "cut_before": cut}
             for i, (z, cut) in enumerate([(1.0, "start"), (1.3, "pace"), (1.65, "pace"), (1.0, "pace"),
                                            (1.3, "silence"), (1.65, "pace"), (1.0, "pace")])]
    shots[6]["move"] = "cut"                                               # chosen by the person: kept
    plan = {"fps": FPS, "shots": shots}
    assert default_moves(plan) == 2
    assert [shot.get("move") for shot in shots] == [None, None, "glide", None, None, "glide", "cut"]


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def test_the_render_moves_the_camera_as_planned(tmp_path):
    ffmpeg = _ffmpeg()
    # A left-to-right ramp: each column's grey says where it is on the source picture.
    source = tmp_path / "ramp.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "nullsrc=s=640x360:r=30:d=6",
                    "-f", "lavfi", "-i", "sine=f=440:d=6", "-vf", "geq=lum='16+220*X/W':cb=128:cr=128",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    plan = _plan()
    output = render_show(source, plan, tmp_path / "moves.mp4", ffmpeg=ffmpeg, width=320, height=180)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(output), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    frames = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 180, 320).astype(float)
    assert len(frames) == 140
    for n in range(0, 140, 3):
        row = frames[n, 80:100].mean(axis=0)
        left, right = row[2] / 255, row[-3] / 255                          # grey is full range
        x0, _, w = window(*camera_at(plan, n))
        assert left == pytest.approx(x0 + w * 2.5 / 320, abs=0.02)
        assert right - left == pytest.approx(w * 314 / 320, abs=0.02)
