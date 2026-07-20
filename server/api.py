"""Versioned Flask API routes for Zucker Videos."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from core.engine import PipelineEngine, StageBlockedError, StageNotFoundError
from core.project import Project, ProjectError, create_project, load_project
from core.media_validation import record_media_path
from core.stages.sync import clear_manual_override, generate_preview, generate_thumbnail, set_manual_override
from server.inbox import (
    app_home,
    classify_paths,
    load_global_config,
    reconcile_registered_inputs,
    register_selected_inputs,
    save_global_config,
    save_uploads,
    scan_inbox,
    suggest_songs_json,
    unique_destination,
)
from server.media import send_file_with_range
from server.wizard import WizardRunner, wizard_report, wizard_song_options

LOGGER = logging.getLogger(__name__)
BROWSER_UPLOAD_MAX_BYTES = 512 * 1024 * 1024


@dataclass
class AppState:
    """Mutable process state shared by API routes."""

    engine: PipelineEngine
    project: Project | None = None
    dev: bool = False
    wizard: WizardRunner | None = None


def create_app(project_path: str | None = None, dev: bool = False) -> Flask:
    """Create and configure the Flask application."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    app = Flask(__name__, static_folder=str(root / "web"), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = BROWSER_UPLOAD_MAX_BYTES
    load_global_config()
    state = AppState(engine=PipelineEngine(), dev=dev, wizard=WizardRunner())
    if project_path:
        state.project = load_project(project_path)
        if reconcile_registered_inputs(state.project):
            LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
    app.config["ZUCKER_STATE"] = state

    if dev:
        _enable_cors(app)

    @app.errorhandler(RequestEntityTooLarge)
    def api_upload_too_large(_: RequestEntityTooLarge) -> tuple[Response, int]:
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return error_response(
            "upload_too_large",
            f"Browser uploads are limited to {limit_mb} MB. Put large videos in the Inbox or use the desktop folder picker.",
            413,
        )

    @app.get("/")
    def index() -> Response:
        return send_from_directory(app.static_folder or "", "index.html")

    @app.get("/advanced")
    def advanced() -> Response:
        return send_from_directory(app.static_folder or "", "advanced.html")

    @app.post("/api/v1/project")
    def api_create_project() -> tuple[Response, int] | Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        folder = str(body.get("folder") or "").strip()
        if not name or not folder:
            return error_response("bad_request", "name and folder are required", 400)
        try:
            state.project = create_project(name, folder)
            _remember_project(state.project)
            return jsonify(state.project.snapshot()), 201
        except ProjectError as exc:
            return error_response("project_error", str(exc), 409)

    @app.get("/api/v1/project")
    def api_get_project() -> Response:
        project = _require_project(state)
        if project.refresh_input_records():
            project.save()
        return jsonify(project.snapshot())

    @app.post("/api/v1/project/open")
    def api_open_project() -> Response:
        body = _json_body()
        folder = str(body.get("folder") or "").strip()
        if not folder:
            return error_response("bad_request", "folder is required", 400)
        try:
            state.project = load_project(folder)
            if reconcile_registered_inputs(state.project):
                LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
            _remember_project(state.project)
            return jsonify(state.project.snapshot())
        except ProjectError as exc:
            return error_response("project_error", str(exc), 404)

    @app.post("/api/v1/inputs/videos")
    def api_register_videos() -> Response:
        project = _require_project(state)
        body = _json_body()
        paths = body.get("paths")
        append = bool(body.get("append", False))
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return error_response("bad_request", "paths must be a list of strings", 400)
        try:
            return jsonify(register_selected_inputs(project, video_paths=paths, append_videos=append))
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.post("/api/v1/inputs/master")
    def api_register_master() -> Response:
        project = _require_project(state)
        body = _json_body()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip()
        if not master and not songs:
            return error_response("bad_request", "master or songs is required", 400)
        try:
            return jsonify(register_selected_inputs(project, master_path=master or None, songs_path=songs or None))
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/inbox")
    def api_inbox() -> Response:
        try:
            return jsonify(scan_inbox())
        except OSError as exc:
            return error_response("inbox_error", str(exc), 500)

    @app.post("/api/v1/inbox/register")
    def api_inbox_register() -> Response:
        project = _require_project(state)
        body = _json_body()
        master = body.get("master")
        songs = body.get("songs")
        videos = body.get("videos") or []
        if master is not None and not isinstance(master, str):
            return error_response("bad_request", "master must be a string path", 400)
        if songs is not None and not isinstance(songs, str):
            return error_response("bad_request", "songs must be a string path", 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos):
            return error_response("bad_request", "videos must be a list of string paths", 400)
        if master is None and songs is None and not videos:
            return error_response("bad_request", "select at least one input", 400)
        try:
            return jsonify(register_selected_inputs(project, master, songs, videos, append_videos=True))
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.post("/api/v1/inputs/upload")
    def api_inputs_upload() -> Response:
        project = _require_project(state)
        max_bytes = int(app.config["MAX_CONTENT_LENGTH"])
        if request.content_length and request.content_length > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            return error_response(
                "upload_too_large",
                f"Browser uploads are limited to {limit_mb} MB. Put large videos in the Inbox or use the desktop folder picker.",
                413,
            )
        files = list(request.files.getlist("files"))
        if not files:
            return error_response("bad_request", "multipart field files is required", 400)
        try:
            return jsonify(save_uploads(project, files))
        except OSError as exc:
            return error_response("upload_error", str(exc), 400)

    @app.get("/api/v1/inputs/suggestions/songs")
    def api_songs_suggestions() -> Response:
        project = _require_project(state)
        master = project.data.get("inputs", {}).get("master")
        master_path = master.get("path") if master else None
        return jsonify({"songs": suggest_songs_json(master_path)})

    @app.post("/api/v1/wizard/upload")
    def api_wizard_upload() -> Response:
        max_bytes = int(app.config["MAX_CONTENT_LENGTH"])
        if request.content_length and request.content_length > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            return error_response(
                "upload_too_large",
                f"Browser uploads are limited to {limit_mb} MB. Put large videos in the Inbox or use the desktop folder picker.",
                413,
            )
        files = list(request.files.getlist("files"))
        if not files:
            return error_response("bad_request", "multipart field files is required", 400)
        upload_dir = app_home() / "WizardUploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for storage in files:
            filename = Path(storage.filename or "upload.bin").name
            destination = unique_destination(upload_dir / filename)
            storage.save(destination)
            saved.append(str(destination.resolve()))
        return jsonify(classify_paths(saved))

    @app.post("/api/v1/wizard/songs")
    def api_wizard_songs() -> Response:
        body = _json_body()
        songs_path = body.get("songs")
        if songs_path is not None and not isinstance(songs_path, str):
            return error_response("bad_request", "songs must be a string path", 400)
        return jsonify({"songs": wizard_song_options(songs_path)})

    @app.post("/api/v1/wizard/start")
    def api_wizard_start() -> Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        platform = str(body.get("platform") or "").strip().lower()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip() or None
        videos = body.get("videos") or []
        if platform not in {"youtube", "instagram", "tiktok"}:
            return error_response("bad_request", "platform must be youtube, instagram, or tiktok", 400)
        if not master:
            return error_response("missing_master", "Falta el audio master", 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", "Falta al menos un vídeo", 400)
        try:
            job = state.wizard.start(
                name=name or "Jam",
                platform=platform,
                song_choice=body.get("song_index", body.get("song_choice")),
                master_path=master,
                songs_path=songs,
                video_paths=videos,
            )
            return jsonify(dict(job.__dict__)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)

    @app.post("/api/v1/wizard/prepare")
    def api_wizard_prepare() -> Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip() or None
        videos = body.get("videos") or []
        if not master:
            return error_response("missing_master", "Falta el audio master", 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", "Falta al menos un vídeo", 400)
        try:
            job = state.wizard.prepare(name=name or "Jam", master_path=master, songs_path=songs, video_paths=videos)
            return jsonify(dict(job.__dict__)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)

    @app.get("/api/v1/wizard/status")
    def api_wizard_status() -> Response:
        status = state.wizard.status()
        if status.get("status") == "idle" and state.project:
            return jsonify(_project_wizard_status(state.project))
        return jsonify(status)

    @app.get("/api/v1/wizard/report")
    def api_wizard_report() -> Response:
        return Response(wizard_report(state.wizard.status()), mimetype="text/plain")

    @app.get("/api/v1/wizard/result")
    def api_wizard_result() -> Response:
        status = state.wizard.status()
        if status.get("status") == "idle" and state.project:
            status = _project_wizard_status(state.project)
        result = status.get("result") or {}
        path = result.get("path")
        if not path:
            return error_response("not_found", "No hay vídeo exportado todavía", 404)
        candidate = Path(path)
        if candidate.suffix.lower() != ".mp4" or not candidate.exists():
            return error_response("not_found", f"El resultado no es un MP4 exportado: {candidate}", 404)
        return send_file_with_range(str(candidate))

    @app.post("/api/v1/inputs/classify-paths")
    def api_classify_paths() -> Response:
        body = _json_body()
        paths = body.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return error_response("bad_request", "paths must be a list of strings", 400)
        return jsonify(classify_paths(paths))

    @app.post("/api/v1/settings/inputs")
    def api_input_settings() -> Response:
        project = _require_project(state)
        body = _json_body()
        settings = project.data["settings"].setdefault("inputs", {})
        if "copy_into_project" in body:
            settings["copy_into_project"] = bool(body["copy_into_project"])
        project.save()
        return jsonify(project.snapshot())

    @app.get("/api/v1/app/config")
    def api_app_config() -> Response:
        config = load_global_config()
        config["dev"] = state.dev
        return jsonify(config)

    @app.post("/api/v1/stages/<name>/run")
    def api_run_stage(name: str) -> Response:
        project = _require_project(state)
        try:
            state.engine.submit(project, name)
            return jsonify({"ok": True, "stage": name}), 202
        except StageNotFoundError as exc:
            return error_response("not_found", str(exc), 404)
        except StageBlockedError as exc:
            return error_response("stage_not_ready", str(exc), 409)
        except RuntimeError as exc:
            return error_response("busy", str(exc), 409)

    @app.get("/api/v1/stages/status")
    def api_stage_status() -> Response:
        if state.project and state.project.refresh_input_records():
            state.project.save()
        return jsonify(state.engine.status(state.project))

    @app.get("/api/v1/artifacts/<stage>")
    def api_artifact(stage: str) -> Response:
        project = _require_project(state)
        if stage not in state.engine.stages:
            return error_response("not_found", f"Unknown stage: {stage}", 404)
        outputs = project.data["stages"][stage].get("outputs") or {}
        if not outputs:
            return error_response("not_found", f"No artifacts for stage: {stage}", 404)
        first_path = next(iter(outputs.values()))
        try:
            with Path(first_path).open("r", encoding="utf-8") as fh:
                return jsonify(json.load(fh))
        except FileNotFoundError:
            return error_response("not_found", f"Artifact missing for stage: {stage}", 404)

    @app.post("/api/v1/stages/sync/override")
    def api_sync_override() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or "offset_sec" not in body:
            return error_response("bad_request", "clip_id and offset_sec are required", 400)
        try:
            clip = set_manual_override(project, clip_id, float(body["offset_sec"]))
            return jsonify({"ok": True, "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/stages/sync/override/clear")
    def api_sync_clear_override() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id:
            return error_response("bad_request", "clip_id is required", 400)
        try:
            clip = clear_manual_override(project, clip_id)
            return jsonify({"ok": True, "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)

    @app.get("/api/v1/stages/sync/preview/<clip_id>")
    def api_sync_preview(clip_id: str) -> Response:
        project = _require_project(state)
        try:
            path = generate_preview(project, clip_id)
            relative = path.relative_to(project.cache_dir)
            return jsonify({"media_url": f"/api/v1/media/cache/{relative.as_posix()}"})
        except KeyError as exc:
            return error_response("not_found", str(exc), 404)
        except RuntimeError as exc:
            return error_response("ffmpeg_error", str(exc), 500)

    @app.get("/api/v1/stages/sync/thumbnail/<clip_id>")
    def api_sync_thumbnail(clip_id: str) -> Response:
        project = _require_project(state)
        try:
            return send_file_with_range(str(generate_thumbnail(project, clip_id)))
        except KeyError as exc:
            return error_response("not_found", str(exc), 404)
        except RuntimeError as exc:
            return error_response("ffmpeg_error", str(exc), 500)

    @app.get("/api/v1/media/<path:kind>/<int:index>")
    def api_media(kind: str, index: int) -> Response:
        project = _require_project(state)
        try:
            path = _media_path(project, kind, index)
        except (KeyError, IndexError, ValueError) as exc:
            return error_response("not_found", str(exc), 404)
        return send_file_with_range(path)

    @app.get("/api/v1/media/cache/<path:relative_path>")
    def api_cached_media(relative_path: str) -> Response:
        project = _require_project(state)
        cache_path = (project.cache_dir / relative_path).resolve()
        try:
            cache_path.relative_to(project.cache_dir.resolve())
        except ValueError:
            return error_response("not_found", "Cached media path is outside project cache", 404)
        return send_file_with_range(str(cache_path))

    @app.errorhandler(404)
    def not_found(_: Exception) -> tuple[Response, int]:
        return error_response("not_found", "Not found", 404)

    @app.errorhandler(ProjectError)
    def project_error(exc: ProjectError) -> tuple[Response, int]:
        return error_response("project_error", str(exc), 400)

    @app.errorhandler(500)
    def server_error(exc: Exception) -> tuple[Response, int]:
        LOGGER.exception("Unhandled API error")
        return error_response("internal_error", str(exc), 500)

    return app


def error_response(code: str, message: str, status: int) -> tuple[Response, int]:
    """Return a consistent JSON error envelope."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def _require_project(state: AppState) -> Project:
    if state.project is None:
        raise ProjectError("No project is open")
    return state.project


def _remember_project(project: Project) -> None:
    config = load_global_config()
    config["last_project_path"] = str(project.folder)
    save_global_config(config)


def _project_wizard_status(project: Project) -> dict[str, Any]:
    stages = project.data.get("stages") or {}
    logs_path = str(project.cache_dir / "logs")
    export_result = _export_result(project)
    if export_result:
        return {
            "id": "project",
            "status": "done",
            "progress": 100,
            "message": "Tu vídeo está listo",
            "detail": export_result["filename"],
            "result": export_result,
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    failed = next(((name, stage) for name, stage in stages.items() if stage.get("status") == "failed"), None)
    if failed:
        name, stage = failed
        return {
            "id": "project",
            "status": "failed",
            "progress": _stage_progress(name),
            "message": "No pude terminar el vídeo",
            "detail": name,
            "error": stage.get("error") or "Error",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    running = next(((name, stage) for name, stage in stages.items() if stage.get("status") == "running"), None)
    if running:
        name, _ = running
        return {
            "id": "project",
            "status": "running",
            "progress": _stage_progress(name),
            "message": _friendly_stage_message(name),
            "detail": "Recuperando estado del proyecto...",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    if stages.get("sync", {}).get("status") == "done" and stages.get("export", {}).get("status") != "done":
        return {
            "id": "project",
            "status": "waiting_choice",
            "progress": 95,
            "message": "Listo para montar",
            "detail": "Elige el tipo de edición",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    return {
        "id": "project",
        "status": "idle",
        "progress": 0,
        "message": "Sin trabajo",
        "project_path": str(project.folder),
        "logs_path": logs_path,
    }


def _export_result(project: Project) -> dict[str, Any] | None:
    outputs = project.data.get("stages", {}).get("export", {}).get("outputs") or {}
    manifest_path = outputs.get("export_manifest")
    if not manifest_path:
        return None
    try:
        with Path(manifest_path).open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    exports = manifest.get("exports") or []
    if not exports:
        return None
    export = exports[0]
    path = Path(export.get("path") or "")
    if not path.exists():
        return None
    return {
        "project_path": str(project.folder),
        "filename": path.name,
        "path": str(path),
        "media_url": "/api/v1/wizard/result",
        "platform": export.get("platform"),
        "logs_path": str(project.cache_dir / "logs"),
    }


def _stage_progress(stage_name: str) -> int:
    return {"ingest": 8, "sync": 35, "cut": 56, "edit": 64, "export": 76}.get(stage_name, 0)


def _friendly_stage_message(stage_name: str) -> str:
    return {
        "ingest": "Escuchando tus vídeos...",
        "sync": "Sincronizando con el audio...",
        "cut": "Cortando la canción...",
        "export": "Exportando el vídeo...",
    }.get(stage_name, "Trabajando...")


def _media_path(project: Project, kind: str, index: int) -> str:
    inputs = project.data["inputs"]
    if kind == "videos":
        return record_media_path(inputs["videos"][index])
    if kind == "master":
        if index != 0 or not inputs.get("master"):
            raise ValueError("Master media is not registered")
        return inputs["master"]["path"]
    raise ValueError(f"Unknown media kind: {kind}")


def _enable_cors(app: Flask) -> None:
    @app.after_request
    def add_cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response
