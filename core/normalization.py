"""Ingest-time video normalization for downstream pipeline stages."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from core.ffmpeg import FFmpegError, tool_status
from core.media_validation import record_media_path
from core.project import Project
from core.stages.base import stable_fingerprint

Progress = Callable[[int, str], None]

SDR_TONEMAP_FILTER = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)
EVEN_SDR_FILTER = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
EQUIRECT_FILTER = "v360=input=equirect:output=flat:yaw=0:pitch=0:h_fov=100:w=1920:h=1080,format=yuv420p"


def ensure_normalized_space(project: Project, records: list[dict[str, Any]]) -> None:
    """Fail early when cache storage is unlikely to fit normalized outputs."""
    estimate = sum(int(record.get("size") or 0) for record in records)
    if estimate <= 0:
        return
    project.cache_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(project.cache_dir).free
    if free < estimate * 2:
        raise RuntimeError(
            "No hay espacio suficiente para preparar los vídeos. "
            "Libera espacio en disco o mueve el proyecto a un disco con más espacio."
        )


def normalize_video_record(project: Project, record: dict[str, Any], progress: Progress) -> dict[str, Any]:
    """Create or reuse the normalized mezzanine for one validated video record."""
    source = Path(record["path"]).expanduser().resolve()
    destination = normalized_path(project, record)
    signature = _source_signature(source)
    existing = record.get("normalized") or {}
    if not needs_normalization(record, destination):
        progress(100, f"Ya preparado: {source.name}")
        return existing

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(".tmp.mp4")
    if tmp_path.exists():
        tmp_path.unlink()

    probe = record.get("probe") or {}
    duration = float(probe.get("duration") or 0.0)
    fps = _bounded_fps(float(probe.get("fps") or 30.0))
    filtergraph = normalization_filter(probe)
    command = _normalization_command(source, tmp_path, fps, filtergraph)
    _run_ffmpeg_progress(command, duration, source.name, progress)
    os.replace(tmp_path, destination)
    normalized = {
        "path": str(destination),
        "source_size": signature["size"],
        "source_mtime": signature["mtime"],
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "fps": fps,
        "audio": "aac stereo 48k",
        "filter": filtergraph,
    }
    if probe.get("projection") == "equirect":
        normalized["projection"] = "equirect"
        normalized["reframe"] = {"yaw": 0, "pitch": 0, "h_fov": 100, "width": 1920, "height": 1080}
    record["normalized"] = normalized
    return normalized


def needs_normalization(record: dict[str, Any], destination: Path | None = None) -> bool:
    """Return True if the normalized mezzanine is missing or stale."""
    source = Path(record["path"]).expanduser()
    if destination is None:
        destination = Path(record_media_path(record))
    normalized = record.get("normalized") or {}
    if not destination.exists():
        return True
    try:
        stat = source.stat()
    except OSError:
        return True
    return normalized.get("source_size") != stat.st_size or normalized.get("source_mtime") != stat.st_mtime


def normalized_path(project: Project, record: dict[str, Any]) -> Path:
    """Return the cache path for a clip's normalized mezzanine."""
    clip_id = stable_fingerprint({"path": record["path"]})[:16]
    return project.cache_dir / "normalized" / f"{clip_id}.mp4"


def normalization_filter(probe: dict[str, Any]) -> str:
    """Return the ffmpeg filter chain used for the normalized mezzanine."""
    if probe.get("projection") == "equirect":
        if probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
            return f"v360=input=equirect:output=flat:yaw=0:pitch=0:h_fov=100:w=1920:h=1080,{SDR_TONEMAP_FILTER}"
        return EQUIRECT_FILTER
    if probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
        return f"{SDR_TONEMAP_FILTER},scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return EVEN_SDR_FILTER


def _normalization_command(source: Path, destination: Path, fps: float, filtergraph: str) -> list[str]:
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    return [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        filtergraph,
        "-r",
        f"{fps:.3f}",
        "-fps_mode",
        "cfr",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-metadata:s:v:0",
        "rotate=0",
        "-c:a",
        "aac",
        "-ac",
        "2",
        "-ar",
        "48000",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(destination),
    ]


def _run_ffmpeg_progress(command: list[str], duration: float, filename: str, progress: Progress) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    current = 0
    last_emit = 0.0
    for line in process.stdout:
        match = re.match(r"out_time_ms=(\d+)", line.strip())
        if not match or duration <= 0:
            continue
        seconds = int(match.group(1)) / 1_000_000
        percent = max(current, min(99, int(seconds / duration * 100)))
        now = time.monotonic()
        if percent > current or now - last_emit >= 5:
            current = percent
            last_emit = now
            progress(percent, f"{filename} — {percent}%")
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError((stderr or "").strip() or "ffmpeg normalization failed")
    progress(100, f"{filename} — 100%")


def _bounded_fps(fps: float) -> float:
    if fps <= 0:
        return 30.0
    return max(1.0, min(120.0, fps))


def _source_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime}
