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
    root = Path(trash_root).expanduser() if trash_root else _default_trash_root(path)
    root.mkdir(parents=True, exist_ok=True)
    destination = root / path.name
    if destination.exists():
        destination = root / f"{path.name}.{int(time.time() * 1000)}"
        while destination.exists():
            destination = root / f"{path.name}.{int(time.time() * 1000)}-1"
    shutil.move(str(path), str(destination))
    return destination


def _volume_root(path: Path) -> Path:
    """Find the mounted volume containing a resolved file or directory."""
    current = path.resolve()
    device = current.stat().st_dev
    while current.parent != current and current.parent.stat().st_dev == device:
        current = current.parent
    return current


def _default_trash_root(path: Path) -> Path:
    volume = _volume_root(path)
    if volume == _volume_root(Path.home()):
        return Path.home() / ".Trash"
    # Recoverable cache cleanup must never copy external media onto the internal disk.
    return volume / ".ZuckerEditorTrash"
