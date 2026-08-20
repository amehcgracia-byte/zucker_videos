from __future__ import annotations

import io
import json

from server.api import create_app
import server.inbox as inbox
from server.inbox import classify_paths, paired_insv_path, reconcile_registered_inputs, scan_inbox, scan_input_paths
from core.project import create_project, file_record


def valid_video_probe(duration: str = "3.0") -> dict:
    return {
        "format": {"duration": duration, "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def test_inbox_analysis_skips_files_moved_during_scan(tmp_path, monkeypatch):
    """A camera import changing underneath the scan must not lose the scan."""
    inbox_root = tmp_path / "Inbox"
    inbox_root.mkdir()
    vanished = inbox_root / "moved.mp4"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(inbox, "scan_input_paths", lambda _paths: [vanished])
    inbox._run_inbox_analysis(str(inbox_root))
    status = inbox.inbox_analysis_snapshot()
    assert status["status"] == "done"
    assert status["files"] == []


def test_inbox_analysis_persists_and_reuses_file_pairs(tmp_path, monkeypatch):
    inbox_root = tmp_path / "Inbox"
    inbox_root.mkdir()
    video = inbox_root / "clip.mp4"
    master = inbox_root / "song.mp3"
    video.write_bytes(b"video")
    master.write_bytes(b"audio")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(inbox, "scan_input_paths", lambda _paths: [video, master])
    monkeypatch.setattr(inbox, "classify_file", lambda path: {
        "kind": "videos" if path.suffix == ".mp4" else "master",
        "path": str(path), "filename": path.name, "size": path.stat().st_size,
        "mtime": path.stat().st_mtime, "note": "test", "checked": True,
        **({"probe": valid_video_probe()} if path.suffix == ".mp4" else {"duration": 3.0}),
    })
    monkeypatch.setattr(inbox, "video_classification_metadata", lambda _path: {"probe": valid_video_probe()})
    monkeypatch.setattr(inbox, "normalize_video_record", lambda _project, record, _progress: {"path": record["path"]})
    monkeypatch.setattr(inbox, "load_or_compute_master_envelope", lambda _project: {"samples": []})
    monkeypatch.setattr(inbox, "sync_clip", lambda *_args: {"confidence": 8.0, "offset_sec": 0.0, "duration_sec": 3.0})
    inbox._run_inbox_analysis(str(inbox_root))
    first = inbox.inbox_analysis_snapshot()
    assert first["status"] == "done"
    assert len(first["masters"]) == 1
    assert first["masters"][0]["matches"][0]["master_overlap"]
    monkeypatch.setattr(inbox, "classify_file", lambda _path: (_ for _ in ()).throw(AssertionError("recomputed")))
    inbox._run_inbox_analysis(str(inbox_root))
    assert inbox.inbox_analysis_snapshot()["status"] == "done"


def test_source_folders_scan_recursively_and_report_unmounted_drive(tmp_path, monkeypatch):
    inbox_root = tmp_path / "Inbox"
    external_root = tmp_path / "Mounted" / "Session"
    (external_root / "raw" / "sound").mkdir(parents=True)
    inbox_root.mkdir()
    (external_root / "raw" / "clip.mp4").write_bytes(b"video")
    (external_root / "raw" / "sound" / "song.mp3").write_bytes(b"audio")
    (external_root / "raw" / "sound" / "guitar.wav").write_bytes(b"stem")
    missing = tmp_path / "Volumes" / "RAWVideos"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = {"inbox_path": str(inbox_root), "source_folders": [str(external_root), str(missing)]}
    monkeypatch.setattr(inbox, "load_global_config", lambda: config)
    monkeypatch.setattr(inbox, "classify_file", lambda path: {
        "kind": "videos" if path.suffix == ".mp4" else "master",
        "path": str(path), "filename": path.name, "note": "test", "checked": True,
        **({"probe": valid_video_probe()} if path.suffix == ".mp4" else {"duration": 3.0}),
    })
    result = scan_inbox()
    assert [item["filename"] for item in result["videos"]] == ["clip.mp4"]
    assert [item["filename"] for item in result["master"]] == ["song.mp3"]
    assert [item["filename"] for item in result["ignored"]] == ["guitar.wav"]
    assert result["groups"][0]["path"] == str(external_root.resolve())
    assert result["groups"][1]["available"] is False
    assert "drive not mounted" in result["missing_sources"][0]


def test_source_folder_settings_api_preserves_unavailable_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = create_app(dev=True)
    client = app.test_client()
    missing = str(tmp_path / "Volumes" / "RAWVideos")
    response = client.post("/api/v1/settings/source-folders", json={"source_folders": [missing]})
    assert response.status_code == 200
    payload = response.get_json()["source_folders"][0]
    assert payload["path"] == str((tmp_path / "Volumes" / "RAWVideos").resolve())
    assert payload["available"] is False
    assert "drive not mounted" in payload["message"]


def test_configured_source_media_is_registered_in_place_even_if_copy_is_enabled(tmp_path, monkeypatch):
    source = tmp_path / "external" / "session"
    source.mkdir(parents=True)
    video = source / "clip.mp4"
    master = source / "song.mp3"
    video.write_bytes(b"video")
    master.write_bytes(b"audio")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(inbox, "load_global_config", lambda: {
        "inbox_path": str(tmp_path / "Inbox"), "source_folders": [str(source)],
    })
    monkeypatch.setattr(inbox, "classify_file", lambda path: {
        "kind": "videos" if path.suffix == ".mp4" else "master",
        "path": str(path), "filename": path.name, "note": "test", "checked": True,
        **({"probe": valid_video_probe()} if path.suffix == ".mp4" else {"duration": 3.0}),
    })
    project = create_project("in-place", str(tmp_path / "in-place.zuckervid"))
    project.data["settings"]["inputs"]["copy_into_project"] = True
    inbox.register_selected_inputs(project, str(master), video_paths=[str(video)])
    assert project.data["inputs"]["master"]["path"] == str(master.resolve())
    assert project.data["inputs"]["videos"][0]["path"] == str(video.resolve())
    assert not (project.folder / "inputs" / "videos" / video.name).exists()


def test_inbox_classification_extensions_and_invalid_songs(tmp_path, monkeypatch):
    master = tmp_path / "master.mp3"
    video = tmp_path / "clip.mov"
    valid_songs = tmp_path / "songs.json"
    invalid_json = tmp_path / "notes.json"
    ignored = tmp_path / "readme.txt"
    sidecar = tmp_path / "clip.lrv"
    for path in (master, video, ignored, sidecar):
        path.write_bytes(b"x")
    valid_songs.write_text(json.dumps({"songs": []}), encoding="utf-8")
    invalid_json.write_text(json.dumps({"items": []}), encoding="utf-8")

    monkeypatch.setattr(
        "server.inbox.ffprobe",
        lambda path: valid_video_probe() if path.endswith("clip.mov") else {"format": {"duration": "12.0"}, "streams": []},
    )

    result = classify_paths([str(master), str(video), str(valid_songs), str(invalid_json), str(ignored), str(sidecar)])

    assert [item["filename"] for item in result["master"]] == ["master.mp3"]
    assert [item["filename"] for item in result["videos"]] == ["clip.mov"]
    assert [item["filename"] for item in result["songs"]] == ["songs.json"]
    ignored_notes = {item["filename"]: item["note"] for item in result["ignored"]}
    assert ignored_notes["notes.json"] == "JSON ignored: missing songs array"
    assert ignored_notes["readme.txt"] == "not a camera video"
    assert ignored_notes["clip.lrv"] == "camera sidecar file (low-resolution proxy)"


def test_classifier_rejects_non_camera_video_files(tmp_path, monkeypatch):
    text = tmp_path / "mix_report.txt"
    sidecar = tmp_path / "clip.lrv"
    audio_only_mov = tmp_path / "audio-only.mov"
    valid = tmp_path / "camera.mp4"
    for path in (text, sidecar, audio_only_mov, valid):
        path.write_bytes(b"x")

    def fake_probe(path: str) -> dict:
        if path.endswith("audio-only.mov"):
            return {"format": {"duration": "10.0"}, "streams": [{"codec_type": "audio", "codec_name": "aac"}]}
        if path.endswith("camera.mp4"):
            return valid_video_probe()
        return {
            "format": {"duration": "3.0", "format_name": "tty"},
            "streams": [{"codec_type": "video", "codec_name": "ansi", "width": 80, "height": 25}],
        }

    monkeypatch.setattr("server.inbox.ffprobe", fake_probe)

    result = classify_paths([str(text), str(sidecar), str(audio_only_mov), str(valid)])

    assert [item["filename"] for item in result["videos"]] == ["camera.mp4"]
    ignored = {item["filename"]: item["note"] for item in result["ignored"]}
    assert ignored["mix_report.txt"] == "not a camera video"
    assert ignored["clip.lrv"] == "camera sidecar file (low-resolution proxy)"
    assert ignored["audio-only.mov"] == "not a camera video"


def test_classifier_accepts_hevc_mts_and_marks_equirect(tmp_path, monkeypatch):
    mts = tmp_path / "sony.mts"
    sphere = tmp_path / "insta360-export.mp4"
    raw = tmp_path / "raw.insv"
    for path in (mts, sphere, raw):
        path.write_bytes(b"x")

    def fake_probe(path: str) -> dict:
        if path.endswith("sony.mts"):
            return {
                "format": {"duration": "12.0", "format_name": "mpegts"},
                "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160}],
            }
        return {
            "format": {"duration": "15.0" if path.endswith(".insv") else "12.0", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 3840, "height": 1920}],
        }

    monkeypatch.setattr("server.inbox.ffprobe", fake_probe)

    result = classify_paths([str(mts), str(sphere), str(raw)])

    videos = {item["filename"]: item for item in result["videos"]}
    assert videos["sony.mts"]["probe"]["video_codec"] == "hevc"
    assert videos["insta360-export.mp4"]["projection"] == "equirect"
    assert videos["raw.insv"]["projection"] == "raw_insv"
    assert videos["raw.insv"]["raw_360"] is True
    assert "stitched automatically" in videos["raw.insv"]["note"]


def test_classifier_accepts_high_res_insta360_studio_export(tmp_path, monkeypatch):
    sphere = tmp_path / "insta360-5760x2880.mp4"
    sphere.write_bytes(b"x")

    monkeypatch.setattr(
        "server.inbox.ffprobe",
        lambda path: {
            "format": {"duration": "120.0", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "hevc", "width": 5760, "height": 2880}],
        },
    )

    result = classify_paths([str(sphere)])

    assert [item["filename"] for item in result["videos"]] == ["insta360-5760x2880.mp4"]
    assert result["videos"][0]["projection"] == "equirect"
    assert result["videos"][0]["probe"]["width"] == 5760


def test_classify_paths_logs_every_scan_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    raw_360 = tmp_path / "clip.insv"
    video = tmp_path / "clip.mp4"
    raw_360.write_bytes(b"x")
    video.write_bytes(b"x")
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe())

    result = classify_paths([str(raw_360), str(video)])

    assert [item["filename"] for item in result["videos"]] == ["clip.insv", "clip.mp4"]
    log_path = tmp_path / "home" / "ZuckerVideos" / "logs" / "ingest.log"
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    verdicts = {line["filename"]: line for line in lines}
    assert verdicts["clip.insv"]["kind"] == "videos"
    assert verdicts["clip.insv"]["probe"]["projection"] == "raw_insv"
    assert verdicts["clip.mp4"]["accepted"] is True
    assert verdicts["clip.mp4"]["probe"]["valid_video"] is True


def test_studio_export_is_preferred_over_matching_raw_insv(tmp_path, monkeypatch):
    raw = tmp_path / "clip.insv"
    studio = tmp_path / "clip.mp4"
    raw.write_bytes(b"x")
    studio.write_bytes(b"x")

    def fake_probe(path: str) -> dict:
        width, height = (3840, 1920) if path.endswith(".mp4") else (5760, 2880)
        return {
            "format": {"duration": "60.0", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": width, "height": height}],
        }

    monkeypatch.setattr("server.inbox.ffprobe", fake_probe)

    result = classify_paths([str(raw), str(studio)])

    assert [item["filename"] for item in result["videos"]] == ["clip.mp4"]
    ignored = {item["filename"]: item["note"] for item in result["ignored"]}
    assert "Studio-exported" in ignored["clip.insv"]


def test_paired_insv_detection(tmp_path):
    first = tmp_path / "VID_20260721_120000_00_001.insv"
    second = tmp_path / "VID_20260721_120000_10_001.insv"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    assert paired_insv_path(first) == second.resolve()
    assert paired_insv_path(second) == first.resolve()


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


def test_classify_paths_recurses_folder_paths_like_inbox(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "server.inbox.ffprobe",
        lambda path: valid_video_probe()
        if path.endswith("clip.mov")
        else {
            "format": {"duration": "3.0", "format_name": "tty"},
            "streams": [{"codec_type": "video", "codec_name": "ansi", "width": 80, "height": 25}],
        },
    )
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
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe())
    inbox = tmp_path / "ZuckerVideos" / "Inbox"
    inbox.mkdir(parents=True)
    master = inbox / "master.mp3"
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
    assert result["master"][0]["filename"] == "master.mp3"

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


def test_project_open_reconcile_demotes_old_invalid_video_record(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "server.inbox.ffprobe",
        lambda path: {
            "format": {"duration": "3.0", "format_name": "tty"},
            "streams": [{"codec_type": "video", "codec_name": "ansi", "width": 80, "height": 25}],
        },
    )
    project = create_project("Old", str(tmp_path / "Old.zuckervid"))
    report = tmp_path / "mix_report.txt"
    report.write_text("old report", encoding="utf-8")
    project.data["inputs"]["videos"] = [file_record(str(report))]
    project.data["stages"]["ingest"]["status"] = "done"
    project.save()

    assert reconcile_registered_inputs(project) is True

    record = project.data["inputs"]["videos"][0]
    assert record["status"] == "not_a_video"
    assert record["not_a_video_reason"] == "not a camera video"
    assert project.data["stages"]["ingest"]["status"] == "stale"


def test_upload_endpoint_saves_and_classifies(tmp_path, monkeypatch):
    monkeypatch.setattr("server.inbox.ffprobe", lambda path: valid_video_probe())
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


def test_upload_endpoint_returns_json_error_when_too_large(tmp_path):
    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)})
    app.config["MAX_CONTENT_LENGTH"] = 16

    response = client.post(
        "/api/v1/inputs/upload",
        data={"files": (io.BytesIO(b"x" * 128), "clip.mp4")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    payload = response.get_json()
    assert payload["error"]["code"] == "upload_too_large"
    assert "Inbox" in payload["error"]["message"]


def test_desktop_mode_has_no_upload_size_cap(tmp_path, monkeypatch):
    # The packaged desktop app references files by path (see handleDrop's
    # file.path branch) and never needs to buffer whole videos through this
    # server -- real camera/360 footage is routinely multi-GB, so the upload
    # cap must not apply here at all.
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(dev=False)
    assert app.config["MAX_CONTENT_LENGTH"] is None


def test_dev_mode_keeps_a_browser_upload_cap(tmp_path, monkeypatch):
    # A plain browser tab has no choice but to upload file bytes over HTTP,
    # so --dev mode keeps a (generous) safety cap.
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app(dev=True)
    assert app.config["MAX_CONTENT_LENGTH"] == 512 * 1024 * 1024


def test_songs_suggestion_scans_master_folder_and_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    inbox = tmp_path / "ZuckerVideos" / "Inbox"
    inbox.mkdir(parents=True)
    master_folder = tmp_path / "session"
    master_folder.mkdir()
    master = master_folder / "master.wav"
    next_to_master = master_folder / "songs.json"
    inbox_songs = inbox / "alt-songs.json"
    invalid = master_folder / "notes.json"
    master.write_bytes(b"master")
    next_to_master.write_text(json.dumps({"songs": []}), encoding="utf-8")
    inbox_songs.write_text(json.dumps({"songs": [{"title": "A"}]}), encoding="utf-8")
    invalid.write_text(json.dumps({"clips": []}), encoding="utf-8")

    app = create_app()
    client = app.test_client()
    folder = tmp_path / "Jam.zuckervid"
    client.post("/api/v1/project", json={"name": "Jam", "folder": str(folder)})
    client.post("/api/v1/inputs/master", json={"master": str(master)})

    response = client.get("/api/v1/inputs/suggestions/songs")

    assert response.status_code == 200
    filenames = sorted(item["filename"] for item in response.get_json()["songs"])
    assert filenames == ["alt-songs.json", "songs.json"]


def test_duplicate_video_content_is_registered_once_with_warning(tmp_path):
    first = tmp_path / "ZZ24.7 01.mov"
    duplicate = tmp_path / "ZZ24.7 01-2.mov"
    first.write_bytes(b"same-video-content")
    duplicate.write_bytes(first.read_bytes())

    records, warnings = inbox._dedupe_video_records([{"path": str(first)}, {"path": str(duplicate)}])

    assert [record["path"] for record in records] == [str(first)]
    assert "ZZ24.7 01-2.mov" in warnings[0]
