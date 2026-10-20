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
from captions.layout import caption_layout
from captions.sources import from_lyrics, from_srt, to_srt
from captions.styles import get_style, list_styles


def test_caption_presets_render_valid_ass() -> None:
    track = CueTrack((Cue(("Hello", "world"), 0, 2),))
    styles = list_styles()
    assert len(styles) == 18
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


def test_autoread_fixed_and_phrase_presets_do_not_enable_word_karaoke() -> None:
    track = CueTrack((Cue(("Hello", "world"), 0, 2, (Word("Hello", 0, 1), Word("world", 1, 2))),))
    fixed = render_ass(track, get_style("autoread_fixed_white"))
    phrase = render_ass(track, get_style("autoread_phrase_color"))
    karaoke = render_ass(track, get_style("autoread_karaoke_yellow"))
    assert "\\k" not in fixed
    assert "\\t(250,250" in phrase
    assert "\\k" in karaoke


def test_autoread_word_preset_falls_back_to_visible_phrase_without_words() -> None:
    track = CueTrack((Cue(("Words are unavailable but this remains visible",), 0, 2),))
    ass = render_ass(track, get_style("autoread_karaoke_yellow"))
    assert "Words are unavailable but this remains visible" in ass
    assert "\\k" not in ass
    assert r"\c&H00FFFFFF&" in ass


def test_autoread_visual_presets_are_not_accidentally_word_animation() -> None:
    track = CueTrack((Cue(("VISIBLE",), 0, 2, (Word("VISIBLE", 0, 2),)),))
    glow = render_ass(track, get_style("autoread_green_glow"))
    box = render_ass(track, get_style("autoread_solid_box"))
    assert "\\k" not in glow
    assert r"\3c&H0000FF00&" in glow
    assert "\\k" not in box
    assert ",3,2,2," in box


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


def test_long_caption_wraps_to_two_word_boundary_lines_and_fits() -> None:
    cue = Cue(("u try to come around now but everything goes",), 0, 2)
    ass = render_ass(CueTrack((cue,)), get_style("autoread_fixed_white"), width=1080, height=1920)
    assert r"u try to come around now but\Neverything goes" in ass
    assert "\\fs" not in ass
    assert len(caption_layout(cue.text, width=1080, requested_size=56, margin_l=65, margin_r=65)[0]) == 2


def test_long_caption_reduces_size_when_two_lines_need_it() -> None:
    lines, size = caption_layout("one two three four five six seven eight nine ten eleven twelve", width=640, requested_size=80, margin_l=40, margin_r=40)
    assert len(lines) == 2
    assert size < 80


def test_phrase_transition_has_initial_colour_and_delayed_whole_phrase_change() -> None:
    ass = render_ass(CueTrack((Cue(("A phrase",), 0, 2),)), get_style("autoread_phrase_color"))
    assert r"\t(250,250,\c&H0000FFFF&" in ass


def test_caption_fade_override_is_emitted_as_ass_fade() -> None:
    track = CueTrack((Cue(("Fade",), 0, 2, style_override={"color": "#00ff00", "animation_in": "fade", "animation_out": "fade"}),))
    ass = render_ass(track, get_style("autoread_fixed_white"))
    assert r"\fad(250,250)" in ass
    assert r"\c&H0000FF00&" in ass


def test_scale_enters_once_and_exits_once_with_native_transforms() -> None:
    track = CueTrack((Cue(("Pop",), 0, 2, style_override={"vertical": 50, "animation_in": "scale", "animation_out": "scale"}),))
    events = [line for line in render_ass(track, get_style("autoread_fixed_white"), width=1080, height=1920).splitlines() if line.startswith("Dialogue")]
    assert len(events) == 1
    assert r"\fscx72\fscy72\t(0,250,0.5,\fscx100\fscy100)" in events[0]
    assert r"\t(1750,2000,2,\fscx72\fscy72)" in events[0]
    assert r"\an5" in events[0] and r"\pos(540,960)" in events[0]


def test_slide_in_and_out_moves_text_and_glow_together() -> None:
    track = CueTrack((Cue(("Slide",), 4, 7.5, style_override={"vertical": 72, "glow_color": "#80DEEA", "glow_blur": 7, "glow_layers": 1, "animation_in": "slide", "animation_out": "slide"}),))
    events = [line for line in render_ass(track, get_style("autoread_fixed_white"), width=1080, height=1920).splitlines() if line.startswith("Dialogue")]
    assert len(events) == 4  # entrance part and exit part, each glow + text
    entrance = [event for event in events if "0:00:04.00,0:00:07.25" in event]
    exit_ = [event for event in events if "0:00:07.25,0:00:07.50" in event]
    assert all(r"\move(475.2,1382.4,540,1382.4,0,250)" in event and r"\fad(250,0)" in event for event in entrance)
    assert all(r"\move(540,1382.4,604.8,1382.4,0,250)" in event and r"\fad(0,250)" in event for event in exit_)
    assert not any(r"\pos(" in event for event in events)


def test_chosen_colour_beats_the_word_style_fallback_colour() -> None:
    # Regression: karaoke_word without word timings burned every caption in
    # its green fallback although the editor showed the colour the user chose.
    track = CueTrack((Cue(("It was beautiful",), 1, 3, style_override={"color": "#FFF176"}),))
    ass = render_ass(track, get_style("karaoke_word"), width=1080, height=1920)
    assert r"\c&H0076F1FF&" in ass
    assert r"\c&H0000FF00&" not in ass


def test_karaoke_keeps_pauses_between_words() -> None:
    words = (Word("one", 1.0, 1.4), Word("two", 2.0, 2.3))
    ass = render_ass(CueTrack((Cue(("one two",), 1.0, 3.0, words),)), get_style("karaoke_word"))
    assert r"{\k40}one {\k60}{\k30}two" in ass


def test_editor_preview_follows_the_burned_effect_curves() -> None:
    import json
    import shutil
    import subprocess
    from captions import effects
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    code = source[source.index("const CAPTION_EFFECT_SEC"):source.index("function captionAnchor")]
    script = code + """
const out = [];
for (const [enter, exit] of [["scale","scale"],["slide","slide"],["fade","none"]]) {
  for (const now of [0, 0.1, 0.25, 1.0, 1.85, 2.0]) {
    out.push([enter, exit, now, captionEffectState({start: 0, end: 2, style_override: {animation_in: enter, animation_out: exit}}, now, 1000)]);
  }
}
console.log(JSON.stringify(out));"""
    states = json.loads(subprocess.run([node, "-e", script], capture_output=True, text=True, check=True).stdout)
    assert effects.EFFECT_SEC == 0.25 and effects.SCALE_FROM == 72 and effects.SLIDE_SHARE == 0.06
    for enter, exit_, now, state in states:
        entered = min(1.0, now / 0.25) if enter != "none" else 1.0
        leaving = max(0.0, min(1.0, 1 - (2 - now) / 0.25)) if exit_ != "none" else 0.0
        assert state["opacity"] == pytest.approx(entered * (1 - leaving), abs=1e-6)
        if enter == "scale":
            expected = 0.72 + 0.28 * entered ** effects.ENTRANCE_ACCEL - 0.28 * leaving ** effects.EXIT_ACCEL
            assert state["scale"] == pytest.approx(expected, abs=1e-6)
        if enter == "slide":
            expected = -60 * (1 - entered) if entered < 1 else 60 * leaving
            assert state["dx"] == pytest.approx(expected, abs=1e-6)
    # Mid-cue the caption is still: no looping entrance.
    assert [state for _, _, now, state in states if now == 1.0] == [{"opacity": 1, "scale": 1, "dx": 0}] * 3


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


def test_unset_effects_are_the_fade_the_menus_show() -> None:
    ass = render_ass(CueTrack((Cue(("Plain",), 0, 2),)), get_style("autoread_fixed_white"))
    assert r"\fad(250,250)" in ass
    explicit = render_ass(CueTrack((Cue(("Still",), 0, 2, style_override={"animation_in": "none", "animation_out": "none"}),)), get_style("autoread_fixed_white"))
    assert r"\fad" not in explicit


def test_caption_dragged_on_the_picture_burns_centred_there() -> None:
    moved = Cue(("Moved",), 0, 2, style_override={"vertical": 25, "horizontal": 30, "animation_in": "none", "animation_out": "none"})
    ass = render_ass(CueTrack((moved,)), get_style("autoread_fixed_white"), width=1080, height=1920)
    assert r"\an5\pos(324,480)" in ass
    # Only a chosen height (the Vertical slider): centred horizontally.
    height_only = Cue(("High",), 0, 2, style_override={"vertical": 25, "animation_in": "none", "animation_out": "none"})
    assert r"\an5\pos(540,480)" in render_ass(CueTrack((height_only,)), get_style("autoread_fixed_white"), width=1080, height=1920)
    # The slide effect moves from the dragged position, not the centre.
    sliding = Cue(("Slide",), 0, 2, style_override={"vertical": 50, "horizontal": 20, "animation_in": "slide", "animation_out": "none"})
    assert r"\move(151.2,960,216,960,0,250)" in render_ass(CueTrack((sliding,)), get_style("autoread_fixed_white"), width=1080, height=1920)


def test_editor_offers_on_picture_caption_editing() -> None:
    root = Path(__file__).parents[1] / "web"
    app = (root / "app.js").read_text(encoding="utf-8")
    page = (root / "index.html").read_text(encoding="utf-8")
    assert 'label: "Edit caption"' in app and 'addEventListener("contextmenu"' in app
    assert 'id="composeCaptionEditExit"' in page and "Exit caption edition" in page
    # Caption blocks on the timeline carry the same resize handles as overlays.
    assert 'compose-timeline-block caption${selected}' in app and 'data-timeline-resize="left"' in app


def _run_editor_effects(script: str):
    import json
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    code = source[source.index("const CAPTION_EFFECT_SEC"):source.index("function captionAnchor")]
    return json.loads(subprocess.run([node, "-e", code + script], capture_output=True, text=True, check=True).stdout)


def test_editor_fun_effects_follow_the_burn_curves() -> None:
    from captions import effects
    times = [0, 0.05, 0.12, 0.2, 0.25, 0.31, 0.4, 0.47, 0.5, 0.52, 0.6, 0.66, 0.75, 0.9, 1.33, 2.71]
    states = _run_editor_effects("""
const out = {};
for (const fun of ["none", ...CAPTION_FUN_EFFECTS]) out[fun] = %s.map((t) => captionFunState(fun, t));
console.log(JSON.stringify({states: out, names: CAPTION_FUN_EFFECTS, flies: CAPTION_FLY_EFFECTS, scaling: CAPTION_SCALING_FUN,
  fly: captionFlyStart("drop", 540, 1500, 1080, 1920, 2, 14, 60), side: captionFlyStart("fly_left", 540, 1500, 1080, 1920, 2, 14, 60)}));""" % times)
    assert states["names"] == list(effects.FUN_EFFECTS[1:])
    assert states["flies"] == list(effects.FLY_EFFECTS) and states["scaling"] == list(effects.SCALING_FUN)
    for fun, values in states["states"].items():
        for t, state in zip(times, values):
            expected = effects.fun_state(fun, t)
            for key in ("sx", "sy", "rot", "dy"):
                assert state[key] == pytest.approx(expected[key], abs=1e-9), (fun, t, key)
    lines, longest, size = 2, 14, 60
    assert states["fly"] == pytest.approx(list(effects.fly_start("drop", x=540, y=1500, width=1080, height=1920, lines=lines, longest=longest, size=size)))
    assert states["side"] == pytest.approx(list(effects.fly_start("fly_left", x=540, y=1500, width=1080, height=1920, lines=lines, longest=longest, size=size)))


def test_editor_resolves_entrances_like_the_burn() -> None:
    from captions import effects
    combos = [(enter, exit_, fun, duration) for enter in ("fade", "slide", "scale") for exit_ in ("none", "slide", "scale")
              for fun in ("none", "pulse", "bounce", "drop") for duration in (0.8, 3.0)]
    resolved = _run_editor_effects("""
console.log(JSON.stringify(%s.map(([enter, exit, fun, d]) => captionResolvedEffects({start: 1, end: 1 + d, style_override: {animation_in: enter, animation_out: exit, fun_effect: fun}}))));""" % [list(c) for c in combos])
    for (enter, exit_, fun, duration), state in zip(combos, resolved):
        assert (state["enter"], state["exit"]) == effects.resolve(enter, exit_, fun, duration), (enter, exit_, fun, duration)


def test_fun_effect_tags_continue_across_a_split_event() -> None:
    from captions import effects
    whole = effects.fun_tags("pulse", offset=0.0, duration=3.0, x=540, y=960, height=1920)
    assert whole[0] == r"\t(0,300,\fscx120\fscy120)" and len(whole) == 10
    # A second event starting 1.5 s in continues the cycle with elapsed times;
    # a step finishing exactly at its start never reads as "0 = event end".
    second = effects.fun_tags("pulse", offset=1.5, duration=0.25, x=540, y=960, height=1920)
    assert second[0] == r"\t(-300,-1,\fscx120\fscy120)"
    once = effects.fun_tags("spin", offset=0.0, duration=2.0, x=540, y=960, height=1920)
    assert once == [r"\fscx0", r"\fscy0", r"\frz360", r"\t(0,500,0.5,\fscx100\fscy100\frz0)"]
    bounce = effects.fun_tags("bounce", offset=0.0, duration=0.5, x=540, y=960, height=1920)
    assert bounce[0] == r"\org(-99460,960)"


def test_slide_entrance_yields_to_a_fly_in() -> None:
    cue = Cue(("Fly",), 0, 3, style_override={"fun_effect": "drop", "animation_in": "slide", "animation_out": "slide", "vertical": 50})
    events = [line for line in render_ass(CueTrack((cue,)), get_style("autoread_fixed_white"), width=1080, height=1920).splitlines() if line.startswith("Dialogue")]
    assert len(events) == 2
    assert r"\move(540,-" in events[0] and r",540,960,0,450)" in events[0]
    assert r"\move(540,960,604.8,960,0,250)" in events[1]


@pytest.mark.skipif(not Path(FFMPEG_FULL).exists(), reason="ffmpeg-full unavailable")
@pytest.mark.parametrize("fun", ["pulse", "bounce", "blink", "drop", "wobble"])
def test_burned_fun_effects_match_their_curves(tmp_path: Path, fun: str) -> None:
    from PIL import Image
    from captions import effects
    cue = Cue(("FUNNY",), 0, 3, style_override={"fun_effect": fun, "vertical": 50, "animation_in": "none", "animation_out": "none"})
    ass = tmp_path / "fun.ass"
    ass.write_text(render_ass(CueTrack((cue,)), get_style("autoread_fixed_white"), width=540, height=960), encoding="utf-8")
    subprocess.run([FFMPEG_FULL, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=540x960:d=3:r=25",
                    "-vf", f"subtitles={ass}", str(tmp_path / "f%03d.png")], check=True)

    def box(time: float):
        image = Image.open(tmp_path / f"f{round(time * 25) + 1:03d}.png").convert("L").point(lambda v: 255 if v > 128 else 0)
        return image.getbbox()

    still = box(0.0) if fun not in ("drop",) else box(1.2)
    width, height = still[2] - still[0], still[3] - still[1]
    for time in (0.12, 0.2, 0.32, 0.44, 0.52, 1.0, 1.16):
        state = effects.fun_state(fun, time)
        shown = box(time)
        if fun == "blink" and state["sx"] == 0:
            assert shown is None
            continue
        assert shown is not None, (fun, time)
        if fun == "pulse":
            assert (shown[2] - shown[0]) / width == pytest.approx(state["sx"], abs=0.04)
        if fun == "bounce":
            lift = -state["dy"] * effects.BOUNCE_SHARE * 960
            assert still[1] - shown[1] == pytest.approx(lift, abs=2.5)
        if fun == "wobble" and abs(state["rot"]) > 3:
            assert (shown[3] - shown[1]) > height + 4  # tilted text is taller
        if fun == "drop" and time < effects.FLY_SEC:
            assert shown[1] < still[1]  # still falling from above


def test_auto_caption_library_is_twenty_attributed_lines() -> None:
    import json
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    source = (Path(__file__).parents[1] / "web" / "app.js").read_text(encoding="utf-8")
    block = source[source.index("const CAPTION_PHRASES = ["):source.index("let captionPhraseBag")]
    phrases = json.loads(subprocess.run([node, "-e", block + "console.log(JSON.stringify(CAPTION_PHRASES));"],
                                        capture_output=True, text=True, check=True).stdout)
    assert len(phrases) == 20 and len({phrase["text"] for phrase in phrases}) == 20
    assert all(phrase["author"] and phrase["source"] for phrase in phrases)
    # Short enough to stay a two-line caption at the preset sizes.
    assert all(len(caption_layout(phrase["text"], width=1080, requested_size=54, margin_l=65, margin_r=65)[0]) <= 2 for phrase in phrases)
    page = (Path(__file__).parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="captionAutoCompose"' in page and 'id="captionComposeUse"' in page and 'id="captionComposeAnother"' in page
