from __future__ import annotations

import json
import os
from pathlib import Path

from core.retention import build_storage_report, cleanup_plan, execute_cleanup


def _touch(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))


def test_report_classifies_projects_and_keeps_current_plus_two_export_families(tmp_path):
    now = 1_800_000_000.0
    root = tmp_path / "ZuckerVideos"
    project = root / "Projects" / "Regression Verify.zuckervid"
    (project / "artifacts").mkdir(parents=True)
    current = project / "exports" / "Regression-reel-3.mp4"
    _touch(current, 30, now)
    _touch(project / "exports" / "Regression-reel-3_overlay-composed.mp4", 10, now)
    _touch(project / "exports" / "Regression-reel-2.mp4", 20, now - 10)
    _touch(project / "exports" / "Regression-reel-1.mp4", 20, now - 20)
    _touch(project / "exports" / "Regression-reel-0.mp4", 20, now - 30)
    (project / "artifacts" / "export_manifest.json").write_text(json.dumps({"exports": [{"path": str(current)}]}), encoding="utf-8")
    report = build_storage_report(root=root, repo_root=tmp_path / "repo", huggingface_root=tmp_path / "hf", now=now)
    item = report["categories"]["projects"]["items"][0]
    assert item["automatic"] is True
    assert str(project / "exports" / "Regression-reel-0.mp4") in item["exports"]["old_candidates"]
    assert str(project / "exports" / "Regression-reel-1.mp4") not in item["exports"]["old_candidates"]
    assert str(project / "exports" / "Regression-reel-3_overlay-composed.mp4") not in item["exports"]["old_candidates"]


def test_cleanup_moves_only_retention_candidates_to_trash(tmp_path):
    now = 1_800_000_000.0
    root = tmp_path / "ZuckerVideos"
    project = root / "Projects" / "User.zuckervid"
    project.mkdir(parents=True)
    current = project / "exports" / "result-3.mp4"
    old = project / "exports" / "result-0.mp4"
    _touch(current, 10, now)
    for index in (1, 2):
        _touch(project / "exports" / f"result-{index}.mp4", 10, now - index)
    _touch(old, 17, now - 10 * 86400)
    (project / "artifacts").mkdir()
    (project / "artifacts" / "export_manifest.json").write_text(json.dumps({"exports": [{"path": str(current)}]}), encoding="utf-8")
    report = build_storage_report(root=root, repo_root=tmp_path / "repo", huggingface_root=tmp_path / "hf", now=now)
    result = execute_cleanup(report, trash_root=tmp_path / "Trash")
    assert result["freed_bytes"] == 17
    assert not old.exists()
    assert (tmp_path / "Trash" / "result-0.mp4").exists()


def test_app_backups_keep_two_newest(tmp_path):
    now = 1_800_000_000.0
    root = tmp_path / "ZuckerVideos"
    for index in range(4):
        _touch(root / "AppBackups" / f"backup-{index}.json", 10 + index, now - index)
    report = build_storage_report(root=root, repo_root=tmp_path / "repo", huggingface_root=tmp_path / "hf", now=now)
    candidates = {item["path"] for item in cleanup_plan(report)}
    assert str(root / "AppBackups" / "backup-0.json") not in candidates
    assert str(root / "AppBackups" / "backup-1.json") not in candidates
    assert str(root / "AppBackups" / "backup-2.json") in candidates


def test_cleanup_lists_stale_generated_temps_but_protects_active_project(tmp_path):
    now = 1_800_000_000.0
    root = tmp_path / "ZuckerVideos"
    active = root / "Projects" / "Active.zuckervid"
    other = root / "Projects" / "Old.zuckervid"
    active_tmp = active / "cache" / ".project.json.deadbeef.tmp"
    other_tmp = other / "cache" / ".project.json.cafebabe.tmp"
    _touch(active_tmp, 7, now - 2 * 86400)
    _touch(other_tmp, 11, now - 2 * 86400)
    report = build_storage_report(root=root, repo_root=tmp_path / "repo", huggingface_root=tmp_path / "hf", now=now)
    candidates = {item["path"] for item in cleanup_plan(report, protected_paths=[active])}
    assert str(active_tmp) not in candidates
    assert str(other_tmp) in candidates
