"""Storytelling: moments of the narration, a picture for each, the program under the voice."""
import subprocess

import numpy as np
import pytest

from core import show_story
from core.ffmpeg import tool_status


def _words(text, start=0.0, step=0.5):
    return [{"text": word, "start": start + i * step, "end": start + i * step + 0.4} for i, word in enumerate(text.split())]


def test_moments_end_at_sentences_and_never_run_too_long():
    words = _words("It was a cold night. Nobody heard the fire until the neighbours saw the smoke coming out of every window of the house", step=0.8)
    moments = show_story.beats(words, 20.0)
    assert moments[0]["text"] == "It was a cold night." and moments[0]["start"] == 0
    assert all(b["start"] == a["end"] for a, b in zip(moments, moments[1:])) and moments[-1]["end"] == 20.0
    assert all(m["end"] - m["start"] <= show_story.BEAT_MAX + 0.6 for m in moments)


def test_each_moment_gets_the_best_picture_not_used_yet(monkeypatch):
    resources = [{"intervals": [{"start": 0, "end": 3}]}, {"intervals": [{"start": 0, "end": 3}]}, {"intervals": [{"start": 0, "end": 3}]}]
    best = {"fire": [(2, 0.2), (0, 0.1)], "house": [(2, 0.3), (1, 0.05)], "night": []}
    monkeypatch.setattr("core.show_resources.match", lambda lines, res, top=12: [
        [{"resource": r, "interval": 0, "score": s} for r, s in best[line]] for line in lines])
    picks = show_story.choose([{"text": "fire"}, {"text": "house"}, {"text": "night"}], resources)
    assert picks == [(2, 0), (1, 0), (0, 0)]          # house's favourite was taken; night: the next one unused


def test_the_program_puts_each_picture_under_the_voice(tmp_path):
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    red, photo, voice = tmp_path / "red.mp4", tmp_path / "blue.png", tmp_path / "voice.mp3"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:s=320x180:r=30:d=1.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(red)], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=640x360", "-frames:v", "1", str(photo)], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=6", "-c:a", "libmp3lame", str(voice)], check=True)
    resources = [{"path": str(red), "kind": "video", "intervals": [{"start": 0.0, "end": 1.5}]},
                 {"path": str(photo), "kind": "photo", "intervals": [{"start": 0.0, "end": 0.0}]}]
    moments = [{"start": 0.0, "end": 3.0, "text": "a"}, {"start": 3.0, "end": 6.0, "text": "b"}]
    program = show_story.render(voice, moments, [(0, 0), (1, 0)], resources, tmp_path / "program.mp4", ffmpeg, width=160, height=90)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(program), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], check=True, capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 90, 160, 3).astype(int)
    assert len(frames) == 180
    assert (frames[10, ..., 0] > frames[10, ..., 2]).mean() > 0.9           # the red clip (slowed, then held) first...
    assert (frames[80, ..., 0] > frames[80, ..., 2]).mean() > 0.9
    assert (frames[150, ..., 2] > frames[150, ..., 0]).mean() > 0.9         # ...then the photo
