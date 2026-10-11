"""First-run tutorial preference, independent of projects and changing server ports."""
import json
import os
import tempfile
import sys
from pathlib import Path
from flask import jsonify, request
from core.storage import preference_path


def installation_id():
    """Persist an answer for this installation, not for every future install.

    Copying/replacing the bundle changes its inode/ctime, even for a reinstall
    of the same build. Development retains a stable identity.
    """
    if not getattr(sys, 'frozen', False):
        return 'development'
    executable = Path(sys.executable)
    stat = executable.stat()
    return f'{executable}:{stat.st_ino}:{stat.st_ctime_ns}'


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
        return jsonify(answer=value.get('answer') if value.get('installation') == installation_id() and value.get('answer') in {'yes', 'no'} else None)

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
                json.dump({'answer': answer, 'installation': installation_id()}, stream)
            os.replace(name, target)
        finally:
            if os.path.exists(name): os.unlink(name)
        return jsonify(answer=answer)
