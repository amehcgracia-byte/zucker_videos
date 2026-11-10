"""A suggested drawing is seen before it is approved: accepting its scene draws it as a
draft kept in the project and linked to it; accepting again approves what was seen."""
import hashlib
import json
import time
from pathlib import Path

from PIL import Image

from core import show_proposals, show_scenes
from core.project import create_project
from server.api import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("ZUCKER_DRAWINGS", "claude")
    project = create_project("Drafts", str(tmp_path / "Drafts.zuckervid"))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": []}))
    drawing = show_proposals.proposal("drawing", 2, 2, reason="Director", kind="drawing", subject="a guitar", style="crayon")
    word = show_proposals.proposal("word", 5, 1, reason="Director", kind="word", text="WOW")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [drawing, word])
    show_scenes.save(project.artifacts_dir / "show_scenes.json", {"identity": {}, "scenes": [
        {"id": "scene-1", "name": "Scene 1", "text": "x", "start": 0, "end": 10, "effects": [drawing["id"], word["id"]], "overlays": [],
         "decision": "pending", "proposal": ""}]})
    return project, drawing["id"], word["id"], create_app(str(project.folder), dev=True).test_client()


def _wait_drawn(client, drawing_id):
    for _ in range(200):
        item = next(p for p in client.get("/api/v1/show/scenes").get_json()["proposals"] if p["id"] == drawing_id)
        if not item.get("drafting"):
            return item
        time.sleep(0.02)
    raise AssertionError("never drawn")


def test_accepting_a_scene_draws_its_drawing_as_a_draft_to_see_before_approving(tmp_path, monkeypatch):
    from core import gags
    project, drawing_id, word_id, client = _client(tmp_path, monkeypatch)
    asked = []
    monkeypatch.setattr(gags, "claude_picture", lambda subject, style, seed=3, prompt=None, library_root=None:
                        asked.append(seed) or Image.new("RGBA", (64, 48), (len(asked) * 30, 80, 40, 255)))
    monkeypatch.setattr("core.show.preview_scene", lambda *a, **k: (_ for _ in ()).throw(InterruptedError()))   # no video here
    answer = client.post("/api/v1/show/scenes/scene-1/decision", json={"decision": "accepted"}).get_json()
    assert answer["drafts"] == 1
    item = _wait_drawn(client, drawing_id)
    statuses = {p["id"]: p["status"] for p in show_proposals.load(project.artifacts_dir / "show_proposals.json")["proposals"]}
    assert statuses == {drawing_id: "proposed", word_id: "approved"}             # not approved blind
    picture = Path(item["params"]["picture"])
    assert picture.parent == (project.folder / "assets" / "show-library" / "drawn").resolve() and item["params"]["draft"] is True
    assert hashlib.sha256(picture.read_bytes()).hexdigest() == item["params"]["picture_sha256"]

    client.post(f"/api/v1/show/review/{drawing_id}/draft", json={"again": True})   # another one
    again = _wait_drawn(client, drawing_id)
    assert again["params"]["picture"] != item["params"]["picture"] and len(set(asked)) == 2

    client.post("/api/v1/show/scenes/scene-1/decision", json={"decision": "accepted"})   # seen: approved now
    final = {p["id"]: p for p in show_proposals.load(project.artifacts_dir / "show_proposals.json")["proposals"]}
    assert final[drawing_id]["status"] == "approved" and final[drawing_id]["params"]["picture"] == again["params"]["picture"]


def test_a_drawing_claude_cannot_make_says_why_and_stays_unapproved(tmp_path, monkeypatch):
    from core import gags
    project, drawing_id, _, client = _client(tmp_path, monkeypatch)
    def refuse(*a, **k):
        raise gags.DrawingFailed("Your Claude usage limit is reached")
    monkeypatch.setattr(gags, "claude_picture", refuse)
    monkeypatch.setattr("core.show.preview_scene", lambda *a, **k: (_ for _ in ()).throw(InterruptedError()))
    client.post(f"/api/v1/show/review/{drawing_id}/draft", json={})
    for _ in range(200):
        item = next(p for p in client.get("/api/v1/show/scenes").get_json()["proposals"] if p["id"] == drawing_id)
        if (item.get("drafting") or {}).get("status") == "failed":
            break
        time.sleep(0.02)
    assert "usage limit" in item["drafting"]["error"] and not item["params"].get("picture") and item["status"] == "proposed"
