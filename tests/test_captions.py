from __future__ import annotations

import importlib
import subprocess
import shutil
from pathlib import Path

import pytest

from captions.burn import burn
from captions.align import align_known_lyrics
from captions.model import Cue, CueTrack, Word
from captions.render import render_ass
from captions.sources import from_lyrics, from_srt, to_srt
from captions.styles import list_styles


def test_caption_presets_render_valid_ass() -> None:
    track = CueTrack((Cue(("Hello", "world"), 0, 2),))
    styles = list_styles()
    assert len(styles) == 16
    for style in styles:
        ass = render_ass(track, style, width=1080, height=1920)
        assert "[Script Info]" in ass
        assert "[V4+ Styles]" in ass
        assert "Dialogue:" in ass
        assert f"\\an{style.alignment}" in ass


def test_neon_glow_preset_and_override_render_layers() -> None:
    track = CueTrack((Cue(("NEON",), 0, 2, style_override={"glow_color": "#00ff00", "glow_blur": 10, "glow_layers": 3, "glow_intensity": 0.8}),))
    ass = render_ass(track, list_styles()[-1], width=1080, height=1920)
    assert ass.count("Dialogue:") == 4
    assert r"\blur10" in ass
    assert r"\3c&H0000FF00&" in ass


def test_glow_layers_share_caption_position_and_size() -> None:
    track = CueTrack((Cue(("Last Session",), 14.0, 16.0, style_override={"size": 103, "vertical": 75, "glow_blur": 9, "glow_layers": 1}),))
    ass = render_ass(track, list_styles()[4], width=1080, height=1920)
    assert ass.count("Last Session") == 2
    assert ass.count(r"\fs103") == 2
    assert ass.count(r"\pos(540,1440)") == 2


def test_caption_style_override_renders_inline_tags() -> None:
    track = CueTrack((Cue(("Override",), 0, 2, style_override={"color": "#00ff00", "size": 80, "vertical": 68, "outline_color": "#ff0000", "outline_width": 4, "shadow_distance": 6, "shadow_color": "#0000ff", "shadow_opacity": 0.5}),))
    ass = render_ass(track, list_styles()[0], width=1080, height=1920)
    assert r"\c&H0000FF00&" in ass
    assert r"\fs80" in ass
    assert r"\pos(540,1305.6)" in ass
    assert r"\bord4" in ass
    assert r"\3c&H000000FF&" in ass
    assert r"\shad6" in ass
    assert r"\4c&H00FF0000&" in ass
    assert r"\4a&H80&" in ass


def test_lyrics_preserves_exact_lines() -> None:
    track = from_lyrics("one\ntwo\n\nthree\n  four  ")
    assert track.cues[0].lines == ("one", "two")
    assert track.cues[1].lines == ("three", "  four  ")


def test_srt_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "captions.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:03,250\nHello\nworld\n", encoding="utf-8")
    track = from_srt(source)
    roundtrip = tmp_path / "roundtrip.srt"
    roundtrip.write_text(to_srt(track), encoding="utf-8")
    assert from_srt(roundtrip) == track


def test_captions_has_no_mode_imports() -> None:
    forbidden = ("server.wizard", "core.stages", "core.backstage", "sync_map", "edit_plan")
    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("captions").glob("*.py"))
    assert not any(token in source for token in forbidden)


def test_forced_alignment_keeps_user_words_and_anchors_whisper() -> None:
    lyrics = from_lyrics("HELLO WORLD")
    whisper = CueTrack((Cue(("hello wurld",), 3.0, 4.0, (Word("hello", 3.1, 3.4), Word("wurld", 3.5, 3.9))),))
    aligned = align_known_lyrics(lyrics, whisper)
    assert aligned.cues[0].text == "HELLO WORLD"
    assert [word.text for word in aligned.cues[0].words] == ["HELLO", "WORLD"]
    assert aligned.cues[0].words[0].start == pytest.approx(3.1)
    assert aligned.cues[0].words[1].start == pytest.approx(3.5)


FFMPEG_FULL = "/usr/local/opt/ffmpeg-full/bin/ffmpeg"


@pytest.mark.skipif(not Path(FFMPEG_FULL).exists(), reason="ffmpeg-full unavailable")
def test_burn_smoke(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "captioned.mp4"
    subprocess.run([FFMPEG_FULL, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=navy:s=360x640:d=2", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "2", "-c:v", "libx264", "-c:a", "aac", str(source)], check=True)
    track = CueTrack((Cue(("VISIBLE CAPTION",), 0.2, 1.8),))
    burn(source, track, list_styles()[0], output_path=output, ffmpeg=FFMPEG_FULL)
    assert output.exists() and output.stat().st_size > 0
    frame = tmp_path / "frame.png"
    subprocess.run([FFMPEG_FULL, "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", str(output), "-frames:v", "1", str(frame)], check=True)
    from PIL import Image
    pixels = list(Image.open(frame).convert("RGB").getdata())
    assert sum(1 for r, g, b in pixels if r > 210 and g > 210 and b > 210) > 20
