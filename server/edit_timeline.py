"""Frame-aligned roll edits: a boundary moves, the programme clock stays fixed."""
import copy, hashlib, json, math, threading
from functools import lru_cache
from pathlib import Path
from flask import jsonify, request
from core.project import atomic_write_json
from core.show_review_history import record

LOCK = threading.RLock()
MIN = .2

@lru_cache(maxsize=256)
def _duration(path, size, mtime):
    from core.ffmpeg import ffprobe
    return float(ffprobe(path)['format']['duration'])

def one_to_one(moment):
    pictures=moment.get('pictures') or []
    if len(pictures)!=1:return None
    pic=pictures[0]
    if 'end' not in pic:return None
    return pic if abs(float(pic['end'])-float(pic.get('start',0))+float(pic.get('pad_before',0))+float(pic.get('pad_after',0))-(float(moment['end'])-float(moment['start'])))<.08 else None

def resize_picture(pic,length,edge):
    total=duration({'clip_path':pic['path']})
    if edge=='end':a=float(pic.get('start',0))-float(pic.get('pad_before',0));b=a+length
    else:b=float(pic['end'])+float(pic.get('pad_after',0));a=b-length
    start=max(0.,min(total-1/30,a));end=max(start+1/30,min(total,b))
    before=min(max(0.,-a),max(0.,length-(end-start)))
    pic.update(start=round(start,6),end=round(end,6),pad_before=round(before,6),pad_after=round(max(0.,length-(end-start)-before),6),keep_speed=True)

def duration(segment):
    path=next((Path(segment[k]) for k in ('clip_path','source_path','proxy_path') if segment.get(k) and Path(segment[k]).is_file()),Path(segment.get('clip_path') or segment.get('source_path') or ''))
    stat=path.stat()
    return _duration(str(path),stat.st_size,stat.st_mtime_ns)

def document_path(p, kind):
    names={'show':'show_plan.json','podcast':'show_podcast.json','story':'story_plan.json','music':'edit_plan.json'}
    if kind not in names:raise ValueError('Choose an edit mode')
    if kind=='podcast':
        from core.show_podcast import setup_path
        return setup_path(p)
    return p.artifacts_dir/names[kind]

def revision(data):return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:20]

def snapshot(p,kind,data):
    shots=[];clock=0.;fps=int(data.get('fps') or 30)
    raw=data.get('moments' if kind=='story' else 'segments' if kind=='music' else 'shots') or []
    for i,s in enumerate(raw):
        if kind=='show':a=clock;b=a+(s['end_frame']-s['start_frame'])/fps;clock=b;label=f"Shot {i+1} · {float(s.get('zoom') or 1):.1f}×"
        elif kind=='music':
            from core.camera_types import kind as camera_kind
            a=float(s['master_start_sec']);b=a+float(s['duration_sec']);label=f'{i+1:02d} · '+{'fixed':'Fixed','moving':'Moving','360':'360'}[camera_kind(p,s)]
            if s.get('spherical_shot'):label+=' · '+str(s['spherical_shot'].get('label') or s['spherical_shot'].get('type') or 'View')
        else:a=float(s['start']);b=float(s['end']);label=', '.join(s.get('shows') or []) if kind=='podcast' else Path((s.get('pictures') or [{}])[0].get('path') or '').stem
        shots.append({'index':i,'start':a,'end':b,'label':label or f'Shot {i+1}'})
    bounds=[]
    for i in range(1,len(shots)):
        left,right=shots[i-1],shots[i];lo=left['start']+MIN;hi=right['end']-MIN
        if kind=='music':
            l,r=raw[i-1],raw[i];at=right['start']
            lo=max(lo,at-float(r['clip_start_sec']));hi=min(hi,at+duration(l)-float(l['clip_start_sec'])-float(l['duration_sec']))
        bounds.append({'index':i,'at':right['start'],'min':lo,'max':hi})
    return {'project_id':str(p.folder),'kind':kind,'revision':revision(data),'shots':shots,'boundaries':bounds,'fps':fps}

def roll(data,kind,index,at):
    raw=data['moments' if kind=='story' else 'segments' if kind=='music' else 'shots'];l,r=raw[index-1:index+1]
    if kind=='show':
        fps=int(data.get('fps') or 30);old=sum((s['end_frame']-s['start_frame'])/fps for s in raw[:index]);delta=round((at-old)*fps)
        l['end_frame']+=delta;r['start_frame']+=delta
        l['end']=l['end_frame']/fps;r['start']=r['start_frame']/fps
        # Words are reassigned from the immutable source clock after the roll.
        for s in (l,r):s['words']=[w['index'] for w in data.get('words',[]) if s['start_frame']/fps<=float(w['start'])<s['end_frame']/fps and 'index' in w]
    elif kind=='music':
        delta=at-float(r['master_start_sec']);l['duration_sec']=round(float(l['duration_sec'])+delta,6);r['duration_sec']=round(float(r['duration_sec'])-delta,6);r['master_start_sec']=at;r['clip_start_sec']=round(float(r['clip_start_sec'])+delta,6)
        for s in (l,r):
            for key in ('frame_count','master_end_sec'):s.pop(key,None)
    else:
        if kind=='story':
            lp,rp=one_to_one(l),one_to_one(r);delta=at-float(r['start'])
            if lp:resize_picture(lp,at-float(l['start']),'end')
            if rp:resize_picture(rp,float(r['end'])-at,'start')
        l['end']=at;r['start']=at;data.update(dirty=True,revision=int(data.get('revision',0))+1)

def register_edit_timeline(app,project,invalidate):
    def load(kind):
        p=project();path=document_path(p,kind);return p,path,json.loads(path.read_text())
    @app.get('/api/v1/edit/timeline')
    def get_timeline():
        try:
            p,_,data=load(request.args.get('kind','show'));return jsonify(snapshot(p,request.args.get('kind','show'),data))
        except (OSError,ValueError,KeyError) as e:return jsonify(error={'message':str(e)}),400
    @app.patch('/api/v1/edit/timeline')
    def change_timeline():
        body=request.get_json(silent=True) or {};kind=body.get('kind','show')
        try:
            with LOCK:
                p,path,data=load(kind)
                if app.config.get('MONTARA_EDIT_BUSY',lambda:False)():return jsonify(error={'message':'Wait for the current render to finish.'}),409
                if body.get('project_id')!=str(p.folder) or body.get('revision')!=revision(data):return jsonify(error={'message':'The edit changed. Reopen this timeline.'}),409
                # Programme renders capture their plan; never allow a racing write to replace a newer edit.
                if kind in ('podcast','story') and app.config.get('MONTARA_PROGRAM_BUSY',lambda *_:False)(p,kind):return jsonify(error={'message':'Wait for the preview to finish.'}),409
                doc=snapshot(p,kind,data);i=int(body['index']);bound=next((b for b in doc['boundaries'] if b['index']==i),None)
                at=float(body['at'])
                if not math.isfinite(at):raise ValueError('Choose a finite time')
                at=round(at*doc['fps'])/doc['fps']
                if not math.isfinite(at) or bound is None or at<bound['min']-1e-7 or at>bound['max']+1e-7:raise ValueError('Keep this cut between its neighbouring shots and inside the source footage')
                before=copy.deepcopy(data);roll(data,kind,i,at);atomic_write_json(path,data)
                key='moments' if kind=='story' else 'segments' if kind=='music' else 'shots'
                invalidate(p);record(p,f'{kind}.cut.moved',before={'shots':before[key][i-1:i+1]},after={'shots':data[key][i-1:i+1]},evidence={'boundary':i,'at':at,'revision':revision(data)})
                return jsonify(snapshot(p,kind,data))
        except (OSError,ValueError,KeyError,TypeError,IndexError) as e:return jsonify(error={'message':str(e)}),400

    def music_choices(p,data,index):
        from core.shot_review import _review_candidate_pool,_candidate_covers_slot
        from core.stages.cut import load_coverage
        s=data['segments'][index];platform=data.get('platform') or p.data.get('settings',{}).get('wizard',{}).get('platform') or 'reel'
        try:pool,_=_review_candidate_pool(load_coverage(p),data['segments'],s,platform)
        except (OSError,ValueError,KeyError):pool=[]
        out=[dict(s)];seen={(str(s.get('clip_path')),json.dumps(s.get('spherical_shot'),sort_keys=True))}
        for c in pool:
            if c.get('spherical_shot') and c.get('projection') not in ('equirect','raw_insv'):continue
            if c.get('spherical_shot') or c.get('projection') in ('equirect','raw_insv'):
                record=next((r for r in p.data.get('inputs',{}).get('videos',[]) if r.get('path') in (c.get('source_path'),c.get('path'),c.get('clip_path'))),None)
                if record and (p.data.get('settings',{}).get('edit',{}).get('camera_types') or {}).get(record['path'])!='360' and (record.get('probe') or {}).get('projection') not in ('equirect','raw_insv') and record.get('projection') not in ('equirect','raw_insv'):continue
            if not _candidate_covers_slot(c,s,platform):continue
            path=c.get('path') or c.get('clip_path') or c.get('source_path')
            if not path:continue
            candidate={**s,'clip_path':path,'source_path':c.get('source_path') or path,'filename':c.get('filename') or Path(path).name,'clip_start_sec':float(s['master_start_sec'])-float(c.get('offset_sec') or 0) if platform=='youtube' else float(c.get('clip_start_sec') or 0),'clip_offset_sec':float(c.get('offset_sec') or 0)}
            for key in ('proxy_path','spherical_source_path','spherical_shot','reel_framing','projection','flat_framing'):
                candidate.pop(key,None)
                if c.get(key) is not None:candidate[key]=c[key]
            if candidate.get('projection') in ('equirect','raw_insv') and not candidate.get('spherical_shot'):candidate['spherical_shot']={'type':'manual','yaw':0,'pitch':0,'fov':95,'projection_preset':'normal'}
            key=(path,json.dumps(candidate.get('spherical_shot'),sort_keys=True))
            if key in seen:continue
            try:
                if candidate['clip_start_sec']<0 or candidate['clip_start_sec']+float(s['duration_sec'])>duration(candidate)+.02:continue
            except (OSError,ValueError):continue
            seen.add(key);out.append(candidate)
        return out
    @app.get('/api/v1/edit/shot')
    def get_music_shot():
        try:
            p,_,data=load('music');i=int(request.args['index'])
            if not 0<=i<len(data['segments']):raise ValueError('Choose an existing shot')
            choices=music_choices(p,data,i)
            from core.camera_types import kind as camera_kind
            records=p.data.get('inputs',{}).get('videos',[])
            def label(c):
                number=next((j+1 for j,r in enumerate(records) if r.get('path')==c.get('source_path')),None)
                return ('Camera '+str(number).zfill(2)+' · ' if number is not None else '')+{'360':'360','fixed':'Fixed','moving':'Moving'}[camera_kind(p,c)]+(' · '+str(c['spherical_shot'].get('label') or 'View') if c.get('spherical_shot') else '')
            return jsonify(project_id=str(p.folder),revision=revision(data),pose=data['segments'][i].get('spherical_shot') or {},framing=data['segments'][i].get('flat_framing') or {'zoom':1,'x':.5,'y':.5},choices=[{'label':label(c),'spherical':bool(c.get('spherical_shot'))} for c in choices])
        except (OSError,ValueError,KeyError,IndexError) as e:return jsonify(error={'message':str(e)}),400
    @app.patch('/api/v1/edit/shot')
    def patch_music_shot():
        body=request.get_json(silent=True) or {}
        try:
            with LOCK:
                p,path,data=load('music')
                if app.config.get('MONTARA_EDIT_BUSY',lambda:False)():return jsonify(error={'message':'Wait for the current render to finish.'}),409
                if body.get('project_id')!=str(p.folder) or body.get('revision')!=revision(data):return jsonify(error={'message':'The edit changed. Reopen this shot.'}),409
                i=int(body['index']);choice=int(body['choice'])
                if not 0<=i<len(data['segments']):raise ValueError('Choose an existing shot')
                choices=music_choices(p,data,i)
                if not 0<=choice<len(choices):raise ValueError('Choose one of this shot’s cameras')
                before=data['segments'][i];s=copy.deepcopy(choices[choice])
                if s.get('spherical_shot'):
                    pose=s['spherical_shot']
                    for key,lo,hi in [('yaw',-180,180),('pitch',-85,85),('fov',30,120)]:
                        value=float(body.get(key,pose.get(key) or (95 if key=='fov' else 0)))
                        if not math.isfinite(value) or not lo<=value<=hi:raise ValueError('Choose a valid camera viewpoint')
                        pose[key]=value
                    pose.update(projection_preset='normal',type='manual',shot_id='manual',label='Custom view',runtime_motion_enabled=False);pose.pop('movement',None);s['manual_view']=True
                else:
                    from core.flat_framing import validate
                    s['flat_framing']=validate(body.get('flat_framing') or {'zoom':1,'x':.5,'y':.5})
                from core.stages.edit import _camera_id
                s['camera_id']=_camera_id(s);data['segments'][i]=s
                for key in ('camera_usage','spherical_shot_usage'):data.pop(key,None)
                atomic_write_json(path,data)
                record(p,'music.camera.corrected',before,s,evidence={'shot':i})
                return jsonify(snapshot(p,'music',data))
        except (OSError,ValueError,KeyError,TypeError,IndexError) as e:return jsonify(error={'message':str(e)}),400

    @app.get('/api/v1/edit/shot/frame')
    def music_shot_frame():
        import subprocess,io
        from flask import send_file
        from core.ffmpeg import tool_status
        try:
            p,_,data=load('music')
            if request.args.get('revision')!=revision(data):return jsonify(error={'message':'Reopen this camera shot'}),409
            i=int(request.args['index']);choice=int(request.args.get('choice',0))
            if i<0 or choice<0:raise ValueError('Choose a camera shot')
            shot=music_choices(p,data,i)[choice]
            source=next(Path(shot[k]) for k in ('source_path','clip_path','proxy_path') if shot.get(k) and Path(shot[k]).is_file())
            at=float(shot.get('clip_start_sec') or 0)+float(shot['duration_sec'])/2
            f=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
            image=subprocess.check_output([f,'-v','error','-nostdin','-ss',str(at),'-i',str(source),'-frames:v','1','-vf','scale=640:-2','-f','image2pipe','-vcodec','mjpeg','-threads','1','-'],timeout=30)
            return send_file(io.BytesIO(image),mimetype='image/jpeg')
        except (OSError,ValueError,KeyError,IndexError,StopIteration,subprocess.SubprocessError) as e:return jsonify(error={'message':'Could not load this camera frame'}),400
