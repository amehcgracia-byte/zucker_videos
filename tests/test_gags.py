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
        {"kind": "drawing", "at": 6, "duration": 2, "subject": "a fish", "style": "oil"},           # same style again
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


def test_math_formulas_orbit_the_host_s_face(tmp_path, monkeypatch):
    monkeypatch.setattr(gag_paint, "can_paint", lambda: False)
    plan = [{"kind": "math", "at": 10.0, "duration": 4.0}]
    items, _ = gags.make_items(plan, tmp_path, face_at=lambda t: (0.6, 0.3))
    assert len(items) == 5 and all(item["path"] for item in items)
    for item in items:
        xs = [x for _, x, _ in item["path"]]
        ys = [y for _, _, y in item["path"]]
        assert min(xs) < 0.6 < max(xs) and min(ys) < 0.3 < max(ys)          # around the face
        assert all(abs(x - 0.6) <= 0.16 and abs(y - 0.3) <= 0.14 for x, y in zip(xs, ys))
    assert len({item["image"] for item in items}) == 5


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


def test_a_blob_kept_from_before_is_painted_again(tmp_path, monkeypatch):
    style = gag_paint.STYLES["marker"].label
    folder = tmp_path / "Painted" / style
    folder.mkdir(parents=True)
    import hashlib
    old = folder / f"a thumbs up {hashlib.sha1('a thumbs up|marker|3'.encode()).hexdigest()[:6]}.png"
    Image.new("RGBA", (200, 200), (5, 5, 5, 255)).save(old)                # painted before the blob check
    fresh = Image.new("RGB", (512, 512), (245, 245, 240))
    fresh.paste((200, 40, 40), (150, 150, 360, 360))
    monkeypatch.setattr(gag_paint, "paint_clean", lambda subject, style, seed=0, step=None, prompt=None: fresh)
    sticker = gags.painted_picture("a thumbs up", "marker", library_root=tmp_path)
    assert not gag_paint.mostly_black(sticker) and not gag_paint.mostly_black(Image.open(old))


def test_a_drawing_is_painted_with_the_prompt_the_person_wrote(tmp_path, monkeypatch):
    asked = []

    def fake_paint(subject, style, seed=0, step=None, prompt=None):
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
