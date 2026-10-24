"""HTTP review of podcast camera decisions and reusable Story plans."""
import json,math,threading
from pathlib import Path
from dataclasses import asdict
from flask import jsonify,request

def register_program_review(app, project, invalidate):
    from core import show_podcast as podcast, show_review_history as history
    from core.project import atomic_write_json
    from server.edit_timeline import LOCK
    jobs={};lock=LOCK
    app.config["MONTARA_PROGRAM_BUSY"]=lambda p,kind:jobs.get(str(p.folder)+("story" if kind=="story" else ""),{}).get("status")=="running"
    def snapshot(p):
        data=podcast.load_setup(p)
        return {**data,'revision':int(data.get('revision',0)),'views':[asdict(v) for v in podcast.views(data.get('cameras') or [],len(data.get('people') or []))],'project_id':str(p.folder)}
    def valid_project(p,body):
        if body.get('project_id')!=str(p.folder):raise ValueError('Reopen this video before editing its camera decisions')
    @app.get('/api/v1/show/decisions')
    def show_decisions():return jsonify(items=history.read(project()))
    @app.get('/api/v1/show/podcast/plan')
    def show_podcast_plan():return jsonify(snapshot(project()))
    @app.patch('/api/v1/show/podcast/plan/<int:index>')
    def show_podcast_shot(index):
        p=project();body=request.get_json(silent=True) or {}
        try:
            with lock:
                valid_project(p,body);data=podcast.load_setup(p)
                if jobs.get(str(p.folder),{}).get('status')=='running':return jsonify(error={'message':'Wait for this preview before changing a camera'}),409
                if body.get('revision')!=data.get('revision',0):return jsonify(error={'message':'Camera decisions changed. Reopen this shot.'}),409
                shots=data.get('shots') or []
                if index>=len(shots):raise ValueError('Choose an existing camera shot')
                views=podcast.views(data['cameras'],len(data['people']));view_index=int(body['view'])
                if not 0<=view_index<len(views):raise ValueError('Choose one of this podcast’s viewpoints')
                view=asdict(views[view_index]);camera=data['cameras'][view['camera']]
                if camera['kind']=='360' and not body.get('use_view'):
                    view['yaw']=float(body.get('yaw',view['yaw'] or 0));view['pitch']=float(body.get('pitch',view['pitch']));view['fov']=float(body.get('fov',view['fov'] or 70));view['projection']='flat'
                    if not all(math.isfinite(view[k]) for k in ('yaw','pitch','fov')) or not -180<=view['yaw']<=180 or not -85<=view['pitch']<=85 or not 20<=view['fov']<=120:raise ValueError('Choose a finite viewpoint within the camera range')
                before=dict(shots[index]);shots[index]={**before,'view':view_index,'override':view,'camera':view['camera'],'kind':view['kind'],'shows':[data['people'][i]['name'] for i in view['people']],'manual':True}
                if camera['kind']!='360':
                    from core.flat_framing import validate
                    shots[index]['flat_framing']=validate(body.get('flat_framing') or {'zoom':1,'x':.5,'y':.5})
                data.update(shots=shots,revision=int(data.get('revision',0))+1,dirty=True)
                atomic_write_json(podcast.setup_path(p),data)
                history.record(p,'podcast.camera.corrected',before,shots[index],evidence={'shot':index,'reason':str(body.get('reason') or 'Camera/viewpoint corrected in the preview')[:500]})
            return jsonify(snapshot(p))
        except (ValueError,KeyError,TypeError,IndexError) as exc:return jsonify(error={'message':str(exc)}),400
    @app.get('/api/v1/show/podcast/program-preview')
    def show_podcast_preview_status():return jsonify(jobs.get(str(project().folder),{'status':'idle'}))
    @app.post('/api/v1/show/podcast/program-preview')
    def show_podcast_preview_start():
        p=project();body=request.get_json(silent=True) or {}
        try:valid_project(p,body)
        except ValueError as exc:return jsonify(error={'message':str(exc)}),409
        key=str(p.folder)
        with lock:
            if jobs.get(key,{}).get('status')=='running':return jsonify(jobs[key]),202
            data=podcast.load_setup(p)
            if not data.get('shots') or not data.get('mix'):return jsonify(error={'message':'Prepare the podcast before previewing its camera corrections'}),409
            jobs[key]={'status':'running','progress':0}
        def run():
            try:
                from core.ffmpeg import tool_status
                ffmpeg=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
                audio=podcast._decode(data['mix'],ffmpeg,48000)
                if len(data['people'])>0:
                    # _decode is mono; the saved mix is already levelled and gated.
                    pass
                podcast.render(data,data['shots'],audio,Path(data['program']),ffmpeg,width=int(data.get('render_width') or 1280),height=int(data.get('render_height') or 720),progress=lambda share:jobs[key].update(progress=round(share*100)))
                with lock:
                    data['dirty']=False;atomic_write_json(podcast.setup_path(p),data);invalidate(p)
                    history.record(p,'podcast.camera.previewed',after={'revision':data.get('revision',0),'shots':data['shots']},method='deterministic-render')
                    jobs[key]={'status':'ready','revision':data.get('revision',0)}
            except Exception as exc:
                app.logger.exception('Podcast camera preview failed');jobs[key]={'status':'failed','message':str(exc)[:400]}
        threading.Thread(target=run,daemon=True).start()
        return jsonify(jobs[key]),202
    @app.get('/api/v1/show/story/plan')
    def show_story_plan():
        p=project();path=p.artifacts_dir/'story_plan.json'
        return jsonify(project_id=str(p.folder),plan=json.loads(path.read_text()) if path.is_file() else None)

    @app.patch('/api/v1/show/story/plan/<int:index>')
    def show_story_moment(index):
        p=project();body=request.get_json(silent=True) or {};path=p.artifacts_dir/'story_plan.json'
        try:
            with lock:
                valid_project(p,body);data=json.loads(path.read_text())
                if jobs.get(str(p.folder)+'story',{}).get('status')=='running':return jsonify(error={'message':'Wait for this preview before changing the Story'}),409
                if body.get('revision')!=data.get('revision',0):return jsonify(error={'message':'The Story changed; reopen its decisions'}),409
                if body.get('path') not in {item['path'] for item in data['catalogue']}:raise ValueError('Choose a clip in this Story')
                before=dict(data['moments'][index]);data['moments'][index]={**before,'pictures':[{'path':body['path'],'interval':0}],'reason':str(body.get('reason') or 'Clip corrected in the review')[:500]}
                data.update(revision=int(data.get('revision',0))+1,dirty=True);atomic_write_json(path,data)
                history.record(p,'story.picture.corrected',before,data['moments'][index],evidence={'moment':index})
            return jsonify(project_id=str(p.folder),plan=data)
        except (OSError,ValueError,KeyError,IndexError,TypeError) as exc:return jsonify(error={'message':str(exc)}),400
    @app.get('/api/v1/show/story/program-preview')
    def show_story_preview_status():return jsonify(jobs.get(str(project().folder)+'story',{'status':'idle'}))
    @app.post('/api/v1/show/story/program-preview')
    def show_story_preview_start():
        p=project();body=request.get_json(silent=True) or {};key=str(p.folder)+'story'
        try:valid_project(p,body)
        except ValueError as exc:return jsonify(error={'message':str(exc)}),409
        with lock:
            if jobs.get(key,{}).get('status')=='running':return jsonify(jobs[key]),202
            path=p.artifacts_dir/'story_plan.json'
            if not path.is_file():return jsonify(error={'message':'Prepare this Story first'}),409
            data=json.loads(path.read_text());jobs[key]={'status':'running','progress':0}
        def run():
            try:
                from core.show_story_manual import render_plan
                render_plan(p,data,lambda value,message:jobs[key].update(progress=value))
                with lock:
                    data['dirty']=False;atomic_write_json(path,data);invalidate(p)
                    history.record(p,'story.previewed',after={'revision':data.get('revision',0),'moments':data['moments']},method='deterministic-render');jobs[key]={'status':'ready','revision':data.get('revision',0)}
            except Exception as exc:
                app.logger.exception('Story preview failed');jobs[key]={'status':'failed','message':str(exc)[:400]}
        threading.Thread(target=run,daemon=True).start();return jsonify(jobs[key]),202

    @app.post('/api/v1/show/captions/style')
    def show_captions_defaults():
        from core import show_caption_layout
        p=project();body=request.get_json(silent=True) or {}
        try:
            with lock:
                valid_project(p,body);before=show_caption_layout.load(p)
                data=show_caption_layout.save_defaults(p,body.get('changes') or {},body.get('revision',-1))
                history.record(p,'captions.style.changed',before.get('defaults',{}),data.get('defaults',{}));invalidate(p)
            return jsonify(revision=data['revision'],defaults=data.get('defaults',{}))
        except (ValueError,TypeError) as exc:return jsonify(error={'message':str(exc)}),409
