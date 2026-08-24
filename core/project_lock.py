"""Process-safe locks for long-running per-project pipelines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - the desktop app runs on macOS/Linux
    fcntl = None


class ProjectPipelineLock:
    """Hold an advisory lock for the complete lifetime of a project job."""

    def __init__(self, project_folder: Path) -> None:
        self.path = Path(project_folder) / ".pipeline.lock"
        self._handle: Any = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        if fcntl is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None

    def __enter__(self) -> "ProjectPipelineLock":
        if not self.acquire():
            raise RuntimeError(f"Project pipeline already running: {self.path.parent}")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
