"""Clean cuts: an old cut next to a new one never flashes another shot, and close-ups and
inserts a few frames from a cut land on it."""
import subprocess

import numpy as np

from core import source_cuts
from core.gag_render import render_gags


def test_frames_of_the_neighbouring_old_shot_hold_a_clean_frame_instead():
    plan = {"fps": 30, "shots": [{"start_frame": 0, "end_frame": 40}, {"start_frame": 100, "end_frame": 160}]}
    # Old cuts inside the source: 3 frames after the second shot starts, 1 frame before it ends.
    holds = source_cuts.holds(plan, [103, 159, 130])
    assert (40, 42, 43) in holds                       # output 40-42 come from the old shot: show 43
    assert (99, 99, 98) in holds                       # output 99 is the next old shot: show 98
    assert not any(first <= 70 <= last for first, last, _ in holds)    # an old cut mid-shot is a real cut, left alone
    assert source_cuts.holds(plan, []) == []


def test_close_ups_and_inserts_a_few_frames_from_a_cut_land_on_it():
    edges = [0.0, 3.1, 5.0]
    snapped = source_cuts.snap([{"source": "c.mp4", "at": 3.05, "duration": 1.9},
                                {"insert": "clip", "at": 4.9, "duration": 2.0},
                                {"image": "p.png", "at": 3.05, "duration": 1.0},
                                {"source": "far.mp4", "at": 1.0, "duration": 1.0}], edges, 30)
    assert snapped[0]["at"] == 3.1 and snapped[0]["duration"] == 1.9    # starts and ends on the cuts
    assert snapped[1]["at"] == 5.0 and snapped[1]["duration"] == 2.0    # an insert keeps its length
    assert snapped[2]["at"] == 3.05                                     # pictures are not touched
    assert snapped[3]["at"] == 1.0


def test_the_render_shows_the_held_frame(tmp_path):
    video = tmp_path / "cut.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", str(video)], check=True)
    out = tmp_path / "out.mp4"
    render_gags(video, [], out, ffmpeg="ffmpeg", holds=[(4, 5, 6), (12, 12, 11)], crf=0)
    def frames(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, 90, 160).astype(int)
    before, after = frames(video), frames(out)
    assert len(after) == len(before)
    assert np.abs(after[4] - before[6]).mean() < 3 and np.abs(after[5] - before[6]).mean() < 3
    assert np.abs(after[12] - before[11]).mean() < 3 and np.abs(after[7] - before[7]).mean() < 3
