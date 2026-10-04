"""Global inbox scanning, input classification, and registration helpers."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from typing import Any

from core.ffmpeg import configure_tools, ffprobe
from core.media_validation import VIDEO_EXTENSIONS, is_raw_360_path, raw_360_model_fov, static_rejection_reason, validate_camera_video_metadata
from core.normalization import normalize_video_record
from core.project import STAGE_NAMES, Project, file_record
from core.stages.sync import coarse_master_match, content_identity, load_or_compute_clip_envelope, load_or_compute_master_envelope, normalized_onset_envelope, sync_clip, sync_confidence_threshold
from core.stages.base import stable_fingerprint

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif"}
DEFAULT_MASTER_AUDIO_EXTENSIONS = [".mp3"]
LOGGER = logging.getLogger(__name__)
_ANALYSIS_LOCK = threading.RLock()
_ANALYSIS_THREAD: threading.Thread | None = None
_ANALYSIS_STATUS: dict[str, Any] = {"status": "idle", "progress": 0, "detail": ""}
COARSE_MATCH_VERSION = 5
COARSE_MATCH_THRESHOLD = 6.0


def app_home() -> Path:
    """Return the global Zucker Videos home directory."""
    from core.storage import data_root
    return data_root()


def config_path() -> Path:
    """Return the global app config path."""
    return app_home() / "config.json"


def load_global_config() -> dict[str, Any]:
    """Load or create global app config."""
    app_home().mkdir(parents=True, exist_ok=True)
    path = config_path()
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            config = json.load(fh)
    else:
        config = {"inbox_path": str(app_home() / "Inbox"), "source_folders": [str(app_home() / "Inbox")]}
        save_global_config(config)
    config.setdefault("band_name", "")
    config.setdefault("handle", "")
    config.setdefault("backstage_audio", {
        "base_volume": 0.45,
        "duck_ratio": 8.0,
        "target_lufs": -16.0,
        "states": ["music_only", "music_plus_clip_audio", "clip_audio_only"],
        "transition_fade_sec": 1.0,
    })
    inbox = Path(config.get("inbox_path") or app_home() / "Inbox").expanduser()
    inbox.mkdir(parents=True, exist_ok=True)
    config["inbox_path"] = str(inbox.resolve())
    previous_folders = config.get("source_folders")
    configured = previous_folders
    if not isinstance(configured, list):
        configured = [config["inbox_path"]]
    config["source_folders"] = normalize_source_folders(configured, default=str(inbox))
    tools = configure_tools(config.get("ffmpeg_path"), config.get("ffprobe_path"))
    if (
        config.get("ffmpeg_path") != tools["ffmpeg_path"]
        or config.get("ffprobe_path") != tools["ffprobe_path"]
        or previous_folders != config["source_folders"]
    ):
        config.update(tools)
        save_global_config(config)
    return config


def save_global_config(config: dict[str, Any]) -> None:
    """Save global app config."""
    app_home().mkdir(parents=True, exist_ok=True)
    path = config_path()
    pending = path.with_name(".config-" + uuid.uuid4().hex + ".tmp")
    try:
        pending.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def scan_inbox(root: str | None = None) -> dict[str, Any]:
    """Scan configured source folders without requiring external drives online."""
    config = load_global_config()
    folders = [Path(root).expanduser().resolve()] if root else [Path(path) for path in config["source_folders"]]
    result = classify_source_folders(folders)
    result["analysis"] = inbox_analysis_snapshot()
    return result


def normalize_source_folders(values: list[Any], default: str | None = None) -> list[str]:
    """Normalize configured source roots while preserving unavailable volumes."""
    candidates = values or ([default] if default else [])
    normalized: list[str] = []
    for value in candidates:
        if not isinstance(value, str) or not value.strip():
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            continue
        resolved = str(path.resolve())
        if resolved not in normalized:
            normalized.append(resolved)
    return normalized


def configured_source_folders() -> list[dict[str, Any]]:
    """Return configured source roots with mount status and a user-facing message."""
    config = load_global_config()
    folders: list[dict[str, Any]] = []
    for raw in config.get("source_folders") or [config["inbox_path"]]:
        path = Path(raw).expanduser().resolve()
        exists = path.is_dir()
        folders.append({
            "path": str(path),
            "name": path.name or str(path),
            "available": exists,
            "message": "" if exists else f"Source folder unavailable (drive not mounted): {path}",
        })
    return folders


def configured_master_audio_extensions() -> list[str]:
    """Return extensions treated as finished mixes, defaulting to MP3."""
    config = load_global_config()
    values = config.get("master_audio_extensions")
    if not isinstance(values, list):
        return list(DEFAULT_MASTER_AUDIO_EXTENSIONS)
    return normalize_master_audio_extensions(values)


def normalize_master_audio_extensions(values: list[Any]) -> list[str]:
    allowed = [str(value).lower() if str(value).startswith(".") else f".{str(value).lower()}" for value in values]
    return [value for value in allowed if value in AUDIO_EXTENSIONS]


def master_filter_message(folder_name: str | None = None) -> str:
    prefix = f"No mixes found in {folder_name} — " if folder_name else "No mixes found — "
    return prefix + "masters are expected as .mp3"


def _cached_classification_entries() -> dict[str, dict[str, Any]]:
    """Return stat-keyed classifications so Inbox refreshes avoid repeat ffprobe calls."""
    path = inbox_analysis_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries") or {}
    return entries if isinstance(entries, dict) else {}


def classify_source_folders(folders: list[Path]) -> dict[str, Any]:
    """Classify all configured roots recursively and group results by root."""
    result: dict[str, Any] = {
        "inbox_path": load_global_config()["inbox_path"],
        "source_folders": [], "groups": [], "master": [], "songs": [], "videos": [], "ignored": [],
        "missing_sources": [],
    }
    allowed_master_extensions = set(configured_master_audio_extensions())
    result["master_audio_extensions"] = sorted(allowed_master_extensions)
    seen: set[Path] = set()
    cached_entries = _cached_classification_entries()
    for folder in folders:
        resolved = folder.expanduser().resolve()
        available = resolved.is_dir()
        folder_info = {
            "path": str(resolved), "name": resolved.name or str(resolved), "available": available,
            "message": "" if available else f"Source folder unavailable (drive not mounted): {resolved}",
        }
        result["source_folders"].append(folder_info)
        if not available:
            result["missing_sources"].append(folder_info["message"])
            result["groups"].append({**folder_info, "master": [], "songs": [], "videos": [], "ignored": []})
            continue
        group = {**folder_info, "master": [], "songs": [], "videos": [], "ignored": []}
        for child in scan_input_paths([str(resolved)]):
            if child in seen:
                continue
            seen.add(child)
            suffix = child.suffix.lower()
            if suffix not in VIDEO_EXTENSIONS and suffix not in AUDIO_EXTENSIONS and suffix != ".json":
                continue
            try:
                cached = None
                try:
                    cached = cached_entries.get(_analysis_key(child))
                except OSError:
                    cached = None
                if isinstance(cached, dict) and str(cached.get("path") or "") == str(child):
                    item = dict(cached)
                else:
                    item = classify_file(child)
                if item.get("kind") == "master" and child.suffix.lower() not in allowed_master_extensions:
                    item = {**item, "kind": "ignored", "note": "Audio stem excluded: only configured mix formats are masters", "checked": False}
            except OSError:
                continue
            item["source_folder"] = str(resolved)
            group.setdefault(item["kind"], []).append(item)
            result.setdefault(item["kind"], []).append(item)
            log_classification_verdict(child, item)
        result["groups"].append(group)
        if available and not group["master"]:
            group["message"] = master_filter_message(group["name"])
    _prefer_studio_exports(result)
    for group in result["groups"]:
        _prefer_studio_exports(group)
    return result


def inbox_analysis_path() -> Path:
    """Return the persistent global Inbox pre-analysis manifest."""
    return app_home() / "Cache" / "inbox_analysis.json"


def inbox_analysis_snapshot() -> dict[str, Any]:
    """Return current Inbox analysis progress and cached match data."""
    with _ANALYSIS_LOCK:
        snapshot = dict(_ANALYSIS_STATUS)
    path = inbox_analysis_path()
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            allowed = set(configured_master_audio_extensions())
            snapshot["masters"] = [item for item in (payload.get("masters") or []) if Path(str(item.get("path") or "")).suffix.lower() in allowed]
            snapshot["files"] = payload.get("files") or []
            snapshot["completed_at"] = payload.get("completed_at")
            snapshot["master_audio_extensions"] = sorted(allowed)
            snapshot["coarse_match_version"] = payload.get("coarse_match_version")
            if payload.get("coarse_match_version") != COARSE_MATCH_VERSION:
                # Do not expose an older, weaker matcher as if it were a
                # completed analysis. The next explicit Inbox load/start will
                # rebuild it with the current threshold and cache format.
                snapshot["status"] = "idle"
                snapshot["progress"] = 0
                snapshot["detail"] = "Inbox analysis needs refresh"
                snapshot["masters"] = []
                snapshot["files"] = []
                snapshot.pop("completed_at", None)
                return snapshot
            if payload.get("completed_at") and snapshot.get("status") == "idle":
                snapshot["status"] = "done"
                snapshot["progress"] = 100
                snapshot["detail"] = f"Inbox ready: {len(snapshot['files'])} analyzed files, {len(snapshot['masters'])} audio masters"
        except (OSError, json.JSONDecodeError):
            pass
    return snapshot


def start_inbox_analysis(root: str | None = None) -> dict[str, Any]:
    """Start one background scan; unchanged files reuse the global manifest."""
    global _ANALYSIS_THREAD
    with _ANALYSIS_LOCK:
        if _ANALYSIS_THREAD and _ANALYSIS_THREAD.is_alive():
            return inbox_analysis_snapshot()
        _ANALYSIS_STATUS.update({"status": "running", "progress": 0, "detail": "Scanning Inbox"})
        _ANALYSIS_THREAD = threading.Thread(target=_run_inbox_analysis, args=(root,), daemon=True, name="zucker-inbox-analysis")
        _ANALYSIS_THREAD.start()
    return inbox_analysis_snapshot()


def _analysis_key(path: Path) -> str:
    stat = path.stat()
    return stable_fingerprint({"path": str(path.resolve()), "size": stat.st_size, "mtime": stat.st_mtime})[:32]


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(root.expanduser().resolve())
        return True
    except ValueError:
        return False


def _source_folder_for_path(path: Path, roots: list[Path]) -> str | None:
    for root in roots:
        if _path_is_within(path, root):
            return str(root.expanduser().resolve())
    return None


def _write_inbox_analysis(payload: dict[str, Any]) -> None:
    path = inbox_analysis_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _analysis_project() -> Project:
    """Create a lightweight in-cache project context for shared normalization/audio caches."""
    root = app_home() / "Cache" / "inbox-analysis-work"
    project = Project(root, {"inputs": {}, "settings": {"sync": {"confidence_threshold": 6.0}}})
    project.ensure_dirs()
    return project


def _run_inbox_analysis(root: str | None) -> None:
    try:
        config = load_global_config()
        configured = [Path(path) for path in config.get("source_folders") or [config["inbox_path"]]]
        selected_root = Path(root).expanduser().resolve() if root else None
        scan_roots = [selected_root] if selected_root else configured
        old: dict[str, Any] = {}
        path = inbox_analysis_path()
        if path.exists():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                old = {}
        entries = dict(old.get("entries") or {})
        # A folder-specific rescan must not discard cached analysis for other
        # roots, especially when their external drive is currently unplugged.
        preserved = []
        for cached_entry in entries.values():
            cached_path = Path(str(cached_entry.get("path") or "")).expanduser()
            if selected_root and _path_is_within(cached_path, selected_root):
                continue
            if not selected_root:
                continue
            preserved.append(cached_entry)
        current: list[dict[str, Any]] = preserved
        files = [
            path for scan_root in scan_roots
            for path in scan_input_paths([str(scan_root)])
            if path.suffix.lower() in VIDEO_EXTENSIONS or path.suffix.lower() in AUDIO_EXTENSIONS
        ]
        current_payload = {
            "schema": 1,
            "entries": entries,
            "files": current,
            "masters": old.get("masters") or [],
        }
        total = max(1, len(files))
        analysis_project = _analysis_project()
        for index, media_path in enumerate(files, start=1):
            try:
                key = _analysis_key(media_path)
            except OSError:
                # Cameras and sync tools can move/rename files while the scan
                # is running.  Skip that item and keep the work already done.
                LOGGER.info("Skipping Inbox item that disappeared during scan: %s", media_path)
                continue
            cached = entries.get(key)
            if cached and cached.get("path") == str(media_path):
                entry = cached
                if media_path.suffix.lower() in AUDIO_EXTENSIONS:
                    if media_path.suffix.lower() in configured_master_audio_extensions():
                        entry = {**entry, "kind": "master", "note": "audio file", "checked": True}
                    else:
                        entry = {**entry, "kind": "ignored", "note": "Audio stem excluded: only configured mix formats are masters", "checked": False}
                    entries[key] = entry
            else:
                try:
                    item = classify_file(media_path)
                    entry = {**item, "analysis_key": key, "source_folder": _source_folder_for_path(media_path, configured)}
                    if entry.get("kind") == "master" and media_path.suffix.lower() not in configured_master_audio_extensions():
                        entry = {**entry, "kind": "ignored", "note": "Audio stem excluded: only configured mix formats are masters", "checked": False}
                    if item.get("kind") == "videos":
                        record = file_record(str(media_path))
                        record.update(video_classification_metadata(media_path))
                        entry["probe"] = record.get("probe") or item.get("probe") or {}
                        # Inbox matching must stay cheap and must not create a
                        # video proxy before we know that its audio belongs to
                        # a selected master.  Full normalization remains part
                        # of the later wizard ingest path.
                        entry["normalized"] = {}
                    elif item.get("kind") == "master":
                        entry["duration"] = item.get("duration")
                except OSError:
                    LOGGER.info("Skipping Inbox item that became unavailable during analysis: %s", media_path)
                    continue
                entries[key] = entry
            current.append(entry)
            current_payload["files"] = current
            current_payload["entries"] = entries
            _write_inbox_analysis(current_payload)
            with _ANALYSIS_LOCK:
                _ANALYSIS_STATUS.update({"progress": int(index / total * 35), "detail": f"Preparing {media_path.name}"})
        videos = [entry for entry in current if entry.get("kind") == "videos" and Path(str(entry.get("path") or "")).exists()]
        allowed_extensions = set(configured_master_audio_extensions())
        masters = [entry for entry in current if entry.get("kind") == "master" and Path(str(entry.get("path") or "")).suffix.lower() in allowed_extensions and Path(str(entry.get("path") or "")).exists()]
        master_results: list[dict[str, Any]] = []
        pair_total = max(1, len(videos) * len(masters))
        pair_index = 0
        old_masters = {item.get("analysis_key"): item for item in old.get("masters") or []}
        for master in masters:
            master_record = file_record(master["path"])
            analysis_project.data["inputs"]["master"] = master_record
            master_key = master.get("analysis_key")
            cached_master = old_masters.get(master_key)
            cached_matches = (cached_master or {}).get("matches") or []
            cached_video_keys = {match.get("video_analysis_key") for match in cached_matches}
            current_video_keys = {video.get("analysis_key") for video in videos}
            if (
                cached_master
                and old.get("coarse_match_version") == COARSE_MATCH_VERSION
                and cached_video_keys == current_video_keys
                and all("coarse_confidence" in match for match in cached_matches)
            ):
                master_results.append(cached_master)
                pair_index += len(videos)
                continue
            # This analysis compares several masters in one pass.  The normal
            # project cache is intentionally single-master, so using it here
            # would reuse the first song's envelope for every subsequent song.
            # Cache each source master separately at the Inbox layer instead.
            master_cache = analysis_project.cache_dir / "inbox-master-envelopes" / f"{master_key}.onset-v4.npy"
            master_cache.parent.mkdir(parents=True, exist_ok=True)
            if len(masters) == 1:
                # Preserve the project-level cache path for single-master
                # callers and existing integrations.
                master_env = load_or_compute_master_envelope(analysis_project)
            elif master_cache.exists() and master_cache.stat().st_mtime >= Path(master["path"]).stat().st_mtime:
                master_env = __import__("numpy").load(master_cache)
            else:
                # Search the entire master at the same onset-envelope rate as
                # the clip.  A short clip may begin hundreds of seconds into
                # the master (e.g. Berlin C0064/C0065), so a 180-second
                # 4-kHz master preview is not a valid comparison window.
                master_env = normalized_onset_envelope(master["path"])
                __import__("numpy").save(master_cache, master_env)
            matches: list[dict[str, Any]] = []
            for video in videos:
                pair_index += 1
                video_record = file_record(video["path"])
                video_record.update({"probe": video.get("probe") or {}, "normalized": video.get("normalized") or {}})
                try:
                    clip_env, _clip_audio_path = load_or_compute_clip_envelope(analysis_project, video_record)
                    # Both envelopes are now full-resolution onset envelopes;
                    # coarse_master_match performs the decimation once, after
                    # the rates are known to be compatible.
                    coarse = coarse_master_match(master_env, clip_env) if clip_env is not None else {"offset_sec": 0.0, "confidence": 0.0, "reasonable_peak": False}
                except Exception:
                    # Keep Inbox analysis resilient for files that were
                    # classified optimistically or are still being copied;
                    # the normal sync path remains the source of truth.
                    coarse = {"offset_sec": 0.0, "confidence": 0.0, "reasonable_peak": True, "unavailable": True}
                if not coarse["reasonable_peak"] or coarse.get("confidence", 0.0) < COARSE_MATCH_THRESHOLD:
                    result = {
                        "offset_sec": coarse["offset_sec"], "confidence": coarse["confidence"],
                        "low_confidence": True, "unstable_sync": False, "no_audio": False,
                        "master_mismatch": True,
                    }
                else:
                    # The Inbox is only a master/video relationship gate.  Do
                    # not run full sync here: that belongs to the selected
                    # wizard inputs and can be expensive/ambiguous.
                    result = {
                        "offset_sec": coarse["offset_sec"],
                        "confidence": coarse["confidence"],
                        "low_confidence": False,
                        "unstable_sync": False,
                        "no_audio": False,
                        "duration_sec": float(video.get("probe", {}).get("duration") or 0.0),
                    }
                start = float(result.get("offset_sec") or 0.0)
                probe = video.get("probe") or {}
                probe_duration = probe.get("duration") or (probe.get("format") or {}).get("duration") or 0.0
                duration = float(result.get("duration_sec") or probe_duration or 0.0)
                master_duration = float(master.get("duration") or 0.0)
                overlap = max(0.0, min(master_duration, start + duration) - max(0.0, start))
                matches.append({
                    "path": video["path"], "confidence": result.get("confidence"), "offset_sec": start,
                    "duration_sec": duration, "low_confidence": bool(result.get("low_confidence")),
                    "unstable_sync": bool(result.get("unstable_sync")), "no_audio": bool(result.get("no_audio")),
                    "master_mismatch": bool(result.get("master_mismatch")) or bool(coarse.get("confidence", 0.0) < COARSE_MATCH_THRESHOLD and not coarse.get("unavailable")), "coarse_confidence": coarse.get("confidence"), "coarse_reasonable_peak": coarse.get("reasonable_peak"),
                    "coarse_match_threshold": COARSE_MATCH_THRESHOLD,
                    "master_overlap_sec": overlap, "master_overlap": overlap > 0.0,
                    "video_analysis_key": video.get("analysis_key"),
                })
                with _ANALYSIS_LOCK:
                    _ANALYSIS_STATUS.update({"progress": 35 + int(pair_index / pair_total * 60), "detail": f"Matching {video['filename']} to {master['filename']}"})
            master_results.append({"path": master["path"], "filename": master["filename"], "duration": master.get("duration"), "analysis_key": master_key, "matches": matches})
        # Keep offline entries in the manifest even though they cannot
        # participate in this run's matching.  Their path/size/mtime keyed
        # analysis is reusable when the external volume is reconnected.
        payload = {"schema": 1, "coarse_match_version": COARSE_MATCH_VERSION, "completed_at": time.time(), "entries": entries, "files": list(entries.values()), "masters": master_results, "master_audio_extensions": sorted(allowed_extensions)}
        _write_inbox_analysis(payload)
        with _ANALYSIS_LOCK:
            _ANALYSIS_STATUS.update({"status": "done", "progress": 100, "detail": f"Inbox ready: {len(videos)} videos, {len(masters)} audio masters"})
    except Exception as exc:  # analysis must never prevent the editor from opening
        LOGGER.exception("Inbox pre-analysis failed")
        with _ANALYSIS_LOCK:
            _ANALYSIS_STATUS.update({"status": "failed", "progress": 0, "detail": str(exc)})


def suggest_songs_json(master_path: str | None, inbox_path: str | None = None) -> list[dict[str, Any]]:
    """Return plausible songs JSON files near the master and in the Inbox."""
    roots: list[Path] = []
    if master_path:
        master = Path(master_path).expanduser()
        if master.exists():
            roots.append(master.resolve().parent)
    config = load_global_config()
    configured_roots = [Path(inbox_path).expanduser()] if inbox_path else [Path(path) for path in config.get("source_folders") or [config["inbox_path"]]]
    roots.extend(root.resolve() for root in configured_roots if root.exists())

    suggestions: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        for candidate in sorted(root.rglob("*.json")):
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen or not is_valid_songs_json(resolved):
                continue
            seen.add(resolved)
            item = _item(resolved, "songs", "valid songs.json candidate", checked=False)
            item["source"] = "master folder" if root == resolved.parent else "inbox"
            suggestions.append(item)
    return suggestions


def scan_input_paths(paths: list[str]) -> list[Path]:
    """Return existing files found by recursively expanding files and folders."""
    files: list[Path] = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(resolved)
    return files


def classify_paths(paths: list[str], inbox_path: str | None = None) -> dict[str, Any]:
    """Classify local files into master, songs, videos, and ignored groups."""
    result: dict[str, Any] = {
        "inbox_path": inbox_path,
        "master": [],
        "songs": [],
        "videos": [],
        "ignored": [],
    }
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            for child in scan_input_paths([str(path)]):
                item = classify_file(child)
                result[item["kind"]].append(item)
                log_classification_verdict(child, item)
            continue
        if not path.exists():
            item = _item(path, "ignored", "missing file", checked=False)
            result["ignored"].append(item)
            log_classification_verdict(path, item)
            continue
        item = classify_file(path)
        result[item["kind"]].append(item)
        log_classification_verdict(path, item)
    _prefer_studio_exports(result)
    return result


_CLASSIFICATION_CACHE: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
_CLASSIFICATION_CACHE_LOCK = threading.Lock()


def classify_file(path: Path) -> dict[str, Any]:
    """Reuse validated metadata while file identity remains unchanged."""
    # Raw lens pairs depend on an additional file arriving; recheck those.
    if is_raw_360_path(path):
        return _classify_file_uncached(path)
    stat = path.stat()
    key = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, id(ffprobe))
    with _CLASSIFICATION_CACHE_LOCK:
        cached = _CLASSIFICATION_CACHE.get(key)
        if cached is not None:
            _CLASSIFICATION_CACHE.move_to_end(key)
            return deepcopy(cached)
    item = _classify_file_uncached(path)
    if item.get("kind") in {"videos", "songs"} or (item.get("kind") == "master" and item.get("duration") is not None):
        with _CLASSIFICATION_CACHE_LOCK:
            _CLASSIFICATION_CACHE[key] = deepcopy(item)
            _CLASSIFICATION_CACHE.move_to_end(key)
            while len(_CLASSIFICATION_CACHE) > 256:
                _CLASSIFICATION_CACHE.popitem(last=False)
    return item


def _classify_file_uncached(path: Path) -> dict[str, Any]:
    """Classify one file with strict extension and media validation."""
    suffix = path.suffix.lower()
    static_video_rejection = static_rejection_reason(path)
    if static_video_rejection == "hidden or system file":
        return _item(path, "ignored", static_video_rejection, checked=False)
    if suffix in AUDIO_EXTENSIONS:
        item = _item(path, "master", "audio file")
        item["duration"] = audio_duration(path)
        return item
    if suffix == ".json":
        if is_valid_songs_json(path):
            return _item(path, "songs", "valid songs.json")
        return _item(path, "ignored", "JSON ignored: missing songs array", checked=False)
    if suffix in VIDEO_EXTENSIONS or static_video_rejection is None:
        try:
            validation = validate_camera_video_metadata(ffprobe(str(path)))
        except Exception:
            note = "unsupported file type" if suffix not in VIDEO_EXTENSIONS else "not a camera video"
            return _item(path, "ignored", note, checked=False)
        item = _item(path, "videos", validation.reason) if validation.valid else _item(path, "ignored", validation.reason, checked=False)
        item["probe"] = validation.summary
        if validation.valid and is_raw_360_path(path):
            item["projection"] = "raw_insv"
            item["raw_360"] = True
            item["note"] = "Raw 360 file — stitched automatically; Studio export has better stabilization"
            item["probe"]["projection"] = "raw_insv"
            item["probe"]["raw_360"] = True
            item["probe"]["input_projection"] = "dfisheye"
            item["probe"]["insv_fov"] = raw_360_model_fov(validation.summary)
            item["info"] = "360 stitched automatically — for best quality, export from Insta360 Studio instead"
            pair = paired_insv_path(path)
            if pair:
                item["paired_path"] = str(pair)
                item["probe"]["paired_path"] = str(pair)
        if validation.summary.get("projection"):
            item["projection"] = validation.summary["projection"]
        return item
    return _item(path, "ignored", static_rejection_reason(path) or "tipo de archivo no compatible", checked=False)


def is_valid_songs_json(path: Path) -> bool:
    """Return True if a JSON file has a top-level songs array."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("songs"), list)


def audio_duration(path: Path) -> float | None:
    """Return an audio file duration when ffprobe can read it."""
    try:
        metadata = ffprobe(str(path))
    except Exception:
        return None
    value = metadata.get("format", {}).get("duration")
    if value is None:
        for stream in metadata.get("streams", []):
            if stream.get("codec_type") == "audio" and stream.get("duration") is not None:
                value = stream["duration"]
                break
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def register_selected_inputs(
    project: Project,
    master_path: str | None = None,
    songs_path: str | None = None,
    video_paths: list[str] | None = None,
    append_videos: bool = True,
) -> dict[str, Any]:
    """Register selected input files, optionally copying them into the project."""
    copy_inputs = bool(project.data["settings"].setdefault("inputs", {}).get("copy_into_project", False))
    # Configured source-folder media is deliberately referenced in place;
    # copying a multi-GB external-drive file defeats the source-folder flow.
    configured_roots = [Path(folder["path"]) for folder in configured_source_folders()]
    earliest_stale_stage: str | None = None
    if master_path:
        master_in_source = any(_path_is_within(Path(master_path), root) for root in configured_roots)
        if master_in_source:
            copy_inputs = False
        registered_master = Path(master_path).expanduser().resolve() if master_in_source else prepare_input_file(project, master_path, "master")
        project.data["inputs"]["master"] = file_record(str(registered_master))
        earliest_stale_stage = _earliest_stage(earliest_stale_stage, "sync")
    if songs_path:
        registered_songs = prepare_input_file(project, songs_path, "songs")
        project.data["inputs"]["songs"] = file_record(str(registered_songs))
        earliest_stale_stage = _earliest_stage(earliest_stale_stage, "cut")
    videos = list(project.data["inputs"].get("videos", [])) if append_videos else []
    for path in expand_video_paths(video_paths or []):
        if any(_path_is_within(Path(path), root) for root in configured_roots):
            copy_inputs = False
        registered_video = prepare_input_file(project, path, "video") if copy_inputs else Path(path).expanduser().resolve()
        record = file_record(str(registered_video))
        record.update(video_classification_metadata(registered_video))
        videos.append(record)
    if video_paths is not None:
        deduped, duplicate_warnings = _dedupe_video_records(videos)
        project.data["inputs"]["videos"] = deduped
        project.data["inputs"]["warnings"] = duplicate_warnings
        earliest_stale_stage = _earliest_stage(earliest_stale_stage, "ingest")
    if earliest_stale_stage:
        project.mark_all_stale_from(earliest_stale_stage)
    project.save()
    return project.snapshot()


def reconcile_registered_inputs(project: Project) -> bool:
    """Reclassify registered project inputs and demote stale invalid video records."""
    changed = False
    kept_videos: list[dict[str, Any]] = []
    for record in project.data.get("inputs", {}).get("videos", []):
        path = Path(record.get("path") or "").expanduser()
        if not path.exists():
            # Keep the record so reopening a project with an unplugged
            # external drive is non-destructive.
            if not record.get("missing"):
                record["missing"] = True
                changed = True
            kept_videos.append(record)
            continue
        if record.get("missing"):
            record.pop("missing", None)
            changed = True
        item = classify_file(path)
        if item["kind"] == "videos":
            for key, value in video_classification_metadata(path).items():
                if record.get(key) != value:
                    record[key] = value
                    changed = True
            if record.get("status") == "not_a_video":
                record.pop("status", None)
                record.pop("not_a_video_reason", None)
                changed = True
            kept_videos.append(record)
            continue
        if record.get("status") != "not_a_video" or record.get("not_a_video_reason") != item["note"]:
            record["status"] = "not_a_video"
            record["not_a_video_reason"] = item["note"]
            record.pop("normalized", None)
            changed = True
        kept_videos.append(record)
    deduped_videos, duplicate_warnings = _dedupe_video_records(kept_videos)
    if deduped_videos != kept_videos or duplicate_warnings != (project.data.get("inputs", {}).get("warnings") or []):
        kept_videos = deduped_videos
        project.data["inputs"]["warnings"] = duplicate_warnings
        changed = True
    if changed:
        project.data["inputs"]["videos"] = kept_videos
        project.mark_all_stale_from("ingest")
        project.save()
    return changed


def expand_video_paths(paths: list[str]) -> list[str]:
    """Expand directories and keep only paths classified as video files."""
    expanded: list[str] = []
    for path in scan_input_paths(paths):
        item = classify_file(path)
        log_classification_verdict(path, item)
        if item["kind"] == "videos":
            expanded.append(str(path.resolve()))
    return expanded


def video_classification_metadata(path: Path) -> dict[str, Any]:
    """Return project-record metadata from the current classifier."""
    item = classify_file(path)
    if item.get("kind") != "videos":
        return {}
    metadata: dict[str, Any] = {}
    for key in ("probe", "projection", "raw_360", "paired_path", "info"):
        if key in item:
            metadata[key] = item[key]
    return metadata


def paired_insv_path(path: Path) -> Path | None:
    """Return the companion lens file for common Insta360 two-file naming."""
    if not is_raw_360_path(path):
        return None
    name = path.name
    candidates = []
    for left, right in (("_00_", "_10_"), ("_10_", "_00_"), ("_00.", "_10."), ("_10.", "_00.")):
        if left in name:
            candidates.append(path.with_name(name.replace(left, right, 1)))
    for candidate in candidates:
        if candidate.exists() and candidate.resolve() != path.resolve():
            return candidate.resolve()
    return None


def _prefer_studio_exports(result: dict[str, Any]) -> None:
    """Demote raw .insv clips when a matching equirect Studio export is present."""
    videos = list(result.get("videos") or [])
    equirect = [item for item in videos if item.get("projection") == "equirect"]
    if not equirect:
        return
    kept = []
    for item in videos:
        if not item.get("raw_360"):
            kept.append(item)
            continue
        raw_duration = float((item.get("probe") or {}).get("duration") or 0.0)
        match = next(
            (
                candidate
                for candidate in equirect
                if raw_duration > 0 and abs(float((candidate.get("probe") or {}).get("duration") or 0.0) - raw_duration) <= 2.0
            ),
            None,
        )
        if match:
            ignored = {**item, "kind": "ignored", "checked": False}
            ignored["note"] = f"Using Studio-exported 360 MP4 instead: {match.get('filename')}"
            result.setdefault("ignored", []).append(ignored)
        else:
            kept.append(item)
    result["videos"] = kept


def log_classification_verdict(path: Path, item: dict[str, Any]) -> None:
    """Write an ingest-facing scan verdict for one candidate file."""
    probe = item.get("probe") or {}
    probe_summary = {
        key: probe.get(key)
        for key in ("valid_video", "video_codec", "audio_codec", "duration", "width", "height", "projection", "fps")
        if probe.get(key) is not None
    }
    payload = {
        "event": "input_scan_verdict",
        "filename": path.name,
        "extension": path.suffix.lower(),
        "path": str(path.expanduser()),
        "kind": item.get("kind"),
        "accepted": item.get("kind") != "ignored",
        "reason": item.get("note"),
        "probe": probe_summary,
    }
    try:
        log_dir = app_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "ingest.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")
    except OSError as exc:
        LOGGER.warning("Could not write input scan verdict for %s: %s", path, exc)


def prepare_input_file(project: Project, path: str, kind: str) -> Path:
    """Return a source path or copied project-local input path."""
    copy_inputs = bool(project.data["settings"].setdefault("inputs", {}).get("copy_into_project", False))
    source = Path(path).expanduser().resolve()
    if not copy_inputs:
        return source
    if kind == "master":
        destination = unique_destination(project.folder / "inputs" / f"master{source.suffix.lower()}", source)
    elif kind == "songs":
        destination = unique_destination(project.folder / "inputs" / "songs.json", source)
    else:
        destination = unique_destination(project.folder / "inputs" / "videos" / source.name, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def save_uploads(project: Project, files: list[Any]) -> dict[str, Any]:
    """Save multipart uploads into project inputs/uploads and classify them."""
    upload_dir = project.folder / "inputs" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for storage in files:
        filename = Path(storage.filename or "upload.bin").name
        destination = unique_destination(upload_dir / filename)
        storage.save(destination)
        saved.append(str(destination.resolve()))
    return classify_paths(saved)


def unique_destination(path: Path, source: Path | None = None) -> Path:
    """Return a non-conflicting destination path."""
    if source and path.exists() and path.resolve() == source.resolve():
        return path
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique destination for {path}")


def _dedupe_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for record in records:
        path = record["path"]
        if path not in seen:
            seen.add(path)
            deduped.append(record)
    return deduped


def _dedupe_video_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Deduplicate Drop box videos by content, not just by filename/path."""
    seen_paths: set[str] = set()
    seen_content: dict[tuple[Any, Any], dict[str, Any]] = {}
    deduped: list[dict[str, Any]] = []
    warnings: list[str] = []
    for record in records:
        path = str(record.get("path") or "")
        if path in seen_paths:
            continue
        seen_paths.add(path)
        identity = content_identity(path)
        key = (identity or {}).get("size"), (identity or {}).get("sample_sha256")
        if identity and key in seen_content:
            original = seen_content[key]
            warnings.append(
                f"Duplicate video ignored: {Path(path).name} has the same content as "
                f"{Path(str(original.get('path') or 'video')).name}; it will not be treated as a second camera."
            )
            continue
        if identity:
            seen_content[key] = record
        deduped.append(record)
    return deduped, warnings


def _earliest_stage(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    return current if STAGE_NAMES.index(current) <= STAGE_NAMES.index(candidate) else candidate


def _item(path: Path, kind: str, note: str, checked: bool = True) -> dict[str, Any]:
    stat = path.stat() if path.exists() else None
    return {
        "kind": kind,
        "path": str(path.resolve()) if path.exists() else str(path.expanduser()),
        "filename": path.name,
        "size": stat.st_size if stat else None,
        "mtime": stat.st_mtime if stat else None,
        "note": note,
        "checked": checked,
    }
