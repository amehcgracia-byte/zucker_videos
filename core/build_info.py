"""Build/version metadata helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

APP_VERSION = "2.1.14"


def build_info() -> dict[str, str]:
    """Return the app version and best-known git commit."""
    packaged = _packaged_build_info()
    commit = packaged.get("git_commit") or _git_commit() or "unknown"
    return {"version": packaged.get("version") or APP_VERSION, "git_commit": commit}


def startup_label() -> str:
    """Return a compact version label for startup logs."""
    info = build_info()
    return f"Zucker Editor v{info['version']} git={info['git_commit']}"


def _packaged_build_info() -> dict[str, str]:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    path = root / "build_info.json"
    if not path.exists():
        return {}
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value is not None}


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
