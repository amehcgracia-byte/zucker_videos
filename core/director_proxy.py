"""Low-cost equirectangular proxies for the interactive 360 Director."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.media_validation import record_media_path
from core.normalization import global_cache_root

Progress = Callable[[int, str], None]

DIRECTOR_PROXY_VERSION = 3
DIRECTOR_PROXY_WIDTH = 1280
DIRECTOR_PROXY_HEIGHT = 640
DIRECTOR_PROXY_FPS = 15
DIRECTOR_PROXY_BITRATE = "550k"


def is_360_record(record: dict[str, Any]) -> bool:
    probe = record.get("probe") or {}
    projection = str(record.get("projection") or probe.get("projection") or "").lower()
    filename = str(record.get("path") or "").lower()
    return projection in {"equirect", "raw_insv"} or bool(record.get("raw_360") or probe.get("raw_360")) or filename.endswith((".insv", ".insp"))


def director_proxy_path(record: dict[str, Any]) -> Path:
    return global_cache_root() / "director_proxies" / f"{director_proxy_key(record)}.mp4"


def director_proxy_key(record: dict[str, Any]) -> str:
    source = Path(record.get("path") or record_media_path(record)).expanduser().resolve()
    stat = source.stat()
    probe = record.get("probe") or {}
    return sha256(
        json.dumps(
            {
                "recipe": f"director_equirect_proxy_v{DIRECTOR_PROXY_VERSION}",
                "path": str(source),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "projection": record.get("projection") or probe.get("projection"),
                "raw": bool(record.get("raw_360") or probe.get("raw_360")),
                "width": DIRECTOR_PROXY_WIDTH,
                "height": DIRECTOR_PROXY_HEIGHT,
                "fps": DIRECTOR_PROXY_FPS,
                "audio": "none",
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()[:24]


def director_proxy_status(record: dict[str, Any]) -> dict[str, Any]:
    path = director_proxy_path(record)
    exists = path.exists()
    metadata = _proxy_metadata(path) if exists else {}
    return {
        "ready": exists,
        "path": str(path),
        "relative": path.name,
        "size_bytes": path.stat().st_size if exists else 0,
        **metadata,
    }


def ensure_director_proxy(record: dict[str, Any], progress: Progress | None = None) -> dict[str, Any]:
    source = Path(record.get("path") or record_media_path(record)).expanduser().resolve()
    if not source.exists():
        raise ValueError("360 source file is missing")
    output = director_proxy_path(record)
    if output.exists():
        result = director_proxy_status(record)
        result["generated"] = False
        result["elapsed_sec"] = 0.0
        if progress:
            progress(100, "360 Director preview already prepared")
        return result

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(".tmp.mp4")
    if tmp.exists():
        tmp.unlink()
    duration = _source_duration(record)
    filtergraph = _director_filtergraph(record)
    start = time.monotonic()
    try:
        _run_proxy_command(_proxy_command(source, tmp, filtergraph, "h264_videotoolbox"), duration, source.name, progress)
        encode_path = "hardware"
    except FFmpegError:
        if tmp.exists():
            tmp.unlink()
        _run_proxy_command(_proxy_command(source, tmp, filtergraph, "libx264"), duration, source.name, progress)
        encode_path = "software"
    os.replace(tmp, output)
    elapsed = time.monotonic() - start
    result = director_proxy_status(record)
    result.update({"generated": True, "elapsed_sec": round(elapsed, 3), "encode_path": encode_path})
    if progress:
        progress(100, f"360 Director preview ready in {elapsed:.1f}s")
    return result


def _director_filtergraph(record: dict[str, Any]) -> str:
    probe = record.get("probe") or {}
    projection = str(record.get("projection") or probe.get("projection") or "").lower()
    raw = projection == "raw_insv" or bool(record.get("raw_360") or probe.get("raw_360"))
    spatial = ""
    if raw:
        fov = int(float(probe.get("insv_fov") or 190))
        spatial = f"v360=input=dfisheye:output=e:ih_fov={fov}:iv_fov={fov}:interp=lanczos,"
    return (
        f"{spatial}fps={DIRECTOR_PROXY_FPS},"
        f"scale={DIRECTOR_PROXY_WIDTH}:{DIRECTOR_PROXY_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={DIRECTOR_PROXY_WIDTH}:{DIRECTOR_PROXY_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1,format=yuv420p"
    )


def _proxy_command(source: Path, destination: Path, filtergraph: str, codec: str) -> list[str]:
    ffmpeg = tool_status().get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    command = [
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
        "-vf",
        filtergraph,
        "-an",
        "-r",
        str(DIRECTOR_PROXY_FPS),
        "-fps_mode",
        "cfr",
        "-c:v",
        codec,
    ]
    if codec == "libx264":
        command.extend(["-preset", "veryfast"])
    command.extend(
        [
            "-b:v",
            DIRECTOR_PROXY_BITRATE,
            "-maxrate",
            "800k",
            "-bufsize",
            "1200k",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ]
    )
    return command


def _run_proxy_command(command: list[str], duration: float, filename: str, progress: Progress | None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    current = 0
    started = time.monotonic()
    last_emit = 0.0
    for line in process.stdout:
        match = re.match(r"out_time_ms=(\d+)", line.strip())
        if not match or duration <= 0:
            continue
        seconds = int(match.group(1)) / 1_000_000
        percent = max(current, min(99, int(seconds / duration * 100)))
        now = time.monotonic()
        if progress and (percent > current or now - last_emit >= 3):
            current = percent
            last_emit = now
            eta = ""
            if percent > 0:
                remaining = max(0.0, (now - started) * (100 - percent) / percent)
                eta = f" · about {max(1, round(remaining / 60))} min remaining" if remaining >= 45 else f" · about {round(remaining)}s remaining"
            progress(percent, f"Preparing lightweight 360 preview for {filename} — {percent}%{eta}")
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError((stderr or "").strip() or "Could not generate 360 Director proxy")


def _source_duration(record: dict[str, Any]) -> float:
    probe = record.get("probe") or {}
    try:
        return max(0.1, float(probe.get("duration") or 0.0))
    except (TypeError, ValueError):
        pass
    try:
        metadata = ffprobe(str(record.get("path") or record_media_path(record)))
        return max(0.1, float((metadata.get("format") or {}).get("duration") or 0.0))
    except Exception:
        return 1.0


def _proxy_metadata(path: Path) -> dict[str, Any]:
    try:
        metadata = ffprobe(str(path))
        streams = metadata.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        return {
            "duration_sec": max(0.1, float((metadata.get("format") or {}).get("duration") or video.get("duration") or 0.0)),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
            "fps": _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        }
    except Exception:
        return {}


def _parse_rate(value: Any) -> float:
    text = str(value or "")
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            denom = float(right)
            return round(float(left) / denom, 3) if denom else 0.0
        except (TypeError, ValueError):
            return 0.0
    try:
        return round(float(text), 3)
    except (TypeError, ValueError):
        return 0.0
