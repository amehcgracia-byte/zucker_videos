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
    plan = [{"kind": "character", "at": 1, "duration": 3, "subject": "a granny", "style": "caricature", "says": "Told you!",
             "position": "left", "size": 0.3, "effect": "pop"}]
    seen = []
    items, notes = gags.make_items(plan, tmp_path, progress=lambda share, text: seen.append(text))
    assert not notes and "Painting 1/1" in seen[0]
    assert Image.open(items[0]["image"]).height > 300   # the bubble sits above the character
