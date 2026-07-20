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
from core.messages import t
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
CACHE_SUBDIRS = ("normalized", "audio", "envelopes", "thumbnails")


def ensure_normalized_space(project: Project, records: list[dict[str, Any]]) -> None:
    """Fail early when cache storage is unlikely to fit normalized outputs."""
    estimate = sum(int(record.get("size") or 0) for record in records)
    if estimate <= 0:
        return
    root = global_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free
    if free < estimate * 2:
        raise RuntimeError(
            t("not_enough_space")
        )


def normalize_video_record(project: Project, record: dict[str, Any], progress: Progress) -> dict[str, Any]:
    """Create or reuse the normalized mezzanine for one validated video record."""
    source = Path(record["path"]).expanduser().resolve()
    destination = normalized_path(project, record)
    signature = _source_signature(source)
    key = cache_key_for_signature(source, signature)
    record["cache_key"] = key
    existing = record.get("normalized") or {}
    if _migrate_legacy_normalized(project, record, destination, signature):
        progress(100, t("already_prepared", filename=source.name))
        return record["normalized"]
    if not needs_normalization(record, destination):
        existing.update(
            {
                "path": str(destination),
                "cache_key": key,
                "source_size": signature["size"],
                "source_mtime": signature["mtime"],
            }
        )
        record["normalized"] = existing
        progress(100, t("already_prepared", filename=source.name))
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
        "cache_key": key,
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
    source = Path(record["path"]).expanduser().resolve()
    if destination is None:
        destination = normalized_path(None, record)
    normalized = record.get("normalized") or {}
    if not destination.exists():
        return True
    try:
        signature = _source_signature(source)
    except OSError:
        return True
    expected_key = cache_key_for_signature(source, signature)
    if record.get("cache_key") != expected_key:
        return True
    if normalized.get("cache_key") not in {None, expected_key}:
        return True
    if normalized.get("source_size") is None and normalized.get("source_mtime") is None:
        return False
    return normalized.get("source_size") != signature["size"] or normalized.get("source_mtime") != signature["mtime"]


def normalized_path(project: Project | None, record: dict[str, Any]) -> Path:
    """Return the cache path for a clip's normalized mezzanine."""
    return global_normalized_path(source_cache_key(record))


def source_cache_key(record: dict[str, Any]) -> str:
    """Return the source-signature cache key for a record."""
    try:
        source = Path(record["path"]).expanduser().resolve()
        signature = _source_signature(source)
        key = cache_key_for_signature(source, signature)
    except OSError:
        key = str(record.get("cache_key") or "")
    if key:
        record["cache_key"] = key
        normalized = record.get("normalized")
        if isinstance(normalized, dict):
            normalized["cache_key"] = key
    if not key:
        key = stable_fingerprint({"path": record.get("path"), "size": record.get("size"), "mtime": record.get("mtime")})[:24]
    return key


def cache_key_for_source(path: str | Path) -> str:
    """Return the cache key for a source file's current path/size/mtime."""
    source = Path(path).expanduser().resolve()
    return cache_key_for_signature(source, _source_signature(source))


def cache_key_for_signature(source: Path, signature: dict[str, Any]) -> str:
    """Return the cheap stable global cache key for a source signature."""
    return stable_fingerprint(
        {
            "path": str(source),
            "size": signature.get("size"),
            "mtime": signature.get("mtime"),
        }
    )[:24]


def global_cache_root() -> Path:
    """Return the app-wide media cache root."""
    return Path.home() / "ZuckerVideos" / "Cache"


def global_normalized_path(key: str) -> Path:
    """Return the global normalized MP4 path for a source key."""
    return global_cache_root() / "normalized" / f"{key}.mp4"


def global_clip_audio_path(key: str) -> Path:
    """Return the global extracted clip-audio path for a source key."""
    return global_cache_root() / "audio" / f"{key}.wav"


def global_clip_envelope_path(key: str) -> Path:
    """Return the global clip-envelope path for a source key."""
    return global_cache_root() / "envelopes" / f"{key}.npy"


def global_thumbnail_path(key: str, signature: dict[str, Any]) -> Path:
    """Return the global thumbnail path for a source key and normalized signature."""
    return global_cache_root() / "thumbnails" / f"{key}-{signature['size']}-{int(signature['mtime'])}.jpg"


def cache_status() -> dict[str, Any]:
    """Return global cache size and entry counts."""
    root = global_cache_root()
    size = _directory_size(root)
    counts = {name: len(list((root / name).glob("*"))) if (root / name).exists() else 0 for name in CACHE_SUBDIRS}
    return {"path": str(root), "size_bytes": size, "counts": counts}


def cleanup_unreferenced_cache(projects_root: Path | None = None) -> dict[str, Any]:
    """Delete global cache files whose source key is not referenced by any project."""
    root = global_cache_root()
    before = _directory_size(root)
    referenced = referenced_cache_keys(projects_root)
    deleted_files = 0
    deleted_bytes = 0
    for subdir in CACHE_SUBDIRS:
        folder = root / subdir
        if not folder.exists():
            continue
        for path in folder.iterdir():
            if not path.is_file():
                continue
            key = path.name.split(".", 1)[0].split("-", 1)[0]
            if key in referenced:
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            deleted_files += 1
            deleted_bytes += size
    return {
        "path": str(root),
        "before_bytes": before,
        "after_bytes": _directory_size(root),
        "deleted_bytes": deleted_bytes,
        "deleted_files": deleted_files,
        "referenced_keys": len(referenced),
    }


def migrate_project_normalization_cache(project: Project) -> bool:
    """Best-effort migration from legacy per-project normalized files to the global cache."""
    changed = False
    for record in project.data.get("inputs", {}).get("videos", []):
        if record.get("status") == "not_a_video" or not record.get("path"):
            continue
        try:
            source = Path(record["path"]).expanduser().resolve()
            signature = _source_signature(source)
        except OSError:
            continue
        key = cache_key_for_signature(source, signature)
        destination = global_normalized_path(key)
        migrated = _migrate_legacy_normalized(project, record, destination, signature)
        if migrated:
            changed = True
        if destination.exists() or migrated:
            normalized = record.get("normalized") or {}
            if normalized.get("path") != str(destination) or normalized.get("cache_key") != key or record.get("cache_key") != key:
                normalized.update(
                    {
                        "path": str(destination),
                        "cache_key": key,
                        "source_size": signature["size"],
                        "source_mtime": signature["mtime"],
                    }
                )
                record["normalized"] = normalized
                record["cache_key"] = key
                changed = True
    if changed:
        project.save()
    return changed


def referenced_cache_keys(projects_root: Path | None = None) -> set[str]:
    """Return cache keys referenced by existing project.json files."""
    import json

    root = Path(projects_root or (Path.home() / "ZuckerVideos" / "Projects")).expanduser()
    keys: set[str] = set()
    if not root.exists():
        return keys
    for project_json in root.rglob("project.json"):
        try:
            with project_json.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        for record in data.get("inputs", {}).get("videos", []):
            key = record.get("cache_key") or (record.get("normalized") or {}).get("cache_key")
            if key:
                keys.add(str(key))
    return keys


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


def _legacy_normalized_path(project: Project, record: dict[str, Any]) -> Path:
    clip_id = stable_fingerprint({"path": record["path"]})[:16]
    return project.cache_dir / "normalized" / f"{clip_id}.mp4"


def _migrate_legacy_normalized(project: Project, record: dict[str, Any], destination: Path, signature: dict[str, Any]) -> bool:
    """Move a matching per-project normalized file into the global cache."""
    normalized = record.get("normalized") or {}
    candidates = []
    existing_path = normalized.get("path")
    if existing_path:
        candidates.append(Path(existing_path).expanduser())
    candidates.append(_legacy_normalized_path(project, record))
    key = cache_key_for_signature(Path(record["path"]).expanduser().resolve(), signature)
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
        except OSError:
            continue
        if candidate == destination.resolve() or not candidate.exists():
            continue
        if normalized.get("source_size") != signature["size"] or normalized.get("source_mtime") != signature["mtime"]:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            try:
                shutil.move(str(candidate), str(destination))
            except OSError:
                try:
                    shutil.copy2(candidate, destination)
                except OSError:
                    continue
        record["cache_key"] = key
        record["normalized"] = {
            **normalized,
            "path": str(destination),
            "cache_key": key,
            "source_size": signature["size"],
            "source_mtime": signature["mtime"],
        }
        return True
    return False


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total
