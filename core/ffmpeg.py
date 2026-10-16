"""Small ffmpeg/ffprobe subprocess wrappers."""

from __future__ import annotations

import json
import copy
from functools import lru_cache
import os
import shutil
import subprocess
import sys
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


# Media tools are allowed to take longer for large camera files, but they must
# never wait forever on a damaged file, an unavailable volume, or a stuck
# hardware decoder.
MEDIA_COMMAND_TIMEOUT_SEC = 300


def locate_executable(name: str) -> str | None:
    """Resolve an executable from PATH and common macOS Homebrew locations."""
    if getattr(sys, "_MEIPASS", None):
        bundled = Path(sys._MEIPASS) / "bin" / (name + (".exe" if os.name == "nt" else ""))
        if bundled.is_file():
            return str(bundled.resolve())
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
    source = Path(path).resolve()
    stat = source.stat()
    stored_key = (str(source), str(status["ffprobe_path"]), stat.st_size, stat.st_mtime_ns)
    stored = _stored_probe(stored_key)
    if stored is None:
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        stored = _cached_ffprobe(str(source), str(status["ffprobe_path"]), signature)
        _store_probe(stored_key, stored)
    return copy.deepcopy(stored)


# Probes of unchanged files survive restarts: opening a project re-validated
# every camera file with ffprobe, seconds on an external disk. Kept on the
# local disk (not the data root, which may be that external disk), keyed by
# path, size and modification time; tests keep to the in-process cache.
_PROBE_MEMORY: dict[tuple, dict[str, Any]] = {}


def _probe_store_dir() -> Path | None:
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("ZUCKER_NO_PROBE_STORE"):
        return None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ZuckerEditor" / "probes"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ZuckerEditor" / "probes"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "zucker-editor" / "probes"


def _probe_store_file(key: tuple) -> Path | None:
    directory = _probe_store_dir()
    if directory is None:
        return None
    import hashlib
    return directory / f"{hashlib.sha1(json.dumps(key).encode('utf-8')).hexdigest()}.json"


def _stored_probe(key: tuple) -> dict[str, Any] | None:
    path = _probe_store_file(key)
    if path is None:
        return None
    if key in _PROBE_MEMORY:
        return _PROBE_MEMORY[key]
    try:
        probe = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(probe, dict):
        return None
    _PROBE_MEMORY[key] = probe
    return probe


def _store_probe(key: tuple, probe: dict[str, Any]) -> None:
    path = _probe_store_file(key)
    if path is None:
        return
    if len(_PROBE_MEMORY) > 2048:
        _PROBE_MEMORY.clear()
    _PROBE_MEMORY[key] = probe
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_suffix(f".{os.getpid()}.tmp")
        pending.write_text(json.dumps(probe), encoding="utf-8")
        pending.replace(path)
    except OSError:
        pass


@lru_cache(maxsize=256)
def _cached_ffprobe(path: str, executable: str, signature: tuple) -> dict[str, Any]:
    """Cache successful probes only; source identity invalidates a replaced file."""
    command = [
        executable,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(Path(path)),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=MEDIA_COMMAND_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffprobe timed out after {MEDIA_COMMAND_TIMEOUT_SEC}s for {path}") from exc
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or f"ffprobe failed for {path}")
    return json.loads(result.stdout or "{}")


def filter_option_value(value: str) -> str:
    """A literal option value inside an FFmpeg filtergraph, unquoted.

    Escaped twice, as FFmpeg parses twice: once per option (\\ ' :) and once
    per graph (\\ ' [ ] , ;). Quoting instead cannot hold an apostrophe:
    "Don't Stop" ended the quote early and the whole graph failed to parse.
    """
    value = str(value).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    for character in "\\'[],;":
        value = value.replace(character, "\\" + character)
    return value


def muxer_args_for(destination: str | Path) -> list[str]:
    """Explicit muxer for a temporary output whose name hides the real suffix.

    Atomic publishes render to ``.<name>.<hex>.part``; FFmpeg cannot infer a
    container from ``.part`` and refuses to open the output at all.
    """
    return ["-f", "mov" if Path(destination).suffix.lower() == ".mov" else "mp4"]


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
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=MEDIA_COMMAND_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffmpeg audio extraction timed out after {MEDIA_COMMAND_TIMEOUT_SEC}s for {video_path}") from exc
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or f"ffmpeg failed for {video_path}")


@lru_cache(maxsize=4)
def hardware_h264(ffmpeg: str) -> bool:
    """Whether this FFmpeg encodes H.264 on the Mac's own hardware (tried once on a
    tiny test picture; anything else answers no)."""
    if sys.platform != "darwin":
        return False
    try:
        # A real picture size and pixel format: it refuses tiny RGB test pictures.
        trial = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=0.3",
                                "-pix_fmt", "yuv420p", "-c:v", "h264_videotoolbox", "-b:v", "4M", "-f", "null", "-"], capture_output=True, timeout=30)
        return trial.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def h264_args(ffmpeg: str, *, preset: str, crf: int, width: int, height: int, fps: float = 30.0, hardware: bool = False) -> list[str]:
    """Encoder arguments: the Mac's hardware encoder for a final render when it is
    there (several times faster, at a generous bitrate), else libx264."""
    if hardware and hardware_h264(ffmpeg):
        rate = max(4_000_000, int(width * height * fps * 0.14))          # ~8.7 Mb/s at 1080p30
        return ["-c:v", "h264_videotoolbox", "-b:v", str(rate), "-maxrate", str(int(rate * 1.5)),
                "-bufsize", str(rate * 2), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]
