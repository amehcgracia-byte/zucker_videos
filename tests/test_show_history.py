"""Making a Show again keeps the storyboard it replaces, and the person can go back to it."""
import json

from core import show
from core.project import create_project
from server.api import create_app


def _storyboard(project, decided, effects):
    scenes = [{"id": f"scene-{n:02d}", "start": n * 10.0, "end": n * 10.0 + 10, "decision": "accepted" if n < decided else "pending",
               "effects": [], "overlays": [{"id": "a"}] if n == 0 and decided else []} for n in range(3)]
    (project.artifacts_dir / "show_scenes.json").write_text(json.dumps({"version": 2, "identity": {}, "intro": None, "scenes": scenes}))
    (project.artifacts_dir / "show_proposals.json").write_text(json.dumps({"version": 1, "proposals": [{"id": f"p{n}", "status": "approved"} for n in range(effects)]}))
    (project.artifacts_dir / "show_resume.json").write_text(json.dumps({"scene_id": "scene-02"}))


def test_making_the_show_again_keeps_the_edited_storyboard_and_it_can_come_back(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("History", str(tmp_path / "History.zuckervid"))
    assert show.keep_storyboard(project, "nothing yet") is None          # no storyboard, nothing to keep
    _storyboard(project, decided=3, effects=71)
    kept = show.keep_storyboard(project, "Before making the Show again")
    assert (kept / "show_resume.json").is_file()
    _storyboard(project, decided=0, effects=36)                           # the new plan replaced it
    client = create_app(str(project.folder)).test_client()
    items = client.get("/api/v1/show/history").get_json()["items"]
    assert items[0]["id"] == kept.name and items[0]["decided"] == 3 and items[0]["effects"] == 71 and items[0]["pictures"] == 1
    restored = client.post(f"/api/v1/show/history/{kept.name}/restore", json={})
    assert restored.status_code == 200, restored.get_json()
    assert len(json.loads((project.artifacts_dir / "show_proposals.json").read_text())["proposals"]) == 71
    after = client.get("/api/v1/show/history").get_json()["items"]
    assert any(item["effects"] == 36 for item in after)                  # the replaced one is kept too
    assert client.post("/api/v1/show/history/..%2Fetc/restore", json={}).status_code in (404, 405)
    assert client.post("/api/v1/show/history/20200101-000000/restore", json={}).status_code == 404


def test_prepare_show_saves_the_storyboard_before_planning(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Again", str(tmp_path / "Again.zuckervid"))
    _storyboard(project, decided=2, effects=5)
    try:
        show.prepare_show(project, str(tmp_path / "missing.mp4"), [], progress=lambda *a: None, cancel=lambda: None)
    except RuntimeError:
        pass                                                              # stops at the missing video, after the copy
    assert show.storyboard_history(project)[0]["effects"] == 5
