"""Show renders exactly the planned frames, framing and audio."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core.ffmpeg import tool_status
from core.show_render import edit_audio, kept_ranges, render_show

FPS = 30


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _plan(shots):
    return {"fps": FPS, "shots": [{"start_frame": a, "end_frame": b, "zoom": z} for a, b, z in shots]}


def _frames(ffmpeg: str, path: Path, w: int, h: int) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, h, w)


def test_each_output_frame_comes_from_its_planned_source_frame(tmp_path):
    ffmpeg = _ffmpeg()
    # Every source frame is a flat grey that encodes its number (mod 50).
    source = tmp_path / "numbered.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "nullsrc=s=320x180:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=f=440:d=8", "-vf", "geq=lum='20+4*mod(N,50)':cb=128:cr=128",
                    "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)], check=True)
    shots = [(10, 40, 1.0), (40, 70, 1.18), (100, 130, 1.0), (180, 200, 1.32)]
    output = render_show(source, _plan(shots), tmp_path / "show.mp4", ffmpeg=ffmpeg, width=320, height=180)
    frames = _frames(ffmpeg, output, 320, 180)
    expected = [n for a, b, _ in shots for n in range(a, b)]
    assert len(frames) == len(expected)
    # Match each output frame to the source frame with the same grey.
    reference = [frame[60:120, 100:220].mean() for frame in _frames(ffmpeg, source, 320, 180)[:50]]
    decoded = [int(np.argmin([abs(frame[60:120, 100:220].mean() - grey) for grey in reference])) for frame in frames]
    assert decoded == [n % 50 for n in expected]


def test_punch_ins_enlarge_the_picture_by_their_zoom(tmp_path):
    ffmpeg = _ffmpeg()
    source = tmp_path / "box.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=3",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-vf", "drawbox=x=280:y=131:w=80:h=80:color=white:t=fill", "-c:v", "libx264", "-crf", "10",
                    "-c:a", "aac", "-t", "3", str(source)], check=True)
    output = render_show(source, _plan([(0, 30, 1.0), (30, 60, 1.32)]), tmp_path / "zoom.mp4", ffmpeg=ffmpeg, width=640, height=360)
    frames = _frames(ffmpeg, output, 640, 360)
    width = lambda frame: int((frame[frame.shape[0] // 2] > 128).sum())
    wide, close = width(frames[15]), width(frames[45])
    assert close / wide == pytest.approx(1.32, rel=0.06)


def test_cuts_join_audio_with_short_fades_and_pace_splits_stay_continuous():
    plan = _plan([(0, 30, 1.0), (30, 60, 1.18), (90, 120, 1.0)])
    assert kept_ranges(plan) == [(0, 60), (90, 120)]
    samples = np.ones((48000 * 5, 2), dtype=np.float32)
    audio = edit_audio(samples, plan)
    assert len(audio) == 48000 * 3
    # A fade at the real cut (2 s into the edit), none at the pace split (1 s).
    assert audio[48000 * 2, 0] < 0.05 and audio[48000 * 2 - 1, 0] < 0.05
    assert audio[48000, 0] == pytest.approx(1.0)


def test_rendered_show_lasts_exactly_its_planned_frames(tmp_path):
    ffmpeg = _ffmpeg()
    source = tmp_path / "talk.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=6",
                    "-f", "lavfi", "-i", "sine=f=300:d=6", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(source)], check=True)
    plan = _plan([(3, 50, 1.0), (61, 100, 1.32), (100, 140, 1.18)])
    output = render_show(source, plan, tmp_path / "out.mp4", ffmpeg=ffmpeg, width=320, height=180)
    ffprobe = str(tool_status().get("ffprobe_path") or "ffprobe")
    probe = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,duration",
                                       "-of", "json", str(output)], capture_output=True, text=True).stdout)
    durations = {s["codec_type"]: float(s["duration"]) for s in probe["streams"]}
    assert durations["video"] == pytest.approx((47 + 39 + 40) / FPS, abs=1 / FPS)
    assert durations["audio"] == pytest.approx(durations["video"], abs=0.03)


def test_a_long_show_with_hundreds_of_cuts_is_accepted_by_ffmpeg():
    # A 10-minute talk gives ~150 kept ranges; FFmpeg refuses flat sums of
    # more than 100 terms, so the expressions must stay shallow.
    from core.show_render import video_filter
    ffmpeg = _ffmpeg()
    shots = [(i * 10, i * 10 + 6, (1.0, 1.18, 1.32)[i % 3]) for i in range(400)]
    graph = video_filter(_plan(shots), 160, 90)
    result = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"testsrc2=s=160x90:r={FPS}:d=3",
                             "-vf", graph, "-f", "null", "-"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-300:]
