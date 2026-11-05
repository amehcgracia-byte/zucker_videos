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


def test_swap_never_reuses_one_cameras_person_coordinates_on_another(tmp_path,monkeypatch):
    from core import shot_review
    from core.project import create_project,atomic_write_json
    p=create_project('Swap',str(tmp_path/'Swap.zuckervid'))
    a,b=str(tmp_path/'fixed.mp4'),str(tmp_path/'moving.mp4')
    segments=[{'source_path':a,'clip_path':a,'master_start_sec':i,'clip_start_sec':i,'duration_sec':1,
               'flat_framing':{'zoom':1.8,'x':.2,'y':.5},'performer_view':'person-1','stage_performers':people()}
              for i in range(2)]
    atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'youtube','segments':segments})
    monkeypatch.setattr(shot_review,'load_coverage',lambda _: {'platform':'youtube'})
    monkeypatch.setattr(shot_review,'_project_candidate_pool',lambda *args,**kwargs:([{'path':b,'source_path':b,'clip_start_sec':0,'offset_sec':0}],'test'))
    monkeypatch.setattr(shot_review,'_candidate_covers_slot',lambda *args,**kwargs:True)
    monkeypatch.setattr(shot_review,'_candidate_has_subject',lambda *args,**kwargs:True)
    monkeypatch.setattr(shot_review,'review_items',lambda *args,**kwargs:[])
    result=shot_review.replace_slots(p,[0])
    assert result['replaced']==[0]
    saved=json.loads((p.artifacts_dir/'edit_plan.json').read_text())['segments']
    assert saved[0]['source_path']==b
    assert 'flat_framing' not in saved[0] and 'stage_performers' not in saved[0]
    assert saved[1]['flat_framing']==segments[1]['flat_framing']


def crop_edges(crop, zoom=1.):
    size=1/crop['zoom'];visible=size/zoom
    return crop['x']-visible/2,crop['y']-visible/2,crop['x']+visible/2,crop['y']+visible/2


def test_head_safe_crop_isolates_one_person_including_motion_and_image_edges():
    from core.stage_performers import subject_crop
    band=[{'id':str(i),'body_box':[x-.06,.30,x+.06,.9],
           'head_box':[x-.025,.30,x+.025,.40]}
          for i,x in enumerate((.08,.30,.52,.74,.93))]
    for person in band:
        crop=subject_crop(person,band)
        assert crop['zoom']>1.8
        for motion in (1.,1.06):
            left,top,right,bottom=crop_edges(crop,motion)
            hx1,hy1,hx2,hy2=person['head_box']
            assert left<=hx1 and right>=hx2 and top<hy1 and bottom>hy2
            assert left>=-1e-6 and top>=-1e-6 and right<=1.000001 and bottom<=1.000001
        left,top,right,bottom=crop_edges(crop)
        for other in band:
            if other is person:continue
            assert right<=other['body_box'][0] or left>=other['body_box'][2]


def test_inseparable_players_keep_whole_head_instead_of_forcing_isolation():
    from core.stage_performers import subject_crop
    person={'id':'one','body_box':[.35,.2,.6,.9],'head_box':[.43,.2,.55,.41]}
    neighbor={'id':'two','body_box':[.46,.2,.76,.9],'head_box':[.50,.2,.64,.40]}
    c=subject_crop(person,[person,neighbor]);l,t,r,b=crop_edges(c,1.06)
    assert l<=.43 and r>=.55 and t<.2 and b>.41


def test_detection_box_union_and_head_location_replace_body_midpoint():
    from core.stage_performers import _people
    cluster={'x':.5,'y':.65,'samples':[{'x':.5,'y':.65,'body':[.43,.25,.57,.95],
              'head':[.47,.25+delta,.53,.34+delta]} for delta in (0,.01,.02)]}
    p=_people([cluster])[0];l,t,r,b=crop_edges(p,1.06)
    assert p['zoom']>3 and t<.25 and b>.36
    assert p['y']<.65  # no more anchoring a close-up on the torso


def test_rendered_closeup_keeps_head_in_every_frame_without_neighbor_heads(tmp_path,monkeypatch):
    import subprocess
    import numpy as np
    from core.stages import export
    from core.stage_performers import subject_crop
    # White head belongs to centre musician; red/blue heads belong to neighbors.
    source=tmp_path/'people.mp4'
    graph='drawbox=x=48:y=30:w=24:h=20:color=red:t=fill,drawbox=x=148:y=30:w=24:h=20:color=white:t=fill,drawbox=x=248:y=30:w=24:h=20:color=blue:t=fill'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=black:s=320x180:r=30:d=1','-vf',graph,'-c:v','libx264','-crf','12','-threads','1',str(source)],check=True)
    band=[{'id':str(i),'head_box':[(x-12)/320,30/180,(x+12)/320,50/180],
           'body_box':[(x-20)/320,30/180,(x+20)/320,.95]} for i,x in enumerate((60,160,260))]
    crop=subject_crop(band[1],band);assert crop['zoom']>3
    segment={'flat_framing':crop}
    monkeypatch.setattr(export,'_target_size',lambda _: (320,180))
    monkeypatch.setattr(export,'_base_video_filter',lambda _: 'scale=320:180,setsar=1')
    filters=export._segment_filtergraph('youtube',1,{}, {},has_watermark=False,text_enabled=False,frame_count=30,segment=segment,motion_filter=export._motion_filter(segment,'youtube',1))
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(source),'-filter_complex',filters,'-map','[v]','-frames:v','30','-pix_fmt','rgb24','-f','rawvideo','-'])
    frames=np.frombuffer(raw,np.uint8).reshape(-1,180,320,3)
    for f in frames:
        white=(f.min(axis=2)>180)
        ys,xs=np.where(white)
        assert len(xs)>500 and ys.min()>3 and ys.max()<176 and xs.min()>3 and xs.max()<316
        assert not ((f[:,:,0]>160)&(f[:,:,1]<70)&(f[:,:,2]<70)).any()
        assert not ((f[:,:,2]>160)&(f[:,:,0]<70)&(f[:,:,1]<70)).any()
