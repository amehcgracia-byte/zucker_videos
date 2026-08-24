"""Small ffmpeg/ffprobe subprocess wrappers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

COMMON_BIN_DIRS = (
    Path("/opt/homebrew/opt/ffmpeg-full/bin"),
    Path("/usr/local/opt/ffmpeg-full/bin"),
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)
_FFMPEG_PATH: str | None = None
_FFPROBE_PATH: str | None = None


class FFmpegError(RuntimeError):
    """Raised when ffmpeg or ffprobe fails."""


def locate_executable(name: str) -> str | None:
    """Resolve an executable from PATH and common macOS Homebrew locations."""
    for folder in COMMON_BIN_DIRS:
        candidate = folder / name
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    return None


def configure_tools(ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> dict[str, str | None]:
    """Set process-local ffmpeg/ffprobe paths and return the effective config."""
    global _FFMPEG_PATH, _FFPROBE_PATH
    requested_ffmpeg = _usable_path(ffmpeg_path)
    requested_ffprobe = _usable_path(ffprobe_path)
    full_ffmpeg = _usable_path(str(COMMON_BIN_DIRS[0] / "ffmpeg")) or _usable_path(str(COMMON_BIN_DIRS[1] / "ffmpeg"))
    full_ffprobe = _usable_path(str(COMMON_BIN_DIRS[0] / "ffprobe")) or _usable_path(str(COMMON_BIN_DIRS[1] / "ffprobe"))
    # The regular Homebrew formula has no libass. Prefer the explicitly
    # installed ffmpeg-full variant even when an older regular path is saved
    # in the user's config; custom non-Homebrew paths remain respected.
    if full_ffmpeg and (not requested_ffmpeg or "/Cellar/ffmpeg/" in requested_ffmpeg):
        requested_ffmpeg = full_ffmpeg
    if full_ffprobe and (not requested_ffprobe or "/Cellar/ffprobe/" in requested_ffprobe or "/Cellar/ffmpeg/" in requested_ffprobe):
        requested_ffprobe = full_ffprobe
    _FFMPEG_PATH = requested_ffmpeg or locate_executable("ffmpeg")
    _FFPROBE_PATH = requested_ffprobe or locate_executable("ffprobe")
    _prepend_tool_directories_to_path()
    return {"ffmpeg_path": _FFMPEG_PATH, "ffprobe_path": _FFPROBE_PATH}


def _prepend_tool_directories_to_path() -> None:
    """Make the configured media-tool directory visible to libraries using ``ffmpeg`` by name.

    Whisper/openai-whisper launches ffmpeg through ``subprocess`` and does not
    accept Zucker's resolved executable path. This keeps that child process
    working in both the dev server and a packaged app whose binary lives inside
    the bundle.
    """
    directories = []
    for executable in (_FFMPEG_PATH, _FFPROBE_PATH):
        if executable:
            directory = str(Path(executable).resolve().parent)
            if directory not in directories:
                directories.append(directory)
    current = os.environ.get("PATH", "").split(os.pathsep)
    prefix = [directory for directory in directories if directory not in current]
    if prefix:
        os.environ["PATH"] = os.pathsep.join(prefix + current)


def ensure_tools_on_path() -> dict[str, Any]:
    """Expose the configured tool paths to subprocess-based media libraries."""
    status = tool_status()
    _prepend_tool_directories_to_path()
    return status


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
