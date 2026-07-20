"""Wizard project discovery, matching, and deletion helpers."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from core.project import Project, ProjectError, load_project
from server.inbox import app_home


def projects_root() -> Path:
    """Return the global projects directory."""
    root = app_home() / "Projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_projects() -> list[dict[str, Any]]:
    """Return lightweight metadata for existing wizard projects."""
    projects: list[dict[str, Any]] = []
    for project_json in sorted(projects_root().glob("*.zuckervid/project.json")):
        try:
            project = load_project(str(project_json.parent))
        except ProjectError:
            continue
        projects.append(project_summary(project))
    return sorted(projects, key=lambda item: item.get("modified_at") or "", reverse=True)


def project_summary(project: Project) -> dict[str, Any]:
    """Return UI-facing metadata for one project."""
    stages = project.data.get("stages") or {}
    export_stage = stages.get("export") or {}
    has_export = False
    export_path = None
    outputs = export_stage.get("outputs") or {}
    manifest_path = outputs.get("export_manifest")
    if manifest_path:
        has_export, export_path = _manifest_has_export(Path(manifest_path))
    return {
        "name": project.data.get("name") or project.folder.stem,
        "path": str(project.folder),
        "created_at": project.data.get("created_at"),
        "modified_at": project.data.get("modified_at"),
        "status": project_status(project),
        "size_bytes": directory_size(project.folder),
        "has_export": has_export,
        "export_path": export_path,
    }


def find_project_by_inputs(master: str, songs: str | None, videos: list[str]) -> Project | None:
    """Find an existing project with the same input paths, sizes, and mtimes."""
    wanted = input_signature(master, songs, videos)
    for project_json in sorted(projects_root().glob("*.zuckervid/project.json")):
        try:
            project = load_project(str(project_json.parent))
        except ProjectError:
            continue
        if project_input_signature(project) == wanted:
            return project
    return None


def project_input_signature(project: Project) -> dict[str, Any] | None:
    """Return the registered input signature for a project."""
    inputs = project.data.get("inputs") or {}
    master = inputs.get("master")
    videos = inputs.get("videos") or []
    if not master or not videos:
        return None
    songs = inputs.get("songs")
    return {
        "master": record_signature(master),
        "songs": record_signature(songs) if songs else None,
        "videos": sorted((record_signature(record) for record in videos), key=lambda item: item["path"]),
    }


def input_signature(master: str, songs: str | None, videos: list[str]) -> dict[str, Any] | None:
    """Return the current filesystem signature for requested inputs."""
    if not master or not videos:
        return None
    return {
        "master": path_signature(master),
        "songs": path_signature(songs) if songs else None,
        "videos": sorted((path_signature(path) for path in videos), key=lambda item: item["path"]),
    }


def delete_project_folder(project_path: str, keep_exports: bool = False) -> dict[str, Any]:
    """Delete one project folder, optionally moving exports out first."""
    folder = Path(project_path).expanduser().resolve()
    folder.relative_to(projects_root().resolve())
    project = load_project(str(folder))
    kept_exports: list[str] = []
    if keep_exports and project.exports_dir.exists():
        destination = unique_export_destination(app_home() / "Exports" / project.folder.stem)
        destination.mkdir(parents=True, exist_ok=True)
        for export in project.exports_dir.iterdir():
            if export.is_file():
                target = destination / export.name
                shutil.move(str(export), str(target))
                kept_exports.append(str(target))
    shutil.rmtree(folder)
    return {"deleted": str(folder), "kept_exports": kept_exports}


def project_status(project: Project) -> str:
    """Return a compact status label from stage states."""
    stages = project.data.get("stages") or {}
    if any(stage.get("status") == "running" for stage in stages.values()):
        return "running"
    for name in ("export", "edit", "cut", "sync", "ingest"):
        status = (stages.get(name) or {}).get("status")
        if status == "done":
            return f"{name} done"
        if status in {"failed", "stale", "blocked"}:
            return f"{name} {status}"
    return "new"


def directory_size(path: Path) -> int:
    """Return total bytes under a directory."""
    total = 0
    if not path.exists():
        return 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total


def path_signature(path: str | None) -> dict[str, Any]:
    """Return path, size, and mtime for a current filesystem path."""
    if not path:
        raise ValueError("path is required")
    candidate = Path(path).expanduser().resolve()
    stat = candidate.stat()
    return {"path": str(candidate), "size": stat.st_size, "mtime": stat.st_mtime}


def record_signature(record: dict[str, Any]) -> dict[str, Any]:
    """Return path, size, and mtime from a project input record."""
    return {"path": str(Path(record["path"]).expanduser().resolve()), "size": record.get("size"), "mtime": record.get("mtime")}


def unique_export_destination(path: Path) -> Path:
    """Return a non-conflicting folder path."""
    if not path.exists():
        return path
    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not create export preservation folder")


def _manifest_has_export(path: Path) -> tuple[bool, str | None]:
    try:
        import json

        with path.open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
        export = (manifest.get("exports") or [{}])[0]
        export_path = Path(export.get("path") or "")
        return export_path.exists(), str(export_path) if export_path.exists() else None
    except Exception:
        return False, None
