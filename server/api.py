"""Versioned Flask API routes for Zucker Videos."""

from __future__ import annotations

import json
import hashlib
import logging
import math
import sys
import threading
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge

from core.engine import PipelineEngine, StageBlockedError, StageNotFoundError
from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.messages import t
from core.project import Project, ProjectError, create_project, load_project
from core.spherical_view import MAX_SPHERICAL_FOV
from core.media_validation import record_media_path
from core.normalization import cache_status, cleanup_unreferenced_cache, global_cache_root, migrate_project_normalization_cache
from core.stages.sync import clear_manual_override, cleanup_closed_sync_diagnostics, generate_preview, generate_thumbnail, invalidate_stale_sync_artifact, set_manual_anchor, set_manual_override, set_manual_override_ranges
from core.shot_review import mark_review_render_failed, replace_slots, review_items
from core.backstage_feedback import record_feedback
from core.stages.backstage import update_backstage_cue_text
from server.inbox import (
    app_home,
    classify_paths,
    configured_source_folders,
    load_global_config,
    normalize_master_audio_extensions,
    normalize_source_folders,
    reconcile_registered_inputs,
    register_selected_inputs,
    save_global_config,
    save_uploads,
    scan_inbox,
    start_inbox_analysis,
    suggest_songs_json,
    unique_destination,
)
from server.media import send_file_with_range
from server.projects import delete_project_folder, find_project_by_inputs, input_signature, list_projects, project_input_signature
from server.wizard import WizardRunner, merge_spherical_landmarks, serialize_wizard_job, wizard_report, wizard_song_options
from captions.burn import burn as burn_captions
from captions.align import align_known_lyrics
from captions.model import Cue, CueTrack, Word
from captions.render import render_ass
from captions.sources import from_lrc, from_lyrics, from_srt, to_srt
from captions.styles import CAPTIONS_VERSION, get_style, list_styles

LOGGER = logging.getLogger(__name__)


def _expand_caption_animations(track: CueTrack) -> CueTrack:
    """Encode per-cue slide/scale motion as short ASS cue segments.

    The captions package remains deliberately pure; this adapter translates
    UI-only animation overrides into ordinary position/size overrides before
    the package's single-pass burn is invoked.
    """
    expanded: list[Cue] = []
    for cue in track.cues:
        override = dict(cue.style_override or {})
        enter = str(override.pop("animation_in", "none") or "none")
        exit_ = str(override.pop("animation_out", "none") or "none")
        base_vertical = float(override.get("vertical", 68))
        base_size = float(override.get("size", 54))
        cuts = {float(cue.start), float(cue.end)}
        if enter in {"slide", "scale"}:
            cuts.update(float(cue.start) + step for step in (0.08, 0.16, 0.24) if float(cue.start) + step < float(cue.end))
        if exit_ in {"slide", "scale"}:
            cuts.update(float(cue.end) - step for step in (0.24, 0.16, 0.08) if float(cue.end) - step > float(cue.start))
        points = sorted(cuts)
        for index, (start, end) in enumerate(zip(points, points[1:])):
            local = dict(override)
            progress_in = min(1.0, max(0.0, (start - float(cue.start)) / 0.24))
            progress_out = min(1.0, max(0.0, (float(cue.end) - end) / 0.24))
            if enter == "slide" and start < float(cue.start) + 0.24:
                local["vertical"] = 105 - (105 - base_vertical) * progress_in
            elif exit_ == "slide" and end > float(cue.end) - 0.24:
                local["vertical"] = 105 - (105 - base_vertical) * progress_out
            if enter == "scale" and start < float(cue.start) + 0.24:
                local["size"] = base_size * (0.72 + 0.28 * progress_in)
            elif exit_ == "scale" and end > float(cue.end) - 0.24:
                local["size"] = base_size * (0.72 + 0.28 * progress_out)
            expanded.append(Cue(cue.lines, start, end, cue.words, local))
    return CueTrack(tuple(expanded), track.lang)


def _invalidate_stale_sync_on_open(project: Project) -> None:
    if invalidate_stale_sync_artifact(project):
        LOGGER.warning("Invalidated stale sync_map.json for %s; sync will be recomputed", project.folder)
# This limit only matters for a plain browser tab (--dev mode), which has no
# choice but to upload file bytes over HTTP. The packaged desktop app
# references files in place by path (see handleDrop's file.path branch in
# web/app.js) and never needs to buffer a whole video through this server, so
# it gets no cap at all -- real camera/360 footage routinely runs many GB,
# and this used to block it even in the desktop app whenever the drag-drop
# path-detection fell through to the upload fallback.
BROWSER_UPLOAD_MAX_BYTES = 512 * 1024 * 1024
DESKTOP_UPLOAD_MAX_BYTES = None


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
    app.config["MAX_CONTENT_LENGTH"] = BROWSER_UPLOAD_MAX_BYTES if dev else DESKTOP_UPLOAD_MAX_BYTES
    load_global_config()
    state = AppState(engine=PipelineEngine(), dev=dev, wizard=WizardRunner())
    if project_path:
        state.project = load_project(project_path)
        _invalidate_stale_sync_on_open(state.project)
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
        limit_mb = int(app.config["MAX_CONTENT_LENGTH"] or 0) // (1024 * 1024)
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
            _invalidate_stale_sync_on_open(state.project)
            reconciled = reconcile_registered_inputs(state.project)
            migrated = migrate_project_normalization_cache(state.project)
            if reconciled or migrated:
                LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
            _remember_project(state.project)
            return jsonify(state.project.snapshot())
        except ProjectError as exc:
            return error_response(
                "project_unrecoverable",
                "Este proyecto no se puede recuperar. Los vídeos del Drop Here siguen disponibles; crea un proyecto nuevo.",
                409,
            )

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

    @app.post("/api/v1/inbox/analysis/start")
    def api_inbox_analysis_start() -> Response:
        try:
            body = request.get_json(silent=True) or {}
            folder = body.get("folder")
            return jsonify(start_inbox_analysis(folder if isinstance(folder, str) and folder.strip() else None))
        except OSError as exc:
            return error_response("inbox_analysis_error", str(exc), 500)

    @app.get("/api/v1/settings/source-folders")
    def api_source_folders() -> Response:
        return jsonify({"source_folders": configured_source_folders()})

    @app.post("/api/v1/settings/source-folders")
    def api_save_source_folders() -> Response:
        body = _json_body()
        folders = body.get("source_folders", body.get("folders"))
        if not isinstance(folders, list) or not all(isinstance(folder, str) for folder in folders):
            return error_response("bad_request", "source_folders must be a list of absolute paths", 400)
        normalized = normalize_source_folders(folders)
        if not normalized:
            return error_response("bad_request", "Add at least one absolute source folder", 400)
        config = load_global_config()
        config["source_folders"] = normalized
        save_global_config(config)
        return jsonify({"source_folders": configured_source_folders()})

    @app.post("/api/v1/settings/master-audio-filter")
    def api_master_audio_filter() -> Response:
        body = _json_body()
        extensions = body.get("extensions")
        if not isinstance(extensions, list) or not all(isinstance(value, str) for value in extensions):
            return error_response("bad_request", "extensions must be a list", 400)
        normalized = normalize_master_audio_extensions(extensions)
        if not normalized:
            return error_response("bad_request", "Choose at least one audio master format", 400)
        config = load_global_config()
        config["master_audio_extensions"] = normalized
        save_global_config(config)
        return jsonify({"master_audio_extensions": normalized})

    @app.post("/api/v1/settings/personal-logo")
    def api_personal_logo() -> Response:
        config = load_global_config()
        branding_dir = app_home() / "Branding"
        branding_dir.mkdir(parents=True, exist_ok=True)
        source_path = ""
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            suffix = Path(uploaded.filename).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                return error_response("bad_request", "Logo must be PNG, JPEG, or WebP", 400)
            destination = branding_dir / f"personal_logo{suffix}"
            uploaded.save(destination)
            source_path = str(destination)
        else:
            body = _json_body()
            raw_path = str(body.get("path") or "").strip()
            candidate = Path(raw_path).expanduser().resolve()
            if not raw_path or not candidate.is_file() or candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return error_response("bad_request", "Choose a valid PNG, JPEG, or WebP logo", 400)
            destination = branding_dir / f"personal_logo{candidate.suffix.lower()}"
            shutil.copy2(candidate, destination)
            source_path = str(destination)
        config["personal_logo_path"] = source_path
        save_global_config(config)
        return jsonify({"personal_logo_path": source_path})

    @app.get("/api/v1/wizard/logo")
    def api_wizard_logo() -> Response:
        project = _require_project(state)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        custom = Path(str(wizard.get("reel_logo_path") or "")).expanduser()
        config = load_global_config()
        default = Path(str(config.get("personal_logo_path") or "")).expanduser()
        mode = str(wizard.get("reel_logo_mode") or ("custom" if custom.is_file() else "none"))
        if mode not in {"custom", "default", "none"}:
            mode = "none"
        return jsonify({
            "mode": mode,
            "custom": {"path": str(custom), "url": "/api/v1/wizard/logo/project"} if custom.is_file() else None,
            "default": {"path": str(default), "url": "/api/v1/wizard/logo/default"} if default.is_file() else None,
        })

    @app.post("/api/v1/wizard/logo")
    def api_wizard_logo_upload() -> Response:
        project = _require_project(state)
        uploaded = request.files.get("file")
        raw_path = str(request.form.get("path") or "").strip()
        source = Path(raw_path).expanduser().resolve() if raw_path else None
        suffix = Path(uploaded.filename).suffix.lower() if uploaded and uploaded.filename else (source.suffix.lower() if source else "")
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            return error_response("bad_request", "Logo must be PNG, JPEG, or WebP", 400)
        destination = project.folder / f"overlay_logo{suffix}"
        for old in project.folder.glob("overlay_logo.*"):
            old.unlink(missing_ok=True)
        if uploaded and uploaded.filename:
            uploaded.save(destination)
        elif source and source.is_file():
            shutil.copy2(source, destination)
        else:
            return error_response("bad_request", "Choose a valid logo file", 400)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        wizard["reel_logo_path"] = str(destination.resolve())
        wizard["reel_logo_mode"] = "custom"
        project.save()
        return jsonify({"path": str(destination.resolve()), "url": "/api/v1/wizard/logo/project", "name": destination.name})

    @app.delete("/api/v1/wizard/logo")
    def api_wizard_logo_remove() -> Response:
        project = _require_project(state)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        for old in project.folder.glob("overlay_logo.*"):
            old.unlink(missing_ok=True)
        wizard["reel_logo_path"] = ""
        wizard["reel_logo_mode"] = "none"
        project.save()
        return jsonify({"ok": True})

    @app.get("/api/v1/wizard/logo/project")
    def api_wizard_logo_project_file() -> Response:
        project = _require_project(state)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        path = Path(str(wizard.get("reel_logo_path") or ""))
        if not path.is_file() or path.parent.resolve() != project.folder.resolve():
            return error_response("not_found", "No project logo", 404)
        return send_file(str(path))

    @app.get("/api/v1/wizard/logo/default")
    def api_wizard_logo_default_file() -> Response:
        path = Path(str(load_global_config().get("personal_logo_path") or ""))
        if not path.is_file():
            return error_response("not_found", "No default brand logo", 404)
        return send_file(str(path))

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
        max_bytes = app.config["MAX_CONTENT_LENGTH"]
        if max_bytes is not None and request.content_length and request.content_length > max_bytes:
            limit_mb = int(max_bytes) // (1024 * 1024)
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
        max_bytes = app.config["MAX_CONTENT_LENGTH"]
        if max_bytes is not None and request.content_length and request.content_length > max_bytes:
            limit_mb = int(max_bytes) // (1024 * 1024)
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

    @app.post("/api/v1/wizard/reel-overlay")
    def api_wizard_reel_overlay() -> Response:
        storage = request.files.get("file")
        if not storage or not storage.filename:
            return error_response("bad_request", "file is required", 400)
        suffix = Path(storage.filename).suffix.lower()
        if suffix not in {".png", ".webp", ".jpg", ".jpeg"}:
            return error_response("bad_request", "Reel overlays must be PNG, WEBP, or JPEG images", 400)
        destination_dir = app_home() / "ReelOverlays"
        destination_dir.mkdir(parents=True, exist_ok=True)
        payload = storage.read()
        digest = hashlib.sha256(payload).hexdigest()
        for existing in sorted(destination_dir.iterdir()) if destination_dir.exists() else []:
            if existing.is_file() and existing.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}:
                try:
                    if hashlib.sha256(existing.read_bytes()).hexdigest() == digest:
                        return jsonify({"path": str(existing.resolve()), "url": f"/api/v1/wizard/reel-overlay/{existing.name}", "reused": True})
                except OSError:
                    continue
        destination = unique_destination(destination_dir / Path(storage.filename).name)
        destination.write_bytes(payload)
        return jsonify({"path": str(destination.resolve()), "url": f"/api/v1/wizard/reel-overlay/{destination.name}"})

    @app.post("/api/v1/wizard/reel-video-overlay")
    def api_wizard_reel_video_overlay() -> Response:
        storage = request.files.get("file")
        if not storage or not storage.filename:
            return error_response("bad_request", "file is required", 400)
        suffix = Path(storage.filename).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
            return error_response("bad_request", "Video overlays must be MP4, MOV, M4V, or WEBM", 400)
        destination_dir = app_home() / "ReelOverlays"
        destination_dir.mkdir(parents=True, exist_ok=True)
        payload = storage.read()
        digest = hashlib.sha256(payload).hexdigest()
        for existing in sorted(destination_dir.iterdir()) if destination_dir.exists() else []:
            if existing.is_file() and existing.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
                try:
                    if hashlib.sha256(existing.read_bytes()).hexdigest() == digest:
                        return jsonify({"path": str(existing.resolve()), "url": f"/api/v1/wizard/reel-video-overlay/{existing.name}", "reused": True})
                except OSError:
                    continue
        destination = unique_destination(destination_dir / Path(storage.filename).name)
        destination.write_bytes(payload)
        return jsonify({"path": str(destination.resolve()), "url": f"/api/v1/wizard/reel-video-overlay/{destination.name}"})

    @app.get("/api/v1/wizard/flyers")
    def api_wizard_flyers() -> Response:
        root = app_home() / "ReelOverlays"
        root.mkdir(parents=True, exist_ok=True)
        allowed = {".png", ".webp", ".jpg", ".jpeg"}
        items = []
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            items.append({"name": path.name, "path": str(path.resolve()), "url": f"/api/v1/wizard/reel-overlay/{path.name}"})
        return jsonify({"items": items})

    @app.get("/api/v1/wizard/reel-overlay/<path:filename>")
    def api_wizard_reel_overlay_file(filename: str) -> Response:
        # Browsers cannot load an absolute local path; serve only uploaded
        # overlays from the dedicated directory.
        return send_from_directory(app_home() / "ReelOverlays", Path(filename).name)

    @app.get("/api/v1/wizard/reel-video-overlay/<path:filename>")
    def api_wizard_reel_video_overlay_file(filename: str) -> Response:
        return send_from_directory(app_home() / "ReelOverlays", Path(filename).name)

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
        platform = str(body.get("platform") or "youtube")
        audio_trim = _audio_trim_from_body(body)
        spherical_landmarks = _spherical_landmarks_from_body(body)
        camera_role_weights = _camera_role_weights_from_body(body)
        fixed_rear_motion = _fixed_rear_motion_from_body(body)
        spherical_motion = _spherical_motion_from_body(body)
        spherical_mode = _spherical_mode_from_body(body)
        spherical_sweep = _spherical_sweep_from_body(body)
        sweep_speed_deg_per_sec = _sweep_speed_from_body(body)
        reel_duration_sec = _backstage_duration_from_body(body) if platform == "backstage" else _reel_duration_from_body(body)
        reel_aspect = _reel_aspect_from_body(body)
        reel_text_overlays = _reel_text_overlays_from_body(body)
        reel_image_overlays = _reel_image_overlays_from_body(body)
        backstage_messages = body.get("backstage_messages") or []
        if not isinstance(backstage_messages, list) or not all(isinstance(value, str) for value in backstage_messages):
            return error_response("bad_request", "backstage_messages must be a list of strings", 400)
        backstage_messages = [value.strip() for value in backstage_messages if value.strip()][:4]
        _save_last_reel_overlays(reel_text_overlays, reel_image_overlays)
        if platform not in {"youtube", "instagram", "tiktok", "reel", "360", "backstage"}:
            return error_response("bad_request", "platform must be youtube, reel, instagram, tiktok, 360, or backstage", 400)
        if not master and platform != "backstage":
            return error_response("missing_master", t("missing_master"), 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", t("missing_video"), 400)
        try:
            options = {
                "name": name or "Jam",
                "platform": platform,
                "song_choice": body.get("song_index", body.get("song_choice")),
                "audio_trim": audio_trim,
                "spherical_landmarks": spherical_landmarks,
                "camera_role_weights": camera_role_weights,
                "fixed_rear_motion": fixed_rear_motion,
                "spherical_motion": spherical_motion,
                "spherical_mode": spherical_mode,
                "spherical_sweep": spherical_sweep,
                "sweep_speed_deg_per_sec": sweep_speed_deg_per_sec,
                "reel_duration_sec": reel_duration_sec,
                "reel_aspect": reel_aspect,
                "reel_text_overlays": reel_text_overlays,
                "reel_image_overlays": reel_image_overlays,
                "backstage_messages": backstage_messages,
                "master_path": master,
                "songs_path": songs,
                "video_paths": videos,
            }
            if state.project is not None and state.wizard._prepared_project is None:
                state.project.data.setdefault("settings", {}).setdefault("wizard", {})["variation_seed"] = str(body.get("variation_seed") or time.time_ns())
                state.project.save()
                job = state.wizard.start_existing(state.project, **options)
                return jsonify(serialize_wizard_job(job)), 202
            matching_project = state.project if _can_reuse_prepared_project(state.project, master, songs, videos) else find_project_by_inputs(master, songs, videos)
            if matching_project and _project_can_skip_prepare(matching_project):
                state.project = matching_project
                state.wizard.adopt_prepared_project(state.project)
            elif matching_project:
                state.project = matching_project
                state.wizard.prepare_existing(matching_project, platform=platform)
            job = state.wizard.start(**options)
            return jsonify(serialize_wizard_job(job)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/wizard/review")
    def api_wizard_review() -> Response:
        project = state.project or _active_wizard_project(state)
        if not project:
            return error_response("not_ready", "The edit plan is not ready yet", 409)
        try:
            render_missing = request.args.get("render", "1") != "0"
            return jsonify({"items": review_items(project, render_missing=render_missing), "platform": project.data.get("settings", {}).get("wizard", {}).get("platform")})
        except Exception as exc:
            LOGGER.exception("Review items failed for %s", project.folder)
            return error_response("review_render_failed", str(exc) or "Review thumbnail render failed", 500)

    @app.get("/api/v1/wizard/paper-edit")
    def api_wizard_paper_edit() -> Response:
        project = state.project or _active_wizard_project(state)
        if not project:
            return error_response("not_ready", "The Backstage paper edit is not ready yet", 409)
        path = project.artifacts_dir / "backstage_paper_edit.json"
        try:
            return jsonify(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_unavailable", str(exc), 409)

    @app.get("/api/v1/wizard/paper-edit/thumbnail/<cut_id>")
    def api_wizard_paper_edit_thumbnail(cut_id: str) -> Response:
        project = state.project or _active_wizard_project(state)
        if not project or not cut_id.startswith("backstage-"):
            return error_response("not_found", "paper-edit thumbnail not found", 404)
        try:
            paper = json.loads((project.artifacts_dir / "backstage_paper_edit.json").read_text(encoding="utf-8"))
            cut = next(row for row in paper.get("cuts") or [] if str(row.get("id")) == cut_id)
            output = project.cache_dir / "paper_edit" / f"{cut_id}.jpg"
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                ffmpeg = tool_status().get("ffmpeg_path")
                if not ffmpeg:
                    return error_response("not_ready", "ffmpeg is unavailable", 503)
                source = str(cut.get("source_path") or "")
                command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", str(cut.get("in_sec") or 0), "-i", source, "-frames:v", "1", "-vf", "scale=320:-2", str(output)]
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    return error_response("thumbnail_failed", result.stderr.strip() or "thumbnail render failed", 500)
            return send_from_directory(output.parent, output.name)
        except (OSError, StopIteration, ValueError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_thumbnail_failed", str(exc), 404)

    @app.post("/api/v1/wizard/paper-edit/approve")
    def api_wizard_paper_edit_approve() -> Response:
        state.project = state.project or _active_wizard_project(state)
        body = _json_body()
        rejected = body.get("rejected") or []
        if not isinstance(rejected, list) or not all(isinstance(value, (str, int)) for value in rejected):
            return error_response("bad_request", "rejected must be a list of paper-edit ids", 400)
        try:
            status = state.wizard.status()
            if status.get("status") != "waiting_paper_edit":
                state.wizard.adopt_paper_edit(state.project)
            job = state.wizard.approve_paper_edit(state.project, [str(value) for value in rejected])
            return jsonify(serialize_wizard_job(job)), 202
        except (RuntimeError, ValueError) as exc:
            return error_response("paper_edit_approve_failed", str(exc), 409)

    @app.post("/api/v1/wizard/paper-edit/text")
    def api_wizard_paper_edit_text() -> Response:
        project = state.project or _active_wizard_project(state)
        body = _json_body()
        cut_id = str(body.get("id") or "").strip()
        text = str(body.get("text") or "").strip()
        if not project or not cut_id or len(text) > 1000:
            return error_response("bad_request", "id and a subtitle text up to 1000 characters are required", 400)
        try:
            paper_path = project.artifacts_dir / "backstage_paper_edit.json"
            edit_path = project.artifacts_dir / "backstage_edit.json"
            paper = json.loads(paper_path.read_text(encoding="utf-8"))
            edit = json.loads(edit_path.read_text(encoding="utf-8"))
            index = next((index for index, cut in enumerate(paper.get("cuts") or []) if str(cut.get("id")) == cut_id), None)
            segment_index = next((index for index, segment in enumerate(edit.get("segments") or []) if str(segment.get("id") or f"backstage-{index:04d}") == cut_id), None)
            if index is None or segment_index is None:
                return error_response("not_found", "paper-edit cut not found", 404)
            # This endpoint is a text edit, not an edit-plan rebuild. Keep
            # every in/out/duration/timestamp byte untouched and address the
            # cue by its stable id.
            update_backstage_cue_text({"cues": paper["cuts"]}, cut_id, text)
            paper["cuts"][index]["subtitle_text"] = text
            paper["cuts"][index]["text"] = text
            edit["segments"][segment_index]["text"] = text
            edit["segments"][segment_index]["subtitle_text"] = text
            paper_path.write_text(json.dumps(paper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            project.mark_all_stale_from("export")
            project.save()
            return jsonify(paper)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_text_failed", str(exc), 409)

    @app.post("/api/v1/wizard/paper-edit/mark")
    def api_wizard_paper_edit_mark() -> Response:
        project = state.project or _active_wizard_project(state)
        body = _json_body()
        cut_id = str(body.get("id") or "").strip()
        mark = str(body.get("mark") or "").strip().lower()
        if not project or not cut_id or mark not in {"keep", "drop", "closing"}:
            return error_response("bad_request", "id and mark (keep, drop, closing) are required", 400)
        try:
            paper_path = project.artifacts_dir / "backstage_paper_edit.json"
            edit_path = project.artifacts_dir / "backstage_edit.json"
            paper = json.loads(paper_path.read_text(encoding="utf-8"))
            edit = json.loads(edit_path.read_text(encoding="utf-8"))
            index = next((i for i, cut in enumerate(paper.get("cuts") or []) if str(cut.get("id")) == cut_id), None)
            if index is None:
                return error_response("not_found", "paper-edit cut not found", 404)
            cut = paper["cuts"][index]
            record_feedback(str(cut.get("source_path") or ""), float(cut.get("in_sec") or 0), float(cut.get("out_sec") or 0), mark, cut.get("text_original", ""), cut.get("english_text", ""), cut.get("selection_reason", ""))
            cut["mark"] = mark
            cut["status"] = "dropped" if mark == "drop" else "pending"
            segments = list(edit.get("segments") or [])
            if index < len(segments):
                segments[index]["paper_mark"] = mark
            edit["segments"] = segments
            edit["cut_count"] = max(0, len(segments) - 1)
            edit["selected_duration_sec"] = round(sum(float(item.get("duration_sec") or 0) for item in segments), 3)
            for order, row in enumerate(paper.get("cuts") or [], 1):
                row["order"] = order
            write = lambda path, payload: path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write(paper_path, paper)
            write(edit_path, edit)
            project.mark_all_stale_from("export")
            project.save()
            return jsonify(paper)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_mark_failed", str(exc), 409)

    @app.post("/api/v1/wizard/review/replace")
    def api_wizard_review_replace() -> Response:
        project = state.project or _active_wizard_project(state)
        body = _json_body()
        rejected = body.get("rejected") or []
        if not project or not isinstance(rejected, list) or not all(isinstance(value, int) for value in rejected):
            return error_response("bad_request", "rejected must be a list of shot indexes", 400)
        try:
            result = replace_slots(project, rejected)
            replaced = {int(value) for value in result.get("replaced", [])}
            if replaced:
                def render_replaced_thumbnails() -> None:
                    try:
                        review_items(project, render_indices=replaced)
                    except Exception as exc:
                        LOGGER.exception("Review thumbnail render failed for %s", project.folder)
                        mark_review_render_failed(project, replaced, str(exc))

                threading.Thread(
                    target=render_replaced_thumbnails,
                    name="review-thumbnail-render",
                    daemon=True,
                ).start()
            return jsonify(result)
        except Exception:
            LOGGER.exception("Review replacement failed for %s", project.folder)
            return error_response("review_replace_failed", "Could not replace the rejected shots", 500)

    @app.post("/api/v1/wizard/review/render")
    def api_wizard_review_render() -> Response:
        try:
            state.project = state.project or _active_wizard_project(state)
            job = state.wizard.render_review(state.project)
            return jsonify(serialize_wizard_job(job)), 202
        except (RuntimeError, ValueError) as exc:
            return error_response("review_render_failed", str(exc), 409)

    @app.get("/api/v1/wizard/review/thumbnail/<signature>/<path:filename>")
    def api_wizard_review_thumbnail(signature: str, filename: str) -> Response:
        project = state.project or _active_wizard_project(state)
        if not project:
            return error_response("not_ready", "No project is active", 404)
        return send_from_directory(project.cache_dir / "shot_review" / signature, Path(filename).name)

    @app.get("/api/v1/wizard/overlays")
    def api_wizard_overlays() -> Response:
        wizard = (state.project.data.get("settings", {}).get("wizard", {}) if state.project else {})
        if wizard.get("reel_text_overlays") or wizard.get("reel_image_overlays") or wizard.get("reel_video_overlays"):
            return jsonify({"texts": wizard.get("reel_text_overlays") or [], "images": wizard.get("reel_image_overlays") or [], "videos": wizard.get("reel_video_overlays") or [], "source": "project"})
        path = app_home() / "reel_overlays.json"
        try:
            return jsonify({**json.loads(path.read_text(encoding="utf-8")), "source": "last"})
        except (OSError, ValueError, TypeError):
            return jsonify({"texts": [], "images": [], "videos": [], "source": "none"})

    @app.get("/api/v1/wizard/master-preview")
    def api_wizard_master_preview() -> Response:
        status = state.wizard.status()
        project_path = status.get("project_path") or ((status.get("result") or {}).get("project_path"))
        project = state.project
        if not project and project_path:
            project = load_project(project_path)
        requested = Path(str(request.args.get("path") or "")).expanduser().resolve()
        if requested.exists() and requested.is_file() and requested.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
            return send_file_with_range(str(requested))
        if not project or not project.data.get("inputs", {}).get("master"):
            return error_response("not_found", "Master media is not registered", 404)
        master_path = project.data["inputs"]["master"]["path"]
        return send_file_with_range(master_path)

    @app.post("/api/v1/wizard/prepare")
    def api_wizard_prepare() -> Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        platform = str(body.get("platform") or "youtube").strip().lower()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip() or None
        videos = body.get("videos") or []
        if not master and platform != "backstage":
            return error_response("missing_master", t("missing_master"), 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", t("missing_video"), 400)
        try:
            matching_project = state.project if _can_reuse_prepared_project(state.project, master, songs, videos) else find_project_by_inputs(master, songs, videos)
            if matching_project and _project_can_skip_prepare(matching_project):
                state.project = matching_project
                job = state.wizard.adopt_prepared_project(state.project)
                LOGGER.info("Reused prepared wizard project %s instead of creating a new project", state.project.folder)
                return jsonify(serialize_wizard_job(job)), 202
            if matching_project:
                state.project = matching_project
                job = state.wizard.prepare_existing(matching_project, platform=platform)
                LOGGER.info("Reused existing wizard project %s instead of creating a new project", matching_project.folder)
                return jsonify(serialize_wizard_job(job)), 202
            job = state.wizard.prepare(name=name or "Jam", master_path=master, songs_path=songs, video_paths=videos, platform=platform)
            return jsonify(serialize_wizard_job(job)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/wizard/status")
    def api_wizard_status() -> Response:
        requested_project_id = str(request.args.get("project_id") or "").strip()
        if requested_project_id:
            try:
                requested_project = load_project(requested_project_id)
            except ProjectError:
                return error_response("project_not_found", "The requested project is not available", 404)
            active_path = state.project.folder.resolve() if state.project else None
            if active_path != requested_project.folder.resolve():
                # A late poll from another project must never read the
                # process-global wizard job. Return the requested project's
                # persisted state instead; the browser also validates the id.
                return jsonify(_project_wizard_status(requested_project))
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
            _invalidate_stale_sync_on_open(state.project)
            reconciled = reconcile_registered_inputs(state.project)
            migrated = migrate_project_normalization_cache(state.project)
            if reconciled or migrated:
                LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
            _remember_project(state.project)
            # Opening is data loading only. Make it always starts a fresh
            # pipeline over the restored Drop Here inputs.
            return jsonify(_project_wizard_status(state.project))
        except ProjectError as exc:
            return error_response(
                "project_unrecoverable",
                "Este proyecto no se puede recuperar. Los vídeos del Drop Here siguen disponibles; crea un proyecto nuevo.",
                409,
            )

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

    @app.post("/api/v1/wizard/cancel")
    def api_wizard_cancel() -> Response:
        cancelled = state.wizard.cancel()
        if not cancelled:
            return error_response("not_running", "No job is currently running", 409)
        return jsonify({"ok": True})

    @app.get("/api/v1/wizard/report")
    def api_wizard_report() -> Response:
        return Response(wizard_report(state.wizard.status()), mimetype="text/plain")

    @app.post("/api/v1/wizard/rescue")
    def api_wizard_rescue() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or ("offset_sec" not in body and not isinstance(body.get("offset_ranges"), list)):
            return error_response("bad_request", "clip_id and offset_sec or offset_ranges are required", 400)
        try:
            if isinstance(body.get("offset_ranges"), list):
                job = state.wizard.rescue_ranges(project, clip_id=clip_id, offset_ranges=body["offset_ranges"])
            else:
                job = state.wizard.rescue(project, clip_id=clip_id, offset_sec=float(body["offset_sec"]))
            return jsonify(serialize_wizard_job(job)), 202
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

    @app.post("/api/v1/sync-diagnostics/cleanup")
    def api_sync_diagnostics_cleanup() -> Response:
        return jsonify({"ok": True, "removed_projects": cleanup_closed_sync_diagnostics()})

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

    @app.get("/api/v1/captions/styles")
    def api_captions_styles() -> Response:
        return jsonify({"version": CAPTIONS_VERSION, "styles": [style.__dict__ for style in list_styles()]})

    @app.get("/api/v1/captions/frame")
    def api_captions_frame() -> Response:
        project = _require_project(state)
        result = _export_result(project)
        if not result:
            return error_response("not_found", "No exported video yet", 404)
        cache = project.cache_dir / "captions" / CAPTIONS_VERSION
        cache.mkdir(parents=True, exist_ok=True)
        frame = cache / "export-frame.jpg"
        if not frame.exists():
            ffmpeg = tool_status().get("ffmpeg_path") or "ffmpeg"
            subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", result["path"], "-frames:v", "1", "-vf", "scale=720:-2", str(frame)], check=True)
        return send_from_directory(frame.parent, frame.name)

    @app.post("/api/v1/captions/parse")
    def api_captions_parse() -> Response:
        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "lyrics")
        text = str(body.get("text") or "")
        if source == "lyrics":
            track = from_lyrics(text)
        elif source in {"srt", "lrc"}:
            project = _require_project(state)
            cache_dir = project.cache_dir / "captions" / CAPTIONS_VERSION
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / ("import.srt" if source == "srt" else "import.lrc")
            cache_path.write_text(text, encoding="utf-8")
            track = from_srt(cache_path) if source == "srt" else from_lrc(cache_path)
        else:
            return error_response("bad_request", "source must be lyrics, srt, or lrc", 400)
        return jsonify({"lang": track.lang, "cues": [{"lines": list(cue.lines), "start": cue.start, "end": cue.end} for cue in track.cues]})

    @app.post("/api/v1/captions/align")
    def api_captions_align() -> Response:
        body = request.get_json(silent=True) or {}
        lyrics = from_lyrics(str(body.get("lyrics") or ""))
        whisper = from_whisper(body.get("segments") or [], lang=str(body.get("lang") or "und"))
        track = align_known_lyrics(lyrics, whisper)
        return jsonify({"lang": track.lang, "cues": [{"lines": list(cue.lines), "start": cue.start, "end": cue.end, "words": [{"text": word.text, "start": word.start, "end": word.end} for word in cue.words]} for cue in track.cues]})

    @app.post("/api/v1/captions/burn")
    def api_captions_burn() -> Response:
        project = _require_project(state)
        result = _export_result(project)
        if not result:
            return error_response("not_found", "No exported video yet", 404)
        body = request.get_json(silent=True) or {}
        try:
            style = get_style(str(body.get("style") or "clean_bottom"))
            cues = tuple(Cue(tuple(str(line) for line in cue.get("lines", [])), float(cue["start"]), float(cue["end"]), tuple(Word(str(word.get("text") or word.get("word") or ""), float(word["start"]), float(word["end"])) for word in cue.get("words", [])), dict(cue.get("style_override") or {})) for cue in body.get("cues", []))
            track = CueTrack(cues, str(body.get("lang") or "und"))
            cache = project.cache_dir / "captions" / CAPTIONS_VERSION
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "captions.ass").write_text(render_ass(track, style), encoding="utf-8")
            (cache / "captions.srt").write_text(to_srt(track), encoding="utf-8")
            destination = project.exports_dir / f"{Path(result['path']).stem}_captions.mp4"
            header = body.get("header") if isinstance(body.get("header"), dict) else None
            letterbox = body.get("letterbox") if isinstance(body.get("letterbox"), dict) else None
            logo = None
            if header and header.get("logo_source") == "custom":
                candidate = Path(str(project.data.get("settings", {}).get("wizard", {}).get("reel_logo_path") or ""))
                if candidate.is_file() and candidate.parent.resolve() == project.folder.resolve(): logo = candidate
            elif header and header.get("logo_source") == "default":
                candidate = Path(str(load_global_config().get("personal_logo_path") or ""))
                if candidate.is_file(): logo = candidate
            track = _expand_caption_animations(track)
            output = burn_captions(result["path"], track, style, output_path=destination, header=header, logo_path=logo, letterbox=letterbox)
            return jsonify({"path": str(output), "filename": output.name, "media_url": "/api/v1/captions/result", "version": CAPTIONS_VERSION})
        except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            return error_response("caption_burn_failed", str(exc), 400)

    @app.get("/api/v1/captions/result")
    def api_captions_result() -> Response:
        project = _require_project(state)
        exports = sorted(project.exports_dir.glob("*_captions.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not exports:
            return error_response("not_found", "No captioned export yet", 404)
        return send_file_with_range(str(exports[0]))

    @app.post("/api/v1/wizard/compose")
    def api_wizard_compose() -> Response:
        project = _require_project(state)
        body = request.get_json(silent=True) or {}
        texts = body.get("texts") if isinstance(body.get("texts"), list) else []
        images = body.get("images") if isinstance(body.get("images"), list) else []
        videos = body.get("videos") if isinstance(body.get("videos"), list) else []
        cues = body.get("cues") if isinstance(body.get("cues"), list) else []
        header = body.get("header") if isinstance(body.get("header"), dict) else {}
        logo_mode = str(header.get("logo_source") or "none")
        if logo_mode not in {"custom", "default", "none"}:
            logo_mode = "none"
        overlay_spec = {"version": 2, "texts": texts, "images": images, "videos": videos}
        header = {**header, "logo_source": logo_mode, "logo_enabled": logo_mode != "none"}
        cue_track = {"version": CAPTIONS_VERSION, "lang": str(body.get("lang") or "und"), "cues": cues, "style": str(body.get("style") or "karaoke_word"), "header": header, "letterbox": body.get("letterbox") or {}}
        (project.folder / "overlay_spec.json").write_text(json.dumps(overlay_spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (project.folder / "cue_track.json").write_text(json.dumps(cue_track, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        wizard["reel_text_overlays"], wizard["reel_image_overlays"], wizard["reel_video_overlays"] = texts, images, videos
        wizard["reel_logo_mode"] = logo_mode
        if logo_mode != "custom":
            wizard["reel_logo_path"] = ""
            for old in project.folder.glob("overlay_logo.*"):
                old.unlink(missing_ok=True)
        project.save()
        return jsonify({"ok": True, "overlay_spec": str(project.folder / "overlay_spec.json"), "cue_track": str(project.folder / "cue_track.json")})

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
        raw_landmarks = body.get("spherical_landmarks", body)
        incoming = _sanitize_spherical_landmarks(raw_landmarks)
        # The sanitizer supplies UI defaults for a complete form. For this
        # endpoint, however, callers may send a partial edit; retain only the
        # fields actually present so omitted values are not reset.
        if isinstance(raw_landmarks, dict):
            for key in list(incoming):
                raw_value = raw_landmarks.get(key)
                if isinstance(raw_value, dict):
                    incoming[key] = {field: incoming[key][field] for field in ("yaw", "pitch", "fov", "weight") if field in raw_value}
        config = load_global_config()
        global_landmarks = config.get("spherical_landmarks") or {}
        project_landmarks = project.data.setdefault("settings", {}).get("spherical_landmarks") or {}
        landmarks = merge_spherical_landmarks(global_landmarks, project_landmarks)
        landmarks = merge_spherical_landmarks(landmarks, incoming)
        project.data["settings"]["spherical_landmarks"] = landmarks
        project.mark_all_stale_from("edit")
        project.save()
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
        config.setdefault("spherical_motion", False)
        config.setdefault("spherical_hold_motion", "none")
        config.setdefault("spherical_mode", "automatic")
        config.setdefault("spherical_sweep", False)
        config.setdefault("sweep_speed_deg_per_sec", 20.0)
        config.setdefault("audio_trim_by_master", {})
        config.setdefault("master_audio_extensions", [".mp3"])
        config.setdefault("personal_logo_path", "")
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

    @app.post("/api/v1/stages/sync/override-ranges")
    def api_sync_override_ranges() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        ranges = body.get("offset_ranges")
        if not clip_id or not isinstance(ranges, list):
            return error_response("bad_request", "clip_id and offset_ranges are required", 400)
        try:
            clip = set_manual_override_ranges(project, clip_id, ranges)
            return jsonify({"ok": True, "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/stages/sync/anchor")
    def api_sync_anchor() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or "master_sec" not in body or "clip_sec" not in body:
            return error_response("bad_request", "clip_id, master_sec, and clip_sec are required", 400)
        try:
            clip = set_manual_anchor(project, clip_id, body["master_sec"], body["clip_sec"])
            return jsonify({"ok": True, "offset_sec": clip["offset_sec"], "clip": clip})
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
            "status": "failed",
            "progress": _stage_progress(name),
            "message": "This project cannot be recovered",
            "detail": "The saved process was interrupted. The videos are loaded below; press Start Again to run a new montage.",
            "error": "This project cannot be recovered from its saved process state.",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    export_result = _export_result(project) if stages.get("export", {}).get("status") == "done" else None
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
    # A review-ready project must survive an app refresh/reopen. The in-memory
    # wizard job reports this state while Edit finishes, but once the process
    # is restarted only the persisted stage statuses remain. Do not downgrade
    # edit-complete projects to the old sync chooser.
    if stages.get("edit", {}).get("status") == "done" and stages.get("export", {}).get("status") != "done":
        platform = str((project.data.get("settings", {}).get("wizard") or {}).get("platform") or "youtube")
        if platform == "backstage" and (project.artifacts_dir / "backstage_paper_edit.json").exists():
            return {
                "id": "project",
                "status": "waiting_paper_edit",
                "progress": _stage_progress("edit"),
                "message": "Paper edit ready",
                "detail": "Review the written Backstage sequence before rendering.",
                "stage": "paper_edit",
                "paper_edit_available": True,
                "project_path": str(project.folder),
                "logs_path": logs_path,
            }
        if platform in {"youtube", "reel"}:
            return {
                "id": "project",
                "status": "waiting_review",
                "progress": _stage_progress("edit"),
                "message": "Review shots",
                "detail": "I'm artificial, but not that intelligent — help me check whether these shots are any good.",
                "stage": "review",
                "project_path": str(project.folder),
                "logs_path": logs_path,
            }
    platform = str((project.data.get("settings", {}).get("wizard") or {}).get("platform") or "youtube")
    prepared_without_sync = platform in {"reel", "backstage"} and stages.get("ingest", {}).get("status") == "done"
    if (stages.get("sync", {}).get("status") == "done" or prepared_without_sync) and stages.get("export", {}).get("status") != "done":
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


def _active_wizard_project(state: AppState) -> Project | None:
    status = state.wizard.status()
    project_path = status.get("project_path")
    if not project_path:
        return None
    try:
        return load_project(project_path)
    except (OSError, ProjectError):
        return None


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
        "spherical_recording_usage": export.get("spherical_recording_usage") or manifest.get("spherical_recording_usage") or {},
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


def _reel_duration_from_body(body: dict[str, Any]) -> float:
    value = _coerce_float(body.get("reel_duration_sec"))
    return max(20.0, min(60.0, value if value is not None else 30.0))


def _backstage_duration_from_body(body: dict[str, Any]) -> float:
    value = _coerce_float(body.get("backstage_duration_sec"))
    return max(30.0, min(240.0, value if value is not None else 180.0))


def _save_last_reel_overlays(texts: list[dict[str, Any]], images: list[dict[str, Any]]) -> None:
    path = app_home() / "reel_overlays.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"texts": texts, "images": images}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        LOGGER.warning("Could not persist last Reel overlays", exc_info=True)


def _reel_aspect_from_body(body: dict[str, Any]) -> str:
    return "16:9" if str(body.get("reel_aspect") or "9:16") == "16:9" else "9:16"


def _reel_text_overlays_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("reel_text_overlays") or []
    if not isinstance(raw, list):
        return []
    positions = {"top-left", "top-center", "top-right", "middle-left", "middle-center", "middle-right", "bottom-left", "bottom-center", "bottom-right"}
    import re
    result = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:200]
        if not text:
            continue
        color = str(item.get("color") or "#ffffff").lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", color):
            color = "#ffffff"
        size = _coerce_float(item.get("size")) or 54.0
        start = max(0.0, _coerce_float(item.get("start_sec")) or 0.0)
        duration = max(0.1, _coerce_float(item.get("duration_sec")) or 3.0)
        x = _coerce_float(item.get("x")); y = _coerce_float(item.get("y"))
        def color_value(key: str, fallback: str) -> str:
            value = str(item.get(key) or fallback).lower()
            return value if re.fullmatch(r"#[0-9a-f]{6}", value) else fallback
        animation = str(item.get("animation") or "fade")
        result.append({
            "text": text, "color": color, "size": max(18.0, min(160.0, size)),
            "position": str(item.get("position") or "middle-center") if str(item.get("position") or "middle-center") in positions else "middle-center",
            "x": max(0.0, min(1.0, x)) if x is not None else None,
            "y": max(0.0, min(1.0, y)) if y is not None else None,
            "opacity": max(0.05, min(1.0, _coerce_float(item.get("opacity")) or 1.0)),
            "font": str(item.get("font") or "bundled")[:40],
            "font_weight": str(item.get("font_weight") or "bold") if str(item.get("font_weight") or "bold") in {"normal", "bold"} else "bold",
            "outline_color": color_value("outline_color", "#000000"),
            "outline_width": max(0.0, min(12.0, _coerce_float(item.get("outline_width")) or 2.0)),
            "shadow_color": color_value("shadow_color", "#000000"),
            "shadow_offset_x": max(-40.0, min(40.0, _coerce_float(item.get("shadow_offset_x")) or 3.0)),
            "shadow_offset_y": max(-40.0, min(40.0, _coerce_float(item.get("shadow_offset_y")) or 3.0)),
            "shadow_blur": max(0.0, min(30.0, _coerce_float(item.get("shadow_blur")) or 4.0)),
            "background_color": color_value("background_color", "#000000"),
            "background_opacity": max(0.0, min(1.0, _coerce_float(item.get("background_opacity")) or 0.0)),
            "background_radius": max(0.0, min(80.0, _coerce_float(item.get("background_radius")) or 0.0)),
            "animation": animation if animation in {"none", "fade", "slide", "scale"} else "fade",
            "start_sec": start, "duration_sec": min(60.0, duration)
        })
    return result


def _reel_image_overlays_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("reel_image_overlays") or []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.exists() or path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
            continue
        start = max(0.0, _coerce_float(item.get("start_sec")) or 0.0)
        duration = min(60.0, max(0.1, _coerce_float(item.get("duration_sec")) or 3.0))
        x_value = _coerce_float(item.get("x")); y_value = _coerce_float(item.get("y"))
        result.append({"path": str(path), "x": max(0.0, min(1.0, x_value if x_value is not None else 0.5)), "y": max(0.0, min(1.0, y_value if y_value is not None else 0.5)), "width": max(0.05, min(1.0, _coerce_float(item.get("width")) or 0.35)), "opacity": max(0.05, min(1.0, _coerce_float(item.get("opacity")) or 1.0)), "animation": str(item.get("animation") or "fade") if str(item.get("animation") or "fade") in {"none", "fade", "slide", "scale"} else "fade", "start_sec": start, "duration_sec": duration})
    return result


def _spherical_landmarks_from_body(body: dict[str, Any]) -> dict[str, dict[str, float]] | None:
    if "spherical_landmarks" not in body:
        return None
    return _sanitize_spherical_landmarks(body.get("spherical_landmarks") or {})


def _camera_role_weights_from_body(body: dict[str, Any]) -> dict[str, float] | None:
    if "camera_role_weights" not in body:
        return None
    return _sanitize_camera_role_weights(body.get("camera_role_weights") or {})


def _spherical_motion_from_body(body: dict[str, Any]) -> bool | None:
    if "spherical_motion" not in body:
        return None
    return bool(body.get("spherical_motion"))


def _fixed_rear_motion_from_body(body: dict[str, Any]) -> bool | None:
    if "fixed_rear_motion" not in body:
        return None
    return bool(body.get("fixed_rear_motion"))


def _spherical_mode_from_body(body: dict[str, Any]) -> str | None:
    if "spherical_mode" not in body:
        return None
    value = str(body.get("spherical_mode") or "automatic").strip().lower()
    return value if value in {"automatic", "directed"} else "automatic"


def _spherical_sweep_from_body(body: dict[str, Any]) -> bool | None:
    if "spherical_sweep" not in body:
        return None
    return bool(body.get("spherical_sweep"))


def _sweep_speed_from_body(body: dict[str, Any]) -> float | None:
    if "sweep_speed_deg_per_sec" not in body:
        return None
    return max(15.0, min(20.0, _optional_float_setting(body.get("sweep_speed_deg_per_sec"), 20.0)))


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
        fov = max(1.0, min(MAX_SPHERICAL_FOV, _optional_float_setting(source.get("fov"), float(meta["fov"]))))
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


# Wide-FOV preview: above this the preview switches to stereographic to
# match the export's wide/tiny-planet rendering (see export._use_stereographic).
def _preview_source_duration(path: Path) -> float:
    try:
        metadata = ffprobe(str(path))
        value = (metadata.get("format") or {}).get("duration")
        return max(0.1, float(value))
    except Exception:
        return 1.0


def _project_audio_trim(project: Project) -> tuple[float, float]:
    master = project.data.get("inputs", {}).get("master") or {}
    duration = 0.0
    try:
        duration = float(master.get("duration") or (master.get("probe") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and master.get("path"):
        try:
            duration = _preview_source_duration(Path(str(master["path"])))
        except Exception:
            duration = 0.0
    trim = project.data.get("settings", {}).get("wizard", {}).get("audio_trim") or {}
    try:
        start = max(0.0, float(trim.get("start_sec") or 0.0))
    except (TypeError, ValueError):
        start = 0.0
    try:
        end = float(trim.get("end_sec") or duration or start + 1.0)
    except (TypeError, ValueError):
        end = duration or start + 1.0
    if duration > 0:
        end = min(duration, end)
    if end <= start:
        end = start + 1.0
    return round(start, 6), round(end, 6)


def _enable_cors(app: Flask) -> None:
    @app.after_request
    def add_cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response
