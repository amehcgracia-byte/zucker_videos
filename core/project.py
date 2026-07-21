"""Project persistence, schema defaults, and atomic JSON writes."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGE_NAMES = ("ingest", "sync", "cut", "edit", "export")
STAGE_STATUSES = ("pending", "running", "done", "failed", "stale", "blocked")


class ProjectError(RuntimeError):
    """Raised when a project cannot be loaded or mutated safely."""


def utc_now() -> str:
    """Return a UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def file_record(path: str, label: str | None = None) -> dict[str, Any]:
    """Build a stable project input record for a local file."""
    p = Path(path).expanduser().resolve()
    stat = p.stat()
    record: dict[str, Any] = {
        "path": str(p),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }
    if label:
        record["label"] = label
    return record


def default_settings() -> dict[str, Any]:
    """Return default per-stage settings."""
    return {
        "ingest": {"copy_inputs": False, "insv_fov": 190},
        "inputs": {"copy_into_project": False},
        "sync": {"confidence_threshold": 6.0},
        "cut": {"min_segment_seconds": 4.0},
        "edit": {
            "style": "coverage_first",
            "camera_role_weights": {"360": 0.5, "handheld": 0.3, "fixed_rear": 0.2},
        },
        "spherical_landmarks": {},
        "export": {"format": "mp4", "resolution": "source", "transiciones_suaves": False},
    }


def default_stage_state() -> dict[str, Any]:
    """Return default stage status objects."""
    return {
        name: {
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "outputs": {},
            "error": None,
            "fingerprint": None,
        }
        for name in STAGE_NAMES
    }


def new_project_document(name: str) -> dict[str, Any]:
    """Create a schema-versioned project document."""
    now = utc_now()
    return {
        "schema_version": 1,
        "name": name,
        "created_at": now,
        "modified_at": now,
        "inputs": {"master": None, "songs": None, "videos": []},
        "stages": default_stage_state(),
        "settings": default_settings(),
    }


@dataclass
class Project:
    """A self-contained Zucker Videos project directory."""

    folder: Path
    data: dict[str, Any]

    @property
    def json_path(self) -> Path:
        """Return the project.json path."""
        return self.folder / "project.json"

    @property
    def cache_dir(self) -> Path:
        """Return the project cache directory."""
        return self.folder / "cache"

    @property
    def artifacts_dir(self) -> Path:
        """Return the project artifacts directory."""
        return self.folder / "artifacts"

    @property
    def exports_dir(self) -> Path:
        """Return the project exports directory."""
        return self.folder / "exports"

    def ensure_dirs(self) -> None:
        """Create expected project subdirectories."""
        for relative in ("inputs", "inputs/videos", "cache", "cache/logs", "artifacts", "exports"):
            (self.folder / relative).mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        """Atomically save project.json."""
        self.data["modified_at"] = utc_now()
        atomic_write_json(self.json_path, self.data)

    def snapshot(self) -> dict[str, Any]:
        """Return a deep-copy snapshot suitable for JSON responses."""
        return deepcopy(self.data)

    def set_master_and_songs(self, master_path: str, songs_path: str) -> None:
        """Register master audio and songs JSON inputs."""
        self.data["inputs"]["master"] = file_record(master_path)
        self.data["inputs"]["songs"] = file_record(songs_path)
        self.mark_all_stale_from("sync")
        self.save()

    def set_videos(self, paths: list[str]) -> None:
        """Register video file inputs."""
        self.data["inputs"]["videos"] = [file_record(path) for path in paths]
        self.mark_all_stale_from("ingest")
        self.save()

    def refresh_input_records(self) -> bool:
        """Refresh input size/mtime values and return True if any input changed."""
        changed = False
        earliest_stale_stage: str | None = None
        master = self.data["inputs"].get("master")
        if master and _refresh_record(master):
            changed = True
            earliest_stale_stage = _earliest_stage(earliest_stale_stage, "sync")
        songs = self.data["inputs"].get("songs")
        if songs and _refresh_record(songs):
            changed = True
            earliest_stale_stage = _earliest_stage(earliest_stale_stage, "cut")
        for record in self.data["inputs"].get("videos", []):
            if _refresh_record(record):
                changed = True
                earliest_stale_stage = _earliest_stage(earliest_stale_stage, "ingest")
        if earliest_stale_stage:
            self.mark_all_stale_from(earliest_stale_stage)
        return changed

    def mark_all_stale_from(self, stage_name: str) -> None:
        """Mark a stage and all following stages stale unless they are pending."""
        seen = False
        for name in STAGE_NAMES:
            if name == stage_name:
                seen = True
            if seen:
                stage = self.data["stages"][name]
                if stage["status"] in {"done", "failed", "blocked"}:
                    stage["status"] = "stale"
                stage["error"] = None


def _refresh_record(record: dict[str, Any]) -> bool:
    path = Path(record["path"])
    if not path.exists():
        changed = not record.get("missing", False)
        record["missing"] = True
        return changed
    stat = path.stat()
    new_size = stat.st_size
    new_mtime = stat.st_mtime
    changed = record.get("size") != new_size or record.get("mtime") != new_mtime or record.get("missing", False)
    record["size"] = new_size
    record["mtime"] = new_mtime
    record["missing"] = False
    return changed


def _earliest_stage(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return current if STAGE_NAMES.index(current) <= STAGE_NAMES.index(candidate) else candidate


def create_project(name: str, folder: str) -> Project:
    """Create a new project folder and initial project.json."""
    root = Path(folder).expanduser().resolve()
    if root.exists() and (root / "project.json").exists():
        raise ProjectError(f"Project already exists at {root}")
    root.mkdir(parents=True, exist_ok=True)
    project = Project(root, new_project_document(name))
    project.ensure_dirs()
    project.save()
    return project


def load_project(folder: str) -> Project:
    """Load an existing project folder."""
    root = Path(folder).expanduser().resolve()
    path = root / "project.json"
    if not path.exists():
        raise ProjectError(f"No project.json found at {root}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    validate_project_document(data)
    project = Project(root, data)
    project.ensure_dirs()
    return project


def validate_project_document(data: dict[str, Any]) -> None:
    """Validate the minimal supported project schema."""
    if data.get("schema_version") != 1:
        raise ProjectError("Unsupported project schema version")
    for key in ("name", "created_at", "modified_at", "inputs", "stages", "settings"):
        if key not in data:
            raise ProjectError(f"Missing project key: {key}")
    for name in STAGE_NAMES:
        if name not in data["stages"]:
            raise ProjectError(f"Missing stage state: {name}")
        status = data["stages"][name].get("status")
        if status not in STAGE_STATUSES:
            raise ProjectError(f"Invalid status for {name}: {status}")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically by fsyncing a temp file and replacing the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
