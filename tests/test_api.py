from __future__ import annotations

import time
from pathlib import Path

from server.api import create_app


def test_api_create_project_run_stub_stage_and_poll(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.stages.ingest.ffprobe",
        lambda path: {
            "format": {"duration": "1.0", "format_name": "mov"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080}],
        },
    )
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


def test_api_error_envelope_without_open_project():
    app = create_app()
    client = app.test_client()

    response = client.get("/api/v1/project")

    assert response.status_code == 400
    assert response.get_json() == {"error": {"code": "project_error", "message": "No project is open"}}


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


def test_register_status_run_ingest_end_to_end_regression(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "core.stages.ingest.ffprobe",
        lambda path: {
            "format": {"duration": "2.5", "format_name": "mov"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        },
    )
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
