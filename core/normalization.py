"""Ingest-time video normalization for downstream pipeline stages."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import logging
import math
from pathlib import Path
from typing import Any, Callable

from core.ffmpeg import FFmpegError, tool_status
from core.build_info import build_info
from core.messages import t
from core.project import Project
from core.stages.base import stable_fingerprint

Progress = Callable[[int, str], None]

NORMALIZATION_VERSION = 6
PROXY_MAX_WIDTH = 1280
PROXY_MAX_HEIGHT = 720
SDR_TONEMAP_FILTER = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)
EVEN_SDR_FILTER = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
LOGGER = logging.getLogger(__name__)

EQUIRECT_FILTER = "v360=input=equirect:output=flat:yaw=0:pitch=0:h_fov=100:v_fov=67.673:w=1280:h=720,fps=30,setpts=PTS-STARTPTS,format=yuv420p"
CACHE_SUBDIRS = ("proxies", "normalized", "segments", "audio", "envelopes", "thumbnails")
SEGMENT_CACHE_MAX_BYTES = 40 * 1024 * 1024 * 1024
SEGMENT_CACHE_MAX_AGE_DAYS = 14


def ensure_global_cache_dirs() -> Path:
    """Recreate disposable cache directories after manual cleanup."""
    root = global_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    for name in CACHE_SUBDIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
    cleanup_expired_segment_cache()
    return root


def ensure_normalized_space(project: Project, records: list[dict[str, Any]]) -> None:
    """Fail early when cache storage is unlikely to fit normalized outputs."""
    estimate = sum(int(record.get("size") or 0) for record in records) // 4
    if estimate <= 0:
        return
    root = ensure_global_cache_dirs()
    free = shutil.disk_usage(root).free
    if free < estimate * 2:
        raise RuntimeError(t("not_enough_space"))


def normalize_video_record(project: Project, record: dict[str, Any], progress: Progress) -> dict[str, Any]:
    """Create or reuse the low-resolution proxy for one validated video record."""
    source = Path(record["path"]).expanduser().resolve()
    destination = normalized_path(project, record)
    signature = _source_signature(source)
    key = cache_key_for_signature(source, signature)
    record["cache_key"] = key
    existing = record.get("normalized") or {}
    probe = record.get("probe") or {}
    if proxy_transcode_compliant(probe):
        normalized = {
            "path": str(source),
            "cache_key": key,
            "normalization_version": NORMALIZATION_VERSION,
            "kind": "original",
            "proxy_skipped": True,
            "skip_reason": "h264 CFR SDR no-rotation <=1080p",
            "source_size": signature["size"],
            "source_mtime": signature["mtime"],
        }
        record["normalized"] = normalized
        LOGGER.info("Proxy skipped for compliant source %s", source)
        progress(100, t("already_prepared", filename=source.name))
        return normalized
    if _migrate_legacy_normalized(project, record, destination, signature):
        progress(100, t("already_prepared", filename=source.name))
        return record["normalized"]
    if not needs_normalization(record, destination):
        existing.update(
            {
                "path": str(destination),
                "cache_key": key,
                "normalization_version": NORMALIZATION_VERSION,
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

    duration = float(probe.get("duration") or 0.0)
    fps = _bounded_fps(float(probe.get("fps") or 30.0))
    filtergraph = normalization_filter(probe, fps=fps, proxy=True)
    if probe.get("projection") == "equirect":
        LOGGER.info("Normalizing equirectangular source %s with probe=%s filter=%s", source, probe, filtergraph)
    try:
        _run_ffmpeg_progress(
            _normalization_command(source, tmp_path, fps, filtergraph, "h264_videotoolbox", hwaccel=True, paired_source=_paired_source(record)),
            duration,
            source.name,
            progress,
        )
        encode_path = "hardware"
        LOGGER.info("Proxy generated with hardware decode/encode for %s", source)
    except FFmpegError:
        if tmp_path.exists():
            tmp_path.unlink()
        _run_ffmpeg_progress(_normalization_command(source, tmp_path, fps, filtergraph, "libx264", paired_source=_paired_source(record)), duration, source.name, progress)
        encode_path = "software"
        LOGGER.info("Proxy generated with software fallback for %s", source)
    os.replace(tmp_path, destination)
    normalized = {
        "path": str(destination),
        "cache_key": key,
        "normalization_version": NORMALIZATION_VERSION,
        "kind": "proxy",
        "encode_path": encode_path,
        "source_size": signature["size"],
        "source_mtime": signature["mtime"],
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "fps": fps,
        "audio": "aac stereo 48k",
        "max_width": PROXY_MAX_WIDTH,
        "max_height": PROXY_MAX_HEIGHT,
        "filter": filtergraph,
    }
    if probe.get("projection") == "equirect":
        normalized["projection"] = "equirect"
        normalized["reframe"] = {"yaw": 0, "pitch": 0, "h_fov": 100, "width": PROXY_MAX_WIDTH, "height": PROXY_MAX_HEIGHT}
    record["normalized"] = normalized
    return normalized


def needs_normalization(record: dict[str, Any], destination: Path | None = None) -> bool:
    """Return True if the normalized mezzanine is missing or stale."""
    source = Path(record["path"]).expanduser().resolve()
    if destination is None:
        destination = normalized_path(None, record)
    normalized = record.get("normalized") or {}
    if proxy_transcode_compliant(record.get("probe") or {}):
        return normalized.get("path") != str(source) or normalized.get("normalization_version") != NORMALIZATION_VERSION
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
    """Return the cache path for a clip's analysis proxy."""
    return global_proxy_path(source_cache_key(record))


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
            normalized["normalization_version"] = NORMALIZATION_VERSION
    if not key:
        key = stable_fingerprint(
            {
                "path": record.get("path"),
                "size": record.get("size"),
                "mtime": record.get("mtime"),
                "normalization_version": NORMALIZATION_VERSION,
            }
        )[:24]
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
            "normalization_version": NORMALIZATION_VERSION,
        }
    )[:24]


def global_cache_root() -> Path:
    """Return the app-wide media cache root."""
    return Path.home() / "ZuckerVideos" / "Cache"


def global_normalized_path(key: str) -> Path:
    """Return the global normalized MP4 path for a source key."""
    return global_cache_root() / "normalized" / f"{key}.mp4"


def global_proxy_path(key: str) -> Path:
    """Return the global proxy MP4 path for a source key."""
    return global_cache_root() / "proxies" / f"{key}.mp4"


def global_segment_path(key: str) -> Path:
    """Return the global rendered-segment path for a segment recipe key."""
    return global_cache_root() / "segments" / f"{key}.mp4"


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


def cleanup_expired_segment_cache(
    projects_root: Path | None = None,
    max_age_days: int = SEGMENT_CACHE_MAX_AGE_DAYS,
    max_bytes: int = SEGMENT_CACHE_MAX_BYTES,
) -> dict[str, Any]:
    """Keep global render segments bounded and remove stale recipe versions.

    Segment files are disposable and globally keyed. A sidecar from an older
    build cannot pass the renderer's attestation check, so it is immediately
    eligible unless a live project explicitly references that segment. Current
    segments are retained for 14 days, then the oldest unreferenced files are
    evicted until the cache is at or below 40 GB.
    """
    folder = global_cache_root() / "segments"
    if not folder.exists():
        return {"deleted_files": 0, "deleted_bytes": 0, "remaining_bytes": 0}
    current_commit = str(build_info().get("git_commit") or "unknown")
    referenced = _referenced_segment_names(projects_root)
    now = time.time()
    entries: list[tuple[Path, Path | None, int, float, bool, bool]] = []
    total = 0
    for segment in folder.glob("*.mp4"):
        try:
            size = segment.stat().st_size
            mtime = segment.stat().st_mtime
        except OSError:
            continue
        sidecar = segment.with_suffix(segment.suffix + ".json")
        commit = ""
        if sidecar.exists():
            try:
                import json
                commit = str(json.loads(sidecar.read_text(encoding="utf-8")).get("git_commit") or "")
            except (OSError, ValueError, json.JSONDecodeError):
                commit = ""
        protected = segment.name in referenced or sidecar.name in referenced
        entries.append((segment, sidecar if sidecar.exists() else None, size, mtime, protected or commit == current_commit, commit != current_commit))
        total += size + (sidecar.stat().st_size if sidecar.exists() else 0)
    deleted_files = 0
    deleted_bytes = 0
    for segment, sidecar, size, mtime, protected, stale_recipe in sorted(entries, key=lambda item: item[3]):
        expired = (now - mtime) > max_age_days * 86400
        over_limit = total > max_bytes
        if protected or not (stale_recipe or expired or over_limit):
            continue
        for path in (segment, sidecar):
            if path is None:
                continue
            try:
                bytes_removed = path.stat().st_size
                path.unlink()
            except OSError:
                continue
            deleted_files += 1
            deleted_bytes += bytes_removed
            total -= bytes_removed
    return {"deleted_files": deleted_files, "deleted_bytes": deleted_bytes, "remaining_bytes": total}


def _referenced_segment_names(projects_root: Path | None = None) -> set[str]:
    """Find segment basenames explicitly retained by live project artifacts."""
    import json
    root = Path(projects_root or (Path.home() / "ZuckerVideos" / "Projects")).expanduser()
    names: set[str] = set()
    if not root.exists():
        return names
    for path in root.rglob("*.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for candidate in (root.parent / "Cache" / "segments").glob("*.mp4"):
            if candidate.name in text or candidate.with_suffix(candidate.suffix + ".json").name in text:
                names.add(candidate.name)
                names.add(candidate.with_suffix(candidate.suffix + ".json").name)
    return names


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
        destination = global_proxy_path(key)
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
                        "normalization_version": NORMALIZATION_VERSION,
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


def normalization_filter(probe: dict[str, Any], fps: float | None = None, proxy: bool = False) -> str:
    """Return the ffmpeg filter chain used for proxies or full-quality fragments."""
    target_fps = _bounded_fps(float(fps or probe.get("fps") or 30.0))
    timing_filter = f"fps={target_fps:.3f},setpts=PTS-STARTPTS"
    if proxy:
        size_filter = f"scale={PROXY_MAX_WIDTH}:{PROXY_MAX_HEIGHT}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"
    else:
        size_filter = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if probe.get("projection") == "raw_insv":
        fov = _int_or_zero(probe.get("insv_fov") or 190) or 190
        width = PROXY_MAX_WIDTH if proxy else 1920
        height = PROXY_MAX_HEIGHT if proxy else 1080
        h_fov, v_fov = _paired_flat_fov(100.0, width / height)
        stitch = (
            f"v360=input=dfisheye:output=e:ih_fov={fov}:iv_fov={fov},"
            f"v360=input=equirect:output=flat:yaw=0:pitch=0:h_fov={h_fov:.3f}:v_fov={v_fov:.3f}:w={width}:h={height},"
            f"{timing_filter}"
        )
        if probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
            return f"{stitch},{SDR_TONEMAP_FILTER}"
        return f"{stitch},format=yuv420p"
    if probe.get("projection") == "equirect":
        width = PROXY_MAX_WIDTH if proxy else 1920
        height = PROXY_MAX_HEIGHT if proxy else 1080
        h_fov, v_fov = _paired_flat_fov(100.0, width / height)
        equirect = f"v360=input=equirect:output=flat:yaw=0:pitch=0:h_fov={h_fov:.3f}:v_fov={v_fov:.3f}:w={width}:h={height},{timing_filter}"
        if probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
            return f"{equirect},{SDR_TONEMAP_FILTER}"
        return f"{equirect},format=yuv420p"
    if probe.get("hdr") or int(probe.get("bit_depth") or 8) > 8:
        return f"{SDR_TONEMAP_FILTER},{size_filter},{timing_filter}"
    return f"{size_filter},{timing_filter},format=yuv420p" if proxy else f"{EVEN_SDR_FILTER},{timing_filter}"


def _paired_flat_fov(horizontal_fov: float, aspect_ratio: float) -> tuple[float, float]:
    horizontal = max(1.0, min(179.0, float(horizontal_fov)))
    aspect = max(0.1, float(aspect_ratio))
    vertical = math.degrees(2.0 * math.atan(math.tan(math.radians(horizontal) / 2.0) / aspect))
    return horizontal, max(1.0, min(179.0, vertical))


def proxy_transcode_compliant(probe: dict[str, Any]) -> bool:
    """Return True when the original can safely serve as the analysis proxy."""
    codec = str(probe.get("video_codec") or "").lower()
    width = _int_or_zero(probe.get("width"))
    height = _int_or_zero(probe.get("height"))
    bit_depth = _int_or_zero(probe.get("bit_depth") or 8)
    rotation = _float_or_zero(probe.get("rotation"))
    return (
        codec == "h264"
        and bool(probe.get("cfr")) is True
        and not bool(probe.get("hdr"))
        and bit_depth <= 8
        and abs(rotation) < 0.01
        and not probe.get("projection")
        and width > 0
        and height > 0
        and width <= 1920
        and height <= 1080
    )


def _normalization_command(
    source: Path,
    destination: Path,
    fps: float,
    filtergraph: str,
    codec: str = "h264_videotoolbox",
    hwaccel: bool = False,
    paired_source: Path | None = None,
) -> list[str]:
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
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
    ]
    if hwaccel:
        command.extend(["-hwaccel", "videotoolbox"])
    command.extend(["-i", str(source)])
    if paired_source:
        command.extend(["-i", str(paired_source)])
        command.extend(
            [
                "-filter_complex",
                f"[0:v][1:v]hstack=inputs=2[dual];[dual]{filtergraph}[v]",
                "-map",
                "[v]",
                "-map",
                "0:a?",
            ]
        )
    else:
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                filtergraph,
            ]
        )
    command.extend([
        "-r",
        f"{fps:.3f}",
        "-fps_mode",
        "cfr",
        "-c:v",
        codec,
        "-b:v",
        "2500k",
        "-maxrate",
        "3500k",
        "-bufsize",
        "7000k",
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
    ])
    return command


def _paired_source(record: dict[str, Any]) -> Path | None:
    path = record.get("paired_path") or (record.get("probe") or {}).get("paired_path")
    if not path:
        return None
    candidate = Path(str(path)).expanduser()
    return candidate if candidate.exists() else None


def _run_ffmpeg_progress(command: list[str], duration: float, filename: str, progress: Progress) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    current = 0
    last_emit = 0.0
    try:
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
    except BaseException:
        # progress() can raise (e.g. a user cancellation) — don't leave the
        # ffmpeg process running in the background when that happens.
        process.kill()
        process.wait()
        raise
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError((stderr or "").strip() or "ffmpeg normalization failed")
    progress(100, f"{filename} — 100%")


def _bounded_fps(fps: float) -> float:
    if fps <= 0:
        return 30.0
    return max(1.0, min(120.0, fps))


def _int_or_zero(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float_or_zero(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime}


def _legacy_normalized_path(project: Project, record: dict[str, Any]) -> Path:
    clip_id = stable_fingerprint({"path": record["path"]})[:16]
    return project.cache_dir / "normalized" / f"{clip_id}.mp4"


def _migrate_legacy_normalized(project: Project, record: dict[str, Any], destination: Path, signature: dict[str, Any]) -> bool:
    """Move a matching per-project normalized file into the global cache."""
    normalized = record.get("normalized") or {}
    if normalized.get("normalization_version") != NORMALIZATION_VERSION or normalized.get("kind") != "proxy":
        return False
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
