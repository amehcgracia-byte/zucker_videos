"""Global music browsing, reviewed Backstage plans and project-owned playback."""
import io,json,threading,time
from collections import OrderedDict
from flask import request,jsonify,Response,send_file
from core import music_library,music_mix_plan
from server.ai_connections import _same_origin


def register_music_routes(app,state):
    results=OrderedDict();jobs={};guard=threading.RLock()
    def current():
        if not state.project:raise ValueError('Open a project first')
        body=request.get_json(silent=True) or {}
        if request.method!='GET' and body.get('project_id')!=str(state.project.folder):raise ValueError('Reopen this plan in the current project')
        return state.project
    def fail(exc,code=400):return jsonify(error={'message':str(exc)}),code
    def record(source,id):
        with guard:
            value=results.get((source,id))
            if not value or time.monotonic()-value[0]>3600:raise ValueError('Search for this track again before previewing or downloading it')
            return value[1]
    @app.before_request
    def guard_music_origin():
        if request.path.startswith('/api/v1/music/') and not _same_origin():return fail('Local authorization required',401)
    @app.get('/api/v1/music/library')
    def library():
        return jsonify(folder=str(music_library.root()),items=[{**r,'url':'/api/v1/music/file/'+r['id']} for r in music_library.local(request.args.get('q',''))])
    @app.put('/api/v1/music/library')
    def library_folder():
        try:music_library.set_root((request.get_json(silent=True) or {}).get('folder'));return library()
        except (ValueError,OSError) as exc:return fail(exc)
    @app.post('/api/v1/music/import')
    def import_file():
        upload=request.files.get('file')
        if not upload:return fail('Choose an audio file')
        try:return jsonify(music_library.import_audio(upload.stream,upload.filename or '')),201
        except Exception as exc:return fail(exc)
    @app.get('/api/v1/music/file/<id>')
    def local_file(id):
        r=next((r for r in music_library.local() if r['id']==id),None)
        return send_file(r['path'],conditional=True) if r else fail('This music file is missing',404)
    @app.get('/api/v1/music/search')
    def search():
        source=request.args.get('source','jamendo')
        try:found=music_library.search(source,request.args.get('q',''))
        except (ValueError,RuntimeError) as exc:return fail(exc)
        with guard:
            for r in found:
                results[source,r['id']]=(time.monotonic(),r)
                while len(results)>200:results.popitem(last=False)
        return jsonify(items=[{k:v for k,v in r.items() if k not in ('preview','download')}|{'preview_url':f'/api/v1/music/preview/{source}/{r["id"]}','download_allowed':bool(r['download'])} for r in found])
    @app.get('/api/v1/music/preview/<source>/<id>')
    def preview(source,id):
        try:return Response(music_library.audio_bytes(record(source,id),preview=True),mimetype='audio/mpeg')
        except (ValueError,RuntimeError) as exc:return fail(exc)
    @app.post('/api/v1/music/download/<source>/<id>')
    def download(source,id):
        try:
            r=record(source,id)
            if not music_library.reusable(r['license']):raise ValueError('Choose a CC0 or CC BY track')
            data=music_library.audio_bytes(r)
            metadata={k:v for k,v in r.items() if k not in ('preview','download','id')};metadata['origin_id']=r['id']
            return jsonify(music_library.import_audio(io.BytesIO(data),r['name']+'.mp3',metadata)),201
        except Exception as exc:return fail(exc)
    @app.get('/api/v1/music/plan')
    def get_plan():
        try:
            p=current();items=music_mix_plan.segments(p)
            return jsonify(plan=music_mix_plan.load(p),signature=music_mix_plan.signature(items),project_id=str(p.folder),
                           segments=[{k:r.get(k) for k in ('id','duration_sec','text','text_original','english_text','filename')} for r in items],
                           library=[{k:r.get(k) for k in ('id','name','duration','license','attribution')} for r in music_mix_plan.available_tracks(p).values()])
        except ValueError as exc:return fail(exc,409)
    @app.put('/api/v1/music/plan')
    def save_plan():
        try:
            p=current();body=request.get_json(silent=True) or {}
            if not isinstance(body.get('approve',False),bool):raise ValueError('Choose whether to approve this plan')
            result=music_mix_plan.save(p,body.get('segments'),approve=body.get('approve',False),revision=body.get('revision'),expected_signature=body.get('signature'))
            return jsonify(result)
        except (ValueError,TypeError) as exc:return fail(exc,409)
    @app.post('/api/v1/music/plan/propose')
    def propose_plan():
        try:
            p=current();id=str(p.folder);sig=music_mix_plan.signature(music_mix_plan.segments(p));revision=music_mix_plan.load(p)['revision']
        except ValueError as exc:return fail(exc,409)
        with guard:
            if jobs.get(id,{}).get('status')=='working':return jsonify(jobs[id]),202
            jobs[id]={'status':'working'}
        def work():
            try:
                rows=music_mix_plan.propose(p)
                result=music_mix_plan.save(p,rows,revision=revision,expected_signature=sig)
                with guard:jobs[id]={'status':'done','result':result}
            except Exception:
                # Provider exception strings can contain request headers/URLs.
                with guard:jobs[id]={'status':'failed','error':'The AI could not prepare a valid plan. Check your model connection or create a manual plan.'}
        threading.Thread(target=work,daemon=True,name='music-plan').start()
        return jsonify(status='working'),202
    @app.get('/api/v1/music/plan/job')
    def plan_job():
        try:p=current()
        except ValueError as exc:return fail(exc,409)
        with guard:return jsonify(jobs.get(str(p.folder),{'status':'idle'}))
