"""Show gags: the director's plan, its cleaning, and turning gags into stickers."""
import json
import subprocess

import pytest
from PIL import Image

from core import gag_director, gag_paint, gags
from core.gag_director import DirectorError, ask_claude, clean_gags, keyword_gags


def _words(text, start=2.0, step=0.4):
    out = []
    for token in text.split():
        out.append({"text": token, "start": round(start, 2), "end": round(start + step - 0.05, 2)})
        start += step
    return out


def test_keyword_director_paints_mentions_answers_people_and_bends_for_spirals():
    words = _words("I had a coffee with my mother.") + _words("It is a spiral!", start=30.0)
    plan = keyword_gags(words, [], language="en")
    first = {}
    for gag in plan:
        first.setdefault(gag["kind"], gag)
    assert first["character"]["subject"].startswith("a funny old mother") and first["character"]["says"]
    assert first["warp"]["warp"] == "swirl"
    assert any(gag["kind"] == "drawing" and "spiral" in gag["subject"] for gag in plan)


def test_cleaning_keeps_one_picture_at_a_time_mixes_styles_and_clears_the_face():
    raw = [
        {"kind": "drawing", "at": 2, "duration": 3, "subject": "a cat", "style": "oil", "position": "right", "size": 0.3},
        {"kind": "drawing", "at": 3, "duration": 2, "subject": "a dog", "style": "oil"},            # overlaps: dropped
        {"kind": "drawing", "at": 12, "duration": 2, "subject": "a bird", "style": "oil"},          # too soon after a drawing: dropped
        {"kind": "drawing", "at": 23, "duration": 2, "subject": "a fish", "style": "oil"},          # same style again
        {"kind": "word", "at": 9, "duration": 2, "text": ""},                                        # empty: dropped
        {"kind": "warp", "at": 9, "duration": 3, "warp": "swirl"},
        {"kind": "warp", "at": 20, "duration": 3, "warp": "wave"},                                   # too soon: dropped
        {"kind": "character", "at": 99, "duration": 2, "subject": "x"},                              # after the end
        "nonsense",
    ]
    plan = clean_gags(raw, duration=60, face_side="right")
    pictures = [gag for gag in plan if gag["kind"] != "warp"]
    assert [gag["subject"] for gag in pictures] == ["a cat", "a fish"]
    assert pictures[0]["position"] == "left"            # a big picture moved off the face
    assert pictures[0]["style"] != pictures[1]["style"]
    assert [gag["warp"] for gag in plan if gag["kind"] == "warp"] == ["swirl"]


def test_claude_director_reads_the_structured_answer(monkeypatch):
    answer = {"type": "result", "is_error": False, "structured_output": {"gags": [{"kind": "word", "at": 3, "duration": 2, "text": "WOW"}]}}
    calls = []
    monkeypatch.setattr(gag_director, "claude_cli", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(gag_director.subprocess, "run", lambda command, **kw: calls.append(command) or subprocess.CompletedProcess(command, 0, json.dumps(answer), ""))
    assert ask_claude("plan")[0]["text"] == "WOW"
    command = calls[0]
    assert command[command.index("--tools") + 1] == ""      # no tools: it only plans
    assert "--json-schema" in command and "--no-session-persistence" in command


def test_claude_director_explains_how_to_sign_in(monkeypatch):
    answer = {"type": "result", "is_error": True, "result": "Failed to authenticate: OAuth session expired"}
    monkeypatch.setattr(gag_director, "claude_cli", lambda: "/usr/local/bin/claude")
    monkeypatch.setattr(gag_director.subprocess, "run", lambda command, **kw: subprocess.CompletedProcess(command, 0, json.dumps(answer), ""))
    with pytest.raises(DirectorError, match="/login"):
        ask_claude("plan")


def test_without_claude_the_keyword_director_takes_over_and_says_why(monkeypatch):
    monkeypatch.setattr(gag_director, "claude_cli", lambda: None)
    plan, used, note = gags.plan_gags([("Coffee!", 3.0, 3.5)], 30.0, director="claude", language="en")
    assert used == "keywords" and "not installed" in note
    assert plan and plan[0]["kind"] == "drawing"


@pytest.mark.parametrize("position, face, expected_left", [
    ("left", "right", True), ("top-right", "right", False), ("right", "left", False), ("bottom-left", "left", True),
])
def test_positions_mirror_with_the_face(position, face, expected_left):
    x, _ = gags.centre(position, face)
    assert (x < 0.5) == expected_left


def test_items_without_the_painter_keep_words_and_explain_the_missing_drawings(tmp_path, monkeypatch):
    monkeypatch.setattr(gag_paint, "can_paint", lambda: False)
    plan = [{"kind": "drawing", "at": 1, "duration": 2, "subject": "a cat", "style": "oil", "position": "left", "size": 0.25, "effect": "pop"},
            {"kind": "word", "at": 4, "duration": 2, "text": "Wow", "position": "top-left", "size": 0.3, "effect": "drop"},
            {"kind": "warp", "at": 6, "duration": 2, "warp": "stretch"}]
    items, notes = gags.make_items(plan, tmp_path)
    assert [item.get("kind") or item.get("warp") for item in items] == ["word", "stretch"]
    assert "painter" in notes[0]
    word = items[0]
    assert Image.open(word["image"]).mode == "RGBA" and 0 < word["x"] < 0.5


def test_items_with_the_painter_get_painted_characters_with_their_bubble(tmp_path, monkeypatch):
    monkeypatch.setattr(gag_paint, "can_paint", lambda: True)
    monkeypatch.setattr(gags, "painted_picture", lambda subject, style, seed=3, **kw: Image.new("RGBA", (300, 300), (200, 30, 30, 255)))
    plan = [{"kind": "character", "at": 1, "duration": 3, "subject": "a granny", "style": "crayon", "says": "Told you!",
             "position": "left", "size": 0.3, "effect": "pop"}]
    seen = []
    items, notes = gags.make_items(plan, tmp_path, progress=lambda share, text: seen.append(text))
    assert not notes and "Painting 1/1" in seen[0]
    assert Image.open(items[0]["image"]).height > 300   # the bubble sits above the character


def test_math_formulas_fill_the_frame_without_rainbow_or_face_orbits(tmp_path, monkeypatch):
    import numpy as np
    from core.show_bridge_text import FORMULAS
    monkeypatch.setattr(gag_paint, "can_paint", lambda: False)
    items, _ = gags.make_items([{"kind": "math", "at": 10.0, "duration": 0.5}], tmp_path,
                              face_at=lambda t: (0.6, 0.3))
    assert len(items) == len(FORMULAS) == 16
    assert all(10 <= item["at"] < item["at"] + item["duration"] <= 10.501 for item in items)
    assert min(item["path"][0][1] for item in items) < 0.2
    assert max(item["path"][0][1] for item in items) > 0.8
    for item in items:
        assert item["effect"] == "none"
        rgb = np.asarray(Image.open(item["image"]))
        visible = rgb[rgb[..., 3] > 200, :3].astype(int)
        assert len(visible) and np.max(np.ptp(visible, axis=1)) == 0


def test_reviewed_render_never_generates_a_replacement_for_an_unbound_picture(tmp_path, monkeypatch):
    monkeypatch.setattr(gag_paint, "can_paint", lambda: True)
    def forbidden(*a, **kw):
        raise AssertionError("The render must not start a painter")
    monkeypatch.setattr(gags, "painted_picture", forbidden)
    chosen = tmp_path / "approved.png"
    Image.new("RGBA", (80, 80), (203, 24, 76, 255)).save(chosen)
    plan = [{"kind": "drawing", "subject": "guitar", "at": 0, "duration": 1, "proposal": "missing"},
            {"kind": "drawing", "subject": "guitar", "at": 2, "duration": 1, "proposal": "chosen", "picture": str(chosen)}]
    outcomes = {}
    items, notes = gags.make_items(plan, tmp_path / "render", allow_generation=False, outcomes=outcomes)
    assert len(items) == 1 and notes and outcomes["missing"][0] == "failed"
    assert outcomes["chosen"][0] == "applied"
    assert Image.open(items[0]["image"]).tobytes() == Image.open(chosen).tobytes()


def test_a_library_photo_the_director_named_is_shown_in_previews_and_the_render(tmp_path, monkeypatch):
    from core import gag_library
    library = tmp_path / "Gag library"
    (library / "Painted").mkdir(parents=True)
    Image.new("RGBA", (60, 40), (20, 140, 200, 255)).save(library / "Painted" / "a lazy man on a sofa 6cb396.png")
    Image.new("RGBA", (60, 40), (200, 140, 20, 255)).save(library / "Painted" / "a lazy cat on a sofa.png")
    monkeypatch.setattr(gag_library, "library_dir", lambda: library)
    assert gag_library.named_picture("6cb396").name == "a lazy man on a sofa 6cb396.png"
    assert gag_library.named_picture("A lazy man on a sofa").name == "a lazy man on a sofa 6cb396.png"
    assert gag_library.named_picture("a lazy dog on a sofa") is None          # no look-alike
    outcomes = {}
    items, notes = gags.make_items([{"kind": "photo", "query": "6cb396", "at": 1, "duration": 2, "proposal": "p1"},
                                    {"kind": "photo", "query": "missing one", "at": 4, "duration": 2, "proposal": "p2"}],
                                   tmp_path / "render", allow_generation=False, outcomes=outcomes)
    assert len(items) == 1 and outcomes["p1"][0] == "applied" and outcomes["p2"][0] == "failed"
    assert any("missing one" in note for note in notes)                        # said, not silently dropped


def test_a_drawing_on_a_desk_is_painted_again_one_on_white_is_kept(monkeypatch):
    import numpy as np
    from core.gag_paint import clean_background, paint_clean
    white = Image.new("RGB", (512, 512), (240, 236, 230))
    for x in range(150, 360):
        for y in range(200, 512):                                  # a bust touching the bottom
            white.putpixel((x, y), (150, 90, 60))
    desk = Image.fromarray(np.full((512, 512, 3), (150, 120, 90), dtype=np.uint8))
    desk.paste((245, 245, 240), (60, 40, 470, 480))                # the sheet on the desk
    assert clean_background(white) and not clean_background(desk)
    tries = []
    monkeypatch.setattr(gag_paint, "paint", lambda subject, style, seed=0, step=None, prompt=None: tries.append(seed) or (desk if len(tries) < 2 else white))
    assert paint_clean("a cat", "pencil", seed=5) is white and tries == [5, 106]
    tries.clear()
    monkeypatch.setattr(gag_paint, "paint", lambda subject, style, seed=0, step=None, prompt=None: tries.append(seed) or desk)
    with pytest.raises(gag_paint.UnusableDrawing):                 # never a blob on screen
        paint_clean("a cat", "pencil", seed=5)
    assert len(tries) == 3



def test_a_black_blob_is_not_a_drawing():
    # The painter's "thumbs up": a hand on a black fill, on white: clean edges, but a blob.
    from core.gag_paint import mostly_black
    blob = Image.new("RGB", (512, 512), (245, 245, 240))
    blob.paste((10, 10, 10), (80, 60, 430, 470))
    blob.paste((230, 190, 160), (200, 180, 300, 300))
    doodle = Image.new("RGB", (512, 512), (245, 245, 240))
    for k in range(6):                                               # a few wobbly black strokes
        doodle.paste((15, 15, 15), (100 + 50 * k, 120, 108 + 50 * k, 400))
    assert mostly_black(gag_paint.cut_out(blob)) and not mostly_black(gag_paint.cut_out(doodle))


def test_a_drawing_the_painter_could_not_clean_is_left_out_with_a_note(tmp_path, monkeypatch):
    monkeypatch.setattr(gag_paint, "can_paint", lambda: True)

    def unusable(subject, style, seed=3, **kw):
        raise gag_paint.UnusableDrawing(subject)
    monkeypatch.setattr(gags, "painted_picture", unusable)
    plan = [{"kind": "drawing", "at": 1, "duration": 2, "subject": "a thumbs up", "style": "marker", "position": "left", "size": 0.25, "effect": "pop"},
            {"kind": "word", "at": 4, "duration": 2, "text": "Wow", "position": "top-left", "size": 0.3, "effect": "drop"}]
    items, notes = gags.make_items(plan, tmp_path)
    assert [item["kind"] for item in items] == ["word"]
    assert "a thumbs up" in notes[0]


def test_a_blob_from_an_older_recipe_is_preserved_but_not_reused(tmp_path, monkeypatch):
    style = gag_paint.STYLES["marker"].label
    folder = tmp_path / "Painted" / style
    folder.mkdir(parents=True)
    import hashlib
    old = folder / f"a thumbs up {hashlib.sha1('a thumbs up|marker|3'.encode()).hexdigest()[:6]}.png"
    Image.new("RGBA", (200, 200), (5, 5, 5, 255)).save(old)                # painted before the blob check
    fresh = Image.new("RGB", (512, 512), (245, 245, 240))
    fresh.paste((200, 40, 40), (150, 150, 360, 360))
    monkeypatch.setattr(gag_paint, "paint_clean", lambda subject, style, seed=0, step=None, prompt=None, tries=3: fresh)
    sticker = gags.painted_picture("a thumbs up", "marker", library_root=tmp_path)
    assert not gag_paint.mostly_black(sticker) and gag_paint.mostly_black(Image.open(old))  # old recipe remains recoverable


def test_a_drawing_is_painted_with_the_prompt_the_person_wrote(tmp_path, monkeypatch):
    asked = []

    def fake_paint(subject, style, seed=0, step=None, prompt=None, tries=3):
        asked.append(prompt)
        picture = Image.new("RGB", (512, 512), (245, 245, 240))
        picture.paste((200, 40, 40), (150, 150, 360, 360))
        return picture
    monkeypatch.setattr(gag_paint, "paint_clean", fake_paint)
    default = gags.painted_picture("a guitar", "crayon", library_root=tmp_path)
    mine = gags.painted_picture("a guitar", "crayon", library_root=tmp_path, prompt="my own guitar, very wobbly")
    again = gags.painted_picture("a guitar", "crayon", library_root=tmp_path, prompt="my own guitar, very wobbly")
    assert asked == [None, "my own guitar, very wobbly"]                     # each version painted once
    assert default.size == mine.size == again.size
    assert len(list((tmp_path / "Painted" / gag_paint.STYLES["crayon"].label).glob("*.png"))) == 2   # the first is kept


def test_the_painter_uses_the_written_prompt_or_the_style_s_own(monkeypatch):
    sent = []

    class Pipe:
        def __call__(self, prompt, **kwargs):
            sent.append(prompt)
            return type("Result", (), {"images": [Image.new("RGB", (8, 8))]})()
    monkeypatch.setattr(gag_paint, "_pipeline", lambda: Pipe())
    gag_paint.paint("a guitar", "crayon")
    gag_paint.paint("a guitar", "crayon", prompt="my own words")
    assert sent == [gag_paint.prompt_for("a guitar", "crayon"), "my own words"]


def test_painter_steps_report_real_progress_and_can_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(gag_paint, 'can_paint', lambda: True)
    def paint(subject, style, **kwargs):
        kwargs['step'](1, 4)
        kwargs['step'](4, 4)
        return Image.new('RGBA', (40, 40), (200, 30, 30, 255))
    monkeypatch.setattr(gags, 'painted_picture', paint)
    plan = [{'kind':'drawing','at':1,'duration':2,'subject':'a sofa','style':'crayon','position':'left','size':.25,'effect':'pop'}]
    events = []
    gags.make_items(plan, tmp_path, progress=lambda fraction, message: events.append((fraction, message)))
    assert [fraction for fraction, _ in events[:3]] == [0, .25, 1]
    assert 'step 1/4' in events[1][1] and 'Wax crayons' in events[1][1]
    checks = []
    def cancel():
        checks.append(True)
        if len(checks) == 2:
            raise InterruptedError('superseded')
    with pytest.raises(InterruptedError, match='superseded'):
        gags.make_items(plan, tmp_path, cancel=cancel)


def test_each_storyboard_gag_says_whether_it_was_pictured(tmp_path, monkeypatch):
    from core import gag_paint, gags
    monkeypatch.setattr(gag_paint, "can_paint", lambda: False)
    outcomes = {}
    items, notes = gags.make_items([
        {"kind": "drawing", "subject": "guitar", "at": 1.0, "duration": 2.0, "proposal": "p-draw"},
        {"kind": "word", "text": "WOW", "at": 4.0, "duration": 1.0, "proposal": "p-word"},
        {"kind": "photo", "query": "zebra crossing", "at": 6.0, "duration": 1.0, "proposal": "p-photo"},
        {"kind": "warp", "warp": "swirl", "at": 8.0, "duration": 1.0, "proposal": "p-warp"}], tmp_path, library=[], outcomes=outcomes)
    assert outcomes["p-draw"] == ("failed", "The drawing painter is not installed")
    assert outcomes["p-word"][0] == "applied" and outcomes["p-warp"][0] == "applied"
    assert outcomes["p-photo"][0] == "failed" and "zebra crossing" in outcomes["p-photo"][1]


def test_a_drawing_can_use_one_of_your_own_pictures_instead_of_a_painting(tmp_path, monkeypatch):
    from core import gag_paint, gags
    monkeypatch.setattr(gag_paint, "can_paint", lambda: False)                  # no painter needed for your own picture
    own = tmp_path / "bulb.png"
    Image.new("RGBA", (200, 300), (250, 220, 90, 255)).save(own)
    outcomes = {}
    items, _ = gags.make_items([{"kind": "drawing", "subject": "lightbulb", "picture": str(own), "at": 1.0, "duration": 2.0, "proposal": "p1"},
                                {"kind": "drawing", "subject": "lamp", "picture": str(tmp_path / "gone.png"), "at": 4.0, "duration": 2.0,
                                 "proposal": "p2"}], tmp_path / "out", outcomes=outcomes)
    assert len(items) == 1 and Image.open(items[0]["image"]).size == (200, 300)
    assert outcomes["p1"] == ("applied", "your picture bulb.png") and outcomes["p2"][0] == "failed" and "gone.png" in outcomes["p2"][1]


def test_a_talking_character_keeps_your_exact_picture_and_gets_its_bubble(tmp_path, monkeypatch):
    from core import gag_paint, gags
    monkeypatch.setattr(gag_paint, "can_paint", lambda: False)                  # never repainted
    monkeypatch.setattr(gags, "painted_picture", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("repainted")))
    own = tmp_path / "teen.png"
    Image.new("RGBA", (200, 300), (40, 120, 200, 255)).save(own)
    gag = {"kind": "character", "subject": "a bored teenager", "says": "Whatever", "picture": str(own), "at": 1.0, "duration": 2.0, "proposal": "p1"}
    outcomes = {}
    items, notes = gags.make_items([gag], tmp_path / "out", outcomes=outcomes)
    shown = Image.open(items[0]["image"])
    assert notes == []                                                          # nothing was waiting for the painter
    assert outcomes["p1"] == ("applied", "your picture teen.png")
    assert shown.tobytes() == gags.character_with_bubble(Image.open(own).convert("RGBA"), "Whatever", side="left", seed=0).tobytes()


def test_a_storyboard_drawing_is_painted_once_for_every_preview(tmp_path, monkeypatch):
    import threading
    import time
    from core import gag_paint, gags
    gag = {"kind": "drawing", "subject": "a tiny potato", "proposal": "drawing-1234"}
    # The same effect gets the same picture alone, in its scene or in the final render.
    assert gags.paint_seed(gag, 0) == gags.paint_seed(gag, 7) == gags.paint_seed(gag, 8)
    assert gags.paint_seed({"kind": "drawing"}, 0) != gags.paint_seed({"kind": "drawing"}, 1)
    painted = []

    def slow_paint(subject, style, **kwargs):
        painted.append(subject)
        time.sleep(0.3)
        return Image.new("RGB", (64, 64), (200, 40, 40))
    monkeypatch.setattr(gag_paint, "paint_clean", slow_paint)
    monkeypatch.setattr(gag_paint, "cut_out", lambda image: image.convert("RGBA"))
    pictures = []
    threads = [threading.Thread(target=lambda: pictures.append(gags.painted_picture("a tiny potato", "crayon", seed=3, library_root=tmp_path)))
               for _ in range(3)]
    [thread.start() for thread in threads]
    [thread.join() for thread in threads]
    assert painted == ["a tiny potato"] and len(pictures) == 3                 # three previews, one painting


def test_a_drawing_the_painter_cannot_make_is_not_retried_by_every_preview(tmp_path, monkeypatch):
    from core import gag_paint, gags
    tries = []

    def unusable(subject, style, **kwargs):
        tries.append(subject)
        raise gag_paint.UnusableDrawing("blob")
    monkeypatch.setattr(gag_paint, "paint_clean", unusable)
    for _ in range(3):
        with pytest.raises(gag_paint.UnusableDrawing):
            gags.painted_picture("a tiny bald man", "crayon", seed=3, library_root=tmp_path)
    assert tries == ["a tiny bald man"]


def test_a_suggestion_is_a_card_and_an_accepted_drawing_is_drawn_by_claude_once(tmp_path, monkeypatch):
    import json as _json
    from core import gag_paint, gags, show_claude_drawing
    monkeypatch.setenv("ZUCKER_DRAWINGS", "claude")
    monkeypatch.setattr("core.gag_library.library_dir", lambda: tmp_path / "library")
    monkeypatch.setattr(gag_paint, "paint_clean", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the local painter was used")))
    asked = []
    strokes = {"strokes": [{"kind": "line", "color": "#2b2118", "width": 10, "points": [[200, 200], [800, 800]]},
                           {"kind": "line", "color": "#2b2118", "width": 10, "points": [[800, 200], [200, 800]]},
                           {"kind": "fill", "color": "#d6402c", "width": 10, "points": [[300, 300], [700, 300], [700, 700], [300, 700]]}]}
    monkeypatch.setattr(show_claude_drawing, "ask", lambda prompt, **kw: asked.append(prompt) or show_claude_drawing.clean(strokes))
    suggested = {"kind": "drawing", "subject": "a tiny potato", "suggested": True, "at": 1.0, "duration": 2.0, "proposal": "drawing-1"}
    accepted = {"kind": "drawing", "subject": "a guitar", "style": "crayon", "at": 4.0, "duration": 2.0, "proposal": "drawing-2"}
    items, notes = gags.make_items([suggested, accepted], tmp_path / "out")
    assert len(items) == 2 and notes == [] and len(asked) == 1 and "a guitar" in asked[0]   # the suggestion was not drawn
    kept = list((tmp_path / "library" / "Claude drawings").rglob("*.png"))
    assert len(kept) == 1 and _json.loads(kept[0].with_suffix(".json").read_text())["strokes"]
    gags.make_items([accepted], tmp_path / "again")
    assert len(asked) == 1                                                       # drawn once, reused after


def test_when_claude_cannot_draw_the_drawing_is_left_out_and_said(tmp_path, monkeypatch):
    from core import gags, show_claude_drawing
    monkeypatch.setenv("ZUCKER_DRAWINGS", "claude")
    monkeypatch.setattr("core.gag_library.library_dir", lambda: tmp_path / "library")
    def refuse(prompt, **kw):
        raise RuntimeError("Your Claude usage limit is reached")
    monkeypatch.setattr(show_claude_drawing, "ask", refuse)
    outcomes = {}
    items, notes = gags.make_items([{"kind": "drawing", "subject": "a cat", "at": 1.0, "duration": 2.0, "proposal": "drawing-3"}],
                                   tmp_path / "out", outcomes=outcomes)
    assert items == [] and "Claude could not draw" in notes[0] and "usage limit" in notes[0]
    assert outcomes["drawing-3"][0] == "failed"


def test_the_final_render_encoder_is_libx264_unless_hardware_is_asked():
    from core.ffmpeg import h264_args
    assert h264_args("ffmpeg", preset="veryfast", crf=20, width=1920, height=1080) == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]


def test_a_reviewed_render_asks_no_provider_even_with_claude_installed(tmp_path, monkeypatch):
    from core import gag_paint, gags, show_claude_drawing
    monkeypatch.setenv("ZUCKER_DRAWINGS", "claude")
    monkeypatch.setattr("core.gag_library.library_dir", lambda: tmp_path / "library")
    monkeypatch.setattr(show_claude_drawing, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Claude was asked during a render")))
    monkeypatch.setattr(gag_paint, "paint_clean", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the painter ran during a render")))
    outcomes = {}
    items, notes = gags.make_items([{"kind": "drawing", "subject": "a guitar", "at": 1.0, "duration": 2.0, "proposal": "drawing-9"}],
                                   tmp_path / "out", outcomes=outcomes, allow_generation=False)
    assert items == [] and outcomes["drawing-9"][0] == "failed" and "no replacement was painted" in notes[0]


def test_only_a_hardware_encoder_failure_renders_again_in_software():
    from core.show import _encoded
    runs = []

    def encoder_refuses(hardware):
        runs.append(hardware)
        if hardware:
            raise RuntimeError("[h264_videotoolbox] Cannot create compression session: -12903")
        return "done"
    assert _encoded(encoder_refuses) == "done" and runs == [True, False]

    def drawing_breaks(hardware):
        raise RuntimeError("A sticker picture is missing")
    with pytest.raises(RuntimeError, match="sticker picture"):
        _encoded(drawing_breaks)                                   # not repeated under a hardware excuse
