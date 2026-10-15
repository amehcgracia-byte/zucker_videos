"""Show Studio served by Zucker for the open project, with its state kept there.

The editor (tools/qa/show_pilot_editor.html and its modules) asks for the same file
names as the standalone prototype; here they come from the project: the recipe it
saved, its source video and sound in the project library, its tracking data. The
person's own backgrounds and masks are library files, their settings in
materials.json (core.show_studio_store).

The standalone prototype runs on another local address and keeps its work in that
browser. It may copy it in once ("Save to project"); only the addresses listed in
ZUCKER_STUDIO_ORIGINS may, and only through these routes. None are allowed by default.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from flask import Response, jsonify, redirect, request, send_from_directory

from core.show_studio_store import ANALYSIS_NAMES, StaleRevision, StudioStore
from server.media import send_file_with_range

MODULE = re.compile(r"^[a-z][a-z-]{1,40}\.js$")
PUBLIC_DIRS = {"backgrounds": "show-backgrounds", "masks": "show-masks", "elements": "show-elements"}
SOURCE_FILES = {"original.mp4": "original", "mix.m4a": "baseAudio", "voice.m4a": "voice", "cutout-atlas.webp": "cutoutAtlas"}
ANALYSIS_FILES = {f"{name}.json": name for name in ANALYSIS_NAMES} | {"recipe.json": "proposal"}
CROSS_ORIGIN_PATHS = ("/api/v1/show-studio/", "/api/v1/library/import", "/api/v1/library/assets/")


def studio_origins() -> set[str]:
    return {origin.strip().rstrip("/") for origin in os.environ.get("ZUCKER_STUDIO_ORIGINS", "").split(",") if origin.strip()}


def register_show_studio_routes(app, state, root: Path) -> None:
    studio_dir = root / "tools" / "qa"

    def store() -> StudioStore:
        if not state.project:
            raise ValueError("Open a saved project first.")
        return StudioStore(state.project.folder)

    def failure(exc: Exception, status: int = 400):
        code = "stale_revision" if isinstance(exc, StaleRevision) else "show_studio_error"
        return jsonify({"error": {"code": code, "message": str(exc)}}), status

    def url(asset_id: str) -> str:
        return f"/api/v1/library/assets/{asset_id}"

    # ---- cross-origin copy from the standalone prototype --------------------------

    def allowed_origin() -> str | None:
        origin = (request.headers.get("Origin") or "").rstrip("/")
        return origin if origin and origin in studio_origins() and request.path.startswith(CROSS_ORIGIN_PATHS) else None

    @app.before_request
    def studio_preflight():
        if not request.path.startswith(CROSS_ORIGIN_PATHS):
            return None
        if request.method == "OPTIONS":
            return Response(status=204 if allowed_origin() else 403)
        # A form on any website could post a file here (no preflight for multipart):
        # writes come from this app's own pages or a listed prototype address only.
        origin = (request.headers.get("Origin") or "").rstrip("/")
        if request.method in ("POST", "PUT") and origin and origin != request.host_url.rstrip("/") and not allowed_origin():
            return jsonify({"error": {"code": "forbidden_origin", "message": "Writes come from Zucker's own pages"}}), 403
        return None

    @app.after_request
    def studio_cross_origin(response: Response) -> Response:
        origin = allowed_origin()
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Vary"] = "Origin"
        return response

    # ---- state -------------------------------------------------------------------

    @app.get("/api/v1/show-studio/state")
    def studio_state():
        try:
            current = store().state()
        except (OSError, ValueError) as exc:
            return failure(exc)
        return jsonify({**current, "project": {"name": Path(state.project.folder).stem, "folder": str(state.project.folder)},
                        "source_urls": {name: url(asset) for name, asset in current["sources"].items()},
                        "material_urls": {item["id"]: url(item["asset"]) for item in current["materials"]}})

    @app.put("/api/v1/show-studio/composition")
    def studio_composition():
        body = request.get_json(silent=True) or {}
        try:
            revision = store().save_composition(body.get("recipe"), revision=int(body.get("revision") or 0))
        except StaleRevision as exc:
            return failure(exc, 409)
        except (OSError, ValueError, TypeError) as exc:
            return failure(exc)
        return jsonify({"revision": revision})

    @app.put("/api/v1/show-studio/materials")
    def studio_materials():
        body = request.get_json(silent=True) or {}
        try:
            materials = store().save_materials(body.get("materials"))
        except (OSError, ValueError, TypeError) as exc:
            return failure(exc)
        return jsonify({"materials": materials, "material_urls": {item["id"]: url(item["asset"]) for item in materials}})

    @app.put("/api/v1/show-studio/sources")
    def studio_sources():
        body = request.get_json(silent=True) or {}
        try:
            sources = store().save_sources(body.get("sources"))
        except (OSError, ValueError, TypeError) as exc:
            return failure(exc)
        return jsonify({"sources": sources})

    @app.route("/api/v1/show-studio/analysis/<name>", methods=["GET", "PUT"])
    def studio_analysis(name: str):
        try:
            if request.method == "PUT":
                store().save_analysis(name, request.get_json(silent=True))
                return jsonify({"saved": name})
            data = store().load_analysis(name)
        except (OSError, ValueError, TypeError) as exc:
            return failure(exc)
        return jsonify(data) if data is not None else failure(ValueError("Not prepared for this project"), 404)

    @app.get("/api/v1/library/assets/<asset_id>")
    def library_asset(asset_id: str):
        try:
            path = store().asset_path(asset_id)
        except (OSError, ValueError) as exc:
            return failure(exc, 404)
        return send_file_with_range(str(path))

    # ---- the editor itself ------------------------------------------------------------

    @app.get("/show-studio")
    def studio_redirect():
        return redirect("/show-studio/")

    @app.get("/show-studio/")
    def studio_page():
        page = (studio_dir / "show_pilot_editor.html").read_text(encoding="utf-8")
        # Before any module: the editor then loads and saves through the open project.
        marker = '<script src="'
        page = page.replace(marker, '<script>window.ZUCKER_STUDIO={project:true}</script>' + marker, 1)
        response = Response(page, mimetype="text/html")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.get("/show-studio/<path:name>")
    def studio_file(name: str):
        if MODULE.match(name):
            module = studio_dir / f"show_{name[:-3].replace('-', '_')}.js"
            return send_from_directory(studio_dir, module.name) if module.is_file() else failure(ValueError("No such module"), 404)
        head, _, rest = name.partition("/")
        if head in PUBLIC_DIRS and rest:
            return send_from_directory(studio_dir / PUBLIC_DIRS[head], rest)
        try:
            thumb = re.match(r"^scene-thumbs/([a-z0-9-]{1,30})\.jpg$", name)
            if name in SOURCE_FILES or thumb:
                key = f"thumb-{thumb.group(1)}" if thumb else SOURCE_FILES[name]
                asset = store().state()["sources"].get(key)
                if not asset:
                    return failure(ValueError(f"This project has no {name} for Show Studio yet"), 404)
                return send_file_with_range(str(store().asset_path(asset)))
            if name in ANALYSIS_FILES:
                data = store().load_analysis(ANALYSIS_FILES[name])
                return jsonify(data) if data is not None else failure(ValueError(f"{name} is not prepared for this project"), 404)
        except (OSError, ValueError) as exc:
            return failure(exc, 404)
        return failure(ValueError("Not a Show Studio file"), 404)
