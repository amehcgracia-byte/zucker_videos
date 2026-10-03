"""Stage interface and helpers."""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from core.project import Project, atomic_write_json

ProgressCallback = Callable[[int, str], None]


class ProgressDetail(str):
    """A readable legacy message with optional measured task metadata."""

    def __new__(cls, message: str, *, task_id: str, label: str, percent: int | None):
        value = super().__new__(cls, message)
        value.task = {"id": task_id, "label": label, "percent": percent, "detail": message}
        return value


class Stage(ABC):
    """Base class for pipeline stages."""

    name: str
    dependencies: list[str] = []

    @abstractmethod
    def inputs_fingerprint(self, project: Project) -> str:
        """Return a stable fingerprint for stage inputs and settings."""

    @abstractmethod
    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Run the stage and return output artifact paths."""

    @abstractmethod
    def outputs(self, project: Project) -> dict[str, str]:
        """Return expected output artifact paths."""


def stable_fingerprint(payload: Any) -> str:
    """Return a SHA-256 fingerprint for JSON-serializable data."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def artifact_path(project: Project, *parts: str) -> Path:
    """Return an artifact path under the project artifacts directory."""
    path = project.artifacts_dir.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_artifact_json(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON artifact atomically."""
    atomic_write_json(path, payload)


def file_signature(path: str) -> dict[str, Any]:
    """Return a small signature for an existing file."""
    stat = os.stat(path)
    return {"path": str(Path(path).resolve()), "size": stat.st_size, "mtime": stat.st_mtime}
