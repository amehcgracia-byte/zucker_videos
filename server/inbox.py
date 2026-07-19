"""Global inbox scanning, input classification, and registration helpers."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from core.ffmpeg import configure_tools, ffprobe
from core.media_validation import VIDEO_EXTENSIONS, static_rejection_reason, validate_camera_video_metadata
from core.project import STAGE_NAMES, Project, file_record

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".aiff", ".aif"}


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
            continue
        if not path.exists():
            result["ignored"].append(_item(path, "ignored", "missing file", checked=False))
            continue
        item = classify_file(path)
        result[item["kind"]].append(item)
    return result


def classify_file(path: Path) -> dict[str, Any]:
    """Classify one file with strict extension and media validation."""
    suffix = path.suffix.lower()
    static_video_rejection = static_rejection_reason(path)
    if static_video_rejection == "archivo oculto o del sistema":
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
            note = "tipo de archivo no compatible" if suffix not in VIDEO_EXTENSIONS else "no es un vídeo de cámara"
            return _item(path, "ignored", note, checked=False)
        item = _item(path, "videos", validation.reason) if validation.valid else _item(path, "ignored", validation.reason, checked=False)
        item["probe"] = validation.summary
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
        videos.append(file_record(str(registered_video)))
    if video_paths is not None:
        project.data["inputs"]["videos"] = _dedupe_records(videos)
        earliest_stale_stage = _earliest_stage(earliest_stale_stage, "ingest")
    if earliest_stale_stage:
        project.mark_all_stale_from(earliest_stale_stage)
    project.save()
    return project.snapshot()


def expand_video_paths(paths: list[str]) -> list[str]:
    """Expand directories and keep only paths classified as video files."""
    expanded: list[str] = []
    for path in scan_input_paths(paths):
        if classify_file(path)["kind"] == "videos":
            expanded.append(str(path.resolve()))
    return expanded


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
