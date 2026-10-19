"""Local connection preferences and bearer-authenticated external render control."""
import hmac
import secrets
from urllib.parse import urlsplit
from flask import request,jsonify
from core import ai_connections
from core.narrative_credentials import _vault


def _local():
    return request.remote_addr in ('127.0.0.1','::1') and urlsplit(request.host_url).hostname in ('localhost','127.0.0.1','::1')

def _same_origin():
    origin=request.headers.get('Origin')
    return _local() and (not origin or urlsplit(origin).netloc==request.host and urlsplit(origin).scheme==request.scheme)

def control_token(create=False):
    vault=_vault();token=vault.get_password(ai_connections.SERVICE,'remote-control')
    if not token and create:
        token=secrets.token_urlsafe(32);vault.set_password(ai_connections.SERVICE,'remote-control',token)
    return token or ''


def register_ai_connection_routes(app,state,render_show,render_review):
    def denied():return jsonify(error={'code':'unauthorized','message':'Local authorization required'}),401
    @app.get('/api/v1/connectors/api')
    def get_connections():
        if not _same_origin():return denied()
        return jsonify(ai_connections.status())
    @app.put('/api/v1/connectors/api/<provider>')
    def save_connection(provider):
        if not _same_origin():return denied()
        body=request.get_json(silent=True) or {}
        try:
            if not isinstance(body.get('key'),str):raise ValueError('Enter an API key')
            ai_connections.save_key(provider,body['key'])
            return jsonify(ai_connections.status())
        except ValueError as exc:return jsonify(error={'message':str(exc)}),400
        except RuntimeError as exc:return jsonify(error={'message':str(exc)}),503
    @app.put('/api/v1/connectors/model')
    def select_model():
        if not _same_origin():return denied()
        body=request.get_json(silent=True) or {}
        try:ai_connections.select(body.get('provider'),body.get('model'));return jsonify(ai_connections.status())
        except ValueError as exc:return jsonify(error={'message':str(exc)}),400
    @app.post('/api/v1/connectors/remote-token')
    def get_control_token():
        if not _same_origin():return denied()
        try:return jsonify(token=control_token(create=True))
        except Exception:return jsonify(error={'message':'The secure credential store is unavailable'}),503
    def authorized():
        if not _same_origin():return False
        header=request.headers.get('Authorization','')
        try:token=control_token()
        except Exception:return False
        return bool(token) and hmac.compare_digest(header,'Bearer '+token)
    @app.get('/api/remote/status')
    def remote_status():
        if not authorized():return denied()
        return jsonify(project_id=str(state.project.folder) if state.project else None,job=state.wizard.status())
    @app.post('/api/remote/render')
    def remote_render():
        if not authorized():return denied()
        body=request.get_json(silent=True) or {}
        if not state.project or body.get('project_id')!=str(state.project.folder):
            return jsonify(error={'message':'Open the intended project and pass its project_id'}),409
        if body.get('mode') not in ('show','review'):return jsonify(error={'message':'Choose show or review'}),400
        return render_show() if body['mode']=='show' else render_review()

    @app.post('/api/remote/audio-plan')
    def remote_audio_plan():
        if not authorized():return denied()
        from core import music_mix_plan
        body=request.get_json(silent=True) or {}
        if not state.project or body.get('project_id')!=str(state.project.folder):return jsonify(error={'message':'Open the intended project first'}),409
        try:
            if not isinstance(body.get('approve',False),bool):raise ValueError('Choose whether to approve the plan')
            result=music_mix_plan.save(state.project,body.get('segments'),approve=body.get('approve',False),revision=body.get('revision'),expected_signature=body.get('signature'))
            return jsonify(result)
        except (ValueError,TypeError) as exc:return jsonify(error={'message':str(exc)}),400
