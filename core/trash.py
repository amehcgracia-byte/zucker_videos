"""Recoverable filesystem deletion helpers."""

from __future__ import annotations

import shutil
import time
from pathlib import Path


def move_to_trash(path: Path, trash_root: Path | None = None) -> Path | None:
    """Move a file or directory to the user's Trash, never unlink it."""
    path = Path(path).expanduser()
    if not path.exists():
        return None
    root = Path(trash_root or (Path.home() / ".Trash")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / path.name
    if destination.exists():
        destination = root / f"{path.name}.{int(time.time() * 1000)}"
        while destination.exists():
            destination = root / f"{path.name}.{int(time.time() * 1000)}-1"
    shutil.move(str(path), str(destination))
    return destination
