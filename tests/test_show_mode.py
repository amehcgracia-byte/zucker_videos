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
    started = client.post("/api/v1/wizard/start", json={"platform": "show", "videos": [str(video)], "show_style": "calm", "show_language": "es", "show_review": False})
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


def test_show_captions_survive_the_final_composition(tmp_path, monkeypatch):
    # A "show" export used to be treated as long-form YouTube when composing,
    # which silently dropped every caption made from the talk.
    import json
    from types import SimpleNamespace

    import server.api as api_module
    from core.project import create_project

    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    folder = tmp_path / "Show.zuckervid"
    project = create_project("Show", str(folder))
    video = project.exports_dir / "Show-show.mp4"
    video.write_bytes(b"not empty")
    manifest = project.artifacts_dir / "export_manifest.json"
    manifest.write_text(json.dumps({"platform": "show", "exports": [{"path": str(video), "platform": "show"}]}))
    project.data["stages"]["export"]["outputs"] = {"export_manifest": str(manifest)}
    project.save()
    app = api_module.create_app(project_path=str(folder))
    monkeypatch.setattr(app.config["ZUCKER_STATE"].composition, "start", lambda _project: SimpleNamespace(snapshot=lambda: {}))
    cue = {"lines": ["Good morning"], "start": 0.5, "end": 1.4, "words": []}
    response = app.test_client().post("/api/v1/wizard/compose", json={"cues": [cue], "style": "autoread_fixed_white"})
    assert response.status_code == 202
    track = json.loads((folder / "cue_track.json").read_text())
    assert track["platform"] == "show"
    assert [c["lines"] for c in track["cues"]] == [["Good morning"]]



def test_by_default_the_show_waits_for_review_and_renders_only_when_asked(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Talk", str(tmp_path / "Talk.zuckervid"))
    video, closeup = tmp_path / "talk.mp4", tmp_path / "closeup.mp4"
    video.write_bytes(b"x")
    closeup.write_bytes(b"y")
    project.data["inputs"]["videos"] = [{"path": str(video)}, {"path": str(closeup)}]
    project.save()
    seen = {}

    def fake_prepare(project, source, resources, **kwargs):
        seen["resources"] = resources
        (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 5, "cuts": 2, "source": source, "shots": []}))
        (project.artifacts_dir / "show_proposals.json").write_text(json.dumps({"version": 1, "proposals": []}))
        return {"proposals": 0, "by_kind": {}, "resources": len(resources), "note": "", "edited_duration_sec": 5, "cuts": 2}

    def fake_render(project, *, progress, cancel):
        destination = project.exports_dir / "Talk-show.mp4"
        destination.write_bytes(b"video")
        manifest = {"platform": "show", "exports": [{"path": str(destination)}], "show": {"cuts": 2}, "warnings": []}
        (project.artifacts_dir / "export_manifest.json").write_text(json.dumps(manifest))
        return destination, manifest

    monkeypatch.setattr("core.show.prepare_show", fake_prepare)
    monkeypatch.setattr("core.show.render_reviewed_show", fake_render)
    client = create_app(project_path=str(project.folder)).test_client()
    assert client.post("/api/v1/wizard/start", json={"platform": "show", "videos": [str(video), str(closeup)]}).status_code == 202
    wizard = client.application.config["ZUCKER_STATE"].wizard
    wizard._thread.join(timeout=10)
    status = client.get("/api/v1/wizard/status").get_json()
    assert status["status"] == "waiting_show_review"
    assert seen["resources"] == [str(closeup)]                    # the other video is a close-up
    assert not list(project.exports_dir.glob("*.mp4"))             # nothing rendered before the review
    assert client.post("/api/v1/show/review/render").status_code == 202
    wizard._thread.join(timeout=10)
    status = client.get("/api/v1/wizard/status").get_json()
    assert status["status"] == "done" and status["result"]["platform"] == "show"
