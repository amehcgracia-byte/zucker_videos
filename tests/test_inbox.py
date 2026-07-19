from __future__ import annotations

import io
import json

from server.api import create_app
from server.inbox import classify_paths, scan_input_paths


def test_inbox_classification_extensions_and_invalid_songs(tmp_path, monkeypatch):
    master = tmp_path / "master.wav"
    video = tmp_path / "clip.mov"
    valid_songs = tmp_path / "songs.json"
    invalid_json = tmp_path / "notes.json"
    ignored = tmp_path / "readme.txt"
    ambiguous = tmp_path / "camera.bin"
    for path in (master, video, ignored, ambiguous):
        path.write_bytes(b"x")
    valid_songs.write_text(json.dumps({"songs": []}), encoding="utf-8")
    invalid_json.write_text(json.dumps({"items": []}), encoding="utf-8")

    monkeypatch.setattr(
        "server.inbox.ffprobe",
        lambda path: {"streams": [{"codec_type": "video"}]} if path.endswith("camera.bin") else {"streams": []},
    )

    result = classify_paths([str(master), str(video), str(valid_songs), str(invalid_json), str(ignored), str(ambiguous)])

    assert [item["filename"] for item in result["master"]] == ["master.wav"]
    assert sorted(item["filename"] for item in result["videos"]) == ["camera.bin", "clip.mov"]
    assert [item["filename"] for item in result["songs"]] == ["songs.json"]
    ignored_notes = {item["filename"]: item["note"] for item in result["ignored"]}
    assert ignored_notes["notes.json"] == "JSON ignored: missing songs array"
    assert ignored_notes["readme.txt"] == "unsupported file type"


def test_shared_scan_function_recurses_nested_dirs(tmp_path):
    root = tmp_path / "drop"
    nested = root / "camera" / "day-1"
    nested.mkdir(parents=True)
    video = nested / "clip.mp4"
    master = root / "master.wav"
    ignored = nested / "notes.txt"
    video.write_bytes(b"video")
    master.write_bytes(b"master")
    ignored.write_text("notes", encoding="utf-8")

    files = scan_input_paths([str(root)])

    assert files == sorted([master.resolve(), video.resolve(), ignored.resolve()])


def test_classify_paths_recurses_folder_paths_like_inbox(tmp_path):
    root = tmp_path / "drop"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "master.wav").write_bytes(b"master")
    (root / "songs.json").write_text(json.dumps({"songs": []}), encoding="utf-8")
    (nested / "clip.mov").write_bytes(b"video")
    (nested / "ignore.txt").write_text("ignore", encoding="utf-8")

    result = classify_paths([str(root)])

    assert [item["filename"] for item in result["master"]] == ["master.wav"]
    assert [item["filename"] for item in result["songs"]] == ["songs.json"]
    assert [item["filename"] for item in result["videos"]] == ["clip.mov"]
    assert [item["filename"] for item in result["ignored"]] == ["ignore.txt"]


def test_register_from_inbox_api_refs_and_missing_badge(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    inbox = tmp_path / "ZuckerVideos" / "Inbox"
    inbox.mkdir(parents=True)
    master = inbox / "master.wav"
    songs = inbox / "songs.json"
    video = inbox / "clip.mov"
    master.write_bytes(b"master")
    songs.write_text(json.dumps({"songs": []}), encoding="utf-8")
    video.write_bytes(b"video")

    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    assert client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)}).status_code == 201

    scan = client.get("/api/v1/inbox")
    assert scan.status_code == 200
    result = scan.get_json()
    assert result["inbox_path"] == str(inbox.resolve())
    assert result["master"][0]["filename"] == "master.wav"

    response = client.post(
        "/api/v1/inbox/register",
        json={"master": str(master), "songs": str(songs), "videos": [str(video)]},
    )

    assert response.status_code == 200
    project = response.get_json()
    assert project["inputs"]["master"]["path"] == str(master.resolve())
    assert project["inputs"]["songs"]["path"] == str(songs.resolve())
    assert project["inputs"]["videos"][0]["path"] == str(video.resolve())

    video.unlink()
    project = client.get("/api/v1/project").get_json()
    assert project["inputs"]["videos"][0]["missing"] is True


def test_upload_endpoint_saves_and_classifies(tmp_path):
    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)})

    response = client.post(
        "/api/v1/inputs/upload",
        data={"files": (io.BytesIO(b"abc"), "clip.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["videos"][0]["filename"] == "clip.mp4"
    uploaded = folder / "inputs" / "uploads" / "clip.mp4"
    assert uploaded.read_bytes() == b"abc"
