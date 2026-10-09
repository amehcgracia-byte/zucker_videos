"""First-run tutorial preference, independent of projects and changing server ports."""
import json
import os
import tempfile
from flask import jsonify, request
from core.storage import preference_path


def register_tutorial_routes(app):
    def path():
        return preference_path().parent / 'tutorial.json'

    @app.get('/api/v1/tutorial')
    def tutorial_state():
        try:
            value = json.loads(path().read_text(encoding='utf-8'))
        except (OSError, ValueError):
            value = {}
        if not isinstance(value, dict): value = {}
        return jsonify(answer=value.get('answer') if value.get('answer') in {'yes', 'no'} else None)

    @app.post('/api/v1/tutorial')
    def tutorial_answer():
        payload = request.get_json(silent=True)
        answer = payload.get('answer') if isinstance(payload, dict) else None
        if answer not in {'yes', 'no'}:
            return jsonify(error={'message': 'Choose yes or no.'}), 400
        target = path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.tutorial-', dir=target.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'answer': answer}, stream)
            os.replace(name, target)
        finally:
            if os.path.exists(name): os.unlink(name)
        return jsonify(answer=answer)
