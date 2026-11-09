"""Frame-aligned roll edits: a boundary moves, the programme clock stays fixed."""
import copy, hashlib, json, math, threading
from functools import lru_cache
from pathlib import Path
from flask import jsonify, request
from core.project import atomic_write_json
from core.show_review_history import record

LOCK = threading.RLock()
_FRAME_LOCKS = [threading.Lock() for _ in range(16)]
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
            for key in ('proxy_path','spherical_source_path','spherical_shot','reel_framing','projection','flat_framing','stage_performers','performer_view','performer_evidence'):
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
    @app.post('/api/v1/settings/video-light')
    def configure_video_light():
        body=request.get_json(silent=True) or {}
        try:
            with LOCK:
                p=project()
                if body.get('project_id')!=str(p.folder):return jsonify(error={'message':'Reopen this project'}),409
                if not isinstance(body.get('enabled'),bool):raise ValueError('Choose automatic light on or off')
                if app.config.get('MONTARA_EDIT_BUSY',lambda:False)():return jsonify(error={'message':'Wait for the current render'}),409
                p.data.setdefault('settings',{}).setdefault('edit',{}).setdefault('video_light',{})['enabled']=body['enabled']
                p.save();invalidate(p)
                return jsonify(enabled=body['enabled'])
        except (OSError,ValueError,TypeError) as e:return jsonify(error={'message':str(e)}),400

    @app.get('/api/v1/edit/native-recipe')
    def native_recipe():
        try:
            p,_,data=load('music')
            if request.args.get('project_id')!=str(p.folder):return jsonify(error={'message':'Reopen this project'}),409
            if (data.get('platform') or p.data.get('settings',{}).get('wizard',{}).get('platform'))!='youtube':raise ValueError('Native export is currently available for Stage')
            records=p.data.get('inputs',{}).get('videos',[])
            keys={str(r['path']):i for i,r in enumerate(records)}
            keys.update({str(r.get('normalized',{}).get('path')):i for i,r in enumerate(records) if r.get('normalized',{}).get('path')})
            segments=[]
            from core.video_light import settings as light_settings
            for raw in data.get('segments') or []:
                source=str(raw.get('source_path') or raw.get('clip_path') or '')
                index=keys.get(source)
                if index is None:raise ValueError('This edit contains a source without a registered original')
                item={k:raw[k] for k in ('clip_start_sec','duration_sec','master_start_sec','playback_speed','spherical_shot','motion','motion_enabled','flat_framing','projection','manual_view','insv_fov') if k in raw}
                item['source']=index
                light=light_settings(p,raw)
                if light is not None:item['video_light']=light
                segments.append(item)
            import subprocess,os
            sha=os.environ.get('MONTARA_ENGINE_SHA') or subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[1]),'rev-parse','HEAD'],text=True).strip()
            from core.native_export_policy import optional_fallbacks, optional_paths
            fallbacks=optional_fallbacks(p,data.get('segments') or [],records)
            used={s['source'] for s in segments} | {s['source'] for choices in fallbacks.values() for s in choices}
            optional=optional_paths(p,data.get("segments") or [])
            return jsonify(version=1,engine=sha,revision=revision(data),platform='youtube',sources=[{'key':i,'path':r['path'],'optional':str(r['path']) in optional,'role':'insert' if str(r['path']) in optional else 'camera'} for i,r in enumerate(records) if i in used],segments=segments,fallbacks=fallbacks)
        except (OSError,ValueError,KeyError) as e:return jsonify(error={'message':str(e)}),400

    @app.get('/api/v1/edit/native-source/<int:index>')
    def native_source(index):
        from flask import send_file
        try:
            p,_,data=load('music')
            if request.args.get('project_id')!=str(p.folder):return jsonify(error={'message':'Reopen this project'}),409
            if request.args.get('revision')!=revision(data):return jsonify(error={'message':'The edit changed. Retry the export.'}),409
            records=p.data.get('inputs',{}).get('videos',[])
            if not 0<=index<len(records):raise ValueError('Choose a camera source')
            target=Path(records[index]['path'])
            if target.name.lower().endswith('.small.mp4'):raise ValueError('Reconnect the original on your computer')
            return send_file(target,conditional=True)
        except (OSError,ValueError,KeyError) as e:return jsonify(error={'message':str(e)}),400

    @app.get('/api/v1/edit/native-master')
    def native_master():
        from flask import send_file
        try:
            p,_,data=load('music')
            if request.args.get('project_id')!=str(p.folder):return jsonify(error={'message':'Reopen this project'}),409
            if request.args.get('revision')!=revision(data):return jsonify(error={'message':'The edit changed. Retry the export.'}),409
            target=Path(p.data.get('inputs',{}).get('master',{}).get('path') or '')
            if not target.is_file():raise ValueError('The master is unavailable')
            return send_file(target,conditional=True)
        except (OSError,ValueError,KeyError) as e:return jsonify(error={'message':str(e)}),400

    @app.post('/api/v1/settings/stage-performers')
    def set_stage_performers():
        body=request.get_json(silent=True) or {}
        try:
            from core.stage_performers import validate_activity
            with LOCK:
                p=project()
                if p is None or body.get('project_id')!=str(p.folder):
                    return jsonify(error={'message':'Reopen this project'}),409
                if app.config.get('MONTARA_EDIT_BUSY',lambda:False)():
                    return jsonify(error={'message':'Wait for the current render'}),409
                activity=validate_activity(body.get('activity'))
                master=str((p.data.get('inputs',{}).get('master') or {}).get('path') or '')
                if activity and body.get('master')!=master:
                    return jsonify(error={'message':'Activity belongs to another song'}),409
                edit=p.data.setdefault('settings',{}).setdefault('edit',{})
                edit['performer_rotation']=True
                if activity:edit['instrument_activity']=activity
                else:edit.pop('instrument_activity',None)
                p.save()
                return jsonify(enabled=True,activity=bool(activity))
        except (OSError,ValueError,TypeError,AttributeError) as e:
            return jsonify(error={'message':str(e)}),400

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
            from core.video_light import settings as light_settings, DEFAULT
            return jsonify(performers=data['segments'][i].get('stage_performers') or [],performer_view=data['segments'][i].get('performer_view'),performer_evidence=data['segments'][i].get('performer_evidence'),playback_speed=float(data['segments'][i].get('playback_speed',1.0)),movement=bool(data['segments'][i].get('motion_enabled',True)),reel_contain=p.data.get('settings',{}).get('wizard',{}).get('reel_aspect')=='mix' or (p.data.get('settings',{}).get('wizard',{}).get('reel_aspect')=='mix_vertical_horizontal' and data['segments'][i].get('reel_mix_treatment')=='horizontal'),project_id=str(p.folder),revision=revision(data),platform=data.get('platform') or p.data.get('settings',{}).get('wizard',{}).get('platform'),pose=data['segments'][i].get('spherical_shot') or {},framing=data['segments'][i].get('flat_framing') or {'zoom':1,'x':.5,'y':.5},light=light_settings(p,choices[0]) or {**DEFAULT,'auto':False},choices=[{'label':label(c),'spherical':bool(c.get('spherical_shot')),'kind':camera_kind(p,c),'light':light_settings(p,c) or {**DEFAULT,'auto':False}} for c in choices])
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
                if not isinstance(body.get('apply_to_source',False),bool):raise ValueError('Choose whether to apply to the source video')
                component=body.get('edit_component','all')
                if component not in ('all','framing','light','movement'):raise ValueError('Choose framing, light or movement')
                if component=='light' and choice!=0:raise ValueError('Choose the camera in Framing first')
                if component == 'movement' and (choice != 0 or body.get('apply_to_source') or any(
                    key in body for key in ('video_light', 'performer_roles', 'remember_musician')
                )):
                    raise ValueError('Movement applies only to this shot')
                if component=='framing' and 'video_light' in body:raise ValueError('Save light separately from framing')
                before=data['segments'][i];s=copy.deepcopy(choices[choice])
                playback_speed=float(body.get('playback_speed',before.get('playback_speed',1.0)))
                if not math.isfinite(playback_speed) or not .5<=playback_speed<=2.0:raise ValueError('Choose a shot speed between 0.5x and 2x')
                s['playback_speed']=round(playback_speed,3)
                movement=body.get('movement',before.get('motion_enabled',True))
                if not isinstance(movement,bool):raise ValueError('Movement must be a checkbox value')
                s['motion_enabled']=movement
                if component != 'light':
                    for field in ('motion', 'spherical_shot'):
                        for key in ('continuity_duration_sec', 'continuity_offset_sec'):
                            (s.get(field) or {}).pop(key, None)
                if s.get('spherical_shot'):
                    if component == 'movement' or not movement:
                        s['spherical_shot']['runtime_motion_enabled']=movement
                if component in ('all','framing') and s.get('spherical_shot'):
                    pose=s['spherical_shot']
                    for key,lo,hi in [('yaw',-180,180),('pitch',-85,85),('fov',30,120)]:
                        value=float(body.get(key,pose.get(key) or (95 if key=='fov' else 0)))
                        if not math.isfinite(value) or not lo<=value<=hi:raise ValueError('Choose a valid camera viewpoint')
                        pose[key]=value
                    pose.update(projection_preset='normal',type='manual',shot_id='manual',label='Custom view',runtime_motion_enabled=movement);pose.pop('movement',None)
                    if movement:
                        pose['hold_motion_rate_deg_per_sec'] = (0.3 if i % 2 else -0.3)
                    else:
                        pose.pop('hold_motion_rate_deg_per_sec',None)
                    s['manual_view']=True
                elif component in ('all','framing'):
                    from core.flat_framing import validate
                    s['flat_framing']=validate(body.get('flat_framing') or {'zoom':1,'x':.5,'y':.5})
                    s['manual_view']=True
                if 'video_light' in body:
                    from core.video_light import validate as validate_light
                    light=validate_light(body['video_light'])
                    if body.get('light_scope','shot') not in ('camera','shot'):raise ValueError('Choose camera or shot lighting')
                    if body.get('light_scope')=='camera':
                        key=str(s.get('source_path') or s.get('clip_path') or '')
                        config=p.data.setdefault('settings',{}).setdefault('edit',{}).setdefault('video_light',{})
                        config.setdefault('cameras',{})[key]=light
                        # Camera-wide changes replace previous shot exceptions on this camera.
                        for item in data['segments']:
                            if str(item.get('source_path') or item.get('clip_path') or '')==key:item.pop('video_light',None)
                        s.pop('video_light',None)
                        p.save()
                    else:s['video_light']=light
                from core.stages.edit import _camera_id
                s['camera_id']=_camera_id(s);data['segments'][i]=s
                if body.get('apply_to_source'):
                    # Same file, not same manufacturer/camera id or filename.
                    source=Path(str(s.get('source_path') or s.get('clip_path') or '')).resolve()
                    for item in data['segments']:
                        other=Path(str(item.get('source_path') or item.get('clip_path') or '')).resolve()
                        if other != source:continue
                        keys=('video_light',) if component=='light' else ('flat_framing','spherical_shot','manual_view') if component=='framing' else ('flat_framing','spherical_shot','manual_view','video_light')
                        for key in keys:
                            if key in s:item[key]=copy.deepcopy(s[key])
                            else:item.pop(key,None)
                if body.get('performer_roles') is not None or body.get('remember_musician'):
                    from core.stage_performers import ROLES, role, apply as apply_performers
                    if choice!=0:raise ValueError('Save the camera choice before assigning musicians')
                    source=str(s.get('source_path') or s.get('clip_path') or '')
                    people=copy.deepcopy(before.get('stage_performers') or [])
                    assignments=body.get('performer_roles') or {}
                    if not isinstance(assignments,dict) or any(k not in {p['id'] for p in people} or v not in ROLES for k,v in assignments.items()):
                        raise ValueError('Choose detected musicians and supported instruments')
                    for person in people:person['role']=assignments.get(person['id'],person.get('role','unknown'))
                    if body.get('remember_musician'):
                        identity=role(body['remember_musician'])
                        if identity not in ROLES-{'unknown'} or s.get('spherical_shot'):
                            raise ValueError('Choose an instrument for this fixed-camera crop')
                        from core.camera_types import kind
                        if kind(p,s)!='fixed':raise ValueError('Remember musicians only on a fixed camera')
                        if len(people)>=8:raise ValueError('At most eight musicians per camera')
                        people.append({'id':f'custom-{len(people)+1}','role':identity,**s['flat_framing']})
                    edit=p.data.setdefault('settings',{}).setdefault('edit',{})
                    # Persist full regions as well as roles, for future reruns.
                    edit.setdefault('performers_by_source',{})[source]={v['id']:v['role'] for v in people}
                    edit.setdefault('performer_regions',{})[source]=people
                    history=[];take=0
                    for item in data['segments']:
                        if str(item.get('source_path') or item.get('clip_path') or '')!=source:continue
                        item['stage_performers']=copy.deepcopy(people)
                        if not item.get('manual_view'):
                            apply_performers(item,people,take,history,edit.get('instrument_activity'))
                        take+=1
                    p.save()
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
            from core.shot_review import _source_for, _spherical_analysis_source
            source=Path(_spherical_analysis_source(p,shot) if shot.get('spherical_shot') else _source_for(shot))
            at=float(shot.get('clip_start_sec') or 0)+float(shot['duration_sec'])/2
            f=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
            filters=[]
            if request.args.get('original')!='1':
                from core.video_light import resolved_profile, validate as validate_light
                from core.stages.export import _color_filter
                if request.args.get('light'):
                    shot={**shot,'video_light':validate_light(json.loads(request.args['light']))}
                color=resolved_profile(p,shot)
                if color:filters.append(_color_filter(color))
            filters.append('scale=640:-2')
            stat=source.stat()
            key=hashlib.sha256(json.dumps([str(source),stat.st_size,stat.st_mtime_ns,at,filters]).encode()).hexdigest()
            target=p.cache_dir/'camera_frames'/f'{key}.jpg'
            with _FRAME_LOCKS[int(key[:8],16)%len(_FRAME_LOCKS)]:
                if not target.is_file():
                    image=subprocess.check_output([f,'-v' ,'error','-nostdin','-ss',str(at),'-i',str(source),'-frames:v','1','-vf',','.join(filters),'-f','image2pipe','-vcodec','mjpeg','-threads','1','-'],timeout=30)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    temporary=target.with_suffix('.tmp')
                    temporary.write_bytes(image);temporary.replace(target)
            return send_file(target,mimetype='image/jpeg')
        except (OSError,ValueError,KeyError,IndexError,StopIteration,subprocess.SubprocessError) as e:return jsonify(error={'message':'Could not load this camera frame'}),400
