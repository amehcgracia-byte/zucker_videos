"""Continue must work while speculative preparation still owns the project."""
import pytest
from core.project import create_project
from server.api import create_app


@pytest.mark.parametrize("status", ["running", "cancelling"])
@pytest.mark.parametrize("existing", [False, True])
def test_identical_settings_are_read_only_while_busy(tmp_path, monkeypatch, status, existing):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Handoff", str(tmp_path / "handoff.zuckervid"))
    camera, filler = str(tmp_path / "camera.mp4"), str(tmp_path / "filler.mp4")
    project.data["inputs"]["videos"] = [{"path": camera}, {"path": filler}]
    subjects = {camera: "singer"} if existing else {}
    fillers = [filler] if existing else []
    project.data["settings"]["edit"] = {"camera_subjects": subjects, "fillers": fillers}
    project.save()
    app = create_app(project_path=str(project.folder))
    state = app.config["ZUCKER_STATE"]
    monkeypatch.setattr(state.wizard, "status", lambda: {"status": status})
    def forbidden_save():
        pytest.fail("An idempotent request must not write during preparation")
    monkeypatch.setattr(state.project, "save", forbidden_save)
    client = app.test_client()
    assert client.post("/api/v1/settings/camera-subjects", json={"camera_subjects": subjects}).status_code == 200
    assert client.post("/api/v1/settings/fillers", json={"fillers": fillers + fillers}).status_code == 200
    assert client.post("/api/v1/settings/camera-subjects", json={"camera_subjects": {camera: "drummer"}}).status_code == 409
    assert client.post("/api/v1/settings/fillers", json={"fillers": [camera]}).status_code == 409


@pytest.mark.parametrize("status", ["waiting_choice", "done", "failed", "cancelled", "idle"])
def test_navigation_cancel_accepts_already_finished_preparation(tmp_path, monkeypatch, status):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    app = create_app()
    wizard = app.config["ZUCKER_STATE"].wizard
    monkeypatch.setattr(wizard, "cancel", lambda: False)
    monkeypatch.setattr(wizard, "status", lambda: {"status": status})
    client = app.test_client()
    response = client.post("/api/v1/wizard/cancel", json={"if_idle": True})
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "status": status}
    assert client.post("/api/v1/wizard/cancel", json={}).status_code == 409


def test_rendering_shots_without_an_active_project_is_refused_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    client = create_app().test_client()
    response = client.post("/api/v1/wizard/review/render", json={"transitions": {}})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "not_ready"
