"""Replay an explicitly reviewed Story edit; no remote director is called."""
import json,math
from pathlib import Path

def validate(plan, clips, duration=None):
    if not isinstance(plan,dict) or not isinstance(plan.get('moments'),list) or not 1<=len(plan['moments'])<=10000:raise ValueError('A reviewed Story plan needs its timed moments')
    if not 320<=int(plan.get('render_width') or 1280)<=1920 or not 180<=int(plan.get('render_height') or 720)<=1080:raise ValueError('Choose a normal video resolution')
    allowed=set(clips);last=0.0
    for moment in plan['moments']:
        a,b=float(moment['start']),float(moment['end'])
        if not math.isfinite(a+b) or abs(a-last)>.04 or b<=a or b-a>600:raise ValueError('Story moments must cover one continuous clock')
        if not isinstance(moment.get('pictures'),list) or not 1<=len(moment['pictures'])<=3:raise ValueError('Choose one to three pictures for every moment')
        for picture in moment['pictures']:
            if picture.get('path') not in allowed:raise ValueError('Choose a clip from this Story’s material')
        last=b
    if duration is not None and abs(last-duration)>.15:raise ValueError('The Story plan must cover its narration')
    return plan

def render_plan(project, plan, progress=lambda *args:None, cancel=lambda:None):
    from core.ffmpeg import ffprobe,tool_status
    from core import show_story
    ffmpeg=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
    catalogue=plan['catalogue'];resources=[]
    for item in catalogue:
        file=Path(item['path']);info=ffprobe(str(file));length=float((info.get('format') or {}).get('duration') or 0)
        resources.append({'path':str(file),'kind':'video','duration':length,'intervals':[{'start':0,'end':length}]})
    by_path={r['path']:i for i,r in enumerate(resources)}
    picks=[]
    for moment in plan['moments']:
        chosen=[]
        for picture in moment['pictures']:
            index=by_path[picture['path']];resource=resources[index]
            start=float(picture.get('start',0));end=float(picture.get('end',resource['duration']))
            if not math.isfinite(start+end) or start<0 or end<=start or end>resource['duration']+.12:raise ValueError('Story source interval is outside its clip')
            resource['intervals'].append({'start':start,'end':min(end,resource['duration'])});chosen.append((index,len(resource['intervals'])-1))
        picks.append(chosen)
    out=Path(plan['program']);out.parent.mkdir(parents=True,exist_ok=True)
    return show_story.render(Path(plan['narration']),plan['moments'],picks,resources,out,ffmpeg,width=int(plan.get('render_width') or 1280),height=int(plan.get('render_height') or 720),progress=lambda share:progress(20+int(share*32),'Rendering the reviewed Story plan'),cancel=cancel)

def build(project, manual, narration, material, progress, cancel):
    from core.ffmpeg import ffprobe
    from core.project import atomic_write_json
    from core.show_review_history import record
    duration=float((ffprobe(str(narration)).get('format') or {}).get('duration') or 0)
    validate(manual,[str(p) for p in material],duration)
    plan={**manual,'version':1,'revision':0,'dirty':False,'narration':str(narration),'program':str(project.folder/'assets/story/program.mp4'),'external_llm':False}
    catalogue=plan.get('catalogue') or [{'path':str(p),'kind':'other','shows':p.stem} for p in material]
    if {v['path'] for v in catalogue}!={str(p) for p in material}:raise ValueError('Story catalogue must describe its material exactly')
    plan['catalogue']=catalogue
    render_plan(project,plan,progress,cancel)
    atomic_write_json(project.artifacts_dir/'story_plan.json',plan)
    record(project,'story.plan.replayed',after=plan,method=plan.get('method','human-reviewed'),evidence={'external_llm':False,'reference_guided':bool(plan.get('reference_guided'))})
    return Path(plan['program'])
