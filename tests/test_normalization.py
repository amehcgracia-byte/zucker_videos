from __future__ import annotations

import os
import time
from pathlib import Path

from core.normalization import (
    EQUIRECT_FILTER,
    cache_key_for_source,
    needs_normalization,
    normalization_filter,
    normalize_video_record,
    normalized_path,
)
from core.project import create_project, file_record


def test_cache_key_is_stable_for_same_source_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video")

    assert cache_key_for_source(source) == cache_key_for_source(str(source.resolve()))


def test_normalization_cache_decision_uses_source_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Norm", str(tmp_path / "Norm.zuckervid"))
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video")
    record = file_record(str(source))
    destination = normalized_path(project, record)
    destination.parent.mkdir(parents=True)

    assert needs_normalization(record, destination) is True
    destination.write_bytes(b"normalized")
    record["normalized"] = {
        "path": str(destination),
        "source_size": record["size"],
        "source_mtime": record["mtime"],
    }
    assert needs_normalization(record, destination) is False

    time.sleep(0.01)
    source.write_bytes(b"changed")
    record["cache_key"] = cache_key_for_source(source)
    assert needs_normalization(record, destination) is True


def test_global_normalization_cache_hits_across_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video")
    record_a = file_record(str(source))
    record_a["probe"] = {"duration": 3.0, "fps": 30.0}
    record_b = file_record(str(source))
    record_b["probe"] = {"duration": 3.0, "fps": 30.0}
    project_a = create_project("A", str(tmp_path / "A.zuckervid"))
    project_b = create_project("B", str(tmp_path / "B.zuckervid"))
    calls = []

    def fake_ffmpeg(command, duration, filename, progress):
        calls.append(command)
        Path(command[-1]).write_bytes(b"normalized")

    monkeypatch.setattr("core.normalization._run_ffmpeg_progress", fake_ffmpeg)
    monkeypatch.setattr("core.normalization.tool_status", lambda: {"ffmpeg_path": "ffmpeg"})

    normalize_video_record(project_a, record_a, lambda percent, message: None)
    normalize_video_record(project_b, record_b, lambda percent, message: None)

    assert len(calls) == 1
    assert record_a["cache_key"] == record_b["cache_key"]
    assert record_a["normalized"]["path"] == record_b["normalized"]["path"]
    assert Path(record_b["normalized"]["path"]).exists()


def test_global_cache_misses_when_mtime_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Norm", str(tmp_path / "Norm.zuckervid"))
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video")
    record = file_record(str(source))
    old_key = cache_key_for_source(source)
    destination = normalized_path(project, record)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"normalized")
    record["cache_key"] = old_key
    record["normalized"] = {"path": str(destination), "cache_key": old_key, "source_size": record["size"], "source_mtime": record["mtime"]}

    time.sleep(0.01)
    source.write_bytes(b"changed")
    os.utime(source, None)
    record["size"] = source.stat().st_size
    record["mtime"] = source.stat().st_mtime

    assert cache_key_for_source(source) != old_key
    assert needs_normalization(record, normalized_path(project, record)) is True


def test_normalization_filter_uses_equirect_and_hdr_paths():
    assert normalization_filter({"projection": "equirect"}) == EQUIRECT_FILTER
    assert "tonemap" in normalization_filter({"hdr": True, "bit_depth": 10})
    assert "trunc(iw/2)*2" in normalization_filter({"hdr": False, "bit_depth": 8})
