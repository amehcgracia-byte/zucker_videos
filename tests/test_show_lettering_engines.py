"""Drawn words can be lettered by ChatGPT or by the person's own Claude (stroke by stroke,
with its lettering instructions), so a used-up allowance on one does not stop them."""
import json

from core import show_ai_images, show_claude_drawing, show_proposals, show_scenes
from core.project import create_project


def test_claude_letters_exactly_the_words_with_its_lettering_instructions(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Words", str(tmp_path / "Words.zuckervid"))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [])
    scene = {"id": "s1", "start": 0, "end": 5}
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [{**scene, "name": "One", "text": "x", "effects": [], "overlays": [], "decision": "pending", "proposal": ""}]})
    job = show_ai_images.create(project, scene, dict(direction="red accent", purpose="lettering", lettering_text="¡Qué vago!", style="marker",
                                                     at=1.0, duration=2, image_center=[.3, .3], size=.5, engine="claude"))
    assert job["provider"] == "claude" and job["purpose"] == "lettering"
    asked = {}

    def fake_ask(prompt, *, timeout=300.0, system=None):
        asked.update(prompt=prompt, system=system)
        return {"strokes": [{"kind": "line", "color": "#111111", "width": 20, "points": [[100, 100 + 40 * n], [900, 120 + 40 * n]]} for n in range(3)]}
    monkeypatch.setattr(show_claude_drawing, "ask", fake_ask)
    show_ai_images.run_claude(project, job["id"])
    assert asked["system"] == show_claude_drawing.LETTERING_SYSTEM
    assert json.dumps("¡Qué vago!", ensure_ascii=False) in asked["prompt"] and "red accent" in asked["prompt"]
    done = show_ai_images.load(project, job["id"])
    assert done["status"] == "ready", done.get("error")
    # Drawings keep their own instructions (no text in them).
    plain = show_claude_drawing.prompt_for({"direction": "a sofa", "style": "crayon"}, None)
    assert plain.startswith("Draw this for the video: a sofa")


def test_words_take_a_typeface_bold_italic_and_colour():
    from core.gag_stickers import WORD_FONTS, word_sticker
    assert "plain" in WORD_FONTS and len(WORD_FONTS) >= 15                  # one plain, many wild
    plain = word_sticker("Qué vago", font="plain", handwritten=True, tilt=0)
    wild = word_sticker("Qué vago", font="impact", handwritten=True, tilt=0, bold=True, italic=True, colour="#ff4fa3")
    assert plain.size != wild.size                                          # another face, thicker and slanted
    pixels = wild.convert("RGBA").getdata()
    assert any(r > 200 and g < 120 and b > 120 and a > 200 for r, g, b, a in pixels)   # the chosen pink


def test_the_review_api_accepts_only_known_typefaces(tmp_path, monkeypatch):
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Type", str(tmp_path / "Type.zuckervid"))
    word = show_proposals.proposal("word", 1.0, 2.0, reason="x", kind="word", text="WOW", position="left", size=0.3, effect="pop")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [word])
    client = create_app(str(project.folder), dev=True).test_client()
    ok = client.patch(f"/api/v1/show/review/{word['id']}", json={"font": "zapfino", "bold": True, "italic": False, "colour": "#ffd400"})
    assert ok.status_code == 200 and ok.get_json()["params"]["font"] == "zapfino"
    assert client.patch(f"/api/v1/show/review/{word['id']}", json={"font": "comic-sans-forever"}).status_code == 400
    assert client.patch(f"/api/v1/show/review/{word['id']}", json={"bold": "yes"}).status_code == 400
