"""Show mode wiring: API validation and the editor's controls."""
from pathlib import Path

from core.project import create_project
from server.api import create_app


def test_show_needs_a_talking_video(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Talk", str(tmp_path / "Talk.zuckervid"))
    client = create_app(project_path=str(project.folder)).test_client()
    response = client.post("/api/v1/wizard/start", json={"platform": "show", "videos": []})
    assert response.status_code == 400


def test_show_job_runs_and_publishes_its_result(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Talk", str(tmp_path / "Talk.zuckervid"))
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"x")
    calls = {}

    def fake_run_show(project, source, *, style_name, language, progress, cancel, **_):
        calls.update(source=source, style=style_name, language=language)
        progress(50, "Transcribing")
        destination = project.exports_dir / "Talk-show.mp4"
        destination.write_bytes(b"video")
        manifest = {"platform": "show", "exports": [{"path": str(destination)}], "show": {"cuts": 12, "captions": 9}, "warnings": []}
        (project.artifacts_dir / "export_manifest.json").write_text(__import__("json").dumps(manifest))
        return destination, manifest

    monkeypatch.setattr("core.show.run_show", fake_run_show)
    client = create_app(project_path=str(project.folder)).test_client()
    started = client.post("/api/v1/wizard/start", json={"platform": "show", "videos": [str(video)], "show_style": "calm", "show_language": "es"})
    assert started.status_code == 202
    app_state = client.application.config["ZUCKER_STATE"]
    app_state.wizard._thread.join(timeout=10)
    status = client.get("/api/v1/wizard/status").get_json()
    assert status["status"] == "done" and status["result"]["platform"] == "show"
    assert calls == {"source": str(video), "style": "calm", "language": "es"}


def test_editor_offers_the_show_mode():
    root = Path(__file__).parents[1] / "web"
    page = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    assert 'data-platform="show"' in page and 'id="showStyle"' in page and 'id="showLanguage"' in page
    assert 'show_style: document.querySelector("#showStyle")' in app
    assert '"medley", "show"].includes(platform)' in app or '"backstage", "medley", "show"].includes(platform)' in app
