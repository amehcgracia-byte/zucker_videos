"""Project library endpoints; no effect renderer or global media duplication."""
from pathlib import Path
from flask import jsonify, request
from core.project_library import ProjectLibrary, catalogue, remember_project, reuse_asset


def register_library_routes(app, state, index_path):
    def project_library():
        if not state.project:
            raise ValueError('Open a saved project first.')
        return ProjectLibrary(state.project.folder)

    def failure(exc):
        return jsonify({'error': {'code': 'library_error', 'message': str(exc)}}), 400

    @app.get('/api/v1/library')
    def library_inventory():
        try:
            lib = project_library()
            return jsonify({**lib.inventory(), 'catalogue': catalogue(index_path())})
        except (OSError, ValueError, KeyError) as exc:
            return failure(exc)

    @app.post('/api/v1/library/import')
    def library_import():
        try:
            lib = project_library()
            upload = request.files.get('file')
            if not upload or not upload.filename:
                raise ValueError('Choose an image, video or audio file.')
            record = lib.import_stream(upload.stream, upload.filename, origin=request.form.get('origin', 'User supplied'))
            remember_project(index_path(), lib.project)
            return jsonify(record), 201
        except (OSError, ValueError, KeyError) as exc:
            return failure(exc)

    @app.post('/api/v1/library/reuse')
    def library_reuse():
        try:
            lib = project_library()
            body = request.get_json(silent=True) or {}
            record = reuse_asset(lib, index_path(), str(body.get('project', '')), str(body.get('id', '')))
            remember_project(index_path(), lib.project)
            return jsonify(record), 201
        except (OSError, ValueError, KeyError) as exc:
            return failure(exc)

    @app.post('/api/v1/library/clean-previews')
    def library_clean():
        try:
            lib = project_library()
            if (state.wizard.status().get('status') in {'running', 'cancelling'}
                    or state.engine.status(state.project).get('busy')
                    or (state.composition.status(state.project) or {}).get('status') in {'running', 'cancelling'}):
                return jsonify({'error': {'code': 'busy', 'message': 'Finish the current render before cleaning previews.'}}), 409
            body = request.get_json(silent=True) or {}
            return jsonify(lib.clean_previews(str(body.get('token', ''))))
        except (OSError, ValueError, KeyError) as exc:
            return failure(exc)
