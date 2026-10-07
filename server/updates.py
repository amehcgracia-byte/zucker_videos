"""Desktop update routes: consent, same-origin tokens and idle-only installation."""
from __future__ import annotations
import secrets
from flask import jsonify, request
from core.updater import UpdateManager


def register_update_routes(app, state):
    manager = UpdateManager()
    app.config['ZUCKER_UPDATER'] = manager

    def busy():
        wizard = state.wizard.status() if state.wizard else {}
        composition = state.composition.status() or {}
        auto_read = state.auto_read.status() or {}
        return bool(state.engine.status(state.project).get('busy')) or any(item.get('status') in {'running','cancelling','waiting_review','preparing'}
                   for item in (wizard,composition,auto_read))

    def authorized():
        origin = request.headers.get('Origin')
        return (not state.dev and request.host.split(':')[0] in {'localhost','127.0.0.1'} and (not origin or origin == request.host_url.rstrip('/'))
                and secrets.compare_digest(str((request.get_json(silent=True) or {}).get('token') or ''),manager.token))

    @app.before_request
    def preserve_update_idle_state():
        if request.method != 'GET' and not request.path.startswith('/api/v1/updates/'):
            if manager.snapshot()['status'] in {'downloading','preparing','ready','installing'}:
                return jsonify(error={'message':'An update is in progress; wait until it finishes.'}),409

    @app.get('/api/v1/updates/check')
    def check_update():
        if state.dev: return jsonify(status='disabled')
        return jsonify(manager.check())

    @app.get('/api/v1/updates/status')
    def update_status():
        return jsonify(manager.snapshot())

    @app.post('/api/v1/updates/download')
    def download_update():
        if not authorized(): return jsonify(error={'message':'Update authorization is missing.'}),403
        if busy(): return jsonify(error={'message':'Finish or cancel the current task and shot review before updating.'}),409
        try:
            manager.download()
            return jsonify(manager.snapshot())
        except ValueError as exc: return jsonify(error={'message':str(exc)}),400

    @app.post('/api/v1/updates/apply')
    def apply_update():
        if not authorized(): return jsonify(error={'message':'Update authorization is missing.'}),403
        if busy(): return jsonify(error={'message':'The application is working; the update has not been installed.'}),409
        try:
            manager.apply()
            return jsonify(manager.snapshot())
        except (ValueError,OSError) as exc: return jsonify(error={'message':str(exc)}),400
