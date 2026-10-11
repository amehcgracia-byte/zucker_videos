"""Text and paths drawn by FFmpeg filters survive every character a title can hold."""
import subprocess

import pytest

from core.ffmpeg import filter_option_value, tool_status

TITLES = ["Don't Stop", "It's 5:00 'live'", "100% live", "Back\\slash", "comma, here", "Semi;colon [x]", "Ñandú & café"]


def _drawtext_supported() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout if ffmpeg else ""
    if " drawtext " not in filters:
        pytest.skip("FFmpeg without drawtext")
    return ffmpeg


def _render(ffmpeg: str, vf: str) -> bytes:
    return subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=400x60:d=1", "-vf", vf,
                           "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout


@pytest.mark.parametrize("title", TITLES)
def test_export_title_renders_exactly_its_text(tmp_path, title):
    from core.stages.export import _drawtext
    ffmpeg = _drawtext_supported()
    drawn = _render(ffmpeg, _drawtext(title, "x=10:y=10:fontsize=20"))
    # Reference: the same text read from a file, with no escaping at all.
    (tmp_path / "title.txt").write_text(title, encoding="utf-8")
    reference_filter = _drawtext("", "x=10:y=10:fontsize=20").replace(":text=:", f":textfile={filter_option_value(str(tmp_path / 'title.txt'))}:")
    assert drawn == _render(ffmpeg, reference_filter)
    assert any(drawn)


def test_option_values_escape_both_parsing_levels():
    assert filter_option_value("a:b") == r"a\\:b"
    assert filter_option_value("Don't") == r"Don\\\'t"
    assert filter_option_value("x,y;z[1]") == r"x\,y\;z\[1\]"


@pytest.mark.parametrize("folder", ["Singer's Live.zuckervid", "Live, Madrid", "take [1]", "drive C:"])
def test_360_motion_commands_load_from_any_project_folder(tmp_path, monkeypatch, folder):
    import core.stages.export as export
    ffmpeg = _drawtext_supported()
    commands = tmp_path / folder / "segment.sendcmd.txt"
    commands.parent.mkdir()
    # The real commands drive v360; a drawbox command proves the file loads.
    monkeypatch.setattr(export, "_v360_motion_commands", lambda shot, duration, aspect_ratio=0: ["0.5 [enter] drawbox@box color red;\n"])
    prefix = export._v360_sendcmd_filter({"movement": "pan"}, 1.0, commands)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-vf", prefix + "drawbox@box=x=0:y=0:w=10:h=10:color=blue", "-f", "null", "-"], check=True)
