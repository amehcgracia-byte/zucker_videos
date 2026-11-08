"""Real subtitle burning on both runner platforms, including unusual paths."""
import subprocess
from pathlib import Path

import pytest
from captions.burn import burn
from captions.model import Cue, CueTrack
from captions.styles import list_styles
from core.ffmpeg import tool_status


@pytest.mark.parametrize("with_progress", [False, True])
def test_burn_caption_in_path_with_spaces_and_filter_characters(tmp_path, monkeypatch, with_progress):
    import importlib
    module = importlib.import_module("captions.burn")
    unusual = tmp_path / "Singer's clips [live], take 1"
    unusual.mkdir()
    original_temp = module.tempfile.TemporaryDirectory
    monkeypatch.setattr(module.tempfile, "TemporaryDirectory", lambda **kw: original_temp(dir=unusual, **kw))
    ffmpeg = tool_status()["ffmpeg_path"]
    assert ffmpeg, "This render regression requires the CI media tools"
    source, output = unusual / "source.mp4", unusual / "captioned.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=navy:s=360x640:d=1", "-c:v", "libx264", str(source)], check=True)
    track = CueTrack((Cue(("VISIBLE CAPTION",), 0.1, 0.9),))
    burn(source, track, list_styles()[0], output_path=output, ffmpeg=ffmpeg,
         progress_callback=(lambda seconds: None) if with_progress else None)
    frame = subprocess.run([ffmpeg, "-v", "error", "-ss", "0.5", "-i", str(output), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], check=True, capture_output=True).stdout
    assert len(frame) == 360 * 640 * 3
    assert sum(1 for r,g,b in zip(frame[::3],frame[1::3],frame[2::3]) if min(r,g,b)>210) > 20
