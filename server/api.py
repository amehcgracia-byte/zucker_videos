"""Versioned Flask API routes for Zucker Videos."""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from core.engine import PipelineEngine, StageBlockedError, StageNotFoundError
from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.messages import t
from core.project import Project, ProjectError, create_project, load_project
from core.media_validation import record_media_path
from core.normalization import cache_status, cleanup_unreferenced_cache, migrate_project_normalization_cache
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
from server.projects import delete_project_folder, find_project_by_inputs, input_signature, list_projects, project_input_signature
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
        reconciled = reconcile_registered_inputs(state.project)
        migrated = migrate_project_normalization_cache(state.project)
        if reconciled or migrated:
            LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
    app.config["ZUCKER_STATE"] = state

    if dev:
        _enable_cors(app)

    @app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        if request.path == "/" or request.path.endswith((".html", ".css", ".js", ".png")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

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
            reconciled = reconcile_registered_inputs(state.project)
            migrated = migrate_project_normalization_cache(state.project)
            if reconciled or migrated:
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
        audio_trim = _audio_trim_from_body(body)
        spherical_landmarks = _spherical_landmarks_from_body(body)
        camera_role_weights = _camera_role_weights_from_body(body)
        fixed_rear_motion = _fixed_rear_motion_from_body(body)
        if platform not in {"youtube", "instagram", "tiktok", "360"}:
            return error_response("bad_request", "platform must be youtube, instagram, tiktok, or 360", 400)
        if not master:
            return error_response("missing_master", t("missing_master"), 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", t("missing_video"), 400)
        try:
            matching_project = state.project if _can_reuse_prepared_project(state.project, master, songs, videos) else find_project_by_inputs(master, songs, videos)
            if matching_project and _project_can_skip_prepare(matching_project):
                state.project = matching_project
                state.wizard.adopt_prepared_project(state.project)
            elif matching_project:
                state.project = matching_project
                state.wizard.prepare_existing(matching_project)
            job = state.wizard.start(
                name=name or "Jam",
                platform=platform,
                song_choice=body.get("song_index", body.get("song_choice")),
                audio_trim=audio_trim,
                spherical_landmarks=spherical_landmarks,
                camera_role_weights=camera_role_weights,
                fixed_rear_motion=fixed_rear_motion,
                master_path=master,
                songs_path=songs,
                video_paths=videos,
            )
            return jsonify(dict(job.__dict__)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/wizard/master-preview")
    def api_wizard_master_preview() -> Response:
        status = state.wizard.status()
        project_path = status.get("project_path") or ((status.get("result") or {}).get("project_path"))
        project = state.project
        if not project and project_path:
            project = load_project(project_path)
        if not project or not project.data.get("inputs", {}).get("master"):
            return error_response("not_found", "Master media is not registered", 404)
        return send_file_with_range(project.data["inputs"]["master"]["path"])

    @app.get("/api/v1/wizard/spherical-preview")
    def api_wizard_spherical_preview() -> Response:
        source = str(request.args.get("source") or "").strip()
        yaw = _optional_degrees(request.args.get("yaw"))
        pitch = _optional_float_setting(request.args.get("pitch"), 0.0)
        fov = max(65.0, min(150.0, _optional_float_setting(request.args.get("fov"), 95.0)))
        if not source or yaw is None:
            return error_response("bad_request", "source and yaw are required", 400)
        try:
            return send_file_with_range(str(_spherical_preview_frame(state.project, source, yaw, pitch, fov)))
        except (OSError, FFmpegError, ValueError) as exc:
            return error_response("ffmpeg_error", str(exc), 500)

    @app.post("/api/v1/wizard/prepare")
    def api_wizard_prepare() -> Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip() or None
        videos = body.get("videos") or []
        if not master:
            return error_response("missing_master", t("missing_master"), 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", t("missing_video"), 400)
        try:
            matching_project = state.project if _can_reuse_prepared_project(state.project, master, songs, videos) else find_project_by_inputs(master, songs, videos)
            if matching_project and _project_can_skip_prepare(matching_project):
                state.project = matching_project
                job = state.wizard.adopt_prepared_project(state.project)
                LOGGER.info("Reused prepared wizard project %s instead of creating a new project", state.project.folder)
                return jsonify(dict(job.__dict__)), 202
            if matching_project:
                state.project = matching_project
                job = state.wizard.prepare_existing(matching_project)
                LOGGER.info("Reused existing wizard project %s instead of creating a new project", matching_project.folder)
                return jsonify(dict(job.__dict__)), 202
            job = state.wizard.prepare(name=name or "Jam", master_path=master, songs_path=songs, video_paths=videos)
            return jsonify(dict(job.__dict__)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/wizard/status")
    def api_wizard_status() -> Response:
        status = state.wizard.status()
        if status.get("status") == "idle" and state.project:
            return jsonify(_project_wizard_status(state.project))
        return jsonify(status)

    @app.get("/api/v1/wizard/projects")
    def api_wizard_projects() -> Response:
        return jsonify({"projects": list_projects()})

    @app.post("/api/v1/wizard/projects/open")
    def api_wizard_project_open() -> Response:
        body = _json_body()
        folder = str(body.get("path") or "").strip()
        if not folder:
            return error_response("bad_request", "path is required", 400)
        try:
            state.project = load_project(folder)
            reconciled = reconcile_registered_inputs(state.project)
            migrated = migrate_project_normalization_cache(state.project)
            if reconciled or migrated:
                LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
            _remember_project(state.project)
            status = _project_wizard_status(state.project)
            if status.get("status") == "waiting_choice":
                state.wizard.adopt_prepared_project(state.project)
            return jsonify(status)
        except ProjectError as exc:
            return error_response("project_error", str(exc), 404)

    @app.post("/api/v1/wizard/projects/delete")
    def api_wizard_project_delete() -> Response:
        body = _json_body()
        folder = str(body.get("path") or "").strip()
        keep_exports = bool(body.get("keep_exports", False))
        if not folder:
            return error_response("bad_request", "path is required", 400)
        try:
            result = delete_project_folder(folder, keep_exports=keep_exports)
            if state.project and Path(result["deleted"]).resolve() == state.project.folder.resolve():
                state.project = None
                config = load_global_config()
                if config.get("last_project_path") == result["deleted"]:
                    config.pop("last_project_path", None)
                    save_global_config(config)
            return jsonify(result)
        except (ProjectError, ValueError, OSError) as exc:
            return error_response("project_error", str(exc), 400)

    @app.post("/api/v1/wizard/projects/new")
    def api_wizard_project_new() -> Response:
        state.project = None
        state.wizard.reset()
        config = load_global_config()
        if config.pop("last_project_path", None) is not None:
            save_global_config(config)
        return jsonify({"ok": True})

    @app.post("/api/v1/wizard/reset")
    def api_wizard_reset() -> Response:
        state.wizard.reset()
        return jsonify({"ok": True})

    @app.get("/api/v1/wizard/report")
    def api_wizard_report() -> Response:
        return Response(wizard_report(state.wizard.status()), mimetype="text/plain")

    @app.post("/api/v1/wizard/rescue")
    def api_wizard_rescue() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or "offset_sec" not in body:
            return error_response("bad_request", "clip_id and offset_sec are required", 400)
        try:
            job = state.wizard.rescue(project, clip_id=clip_id, offset_sec=float(body["offset_sec"]))
            return jsonify(dict(job.__dict__)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/wizard/frontend-log")
    def api_wizard_frontend_log() -> Response:
        body = _json_body()
        log_dir = app_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        message = str(body.get("message") or "frontend event")
        stack = str(body.get("stack") or "")
        url = str(body.get("url") or "")
        with (log_dir / "frontend.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{message}\n")
            if url:
                fh.write(f"url: {url}\n")
            if stack:
                fh.write(f"{stack}\n")
        return jsonify({"ok": True})

    @app.get("/api/v1/wizard/result")
    def api_wizard_result() -> Response:
        status = state.wizard.status()
        if status.get("status") == "idle" and state.project:
            status = _project_wizard_status(state.project)
        result = status.get("result") or {}
        path = result.get("path")
        if not path:
            return error_response("not_found", "No exported video yet", 404)
        candidate = Path(path)
        if candidate.suffix.lower() != ".mp4" or not candidate.exists():
            return error_response("not_found", f"The result is not an exported MP4: {candidate}", 404)
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

    @app.post("/api/v1/settings/spherical-landmarks")
    def api_spherical_landmarks() -> Response:
        project = _require_project(state)
        body = _json_body()
        landmarks = _sanitize_spherical_landmarks(body.get("spherical_landmarks", body))
        project.data.setdefault("settings", {})["spherical_landmarks"] = landmarks
        project.mark_all_stale_from("edit")
        project.save()
        config = load_global_config()
        config["spherical_landmarks"] = landmarks
        save_global_config(config)
        return jsonify({"spherical_landmarks": landmarks})

    @app.get("/api/v1/app/config")
    def api_app_config() -> Response:
        config = load_global_config()
        config["dev"] = state.dev
        config["desktop"] = not state.dev
        config.setdefault("camera_role_weights", {"360": 50.0, "handheld": 30.0, "fixed_rear": 20.0})
        config.setdefault("fixed_rear_motion", True)
        config.setdefault("audio_trim_by_master", {})
        return jsonify(config)

    @app.get("/api/v1/cache/status")
    def api_cache_status() -> Response:
        return jsonify(cache_status())

    @app.post("/api/v1/cache/free")
    def api_cache_free() -> Response:
        return jsonify(cleanup_unreferenced_cache())

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
            "message": t("done"),
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
            "message": t("cannot_finish"),
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
            "detail": "Recovering project state...",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    if stages.get("sync", {}).get("status") == "done" and stages.get("export", {}).get("status") != "done":
        return {
            "id": "project",
            "status": "waiting_choice",
            "progress": 95,
            "message": t("ready_to_edit"),
            "detail": t("choose_edit_type"),
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    return {
        "id": "project",
        "status": "idle",
        "progress": 0,
        "message": "Idle",
        "project_path": str(project.folder),
        "logs_path": logs_path,
    }


def _can_reuse_prepared_project(project: Project | None, master: str, songs: str | None, videos: list[str]) -> bool:
    """Return True when a loaded project already has these inputs prepared through sync."""
    if not project:
        return False
    if project.refresh_input_records():
        project.save()
    stages = project.data.get("stages") or {}
    if stages.get("sync", {}).get("status") != "done":
        return False
    if not _project_has_sync_candidates(project):
        return False
    inputs = project.data.get("inputs") or {}
    if _resolved(inputs.get("master", {}).get("path")) != _resolved(master):
        return False
    project_songs = inputs.get("songs")
    project_songs_path = project_songs.get("path") if project_songs else None
    if songs and _resolved(project_songs_path) != _resolved(songs):
        return False
    return project_input_signature(project) == input_signature(master, songs, videos)


def _project_can_skip_prepare(project: Project) -> bool:
    """Return True when an existing project can resume at edit-choice time."""
    if project.refresh_input_records():
        project.save()
    stages = project.data.get("stages") or {}
    return stages.get("sync", {}).get("status") == "done" and _project_has_sync_candidates(project)


def _resolved(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(path).expanduser().resolve())


def _project_has_sync_candidates(project: Project) -> bool:
    outputs = project.data.get("stages", {}).get("sync", {}).get("outputs") or {}
    sync_map_path = outputs.get("sync_map") or str(project.artifacts_dir / "sync_map.json")
    try:
        with Path(sync_map_path).open("r", encoding="utf-8") as fh:
            sync_map = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    for clip in (sync_map.get("clips") or {}).values():
        if clip.get("error") or clip.get("no_audio"):
            continue
        return True
    return False


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
        "cut_count": export.get("cut_count"),
        "camera_usage": export.get("camera_usage"),
        "spherical_shot_usage": export.get("spherical_shot_usage") or manifest.get("spherical_shot_usage") or {},
        "warnings": export.get("warnings") or manifest.get("warnings") or [],
        "excluded_clips": export.get("excluded_clips") or [],
        "clip_fates": export.get("clip_fates") or [],
    }


def _stage_progress(stage_name: str) -> int:
    return {"ingest": 8, "sync": 35, "cut": 56, "edit": 64, "export": 76}.get(stage_name, 0)


def _friendly_stage_message(stage_name: str) -> str:
    return {
        "ingest": t("listening"),
        "sync": t("syncing_audio"),
        "cut": t("cutting_song"),
        "edit": t("building_edit"),
        "export": t("exporting_video"),
    }.get(stage_name, t("working"))


def _media_path(project: Project, kind: str, index: int) -> str:
    inputs = project.data["inputs"]
    if kind == "videos":
        return record_media_path(inputs["videos"][index])
    if kind == "master":
        if index != 0 or not inputs.get("master"):
            raise ValueError("Master media is not registered")
        return inputs["master"]["path"]
    raise ValueError(f"Unknown media kind: {kind}")


def _audio_trim_from_body(body: dict[str, Any]) -> dict[str, float] | None:
    start = body.get("trim_start_sec")
    end = body.get("trim_end_sec")
    start_value = _coerce_float(start)
    end_value = _coerce_float(end)
    trim: dict[str, float] = {}
    if start_value is not None:
        trim["start_sec"] = max(0.0, start_value)
    if end_value is not None:
        trim["end_sec"] = max(0.0, end_value)
    if trim and trim.get("end_sec", 1.0) <= trim.get("start_sec", 0.0):
        return None
    return trim or None


def _spherical_landmarks_from_body(body: dict[str, Any]) -> dict[str, dict[str, float]] | None:
    if "spherical_landmarks" not in body:
        return None
    return _sanitize_spherical_landmarks(body.get("spherical_landmarks") or {})


def _camera_role_weights_from_body(body: dict[str, Any]) -> dict[str, float] | None:
    if "camera_role_weights" not in body:
        return None
    return _sanitize_camera_role_weights(body.get("camera_role_weights") or {})


def _fixed_rear_motion_from_body(body: dict[str, Any]) -> bool | None:
    if "fixed_rear_motion" not in body:
        return None
    return bool(body.get("fixed_rear_motion"))


def _sanitize_camera_role_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    weights: dict[str, float] = {}
    for key in ("360", "handheld", "fixed_rear"):
        if key not in raw:
            continue
        weights[key] = max(0.0, _optional_float_setting(raw.get(key), 0.0))
    return weights


def _sanitize_spherical_landmarks(raw: Any) -> dict[str, dict[str, float]]:
    if not isinstance(raw, dict):
        return {}
    defaults = {
        "full_stage": {"legacy": "full_stage_yaw", "fov": 120.0},
        "singer": {"legacy": "singer_yaw", "fov": 95.0},
        "drummer": {"legacy": "drummer_yaw", "fov": 95.0},
        "left": {"legacy": "left_yaw", "fov": 95.0},
        "right": {"legacy": "right_yaw", "fov": 95.0},
        "audience": {"legacy": "audience_yaw", "fov": 95.0},
        "audience_stage_wide": {"legacy": "audience_stage_wide_yaw", "fov": 125.0},
        "planet": {"legacy": "planet_yaw", "fov": 150.0},
    }
    landmarks: dict[str, dict[str, float]] = {}
    for key, meta in defaults.items():
        source = raw.get(key)
        if source is None and meta["legacy"] in raw:
            source = {"yaw": raw.get(meta["legacy"])}
        if not isinstance(source, dict):
            continue
        yaw = _optional_degrees(source.get("yaw"))
        if yaw is None:
            continue
        pitch = _optional_float_setting(source.get("pitch"), 0.0)
        fov = max(1.0, min(190.0, _optional_float_setting(source.get("fov"), float(meta["fov"]))))
        weight = max(0.0, _optional_float_setting(source.get("weight"), 1.0))
        landmarks[key] = {"yaw": yaw, "pitch": pitch, "fov": fov, "weight": weight}
    return landmarks


def _optional_degrees(value: Any) -> float | None:
    number = _coerce_float(value)
    return None if number is None else round(number % 360.0, 3)


def _optional_float_setting(value: Any, fallback: float) -> float:
    number = _coerce_float(value)
    return fallback if number is None else round(number, 3)


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _spherical_preview_frame(project: Project | None, source: str, yaw: float, pitch: float, fov: float) -> Path:
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise ValueError("360 source does not exist")
    cache_root = (project.cache_dir if project else app_home() / "cache") / "spherical_previews"
    cache_root.mkdir(parents=True, exist_ok=True)
    stat = source_path.stat()
    key = sha256(json.dumps({"path": str(source_path), "size": stat.st_size, "mtime": stat.st_mtime, "yaw": round(yaw, 3), "pitch": round(pitch, 3), "fov": round(fov, 3)}, sort_keys=True).encode()).hexdigest()[:24]
    output = cache_root / f"{key}.jpg"
    if output.exists():
        return output
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    duration = _preview_source_duration(source_path)
    timestamp = max(0.0, min(duration * 0.35, max(0.0, duration - 0.1)))
    tmp = output.with_suffix(".tmp.jpg")
    filtergraph = (
        f"v360=input=equirect:output=flat:yaw={_signed_degrees(yaw):.3f}:pitch={pitch:.3f}:v_fov={fov:.3f}:"
        "w=480:h=270:interp=lanczos,format=yuvj420p"
    )
    command = [
        str(ffmpeg),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(source_path),
        "-frames:v",
        "1",
        "-vf",
        filtergraph,
        "-q:v",
        "3",
        str(tmp),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if tmp.exists():
            tmp.unlink()
        raise FFmpegError(result.stderr.strip() or "Could not render 360 preview")
    os.replace(tmp, output)
    return output


def _preview_source_duration(path: Path) -> float:
    try:
        metadata = ffprobe(str(path))
        value = (metadata.get("format") or {}).get("duration")
        return max(0.1, float(value))
    except Exception:
        return 1.0


def _signed_degrees(value: float) -> float:
    value = float(value) % 360.0
    return value - 360.0 if value > 180.0 else value


def _enable_cors(app: Flask) -> None:
    @app.after_request
    def add_cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response
