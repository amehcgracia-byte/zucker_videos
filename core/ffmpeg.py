"""Small ffmpeg/ffprobe subprocess wrappers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

COMMON_BIN_DIRS = (Path("/opt/homebrew/bin"), Path("/usr/local/bin"))
_FFMPEG_PATH: str | None = None
_FFPROBE_PATH: str | None = None


class FFmpegError(RuntimeError):
    """Raised when ffmpeg or ffprobe fails."""


def locate_executable(name: str) -> str | None:
    """Resolve an executable from PATH and common macOS Homebrew locations."""
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    for folder in COMMON_BIN_DIRS:
        candidate = folder / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None


def configure_tools(ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> dict[str, str | None]:
    """Set process-local ffmpeg/ffprobe paths and return the effective config."""
    global _FFMPEG_PATH, _FFPROBE_PATH
    _FFMPEG_PATH = _usable_path(ffmpeg_path) or locate_executable("ffmpeg")
    _FFPROBE_PATH = _usable_path(ffprobe_path) or locate_executable("ffprobe")
    return {"ffmpeg_path": _FFMPEG_PATH, "ffprobe_path": _FFPROBE_PATH}


def _usable_path(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    if candidate.exists() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    return None


def tool_status() -> dict[str, Any]:
    """Return resolved ffmpeg tool paths and availability."""
    if _FFMPEG_PATH is None or _FFPROBE_PATH is None:
        configure_tools()
    return {
        "ffmpeg_path": _FFMPEG_PATH,
        "ffprobe_path": _FFPROBE_PATH,
        "ok": bool(_FFMPEG_PATH and _FFPROBE_PATH),
    }


def ffprobe(path: str) -> dict[str, Any]:
    """Probe a media file with ffprobe and return parsed JSON metadata."""
    status = tool_status()
    if not status["ffprobe_path"]:
        raise FFmpegError("ffprobe is missing. Install it with: brew install ffmpeg")
    command = [
        str(status["ffprobe_path"]),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(Path(path)),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or f"ffprobe failed for {path}")
    return json.loads(result.stdout or "{}")


def extract_audio(video_path: str, output_path: str) -> None:
    """Extract mono 48 kHz WAV audio from a video file."""
    status = tool_status()
    if not status["ffmpeg_path"]:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    command = [
        str(status["ffmpeg_path"]),
        "-y",
        "-i",
        str(Path(video_path)),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "48000",
        str(Path(output_path)),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or f"ffmpeg failed for {video_path}")
