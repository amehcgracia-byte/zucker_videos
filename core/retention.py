"""Storage inventory and recoverable retention for user-generated media."""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.build_info import build_info
from core.normalization import CACHE_SUBDIRS, global_cache_root, referenced_cache_keys
from core.project import load_project
from core.trash import move_to_trash

AUTO_PROJECT_MARKERS = (
    "regression", "packaged", "captionverify", "caption verify", "final single",
    "verification", "verify", "smoke test", "test project",
)
AUTO_PROJECT_MAX_AGE_DAYS = 3
BACKUP_KEEP_COUNT = 2
PROJECT_EXPORT_HISTORY_COUNT = 2
EXPORT_SUFFIXES = ("_composed-captions", "_overlay-composed")


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.exists():
        return 0
    total = 0
    seen: set[tuple[int, int]] = set()
    for child in path.rglob("*"):
        try:
            if child.is_file():
                stat = child.stat()
                inode = (stat.st_dev, stat.st_ino)
                if inode not in seen:
                    seen.add(inode)
                    total += stat.st_size
        except OSError:
            continue
    return total


def _iso_mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def _is_auto_project(path: Path) -> bool:
    name = path.name.lower()
    return any(marker in name for marker in AUTO_PROJECT_MARKERS)


def _manifest_paths(project: Path) -> set[Path]:
    manifest = project / "artifacts" / "export_manifest.json"
    if not manifest.exists():
        return set()
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return set()
    paths = set()
    for item in payload.get("exports") or []:
        raw = item.get("path") if isinstance(item, dict) else None
        if raw:
            paths.add(Path(str(raw)).expanduser().resolve())
    return paths


def _export_family(path: Path) -> str:
    stem = path.stem
    for suffix in EXPORT_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _verification_temp_files(root: Path) -> list[Path]:
    """Only inspect dedicated verification temp directories, never user uploads."""
    files: list[Path] = []
    for name in ("Verification", "VerificationTemp", ".verification"):
        folder = root / name
        if not folder.is_dir():
            continue
        files.extend(path for path in folder.rglob("*") if path.is_file())
    return sorted(files)


def project_export_inventory(project: Path) -> dict[str, Any]:
    folder = project / "exports"
    files = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".mp4"] if folder.exists() else []
    current_paths = _manifest_paths(project)
    current_families = {_export_family(path) for path in current_paths}
    families: dict[str, list[Path]] = {}
    for path in files:
        families.setdefault(_export_family(path), []).append(path)
    ordered = sorted(families.items(), key=lambda item: max(p.stat().st_mtime for p in item[1]), reverse=True)
    history_families = [name for name, _paths in ordered if name not in current_families][:PROJECT_EXPORT_HISTORY_COUNT]
    old = [path for name, paths in families.items() if name not in current_families and name not in history_families for path in paths]
    return {
        "files": [{"path": str(path), "bytes": _size(path), "mtime": _iso_mtime(path), "current": _export_family(path) in current_families, "retained_history": _export_family(path) in history_families} for path in sorted(files)],
        "current": sorted(str(path) for path in current_paths),
        "old_candidates": [str(path) for path in sorted(old)],
    }


def build_storage_report(
    root: Path | None = None,
    repo_root: Path | None = None,
    huggingface_root: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Build a read-only report. This function does not mutate the filesystem."""
    root = Path(root or (Path.home() / "ZuckerVideos")).expanduser().resolve()
    repo_root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    huggingface_root = Path(huggingface_root or (Path.home() / ".cache" / "huggingface")).expanduser().resolve()
    now = now or time.time()
    cache = root / "Cache"
    backups = root / "AppBackups"
    projects_root = root / "Projects"
    backup_files = sorted((p for p in backups.iterdir() if p.is_file() and not p.name.startswith(".")), key=lambda p: p.stat().st_mtime, reverse=True) if backups.exists() else []
    projects = []
    for project in sorted(projects_root.glob("*.zuckervid")) if projects_root.exists() else []:
        age_days = max(0.0, (now - project.stat().st_mtime) / 86400)
        projects.append({
            "path": str(project), "name": project.name, "bytes": _size(project),
            "mtime": _iso_mtime(project), "automatic": _is_auto_project(project),
            "expired_verification": _is_auto_project(project) and age_days >= AUTO_PROJECT_MAX_AGE_DAYS,
            "exports": project_export_inventory(project),
        })
    cache_subdirs = {name: {"path": str(cache / name), "bytes": _size(cache / name), "recoverability": "regenerable"} for name in CACHE_SUBDIRS}
    hf_models = []
    hub = huggingface_root / "hub"
    for model in sorted(hub.glob("models--*")) if hub.exists() else []:
        hf_models.append({"path": str(model), "name": model.name, "bytes": _size(model), "recoverability": "regenerable/downloadable"})
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "root": {"path": str(root), "bytes": _size(root), "recoverability": "user data / imprescindible"},
        "categories": {
            "cache": {"path": str(cache), "bytes": _size(cache), "subfolders": cache_subdirs, "recoverability": "regenerable"},
            "app_backups": {"path": str(backups), "bytes": _size(backups), "copies": [{"path": str(p), "bytes": _size(p), "mtime": _iso_mtime(p)} for p in backup_files], "recoverability": "recoverable from project/config state"},
            "projects": {"path": str(projects_root), "bytes": _size(projects_root), "items": projects, "recoverability": "user projects imprescindible; verification regenerable"},
            "wizard_uploads": {"path": str(root / "WizardUploads"), "bytes": _size(root / "WizardUploads"), "recoverability": "original user media / imprescindible"},
            "repo_build": {"path": str(repo_root / "build"), "bytes": _size(repo_root / "build"), "recoverability": "regenerable"},
            "repo_dist": {"path": str(repo_root / "dist"), "bytes": _size(repo_root / "dist"), "recoverability": "regenerable; DMG handoff"},
            "huggingface": {"path": str(huggingface_root), "bytes": _size(huggingface_root), "models": hf_models, "recoverability": "regenerable/downloadable"},
            "verification_temp": {"path": str(root), "bytes": sum(_size(path) for path in _verification_temp_files(root)), "files": [{"path": str(path), "bytes": _size(path), "mtime": _iso_mtime(path)} for path in _verification_temp_files(root)], "recoverability": "regenerable temporary verification output"},
        },
    }


def cleanup_plan(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only safe, recoverable candidates for the manual cleanup."""
    candidates: list[dict[str, Any]] = []
    backups = report["categories"]["app_backups"]["copies"]
    for item in backups[BACKUP_KEEP_COUNT:]:
        candidates.append({"path": item["path"], "bytes": item["bytes"], "reason": "AppBackups beyond newest two"})
    for project in report["categories"]["projects"]["items"]:
        if project["expired_verification"]:
            candidates.append({"path": project["path"], "bytes": project["bytes"], "reason": "verification project older than three days"})
            continue
        for path in project["exports"]["old_candidates"]:
            candidate = Path(path)
            candidates.append({"path": path, "bytes": _size(candidate), "reason": "project export older than current plus two previous"})
    for item in report["categories"].get("verification_temp", {}).get("files", []):
        candidates.append({"path": item["path"], "bytes": item["bytes"], "reason": "temporary verification output"})
    return candidates


def execute_cleanup(report: dict[str, Any], *, trash_root: Path | None = None) -> dict[str, Any]:
    """Move the planned candidates to Trash and return before/after totals."""
    candidates = cleanup_plan(report)
    moved = []
    for item in candidates:
        destination = move_to_trash(Path(item["path"]), trash_root=trash_root)
        if destination:
            moved.append({**item, "trash_path": str(destination)})
    after = build_storage_report(Path(report["root"]["path"]), repo_root=Path(__file__).resolve().parents[1])
    return {"before_bytes": report["root"]["bytes"], "after_bytes": after["root"]["bytes"], "freed_bytes": sum(item["bytes"] for item in moved), "moved": moved, "report_after": after}


def cleanup_automatic_retention() -> dict[str, Any]:
    """Apply the safe retention rules during normal cache maintenance."""
    report = build_storage_report()
    if not cleanup_plan(report):
        return {"moved": [], "freed_bytes": 0}
    result = execute_cleanup(report)
    return {"moved": result["moved"], "freed_bytes": result["freed_bytes"]}
