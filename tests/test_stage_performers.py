import copy
import json
from types import SimpleNamespace

import pytest

from core.stage_performers import apply, activity_for, choose, detect, validate_activity, role


def people():
    return [{'id':str(i),'x':x,'y':.5,'zoom':1.8,'role':r}
            for i,(x,r) in enumerate(zip((.2,.5,.8),('bassist','pianist','saxophonist')))]


def test_rotation_has_wides_diverse_order_and_no_repeated_individual():
    history=[];sequence=[]
    for i in range(16):
        s={'master_start_sec':i*4,'duration_sec':4}
        apply(s,people(),i,history)
        sequence.append(s['performer_view'])
        assert 'flat_framing' in s and s['performer_evidence']=='visual_rotation_only'
    assert sequence[::4]==['general']*4
    assert set(sequence[1:4])=={'0','1','2'}
    assert sequence[1:4]!=sequence[5:8]
    assert all(a!=b for a,b in zip(history,history[1:]))


def test_silent_sax_excluded_unknown_piano_not_invented_silent_and_lead_prioritized():
    activity=validate_activity({'source':'separate_tracks','interval_sec':1,'tracks':[
        {'role':'sax','db':[-90]*8+[-15]*24}, {'role':'bass','db':[-40]*16+[-20]*16}]})
    active,lead=activity_for(activity,0,8)
    assert active=={'known':{'saxophonist','bassist'},'active':{'bassist'}}
    assert lead=='bassist'
    history=[]
    selected=[]
    for i in range(1,4):
        choice=choose(people(),i,history,active=active,lead=lead)
        selected.append(choice['role']);history.append(choice['id'])
    assert 'saxophonist' not in selected and 'pianist' in selected
    assert selected.count('bassist')==2
    assert activity_for(activity,40,44)==(None,None)
    assert role('horn')=='horn'  # brass/flute is NOT evidence of sax


@pytest.mark.parametrize('raw',[
    {'source':'master','interval_sec':1,'tracks':[]},
    {'source':'separate_tracks','interval_sec':1,'tracks':[{'role':'sax','db':[float('nan')]}]},
    {'source':'separate_tracks','interval_sec':1,'tracks':[{'role':'sax','db':[True]}]},
])
def test_reject_unusable_audio_evidence(raw):
    with pytest.raises(ValueError):validate_activity(raw)


def test_detector_cache_does_not_decode_again_and_cancel_propagates(tmp_path,monkeypatch):
    import core.stage_performers as m
    source=tmp_path/'proxy.mp4';source.touch();p=SimpleNamespace(cache_dir=tmp_path/'cache')
    calls=[]
    def run(*args):calls.append(1);return people()
    monkeypatch.setattr(m,'_detect',run)
    assert detect(p,{'path':str(source)},{})==people()
    assert detect(p,{'path':str(source)},{})==people() and len(calls)==1
    def cancel(*args):args[-1](0)
    monkeypatch.setattr(m,'_detect',cancel)
    def checkpoint(_):raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError,match='cancelled'):
        detect(p,{'path':str(source)},{'start_sec':2},checkpoint)


def test_settings_and_roles_survive_reload_preserve_manual_crops_and_light(tmp_path,monkeypatch):
    from core.project import create_project, atomic_write_json, load_project
    from core.stages import cut
    from server.api import create_app
    import server.edit_timeline as timeline
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    p=create_project('Band',str(tmp_path/'Band.zuckervid'))
    video=tmp_path/'fixed.mp4';video.touch()
    p.data['inputs']['videos']=[{'path':str(video),'probe':{}}]
    p.data['inputs']['master']={'path':'master.wav'}
    p.data['settings']['edit']['camera_types']={str(video):'fixed'};p.save()
    segments=[]
    for i in range(8):
        s={'source_path':str(video),'clip_path':str(video),'clip_start_sec':i,
           'master_start_sec':i,'duration_sec':1,'video_light':{'auto':True,'light':0,'shadows':0}}
        apply(s,people(),i,[])
        segments.append(s)
    segments[6]['manual_view']=True;segments[6]['flat_framing']={'zoom':1.3,'x':.4,'y':.5}
    atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'youtube','segments':segments})
    monkeypatch.setattr(cut,'load_coverage',lambda _: {'sources':[]})
    monkeypatch.setattr(timeline,'duration',lambda _:100)
    client=create_app(str(p.folder),dev=True).test_client()
    assert client.post('/api/v1/settings/stage-performers',json={'project_id':'other'}).status_code==409
    evidence={'source':'separate_tracks','interval_sec':1,'tracks':[{'role':'sax','db':[-90]*8}]}
    assert client.post('/api/v1/settings/stage-performers',json={'project_id':str(p.folder),'master':'other','activity':evidence}).status_code==409
    assert client.post('/api/v1/settings/stage-performers',json={'project_id':str(p.folder),'master':'master.wav','activity':evidence}).status_code==200
    doc=client.get('/api/v1/edit/shot?index=1').json
    assert len(doc['performers'])==3 and doc['choices'][0]['kind']=='fixed'
    response=client.patch('/api/v1/edit/shot',json={'project_id':str(p.folder),'revision':doc['revision'],
        'index':1,'choice':0,'edit_component':'framing','flat_framing':doc['framing'],
        'performer_roles':{'2':'saxophonist'}})
    assert response.status_code==200,response.json
    saved=json.loads((p.artifacts_dir/'edit_plan.json').read_text())['segments']
    assert saved[6]['flat_framing']==segments[6]['flat_framing']
    assert all(s['video_light']==segments[0]['video_light'] for s in saved)
    assert all(s.get('performer_view')!='2' for i,s in enumerate(saved) if i!=6)
    assert load_project(p.folder).data['settings']['edit']['performer_regions'][str(video)]


def test_multicam_plan_uses_people_before_notifying_thumbnails(monkeypatch):
    from core.stages.edit import _youtube_multicam_plan
    import core.stages.edit as edit
    source={'path':'fixed.mp4','source_path':'fixed.mp4','offset_sec':0,'duration_sec':80,
            'camera_role':'fixed_rear','stage_performers':people(),'probe':{'width':1920,'height':1080}}
    coverage={'platform':'youtube','window':{'start_sec':0,'duration_sec':40,'end_sec':40},'sources':[source]}
    seen=[]
    plan=_youtube_multicam_plan(coverage,{'beats_sec':list(range(40)),'bars_sec':list(range(0,40,4)),
               'tempo':120},{'edit':{'performer_rotation':True}},shot_ready=lambda i,s:seen.append(copy.deepcopy(s)))
    assert len(seen)>3
    assert any(s.get('flat_framing',{}).get('x')==.2 for s in seen)
    assert any(s.get('performer_view')=='general' for s in seen)
    assert all(s.get('stage_performers') for s in seen)
