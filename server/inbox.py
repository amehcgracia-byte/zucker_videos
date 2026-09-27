"""Global inbox scanning, input classification, and registration helpers."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from core.ffmpeg import configure_tools, ffprobe
from core.media_validation import VIDEO_EXTENSIONS, is_raw_360_path, raw_360_model_fov, static_rejection_reason, validate_camera_video_metadata
from core.project import STAGE_NAMES, Project, file_record

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif"}
LOGGER = logging.getLogger(__name__)


def app_home() -> Path:
    """Return the global Zucker Videos home directory."""
    return Path.home() / "ZuckerVideos"


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
        config = {"inbox_path": str(app_home() / "Inbox")}
        save_global_config(config)
    config.setdefault("band_name", "")
    config.setdefault("handle", "")
    inbox = Path(config.get("inbox_path") or app_home() / "Inbox").expanduser()
    inbox.mkdir(parents=True, exist_ok=True)
    config["inbox_path"] = str(inbox.resolve())
    tools = configure_tools(config.get("ffmpeg_path"), config.get("ffprobe_path"))
    if config.get("ffmpeg_path") != tools["ffmpeg_path"] or config.get("ffprobe_path") != tools["ffprobe_path"]:
        config.update(tools)
        save_global_config(config)
    return config


def save_global_config(config: dict[str, Any]) -> None:
    """Save global app config."""
    app_home().mkdir(parents=True, exist_ok=True)
    with config_path().open("w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, sort_keys=True)
        fh.write("\n")


def scan_inbox(root: str | None = None) -> dict[str, Any]:
    """Scan and classify files in the configured inbox."""
    inbox = Path(root or load_global_config()["inbox_path"]).expanduser().resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    return classify_paths([str(inbox)], inbox_path=str(inbox))


def suggest_songs_json(master_path: str | None, inbox_path: str | None = None) -> list[dict[str, Any]]:
    """Return plausible songs JSON files near the master and in the Inbox."""
    roots: list[Path] = []
    if master_path:
        master = Path(master_path).expanduser()
        if master.exists():
            roots.append(master.resolve().parent)
    inbox = Path(inbox_path or load_global_config()["inbox_path"]).expanduser()
    if inbox.exists():
        roots.append(inbox.resolve())

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
        if record.get("missing"):
            record["missing"] = False
            changed = True
        item = classify_file(path)
        result[item["kind"]].append(item)
        log_classification_verdict(path, item)
    _prefer_studio_exports(result)
    return result


def classify_file(path: Path) -> dict[str, Any]:
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
    earliest_stale_stage: str | None = None
    if master_path:
        registered_master = prepare_input_file(project, master_path, "master")
        project.data["inputs"]["master"] = file_record(str(registered_master))
        earliest_stale_stage = _earliest_stage(earliest_stale_stage, "sync")
    if songs_path:
        registered_songs = prepare_input_file(project, songs_path, "songs")
        project.data["inputs"]["songs"] = file_record(str(registered_songs))
        earliest_stale_stage = _earliest_stage(earliest_stale_stage, "cut")
    videos = list(project.data["inputs"].get("videos", [])) if append_videos else []
    for path in expand_video_paths(video_paths or []):
        registered_video = prepare_input_file(project, path, "video") if copy_inputs else Path(path).expanduser().resolve()
        record = file_record(str(registered_video))
        record.update(video_classification_metadata(registered_video))
        videos.append(record)
    if video_paths is not None:
        project.data["inputs"]["videos"] = _dedupe_records(videos)
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
            if not record.get("missing", False):
                record["missing"] = True
                changed = True
            kept_videos.append(record)
            continue
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
