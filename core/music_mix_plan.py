"""Reviewed, segment-addressed Backstage music plans. No generation during export."""
import hashlib,json,math,threading
from pathlib import Path
from core.project import atomic_write_json
from core import music_library

LOCK=threading.RLock()
MODES={'narrador_solo','musica_bajo_narrador','solo_video','musica_solo'}


def segments(project):
    from core.stages.backstage import selected_segments
    path=project.artifacts_dir/'backstage_edit.json'
    if not path.is_file():raise ValueError('Prepare the Backstage paper edit first')
    return selected_segments(json.loads(path.read_text()).get('segments',[]))


def signature(items):
    return hashlib.sha256(json.dumps([{k:r.get(k) for k in ('id','source_path','clip_start_sec','duration_sec')} for r in items],sort_keys=True).encode()).hexdigest()


def path(project):return project.artifacts_dir/'music_mix_plan.json'


def approved_path(project):return project.artifacts_dir/'music_mix_approved.json'


def load(project):
    try:return json.loads(path(project).read_text())
    except (OSError,ValueError):return {'revision':0,'status':'empty','segments':[]}


def available_tracks(project):
    catalog={r['id']:r for r in music_library.local()}
    try:approved=json.loads(approved_path(project).read_text())
    except (OSError,ValueError):approved={}
    for id,record in approved.get('tracks',{}).items():
        p=Path(record.get('path',''))
        if p.is_file() and p.resolve().is_relative_to((project.folder/'assets/music').resolve()):catalog.setdefault(id,record)
    return catalog


def validate(rows,items,catalog):
    if not isinstance(rows,list) or len(rows)!=len(items):raise ValueError('Provide one mix choice per kept segment')
    known={r['id']:r for r in items};result=[];seen=set()
    for r in rows:
        id=r.get('segment_id',r.get('segmento_id'));mode=r.get('mode',r.get('modo'))
        if mode=='mix_con_ducking':mode='musica_bajo_narrador'
        if id not in known or id in seen or mode not in MODES:raise ValueError('The mix plan has an unknown, duplicate or invalid segment')
        seen.add(id);track=r.get('track_id',r.get('pista_id'))
        if mode in ('musica_solo','musica_bajo_narrador') and track not in catalog:raise ValueError('Choose a library track for each music segment')
        if mode in ('narrador_solo','solo_video'):track=None
        gain=float(r.get('gain_db',r.get('ganancia_db',-18)));fade_in=float(r.get('fade_in_s',0));fade_out=float(r.get('fade_out_s',0));duration=float(known[id]['duration_sec'])
        if not all(math.isfinite(v) for v in (gain,fade_in,fade_out)) or not -60<=gain<=6 or min(fade_in,fade_out)<0 or max(fade_in,fade_out)>min(10,duration/2):raise ValueError('Use -60 to +6 dB and fades no longer than half the segment')
        result.append(dict(segment_id=id,mode=mode,track_id=track,gain_db=gain,fade_in_s=fade_in,fade_out_s=fade_out))
    return result


def propose(project):
    from core.narrative_scoring import _provider_config,_completion
    from openai import OpenAI
    items=segments(project);catalog=music_library.local();config=_provider_config()
    if not config['key']:raise ValueError('Add a narrative API key in Connectors, or create a manual mix plan')
    compact=[{'id':r['id'],'duration':r['duration_sec'],'text':r.get('text_original') or r.get('text') or r.get('english_text') or ' '.join(s.get('text_original') or s.get('text') or '' for s in r.get('story_sequences',[])),
              'existing_music_policy':r.get('background_music_policy')} for r in items]
    sounds=[{k:r.get(k) for k in ('id','name','duration','license')} for r in catalog[:100]]
    client=OpenAI(api_key=config['key'],base_url=config['base_url'],timeout=90,max_retries=1)
    response=_completion(client,config,model=config['model'],messages=[{'role':'system','content':'Plan audio for this exact documentary paper edit. Return JSON only as {segments:[{segment_id,mode,track_id,gain_db,fade_in_s,fade_out_s}]}. One row per supplied segment. Modes: narrador_solo, musica_bajo_narrador, solo_video, musica_solo. Use catalog IDs only; no URLs or invented assets. Preserve meaningful dialogue and existing source music. Prefer source-only when uncertain. Gains -60 to +6 dB. Fades <= half the segment. No speech generation.'},{'role':'user','content':json.dumps({'segments':compact,'library':sounds},ensure_ascii=False)}],max_tokens=6000,temperature=.2,response_format={'type':'json_object'})
    raw=json.loads(response.choices[0].message.content or '{}')
    return validate(raw.get('segments'),items,{r['id']:r for r in catalog})


def save(project,rows,*,approve=False,revision=None,expected_signature=None):
    with LOCK:
        current=load(project);items=segments(project)
        if revision is not None and int(revision)!=current['revision']:raise ValueError('The mix plan changed in another window; reopen it')
        if expected_signature and signature(items)!=expected_signature:raise ValueError('The paper edit changed while planning; generate the plan again')
        catalog=available_tracks(project);rows=validate(rows,items,catalog)
        tracks={}
        if approve:
            ids={r['track_id'] for r in rows if r['track_id']}
            global_ids={r['id'] for r in music_library.local()}
            tracks=music_library.project_tracks(project,ids & global_ids)
            tracks.update({id:catalog[id] for id in ids-global_ids})
        result={'version':1,'revision':current['revision']+1,'status':'approved' if approve else 'draft',
                'signature':signature(items),'segments':rows,'tracks':tracks}
        atomic_write_json(path(project),result)
        if approve:
            atomic_write_json(approved_path(project),result)
            write_credits(project,result)
        return result


def for_export(project,items):
    try:doc=json.loads(approved_path(project).read_text())
    except (OSError,ValueError):doc=load(project)
    if doc.get('status')!='approved':return None
    if doc.get('signature')!=signature(items):raise ValueError('The paper edit changed after the audio mix was approved. Review the music plan again.')
    validate(doc['segments'],items,doc['tracks'])
    for track in doc['tracks'].values():
        p=Path(track['path'])
        if not p.is_file() or not p.resolve().is_relative_to((project.folder/'assets/music').resolve()):raise ValueError('A prepared music track is missing from this project')
    write_credits(project,doc)
    return doc


def write_credits(project,doc):
    lines=['Music and sound credits','']
    for record in doc.get('tracks',{}).values():
        lines.append(record.get('attribution') or f"{record['name']} — {record.get('artist','')} ({record.get('license','User supplied')})")
        if record.get('preview_only'):lines.append('Freesound preview audio; original download requires OAuth2.')
    target=project.folder/'credits.txt';pending=target.with_suffix('.pending');pending.write_text('\n'.join(lines)+'\n',encoding='utf-8');pending.replace(target)


def segment_graph(items,doc,start_index):
    """Inputs + graph clauses + output labels. Keeps Backstage in one FFmpeg pass."""
    rows={r['segment_id']:r for r in doc['segments']};inputs=[];filters=[];labels=[]
    for i,item in enumerate(items):
        row=rows[item['id']];mode=row['mode'];source=f'a{i}'
        if mode in ('narrador_solo','solo_video'):labels.append(source);continue
        track=doc['tracks'][row['track_id']];length=float(item['duration_sec']);available=float(track.get('duration') or 0)
        if available<=0:raise ValueError('The selected music track needs a measured duration; import it again')
        index=start_index+len(inputs)//2;inputs.extend(['-i',track['path']]);prefix=f'mp{i}'
        overlap=min(1.,available/3)
        repeats=max(1,math.ceil((length-overlap)/(available-overlap)))
        if repeats>128:raise ValueError('Choose a longer music track for this segment')
        base=f'[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=duration={available:.6f},asetpts=PTS-STARTPTS'
        if repeats>1:
            filters.append(base+f',asplit={repeats}'+''.join(f'[{prefix}s{n}]' for n in range(repeats)))
            last=f'{prefix}s0'
            for n in range(1,repeats):
                out=f'{prefix}loop{n}';filters.append(f'[{last}][{prefix}s{n}]acrossfade=d={overlap:.6f}:c1=tri:c2=tri[{out}]');last=out
        else:filters.append(base+f'[{prefix}sample]');last=f'{prefix}sample'
        filters.append(f'[{last}]atrim=duration={length:.6f},afade=t=in:d={row["fade_in_s"]:.6f},afade=t=out:st={max(0,length-row["fade_out_s"]):.6f}:d={row["fade_out_s"]:.6f},volume={row["gain_db"]:.3f}dB[{prefix}music]')
        if mode=='musica_bajo_narrador':
            filters.append(f'[{source}]asplit=2[{prefix}voice][{prefix}side]')
            filters.append(f'[{prefix}music][{prefix}side]sidechaincompress=threshold=0.025:ratio=6:attack=20:release=250[{prefix}duck]')
            filters.append(f'[{prefix}voice][{prefix}duck]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95:level=0:latency=1[{prefix}out]')
        else:
            filters.append(f'[{source}]volume=0[{prefix}silent]')
            filters.append(f'[{prefix}silent][{prefix}music]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95:level=0:latency=1[{prefix}out]')
        labels.append(prefix+'out')
    return inputs,filters,labels
