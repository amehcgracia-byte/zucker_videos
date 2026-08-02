from __future__ import annotations

import time
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.camera_moves import save_camera_move
from core.project import create_project, file_record, load_project
from core.stages.base import write_artifact_json
from core.stages.cut import CutStage
from core.stages.export import ExportStage
from core.stages.ingest import IngestStage
from core.stages.sync import SyncStage
from server.api import (
    MAX_PREVIEW_FOV,
    STEREOGRAPHIC_PREVIEW_FOV_THRESHOLD,
    create_app,
    _can_reuse_prepared_project,
    _project_wizard_status,
    _sanitize_camera_role_weights,
    _sanitize_spherical_landmarks,
    _spherical_preview_frame,
)
from server.inbox import load_global_config
from server.wizard import WizardJob, _select_360_inputs, _store_audio_trim


def valid_video_probe(duration: str = "3.0", width: int = 1280, height: int = 720) -> dict:
    return {
        "format": {"duration": duration, "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": width, "height": height},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def test_api_create_project_run_stub_stage_and_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.stages.ingest.ffprobe",
        lambda path: valid_video_probe(),
    )
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe())
    monkeypatch.setattr("core.stages.ingest.normalize_video_record", lambda project, record, progress: record.setdefault("normalized", {"path": record["path"]}))
    monkeypatch.setattr("core.stages.sync.load_or_compute_master_envelope", lambda project: [1, 2, 3])
    monkeypatch.setattr(
        "core.stages.sync.sync_clip",
        lambda project, record, master_env, threshold: {
            "path": record["path"],
            "filename": "clip.mov",
            "offset_sec": 0.0,
            "duration_sec": 1.0,
            "confidence": 8.0,
            "low_confidence": False,
            "manual_override": False,
            "source_signature": {"path": record["path"], "size": record["size"], "mtime": record["mtime"]},
        },
    )
    monkeypatch.setattr("core.stages.sync.media_duration", lambda path: 30.0)
    master = tmp_path / "master.wav"
    songs = tmp_path / "songs.json"
    video = tmp_path / "clip.mov"
    master.write_bytes(b"master")
    songs.write_text("[]", encoding="utf-8")
    video.write_bytes(b"video")

    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"

    response = client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)})
    assert response.status_code == 201
    response = client.post("/api/v1/inputs/master", json={"master": str(master), "songs": str(songs)})
    assert response.status_code == 200
    response = client.post("/api/v1/inputs/videos", json={"paths": [str(video)]})
    assert response.status_code == 200

    response = client.post("/api/v1/stages/ingest/run")
    assert response.status_code == 202

    deadline = time.time() + 5
    status = {}
    while time.time() < deadline:
        status = client.get("/api/v1/stages/status").get_json()
        if not status["busy"] and status["stages"]["ingest"]["status"] == "done":
            break
        time.sleep(0.05)

    assert status["stages"]["ingest"]["status"] == "done"

    response = client.post("/api/v1/stages/sync/run")
    assert response.status_code == 202

    deadline = time.time() + 5
    status = {}
    while time.time() < deadline:
        status = client.get("/api/v1/stages/status").get_json()
        if not status["busy"] and status["stages"]["sync"]["status"] == "done":
            break
        time.sleep(0.05)

    assert status["stages"]["sync"]["status"] == "done"
    artifact = client.get("/api/v1/artifacts/sync")
    assert artifact.status_code == 200
    assert artifact.get_json()["schema_version"] == 1


def test_readiness_matrix_defers_master_and_songs_until_later_stages(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.stages.ingest.ffprobe",
        lambda path: valid_video_probe(),
    )
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe())
    monkeypatch.setattr("core.stages.ingest.normalize_video_record", lambda project, record, progress: record.setdefault("normalized", {"path": record["path"]}))
    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Matrix.zuckervid"
    video = tmp_path / "clip.mov"
    master = tmp_path / "master.wav"
    songs = tmp_path / "songs.json"
    video.write_bytes(b"video")
    master.write_bytes(b"master")
    songs.write_text('{"songs": []}', encoding="utf-8")
    assert client.post("/api/v1/project", json={"name": "Matrix", "folder": str(folder)}).status_code == 201

    assert client.post("/api/v1/inputs/videos", json={"paths": [str(video)]}).status_code == 200
    status = client.get("/api/v1/stages/status").get_json()
    assert status["readiness"]["ingest"] == {"ready": True, "reasons": []}
    assert status["readiness"]["sync"]["ready"] is False
    assert "ingest is pending" in status["readiness"]["sync"]["reasons"]
    assert "Master audio is not registered" in status["readiness"]["sync"]["reasons"]
    assert "songs.json is not registered" in status["readiness"]["cut"]["reasons"]

    assert client.post("/api/v1/stages/ingest/run").status_code == 202
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get("/api/v1/stages/status").get_json()
        if not status["busy"] and status["stages"]["ingest"]["status"] == "done":
            break
        time.sleep(0.05)
    assert status["stages"]["ingest"]["status"] == "done"
    assert status["readiness"]["sync"]["ready"] is False
    assert status["readiness"]["sync"]["reasons"] == ["Master audio is not registered"]

    assert client.post("/api/v1/inputs/master", json={"master": str(master)}).status_code == 200
    status = client.get("/api/v1/stages/status").get_json()
    assert status["readiness"]["sync"]["ready"] is True
    assert status["readiness"]["cut"]["ready"] is False
    assert "songs.json is not registered" in status["readiness"]["cut"]["reasons"]

    assert client.post("/api/v1/inputs/master", json={"songs": str(songs)}).status_code == 200
    status = client.get("/api/v1/stages/status").get_json()
    assert status["readiness"]["cut"]["ready"] is False
    assert "sync is pending" in status["readiness"]["cut"]["reasons"]


def test_wizard_start_soft_rules_require_video_and_master(tmp_path):
    app = create_app()
    client = app.test_client()
    video = tmp_path / "clip.mov"
    master = tmp_path / "master.wav"
    video.write_bytes(b"video")
    master.write_bytes(b"master")

    response = client.post(
        "/api/v1/wizard/start",
        json={"name": "Jam", "platform": "youtube", "master": str(master), "videos": []},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_video"

    response = client.post(
        "/api/v1/wizard/start",
        json={"name": "Jam", "platform": "youtube", "master": "", "videos": [str(video)]},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_master"


def test_360_input_selection_keeps_audio_candidates(tmp_path, monkeypatch):
    """360 narrowing must filter only camera videos, never the master audio."""
    spherical = tmp_path / "camera.mp4"
    other_video = tmp_path / "sony.mp4"
    master = tmp_path / "master.mp3"
    spherical.write_bytes(b"360")
    other_video.write_bytes(b"sony")
    master.write_bytes(b"audio")

    def fake_classify(path):
        if path.suffix == ".mp3":
            return {"kind": "master"}
        if path.name == "camera.mp4":
            return {"kind": "videos", "projection": "equirect"}
        return {"kind": "videos", "projection": "flat"}

    monkeypatch.setattr("server.wizard.classify_file", fake_classify)
    selected_master, selected_videos = _select_360_inputs("", [str(spherical), str(other_video), str(master)])

    assert selected_master == str(master)
    assert selected_videos == [str(spherical)]


def test_wizard_start_after_relaunch_reuses_prepared_project(tmp_path, monkeypatch):
    folder = tmp_path / "Prepared.zuckervid"
    project = create_project("Prepared", str(folder))
    master = tmp_path / "master.wav"
    songs = tmp_path / "songs.json"
    video = tmp_path / "clip.mp4"
    master.write_bytes(b"master")
    songs.write_text('{"songs": []}', encoding="utf-8")
    video.write_bytes(b"video")
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["songs"] = file_record(str(songs))
    project.data["inputs"]["videos"] = [file_record(str(video))]
    sync_map = folder / "artifacts" / "sync_map.json"
    write_artifact_json(
        sync_map,
        {
            "schema_version": 1,
            "master_duration_sec": 3.0,
            "clips": {"clip": {"path": str(video.resolve()), "filename": "clip.mp4", "confidence": 9.0, "low_confidence": False}},
        },
    )
    project.data["stages"]["ingest"]["status"] = "done"
    project.data["stages"]["sync"]["status"] = "done"
    project.data["stages"]["sync"]["outputs"] = {"sync_map": str(sync_map)}
    project.save()
    monkeypatch.setattr("server.api.reconcile_registered_inputs", lambda project: False)
    monkeypatch.setattr("server.api.migrate_project_normalization_cache", lambda project: False)

    app = create_app(project_path=str(folder))
    state = app.config["ZUCKER_STATE"]

    def fake_start(**kwargs):
        assert state.wizard._prepared_project is state.project
        return WizardJob(id="current", status="running", project_path=str(state.project.folder))

    state.wizard.start = fake_start
    client = app.test_client()

    status = client.get("/api/v1/wizard/status").get_json()
    assert status["status"] == "waiting_choice"
    response = client.post(
        "/api/v1/wizard/start",
        json={
            "name": "Prepared",
            "platform": "youtube",
            "master": str(master),
            "songs": str(songs),
            "videos": [str(video)],
        },
    )

    assert response.status_code == 202
    assert response.get_json()["project_path"] == str(folder.resolve())


def test_wizard_prepare_adopts_matching_project_from_disk_without_suffix_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    projects_root = tmp_path / "ZuckerVideos" / "Projects"
    folder = projects_root / "Jam.zuckervid"
    project = create_project("Jam", str(folder))
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mp4"
    master.write_bytes(b"master")
    video.write_bytes(b"video")
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [file_record(str(video))]
    sync_map = folder / "artifacts" / "sync_map.json"
    write_artifact_json(
        sync_map,
        {
            "schema_version": 1,
            "master_duration_sec": 3.0,
            "clips": {"clip": {"path": str(video.resolve()), "filename": "clip.mp4", "confidence": 9.0, "low_confidence": False}},
        },
    )
    project.data["stages"]["sync"]["status"] = "done"
    project.data["stages"]["sync"]["outputs"] = {"sync_map": str(sync_map)}
    project.save()

    app = create_app()
    client = app.test_client()

    response = client.post("/api/v1/wizard/prepare", json={"name": "Jam", "master": str(master), "videos": [str(video)]})

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["status"] == "waiting_choice"
    assert payload["project_path"] == str(folder.resolve())
    assert not (projects_root / "Jam-2.zuckervid").exists()


def test_project_list_open_and_delete_keep_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "ZuckerVideos" / "Projects" / "Managed.zuckervid"
    project = create_project("Managed", str(folder))
    export = project.exports_dir / "Managed-youtube.mp4"
    export.write_bytes(b"mp4")
    manifest = project.artifacts_dir / "export_manifest.json"
    write_artifact_json(manifest, {"exports": [{"path": str(export), "platform": "youtube"}]})
    project.data["stages"]["export"]["status"] = "done"
    project.data["stages"]["export"]["outputs"] = {"export_manifest": str(manifest)}
    project.save()
    app = create_app()
    client = app.test_client()

    listed = client.get("/api/v1/wizard/projects").get_json()["projects"]
    assert listed[0]["name"] == "Managed"
    assert listed[0]["has_export"] is True

    opened = client.post("/api/v1/wizard/projects/open", json={"path": str(folder)}).get_json()
    assert opened["status"] == "done"
    assert opened["result"]["filename"] == "Managed-youtube.mp4"

    response = client.post("/api/v1/wizard/projects/delete", json={"path": str(folder), "keep_exports": True})

    assert response.status_code == 200
    assert not folder.exists()
    kept = response.get_json()["kept_exports"]
    assert len(kept) == 1
    assert Path(kept[0]).read_bytes() == b"mp4"


def test_new_project_action_clears_resume_without_deleting_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    folder = tmp_path / "ZuckerVideos" / "Projects" / "Resume.zuckervid"
    project = create_project("Resume", str(folder))
    project.save()
    app = create_app(project_path=str(folder))
    client = app.test_client()

    response = client.post("/api/v1/wizard/projects/new", json={})

    assert response.status_code == 200
    assert folder.exists()
    assert client.get("/api/v1/wizard/status").get_json()["status"] == "idle"
    assert "last_project_path" not in (tmp_path / "ZuckerVideos" / "config.json").read_text(encoding="utf-8")


@pytest.mark.slow
def test_wizard_orchestration_exports_tiny_media(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.stages.sync.sync_confidence_threshold", lambda project: 0.0)
    monkeypatch.setattr("core.stages.cut.sync_confidence_threshold", lambda project: 0.0)
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            str(master),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=321x241:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-vf",
            "select='not(eq(mod(n,5),0))'",
            "-vsync",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv444p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    app = create_app()
    client = app.test_client()
    response = client.post(
        "/api/v1/wizard/start",
        json={"name": "WizardTest", "platform": "youtube", "master": str(master), "videos": [str(video)]},
    )
    assert response.status_code == 202

    deadline = time.time() + 120
    status = {}
    while time.time() < deadline:
        status = client.get("/api/v1/wizard/status").get_json()
        if status["status"] in {"done", "failed"}:
            break
        time.sleep(0.1)

    assert status["status"] == "done", status
    export_path = Path(status["result"]["path"])
    assert export_path.exists()
    assert export_path.stat().st_size > 0
    assert client.get("/api/v1/wizard/result", headers={"Range": "bytes=0-4"}).status_code == 206
    assert (Path(status["result"]["project_path"]) / "cache" / "logs" / "ingest.log").exists()
    assert (Path(status["result"]["project_path"]) / "cache" / "logs" / "sync.log").exists()
    assert (Path(status["result"]["project_path"]) / "cache" / "logs" / "export.log").exists()
    project_json = json.loads((Path(status["result"]["project_path"]) / "project.json").read_text(encoding="utf-8"))
    normalized_path = Path(project_json["inputs"]["videos"][0]["normalized"]["path"])
    assert normalized_path.exists()
    first_normalized_mtime = normalized_path.stat().st_mtime
    normalized_metadata = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(normalized_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    normalized_video = next(stream for stream in normalized_metadata["streams"] if stream["codec_type"] == "video")
    assert normalized_video["codec_name"] == "h264"
    assert normalized_video["pix_fmt"] == "yuv420p"
    assert int(normalized_video["width"]) % 2 == 0
    assert int(normalized_video["height"]) % 2 == 0
    assert normalized_video["avg_frame_rate"] == normalized_video["r_frame_rate"]
    metadata = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(export_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    streams = metadata["streams"]
    video_stream = next(stream for stream in streams if stream["codec_type"] == "video")
    audio_stream = next(stream for stream in streams if stream["codec_type"] == "audio")
    assert sum(1 for stream in streams if stream["codec_type"] == "audio") == 1
    assert video_stream["codec_name"] == "h264"
    assert audio_stream["codec_name"] == "aac"
    assert int(video_stream["width"]) % 2 == 0
    assert int(video_stream["height"]) % 2 == 0
    assert float(metadata["format"]["duration"]) == pytest.approx(23.4, abs=1.2)

    first_export_size = export_path.stat().st_size
    response = client.post(
        "/api/v1/wizard/start",
        json={"name": "WizardTest", "platform": "youtube", "master": str(master), "videos": [str(video)]},
    )
    assert response.status_code == 202
    deadline = time.time() + 120
    second_status = {}
    while time.time() < deadline:
        second_status = client.get("/api/v1/wizard/status").get_json()
        if second_status["status"] in {"done", "failed"}:
            break
        time.sleep(0.1)

    assert second_status["status"] == "done", second_status
    second_export_path = Path(second_status["result"]["path"])
    assert second_export_path.exists()
    second_project_json = json.loads((Path(second_status["result"]["project_path"]) / "project.json").read_text(encoding="utf-8"))
    second_normalized_path = Path(second_project_json["inputs"]["videos"][0]["normalized"]["path"])
    assert second_normalized_path == normalized_path
    assert second_normalized_path.stat().st_mtime == pytest.approx(first_normalized_mtime, abs=0.001)
    assert second_export_path.stat().st_size > 0
    assert second_export_path.stat().st_size == pytest.approx(first_export_size, rel=0.05)


def test_wizard_cancel_stops_a_running_job(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("core.stages.sync.sync_confidence_threshold", lambda project: 0.0)
    monkeypatch.setattr("core.stages.cut.sync_confidence_threshold", lambda project: 0.0)
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(master)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=321x241:rate=30:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-vf",
            "select='not(eq(mod(n,5),0))'",
            "-vsync",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv444p",
            "-c:a",
            "aac",
            "-shortest",
            str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    app = create_app()
    client = app.test_client()
    response = client.post(
        "/api/v1/wizard/start",
        json={"name": "CancelTest", "platform": "youtube", "master": str(master), "videos": [str(video)]},
    )
    assert response.status_code == 202

    # Cancel essentially immediately — the job dataclass starts "running" the
    # instant start() returns, before the background thread has necessarily
    # done any real work, so this exercises the cooperative-cancel path
    # rather than racing a job that might already be finishing.
    cancel_response = client.post("/api/v1/wizard/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.get_json()["ok"] is True

    deadline = time.time() + 30
    status: dict = {}
    while time.time() < deadline:
        status = client.get("/api/v1/wizard/status").get_json()
        if status["status"] in {"done", "failed", "cancelled"}:
            break
        time.sleep(0.05)

    assert status["status"] == "cancelled", status
    assert status.get("error") is None


def test_wizard_cancel_returns_conflict_when_nothing_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app()
    client = app.test_client()

    response = client.post("/api/v1/wizard/cancel")

    assert response.status_code == 409


def test_api_error_envelope_without_open_project():
    app = create_app()
    client = app.test_client()

    response = client.get("/api/v1/project")

    assert response.status_code == 400
    assert response.get_json() == {"error": {"code": "project_error", "message": "No project is open"}}


def test_app_config_reports_desktop_mode():
    browser_app = create_app(dev=True)
    desktop_app = create_app(dev=False)

    assert browser_app.test_client().get("/api/v1/app/config").get_json()["desktop"] is False
    assert desktop_app.test_client().get("/api/v1/app/config").get_json()["desktop"] is True


def test_sync_override_endpoint_persists_and_marks_downstream_stale(tmp_path):
    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    video = tmp_path / "clip.mov"
    video.write_bytes(b"video")
    client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)})
    client.post("/api/v1/inputs/videos", json={"paths": [str(video)]})
    state = app.config["ZUCKER_STATE"]
    project = state.project
    clip_id = "clip123"
    artifact = folder / "artifacts" / "sync_map.json"
    artifact.write_text(
        """
{
  "schema_version": 1,
  "master_duration_sec": 60.0,
  "clips": {
    "clip123": {
      "path": "%s",
      "filename": "clip.mov",
      "offset_sec": 10.0,
      "duration_sec": 3.0,
      "confidence": 9.0,
      "low_confidence": false,
      "manual_override": false,
      "source_signature": {"path": "%s", "size": 5, "mtime": %s}
    }
  }
}
"""
        % (str(video), str(video), video.stat().st_mtime),
        encoding="utf-8",
    )
    project.data["stages"]["sync"]["status"] = "done"
    project.data["stages"]["sync"]["outputs"] = {"sync_map": str(artifact)}
    for name in ("cut", "edit", "export"):
        project.data["stages"][name]["status"] = "done"
    project.save()

    response = client.post("/api/v1/stages/sync/override", json={"clip_id": clip_id, "offset_sec": 12.5})

    assert response.status_code == 200
    payload = client.get("/api/v1/artifacts/sync").get_json()
    clip = payload["clips"][clip_id]
    assert clip["offset_sec"] == 12.5
    assert clip["detected_offset_sec"] == 10.0
    assert clip["manual_override"] is True
    assert project.data["stages"]["sync"]["status"] == "done"
    assert project.data["stages"]["cut"]["status"] == "stale"

    response = client.post("/api/v1/stages/sync/override/clear", json={"clip_id": clip_id})

    assert response.status_code == 200
    clip = client.get("/api/v1/artifacts/sync").get_json()["clips"][clip_id]
    assert clip["offset_sec"] == 10.0
    assert "detected_offset_sec" not in clip
    assert clip["manual_override"] is False


def test_wizard_rescue_endpoint_starts_manual_override_rerender(tmp_path):
    folder = tmp_path / "Rescue.zuckervid"
    create_project("Rescue", str(folder))
    app = create_app(project_path=str(folder))
    state = app.config["ZUCKER_STATE"]

    def fake_rescue(project_arg, *, clip_id, offset_sec):
        assert project_arg is state.project
        assert clip_id == "clip-a"
        assert offset_sec == 4.25
        return WizardJob(id="current", status="running", progress=0, project_path=str(project_arg.folder))

    state.wizard.rescue = fake_rescue
    client = app.test_client()

    response = client.post("/api/v1/wizard/rescue", json={"clip_id": "clip-a", "offset_sec": 4.25})

    assert response.status_code == 202
    assert response.get_json()["status"] == "running"


def test_register_status_run_ingest_end_to_end_regression(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.stages.ingest.ffprobe",
        lambda path: valid_video_probe("2.5"),
    )
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe("2.5"))
    monkeypatch.setattr("core.stages.ingest.normalize_video_record", lambda project, record, progress: record.setdefault("normalized", {"path": record["path"]}))
    master = tmp_path / "master.wav"
    songs = tmp_path / "songs.json"
    video_a = tmp_path / "clip-a.mov"
    video_b = tmp_path / "nested" / "clip-b.mp4"
    video_b.parent.mkdir()
    master.write_bytes(b"master")
    songs.write_text('{"songs": []}', encoding="utf-8")
    video_a.write_bytes(b"video-a")
    video_b.write_bytes(b"video-b")

    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    assert client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)}).status_code == 201

    registered = client.post(
        "/api/v1/inbox/register",
        json={"master": str(master), "songs": str(songs), "videos": [str(video_a), str(video_b)]},
    )
    assert registered.status_code == 200
    payload = registered.get_json()
    assert payload["inputs"]["master"]["path"] == str(master.resolve())
    assert payload["inputs"]["songs"]["path"] == str(songs.resolve())
    assert [record["path"] for record in payload["inputs"]["videos"]] == [str(video_a.resolve()), str(video_b.resolve())]

    status = client.get("/api/v1/stages/status").get_json()
    assert status["readiness"]["ingest"]["ready"] is True
    assert status["stages"]["ingest"]["status"] in {"pending", "stale"}

    response = client.post("/api/v1/stages/ingest/run")
    assert response.status_code == 202

    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get("/api/v1/stages/status").get_json()
        if not status["busy"] and status["stages"]["ingest"]["status"] == "done":
            break
        time.sleep(0.05)

    assert status["stages"]["ingest"]["status"] == "done"
    project = client.get("/api/v1/project").get_json()
    videos = project["inputs"]["videos"]
    assert [Path(record["path"]).name for record in videos] == ["clip-a.mov", "clip-b.mp4"]
    assert all(record["probe"]["video_codec"] == "h264" for record in videos)
    status = client.get("/api/v1/stages/status").get_json()
    assert status["readiness"]["sync"]["ready"] is True


def test_master_replacement_preserves_clip_cache_and_marks_sync_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe("2.5"))
    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    master_a = tmp_path / "master-a.wav"
    master_b = tmp_path / "master-b.wav"
    video = tmp_path / "clip.mp4"
    for path in (master_a, master_b, video):
        path.write_bytes(b"media")
    assert client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)}).status_code == 201
    assert client.post("/api/v1/inbox/register", json={"master": str(master_a), "videos": [str(video)]}).status_code == 200
    project = load_project(str(folder))
    project.data["inputs"]["videos"][0]["cache_key"] = "global-key"
    project.data["inputs"]["videos"][0]["normalized"] = {"path": "/cache/proxy.mp4", "cache_key": "global-key", "kind": "proxy"}
    for stage in ("ingest", "sync", "cut", "edit", "export"):
        project.data["stages"][stage]["status"] = "done"
    project.save()
    app.config["ZUCKER_STATE"].project = project

    response = client.post("/api/v1/inputs/master", json={"master": str(master_b)})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["inputs"]["videos"][0]["cache_key"] == "global-key"
    assert payload["stages"]["ingest"]["status"] == "done"
    assert payload["stages"]["sync"]["status"] == "stale"


def test_same_path_master_replacement_blocks_prepared_project_reuse(tmp_path):
    folder = tmp_path / "Jam.zuckervid"
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mp4"
    master.write_bytes(b"old")
    video.write_bytes(b"video")
    project = create_project("Jam", str(folder))
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [file_record(str(video))]
    for stage in ("sync", "cut", "edit", "export"):
        project.data["stages"][stage]["status"] = "done"
    project.save()

    time.sleep(0.01)
    master.write_bytes(b"new-audio-master")

    assert _can_reuse_prepared_project(project, str(master), None, [str(video)]) is False
    assert project.data["stages"]["sync"]["status"] == "stale"


def test_spherical_landmarks_accept_comma_decimal_and_normalize_yaw():
    result = _sanitize_spherical_landmarks(
        {
            "singer": {"yaw": "-23,2", "pitch": "-28,8", "fov": "74,8", "weight": "1,5"},
            "right": {"yaw": "322,1", "pitch": "-15.0", "fov": "95.0", "weight": "0"},
        }
    )

    assert result["singer"] == {"yaw": 336.8, "pitch": -28.8, "fov": 74.8, "weight": 1.5}
    assert result["right"]["yaw"] == 322.1
    assert result["right"]["weight"] == 0.0


def test_spherical_preview_frame_renders_cached_vertical_fov_jpeg(tmp_path, monkeypatch):
    project = create_project("Preview", str(tmp_path / "Preview.zuckervid"))
    source = tmp_path / "sphere.mp4"
    source.write_bytes(b"video")
    calls = []

    monkeypatch.setattr("server.api.tool_status", lambda: {"ffmpeg_path": "ffmpeg"})
    monkeypatch.setattr("server.api.ffprobe", lambda path: {"format": {"duration": "10"}})

    def fake_run(command, capture_output, text, check):
        calls.append(command)
        Path(command[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("server.api.subprocess.run", fake_run)

    first = _spherical_preview_frame(project, str(source), 336.8, -28.8, 80.0, quality="drag")
    second = _spherical_preview_frame(project, str(source), 336.8, -28.8, 80.0, quality="drag")

    assert first == second
    assert first.read_bytes() == b"jpg"
    assert len(calls) == 1
    command_text = " ".join(calls[0])
    assert "v360=input=equirect:output=flat:yaw=-23.200:pitch=-28.800:h_fov=80.000:v_fov=50.534" in command_text


def test_wide_shot_preview_is_stereographic_like_the_export(tmp_path, monkeypatch):
    # The preview exists to show what will be rendered. A wide shot renders
    # stereographically (a rectilinear view tears as it nears 180 deg), so a
    # preview that stayed flat would show a framing the export never produces.
    project = create_project("WidePreview", str(tmp_path / "WidePreview.zuckervid"))
    source = tmp_path / "sphere.mp4"
    source.write_bytes(b"video")
    calls = []

    monkeypatch.setattr("server.api.tool_status", lambda: {"ffmpeg_path": "ffmpeg"})
    monkeypatch.setattr("server.api.ffprobe", lambda path: {"format": {"duration": "10"}})

    def fake_run(command, capture_output, text, check):
        calls.append(command)
        Path(command[-1]).write_bytes(b"jpg")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("server.api.subprocess.run", fake_run)

    _spherical_preview_frame(project, str(source), 0.0, -20.0, 240.0, quality="final")

    assert "v360=input=equirect:output=sg:" in " ".join(calls[0])
    assert "h_fov=240.000" in " ".join(calls[0])


def test_preview_and_export_agree_on_when_a_shot_goes_stereographic():
    # server.api deliberately keeps its own copies of this geometry rather than
    # importing the export stage into the web server (as it already does for
    # _paired_flat_fov). Pin the copies together so they cannot drift into
    # previewing one projection and rendering the other.
    from core.stages import export

    assert STEREOGRAPHIC_PREVIEW_FOV_THRESHOLD == export.STEREOGRAPHIC_FOV_THRESHOLD
    assert MAX_PREVIEW_FOV == export.MAX_SPHERICAL_FOV


def test_camera_move_routes_save_list_and_delete_take(tmp_path):
    project = create_project("MoveRoutes", str(tmp_path / "MoveRoutes.zuckervid"))
    app = create_app()
    app.config["ZUCKER_STATE"].project = project
    client = app.test_client()
    samples = [{"t": index / 15, "yaw": index, "pitch": 0, "fov": 100} for index in range(20)]

    response = client.post("/api/v1/wizard/camera-moves", json={"name": "Main", "samples": samples})
    assert response.status_code == 201
    assert response.get_json()["take"]["sample_count"] == 20
    assert client.get("/api/v1/wizard/camera-moves").get_json()["takes"][0]["name"] == "Main"
    assert client.delete("/api/v1/wizard/camera-moves/Main").status_code == 200
    assert client.get("/api/v1/wizard/camera-moves").get_json()["takes"] == []


def test_director_media_route_returns_proxy_master_and_sync_offset(tmp_path, monkeypatch):
    project = create_project("Director", str(tmp_path / "Director.zuckervid"))
    source = tmp_path / "wide360.mp4"
    proxy = project.cache_dir / "director_proxies" / "proxy.mp4"
    source.write_bytes(b"source")
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"proxy")
    project.data["inputs"]["master"] = {"path": str(tmp_path / "master.wav")}
    Path(project.data["inputs"]["master"]["path"]).write_bytes(b"master")
    project.data["inputs"]["videos"] = [{"path": str(source), "projection": "equirect", "probe": {"projection": "equirect"}}]
    project.save()
    monkeypatch.setattr(
        "server.api.director_proxy_status",
        lambda record: {"ready": True, "path": str(proxy), "duration_sec": 9.0, "size_bytes": 5, "width": 1280, "height": 640, "fps": 15},
    )
    monkeypatch.setattr("server.api._director_sync_offset", lambda project, record: 12.34)
    monkeypatch.setattr("server.api._preview_source_duration", lambda path: 9.0)
    app = create_app()
    app.config["ZUCKER_STATE"].project = project

    response = app.test_client().get("/api/v1/wizard/director-media")

    assert response.status_code == 200
    data = response.get_json()
    assert data["proxy_ready"] is True
    assert data["video_url"] == "/api/v1/media/director-proxy/proxy.mp4"
    assert data["master_url"] == "/api/v1/wizard/master-preview"
    assert data["offset_sec"] == 12.34
    assert data["duration_sec"] == 9.0


def test_director_media_missing_proxy_returns_progress_job(tmp_path, monkeypatch):
    project = create_project("Director", str(tmp_path / "Director.zuckervid"))
    source = tmp_path / "wide360.mp4"
    proxy = tmp_path / "global-proxy.mp4"
    source.write_bytes(b"source")
    project.data["inputs"]["master"] = {"path": str(tmp_path / "master.wav")}
    Path(project.data["inputs"]["master"]["path"]).write_bytes(b"master")
    project.data["inputs"]["videos"] = [{"path": str(source), "projection": "equirect", "probe": {"projection": "equirect"}}]
    project.save()
    monkeypatch.setattr("server.api.director_proxy_status", lambda record: {"ready": False, "path": str(proxy)})
    monkeypatch.setattr("server.api._director_sync_offset", lambda project, record: 1.25)
    monkeypatch.setattr("server.api._preview_source_duration", lambda path: 12.0)

    def fake_ensure(record, progress=None):
        if progress:
            progress(55, "Preparing lightweight 360 preview — 55%")
        return {"ready": True, "path": str(proxy), "duration_sec": 12.0, "size_bytes": 123, "width": 1280, "height": 640, "fps": 15}

    monkeypatch.setattr("server.api.ensure_director_proxy", fake_ensure)
    app = create_app()
    app.config["ZUCKER_STATE"].project = project
    client = app.test_client()

    response = client.get("/api/v1/wizard/director-media")

    assert response.status_code == 200
    initial = response.get_json()
    assert initial["proxy_ready"] is False
    assert initial["job_id"]
    for _ in range(20):
        status = client.get(f"/api/v1/wizard/director-media/status?job_id={initial['job_id']}").get_json()
        if status.get("proxy_ready"):
            break
        time.sleep(0.05)
    assert status["proxy_ready"] is True
    assert status["video_url"] == "/api/v1/media/director-proxy/global-proxy.mp4"
    assert status["proxy_width"] == 1280
    assert status["proxy_fps"] == 15


def test_project_status_running_wins_over_existing_export_manifest(tmp_path):
    project = create_project("Status", str(tmp_path / "Status.zuckervid"))
    export_path = tmp_path / "done.mp4"
    export_path.write_bytes(b"video")
    manifest = project.artifacts_dir / "export_manifest.json"
    write_artifact_json(manifest, {"exports": [{"path": str(export_path), "platform": "youtube"}]})
    project.data["stages"]["export"] = {"status": "running", "outputs": {"export_manifest": str(manifest)}}

    status = _project_wizard_status(project)

    assert status["status"] == "running"


def test_camera_move_smoothing_strength_is_persisted_and_stronger(tmp_path):
    project = create_project("Moves", str(tmp_path / "Moves.zuckervid"))
    samples = [{"t": index / 15, "yaw": 0 if index % 2 == 0 else 20, "pitch": 0, "fov": 100} for index in range(20)]

    take = save_camera_move(project, "Strong", samples, smoothing="strong")

    data = json.loads(Path(take["path"]).read_text(encoding="utf-8"))
    assert data["smoothing"] == "strong"
    raw_delta = max(sample["yaw"] for sample in data["raw"]) - min(sample["yaw"] for sample in data["raw"])
    smooth_delta = max(sample["yaw"] for sample in data["smoothed"]) - min(sample["yaw"] for sample in data["smoothed"])
    assert smooth_delta < raw_delta


def test_adding_video_only_marks_ingest_and_dedupes_existing_clip(tmp_path, monkeypatch):
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe("2.5"))
    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    video_a.write_bytes(b"a")
    video_b.write_bytes(b"b")
    assert client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)}).status_code == 201
    assert client.post("/api/v1/inputs/videos", json={"paths": [str(video_a)]}).status_code == 200
    project = load_project(str(folder))
    project.data["inputs"]["videos"][0]["cache_key"] = "a-key"
    project.data["inputs"]["videos"][0]["normalized"] = {"path": "/cache/a.mp4", "cache_key": "a-key"}
    project.data["stages"]["ingest"]["status"] = "done"
    project.save()
    app.config["ZUCKER_STATE"].project = project

    response = client.post("/api/v1/inputs/videos", json={"paths": [str(video_a), str(video_b)], "append": True})

    assert response.status_code == 200
    payload = response.get_json()
    assert [Path(record["path"]).name for record in payload["inputs"]["videos"]] == ["a.mp4", "b.mp4"]
    assert payload["inputs"]["videos"][0]["cache_key"] == "a-key"
    assert payload["stages"]["ingest"]["status"] == "stale"


def test_missing_video_is_dropped_on_project_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe("2.5"))
    folder = tmp_path / "Jam.zuckervid"
    project = create_project("Jam", str(folder))
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    video_a.write_bytes(b"a")
    video_b.write_bytes(b"b")
    project.data["inputs"]["videos"] = [file_record(str(video_a)), file_record(str(video_b))]
    project.data["stages"]["ingest"]["status"] = "done"
    project.save()
    video_b.unlink()

    app = create_app(project_path=str(folder))
    client = app.test_client()
    payload = client.get("/api/v1/project").get_json()

    assert [Path(record["path"]).name for record in payload["inputs"]["videos"]] == ["a.mp4"]
    assert payload["stages"]["ingest"]["status"] == "stale"


def test_ingest_demotes_registered_invalid_clip(tmp_path, monkeypatch):
    project = create_project("Invalid", str(tmp_path / "Invalid.zuckervid"))
    video = tmp_path / "mix_report.txt"
    video.write_text("not video", encoding="utf-8")
    project.data["inputs"]["videos"] = [file_record(str(video))]
    monkeypatch.setattr(
        "core.stages.ingest.ffprobe",
        lambda path: {
            "format": {"duration": "3.0", "format_name": "tty"},
            "streams": [{"codec_type": "video", "codec_name": "ansi", "width": 80, "height": 25}],
        },
    )

    IngestStage().run(project, lambda percent, message: None)

    record = project.data["inputs"]["videos"][0]
    assert record["status"] == "not_a_video"
    assert record["not_a_video_reason"] == "not a camera video"
    assert record["probe"]["valid_video"] is False


def test_cut_fails_cleanly_when_no_valid_confident_clip(tmp_path):
    project = create_project("NoClip", str(tmp_path / "NoClip.zuckervid"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"bad")
    record = file_record(str(video))
    record["status"] = "not_a_video"
    record["not_a_video_reason"] = "not a camera video"
    record["probe"] = {"valid_video": False, "video_codec": "ansi", "duration": 3.0, "width": 80, "height": 25}
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "youtube", "song_choice": None}
    write_artifact_json(
        project.artifacts_dir / "sync_map.json",
        {
            "schema_version": 1,
            "confidence_threshold": 6.0,
            "master_duration_sec": 30.0,
            "clips": {
                "bad": {
                    "path": str(video),
                    "filename": "clip.mp4",
                    "offset_sec": 0.0,
                    "duration_sec": 3.0,
                    "confidence": 9.0,
                    "low_confidence": False,
                }
            },
        },
    )

    with pytest.raises(ValueError) as exc_info:
        CutStage().run(project, lambda percent, message: None)
    message = str(exc_info.value)
    assert "None of the files looks like a usable camera video" in message
    assert "clip.mp4: valid video=no, confidence=9.000, threshold=6.000" in message


def test_cut_excludes_low_confidence_clip_without_manual_override(tmp_path):
    project = create_project("CachedCut", str(tmp_path / "CachedCut.zuckervid"))
    source = tmp_path / "clip.mov"
    normalized = tmp_path / "global-cache" / "normalized" / "clip.mp4"
    master = tmp_path / "master.wav"
    source.write_bytes(b"source")
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"normalized")
    master.write_bytes(b"master")
    record = file_record(str(source))
    record["probe"] = {
        "valid_video": True,
        "video_codec": "h264",
        "duration": 10.0,
        "width": 1280,
        "height": 720,
    }
    record["cache_key"] = "cache-key"
    record["normalized"] = {"path": str(normalized), "cache_key": "cache-key", "source_size": record["size"], "source_mtime": record["mtime"]}
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "youtube", "song_choice": None}
    project.data["settings"]["sync"]["confidence_threshold"] = 6.0
    write_artifact_json(
        project.artifacts_dir / "sync_map.json",
        {
            "schema_version": 1,
            "confidence_threshold": 6.0,
            "master_duration_sec": 10.0,
            "clips": {
                "cached": {
                    "path": str(normalized),
                    "source_path": str(source.resolve()),
                    "filename": "clip.mov",
                    "offset_sec": 0.0,
                    "duration_sec": 10.0,
                    "confidence": 2.5,
                    "low_confidence": True,
                    "manual_override": False,
                }
            },
        },
    )

    with pytest.raises(ValueError) as exc_info:
        CutStage().run(project, lambda percent, message: None)
    assert "clip.mov: valid video=yes, confidence=2.500, threshold=6.000 (low confidence)" in str(exc_info.value)


def test_cut_accepts_low_confidence_single_360_clip(tmp_path):
    project = create_project("Low360", str(tmp_path / "Low360.zuckervid"))
    source = tmp_path / "wide.mp4"
    master = tmp_path / "master.wav"
    source.write_bytes(b"source")
    master.write_bytes(b"master")
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "video_codec": "h264", "duration": 10.0, "width": 3840, "height": 1920, "projection": "equirect"}
    record["projection"] = "equirect"
    record["normalized"] = {"path": str(source)}
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "360", "song_choice": None, "audio_trim": {"start_sec": 0.0, "end_sec": 8.0}}
    write_artifact_json(
        project.artifacts_dir / "sync_map.json",
        {"schema_version": 1, "confidence_threshold": 6.0, "master_duration_sec": 10.0, "clips": {
            "wide": {"path": str(source), "source_path": str(source), "filename": "wide.mp4", "offset_sec": 0.0,
                     "duration_sec": 10.0, "confidence": 4.54, "low_confidence": True, "unstable_sync": True}
        }},
    )

    CutStage().run(project, lambda percent, message: None)
    coverage = json.loads((project.artifacts_dir / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["segments"]
    assert coverage["excluded_clips"] == []
    assert any("sync confidence low" in warning for warning in coverage["warnings"])


def test_cut_and_export_use_manual_override_global_cached_clip(tmp_path, monkeypatch):
    project = create_project("CachedCut", str(tmp_path / "CachedCut.zuckervid"))
    source = tmp_path / "clip.mov"
    normalized = tmp_path / "global-cache" / "normalized" / "clip.mp4"
    master = tmp_path / "master.wav"
    source.write_bytes(b"source")
    normalized.parent.mkdir(parents=True)
    normalized.write_bytes(b"normalized")
    master.write_bytes(b"master")
    record = file_record(str(source))
    record["probe"] = {
        "valid_video": True,
        "video_codec": "h264",
        "duration": 10.0,
        "width": 1280,
        "height": 720,
    }
    record["cache_key"] = "cache-key"
    record["normalized"] = {"path": str(normalized), "cache_key": "cache-key", "source_size": record["size"], "source_mtime": record["mtime"]}
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "youtube", "song_choice": None}
    project.data["settings"]["sync"]["confidence_threshold"] = 6.0
    write_artifact_json(
        project.artifacts_dir / "sync_map.json",
        {
            "schema_version": 1,
            "confidence_threshold": 6.0,
            "master_duration_sec": 10.0,
            "clips": {
                "cached": {
                    "path": str(normalized),
                    "source_path": str(source.resolve()),
                    "filename": "clip.mov",
                    "offset_sec": 0.0,
                    "duration_sec": 10.0,
                    "confidence": 2.5,
                    "low_confidence": True,
                    "manual_override": True,
                }
            },
        },
    )

    monkeypatch.setattr(
        "core.stages.export._render_plan",
        lambda project, segments, master_path, output_path, platform, video_bitrate, warnings, progress_callback: output_path.write_bytes(b"export"),
    )

    CutStage().run(project, lambda percent, message: None)
    coverage = json.loads((project.artifacts_dir / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["segments"][0]["clip_path"] == str(normalized)
    assert coverage["excluded_clips"] == []

    ExportStage().run(project, lambda percent, message: None)
    manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
    assert Path(manifest["exports"][0]["path"]).read_bytes() == b"export"
    assert manifest["warnings"] == []


def test_sync_skips_ingest_demoted_clip(tmp_path, monkeypatch):
    project = create_project("SkipInvalid", str(tmp_path / "SkipInvalid.zuckervid"))
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mp4"
    master.write_bytes(b"master")
    video.write_bytes(b"bad")
    project.data["inputs"]["master"] = file_record(str(master))
    record = file_record(str(video))
    record["status"] = "not_a_video"
    record["not_a_video_reason"] = "not a camera video"
    record["probe"] = {"valid_video": False, "video_codec": "ansi", "duration": 3.0, "width": 80, "height": 25}
    project.data["inputs"]["videos"] = [record]
    monkeypatch.setattr("core.stages.sync.load_or_compute_master_envelope", lambda project: [])
    monkeypatch.setattr("core.stages.sync.media_duration", lambda path: 30.0)

    SyncStage().run(project, lambda percent, message: None)

    payload = json.loads((project.artifacts_dir / "sync_map.json").read_text(encoding="utf-8"))
    assert payload["clips"] == {}


def test_spherical_landmark_sanitizer_preserves_saved_values_and_defaults_blanks():
    landmarks = _sanitize_spherical_landmarks({"singer": {"yaw": "-23.2", "pitch": "-28.8", "fov": "74.8", "weight": "1"}})
    blank_defaults = _sanitize_spherical_landmarks({"singer": {"yaw": "-23.2", "pitch": "", "fov": "", "weight": ""}})

    assert landmarks["singer"] == {"yaw": 336.8, "pitch": -28.8, "fov": 74.8, "weight": 1.0}
    assert blank_defaults["singer"] == {"yaw": 336.8, "pitch": 0.0, "fov": 95.0, "weight": 1.0}


def test_camera_role_weight_sanitizer_allows_zero_exclusion():
    weights = _sanitize_camera_role_weights({"360": "0", "handheld": "1", "fixed_rear": ""})

    assert weights == {"360": 0.0, "handheld": 1.0, "fixed_rear": 0.0}


def test_edit_type_ui_separates_camera_mix_from_360_landmarks():
    html = Path("web/index.html").read_text(encoding="utf-8")
    camera_mix = html.split('id="cameraMix"', 1)[1].split('id="sphericalSetup"', 1)[0]
    spherical_setup = html.split('id="sphericalSetup"', 1)[1].split('id="startWizard"', 1)[0]

    assert 'data-camera-role="handheld"' in camera_mix
    assert 'data-camera-role="fixed_rear"' in camera_mix
    assert 'data-field="yaw"' not in camera_mix
    assert 'data-field="pitch"' not in camera_mix
    assert 'data-spherical-landmark="right"' in spherical_setup
    assert 'data-field="yaw"' in spherical_setup
    assert 'data-field="fov"' in spherical_setup


def test_director_entry_point_and_mode_choice_render_in_360_setup():
    html = Path("web/index.html").read_text(encoding="utf-8")
    spherical_setup = html.split('id="sphericalSetup"', 1)[1].split('id="startWizard"', 1)[0]

    assert "Direct the 360 camera live" in spherical_setup
    assert 'id="openDirector"' in spherical_setup
    assert 'name="sphericalMode" value="automatic"' in spherical_setup
    assert 'name="sphericalMode" value="directed"' in spherical_setup


def test_audio_trim_persists_by_master_in_global_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    master = str(tmp_path / "song.wav")

    _store_audio_trim(master, {"start_sec": 12.5, "end_sec": 98.0})
    config = load_global_config()

    assert config["audio_trim_by_master"][master] == {"start_sec": 12.5, "end_sec": 98.0}
