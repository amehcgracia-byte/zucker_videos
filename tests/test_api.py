from __future__ import annotations

import time
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.project import create_project, file_record
from core.stages.base import write_artifact_json
from core.stages.cut import CutStage
from core.stages.ingest import IngestStage
from core.stages.sync import SyncStage
from server.api import create_app


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
            "testsrc=size=321x241:rate=15:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
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

    deadline = time.time() + 20
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
    assert video_stream["codec_name"] == "h264"
    assert audio_stream["codec_name"] == "aac"
    assert int(video_stream["width"]) % 2 == 0
    assert int(video_stream["height"]) % 2 == 0
    assert float(metadata["format"]["duration"]) == pytest.approx(3.0, abs=0.5)


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
        lambda path: valid_video_probe("2.5"),
    )
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe("2.5"))
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
    assert record["not_a_video_reason"] == "no es un vídeo de cámara"
    assert record["probe"]["valid_video"] is False


def test_cut_fails_cleanly_when_no_valid_confident_clip(tmp_path):
    project = create_project("NoClip", str(tmp_path / "NoClip.zuckervid"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"bad")
    record = file_record(str(video))
    record["status"] = "not_a_video"
    record["not_a_video_reason"] = "no es un vídeo de cámara"
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

    with pytest.raises(ValueError, match="Ninguno de los archivos parece un vídeo de cámara utilizable"):
        CutStage().run(project, lambda percent, message: None)


def test_sync_skips_ingest_demoted_clip(tmp_path, monkeypatch):
    project = create_project("SkipInvalid", str(tmp_path / "SkipInvalid.zuckervid"))
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mp4"
    master.write_bytes(b"master")
    video.write_bytes(b"bad")
    project.data["inputs"]["master"] = file_record(str(master))
    record = file_record(str(video))
    record["status"] = "not_a_video"
    record["not_a_video_reason"] = "no es un vídeo de cámara"
    record["probe"] = {"valid_video": False, "video_codec": "ansi", "duration": 3.0, "width": 80, "height": 25}
    project.data["inputs"]["videos"] = [record]
    monkeypatch.setattr("core.stages.sync.load_or_compute_master_envelope", lambda project: [])
    monkeypatch.setattr("core.stages.sync.media_duration", lambda path: 30.0)

    SyncStage().run(project, lambda percent, message: None)

    payload = json.loads((project.artifacts_dir / "sync_map.json").read_text(encoding="utf-8"))
    assert payload["clips"] == {}
