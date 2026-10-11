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
