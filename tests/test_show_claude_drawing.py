"""Claude draws for the Show stroke by stroke: a crayon doodle on a transparent
picture, which can also draw itself on the video."""
import json
import time

import numpy as np
import pytest
from PIL import Image

from core import show_ai_images as ai
from core import show_claude_drawing as claude
from core import show_proposals, show_scenes

GUITAR = {"strokes": [
    {"kind": "line", "color": "#2b2118", "width": 10, "points": [[300, 700], [250, 600], [320, 500], [450, 520], [500, 650], [420, 760], [300, 700]]},
    {"kind": "line", "color": "#2b2118", "width": 8, "points": [[460, 560], [800, 220]]},
    {"kind": "fill", "color": "#d6402c", "width": 12, "points": [[300, 700], [250, 600], [320, 500], [450, 520], [500, 650], [420, 760]]},
    {"kind": "fill", "color": "#c98a3a", "width": 10, "points": [[455, 550], [800, 210], [815, 228], [470, 570]]}]}


def test_strokes_are_checked_and_drawn_in_crayon_on_a_transparent_picture():
    assert claude.clean({"strokes": GUITAR["strokes"] + [{"kind": "line", "color": "red", "width": 999, "points": [[5, 5], [2000, -4]]}]})["strokes"][-1] == \
        {"kind": "line", "color": "#2b2118", "width": 30.0, "points": [(5.0, 5.0), (1000.0, 0.0)]}          # bad colour, width and points tamed
    with pytest.raises(RuntimeError):
        claude.clean({"strokes": [{"kind": "line", "color": "#000000", "width": 4, "points": [[1, 1]]}]})
    picture = claude.render(GUITAR, size=256)
    assert picture.shape == (256, 256, 4)
    assert picture[0, 0, 3] == 0 and picture[250, 10, 3] == 0                  # the sheet stays transparent
    body = picture[int(640 * 0.256), int(380 * 0.256)]
    assert body[3] > 150 and body[0] > body[2]                                  # the red body is scribbled in, densely
    outline = picture[..., 3][int(700 * 0.256) - 3:int(700 * 0.256) + 3, int(300 * 0.256) - 3:int(300 * 0.256) + 3]
    assert outline.max() > 200


def test_a_drawing_draws_itself_stroke_by_stroke_in_claude_s_order():
    painter = claude.Painter(GUITAR, 200)
    inked = [int((painter.at(share)[..., 3] > 40).sum()) for share in (0.1, 0.3, 0.6, 1.0)]
    assert inked == sorted(inked) and inked[0] < inked[-1] * 0.4             # more and more of it
    assert np.array_equal(painter.at(1.0), claude.render(GUITAR, size=200))     # and at the end exactly the picture


def test_claude_is_a_provider_of_einstein_beside_chatgpt(tmp_path, monkeypatch):
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("AI", str(tmp_path / "AI.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [])
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [
        {"id": "scene-1", "name": "Scene 1", "text": "guitar", "start": 0, "end": 10, "effects": [], "overlays": [], "decision": "pending", "proposal": ""}]})
    asked = []
    monkeypatch.setattr(claude, "ask", lambda prompt, **kw: asked.append(prompt) or claude.clean(GUITAR))
    client = create_app(str(project.folder), dev=True).test_client()
    sent = client.post("/api/v1/show/ai/images", json={"scene_id": "scene-1", "direction": "a badly drawn electric guitar", "selected_text": "I play",
                                                       "at": 2, "duration": 2, "style": "crayon", "engine": "claude"})
    assert sent.status_code == 202 and sent.get_json()["provider"] == "claude"
    job_id = sent.get_json()["id"]
    for _ in range(200):
        job = client.get(f"/api/v1/show/ai/images/{job_id}").get_json()
        if job["status"] not in ("queued", "generating"):
            break
        time.sleep(0.02)
    assert job["status"] == "ready" and "a badly drawn electric guitar" in asked[0] and "I play" in asked[0]
    folder = ai.folder(project, job_id)
    assert (folder / "strokes.json").is_file() and Image.open(folder / "image.png").mode == "RGBA"
    applied = client.post(f"/api/v1/show/ai/images/{job_id}/apply").get_json()
    assert applied["params"]["effect"] == "draw" and applied["params"]["strokes"] == str(folder / "strokes.json")
    assert applied["params"]["image_engine"].startswith("Claude")
    assert client.post("/api/v1/show/ai/images", json={"scene_id": "scene-1", "direction": "x", "at": 2, "engine": "dalle"}).status_code == 400


def test_a_used_up_chatgpt_allowance_says_when_it_comes_back():
    log = "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "error", "message": "You've hit your usage limit. Upgrade to Pro (https://x) or try again at Oct 16th, 2026 12:17 AM."}])
    said = ai.plain_failure(log)
    assert "Oct 16th, 2026 12:17 AM" in said and "Claude" in said and "Upgrade" not in said


def test_a_drawing_draws_itself_on_the_video(tmp_path):
    from core.gag_render import drawing_pixels, load_sticker
    picture, strokes = tmp_path / "image.png", tmp_path / "strokes.json"
    Image.fromarray(claude.render(GUITAR, size=256), "RGBA").save(picture)
    strokes.write_text(json.dumps(GUITAR))
    raw = {"image": str(picture), "at": 1.0, "duration": 4.0, "x": 0.5, "y": 0.5, "width": 0.3, "effect": "draw", "strokes": str(strokes)}
    sticker = load_sticker(raw, 640, 360)
    early, late, after = (drawing_pixels(sticker, t)[..., 3].sum() for t in (1.2, 1.0 + sticker.draw_sec * 0.8, 1.0 + sticker.draw_sec + 0.1))
    assert early < late < after * 1.05 and early < after * 0.5
    painted = load_sticker({**raw, "strokes": None}, 640, 360)                    # a painted picture: a crayon colours it in
    assert painted.painter is None and drawing_pixels(painted, 1.0 + painted.draw_sec * 0.3)[..., 3].sum() < painted.pixels[..., 3].sum() * 0.7
    assert load_sticker({**raw, "effect": "pop"}, 640, 360).draw_sec == 0
