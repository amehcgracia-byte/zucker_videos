import json,copy
import pytest
from core.project import create_project,atomic_write_json
from server.api import create_app
from server.edit_timeline import roll,snapshot
from core import show_podcast as pod

@pytest.mark.parametrize('kind',['show','podcast','story','music'])
def test_roll_preserves_clock_and_adjacent_shots(kind,tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Cuts',str(tmp_path/'Cuts.zuckervid'))
 if kind=='show':data={'fps':30,'edited_duration':4,'shots':[{'start_frame':0,'end_frame':60,'zoom':1},{'start_frame':90,'end_frame':150,'zoom':2}]};name='show_plan.json'
 elif kind=='music':
  import server.edit_timeline as module
  monkeypatch.setattr(module,'duration',lambda _:20)
  data={'segments':[{'master_start_sec':0,'duration_sec':2,'clip_start_sec':3,'clip_path':'a'},{'master_start_sec':2,'duration_sec':2,'clip_start_sec':5,'clip_path':'b'}]};name='edit_plan.json'
 else:data={'moments' if kind=='story' else 'shots':[{'start':0,'end':2},{'start':2,'end':4}],'revision':0};name='story_plan.json' if kind=='story' else pod.setup_path(p).name
 atomic_write_json(p.artifacts_dir/name,data);c=create_app(str(p.folder),dev=True).test_client();doc=c.get('/api/v1/edit/timeline?kind='+kind).json
 body={'kind':kind,'project_id':str(p.folder),'revision':doc['revision'],'index':1,'at':2.5}
 result=c.patch('/api/v1/edit/timeline',json=body);assert result.status_code==200,result.json
 shots=result.json['shots'];assert shots[0]['end']==shots[1]['start']==2.5;assert shots[-1]['end']==4
 assert c.patch('/api/v1/edit/timeline',json=body).status_code==409
 fresh={**body,'revision':result.json['revision']};assert c.patch('/api/v1/edit/timeline',json={**fresh,'at':3.99}).status_code==400
 assert c.patch('/api/v1/edit/timeline',json={**fresh,'at':float('nan')}).status_code==400
 assert c.patch('/api/v1/edit/timeline',json={**fresh,'project_id':'other'}).status_code==409
 saved=json.loads((p.artifacts_dir/name).read_text())
 if kind=='show':assert saved['shots'][1]['start_frame']-saved['shots'][0]['end_frame']==30
 if kind=='music':assert saved['segments'][1]['clip_start_sec']==5.5
 if kind in ('podcast','story'):assert saved['dirty'] and saved['revision']==1
 assert c.get('/api/v1/show/decisions').json['items'][-1]['action']==kind+'.cut.moved'

def test_roll_moves_real_camera_switch(tmp_path):
 import subprocess,numpy as np
 from dataclasses import asdict
 from core.ffmpeg import tool_status
 f=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
 for color in ['red','blue']:subprocess.run([f,'-v','error','-y','-f','lavfi','-i',f'color={color}:s=160x90:r=30:d=2','-c:v','libx264','-threads','1',str(tmp_path/(color+'.mp4'))],check=True)
 data={'people':[{'name':'One'}],'cameras':[{'path':str(tmp_path/(c+'.mp4')),'kind':'flat','people':[0]} for c in ['red','blue']],'shots':[{'start':0,'end':1,'view':0},{'start':1,'end':2,'view':0}]}
 views=pod.views(data['cameras'],1)
 for shot,camera in zip(data['shots'],[0,1]):shot['override']=asdict(next(v for v in views if v.camera==camera))
 roll(data,'podcast',1,1.5);out=pod.render(data,data['shots'],np.zeros(96000,np.float32),tmp_path/'out.mp4',f,width=160,height=90)
 for at,channel in [(1.2,0),(1.7,2)]:
  raw=subprocess.check_output([f,'-v','error','-ss',str(at),'-i',str(out),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-']);img=np.frombuffer(raw,np.uint8).reshape(90,160,3);assert img[:,:,channel].mean()>180

def test_story_roll_preserves_interview_speed(tmp_path,monkeypatch):
 import server.edit_timeline as module
 monkeypatch.setattr(module,'duration',lambda _:20)
 data={'moments':[{'start':0,'end':3,'pictures':[{'path':'a','start':1,'end':4}]},{'start':3,'end':6,'pictures':[{'path':'b','start':2,'end':5}]}]}
 roll(data,'story',1,3.5)
 for moment in data['moments']:
  picture=moment['pictures'][0];assert picture['end']-picture['start']==moment['end']-moment['start']
 assert data['moments'][1]['pictures'][0]['start']==2.5

def test_music_boundaries_stop_at_source_limits(tmp_path,monkeypatch):
 import server.edit_timeline as module
 monkeypatch.setattr(module,'duration',lambda _:5)
 data={'segments':[{'master_start_sec':0,'duration_sec':2,'clip_start_sec':2,'clip_path':'a'},{'master_start_sec':2,'duration_sec':2,'clip_start_sec':.1,'clip_path':'b'}]}
 p=create_project('Bounds',str(tmp_path/'Bounds.zuckervid'));doc=snapshot(p,'music',data);assert doc['boundaries'][0]['min']==1.9;assert doc['boundaries'][0]['max']==3

def test_music_manual_view_survives_preview_and_export(tmp_path,monkeypatch):
 from core.stages.export import _apply_saved_spherical_landmarks
 from core.shot_review import _review_segment
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('View',str(tmp_path/'View.zuckervid'));p.data['settings']['spherical_landmarks']={'singer':{'yaw':88,'pitch':20,'fov':95}}
 shot={'manual_view':True,'source_path':'sphere','spherical_shot':{'type':'singer','yaw':-33,'pitch':4,'fov':66}}
 assert _review_segment(p,shot)['spherical_shot']['yaw']==-33
 assert _apply_saved_spherical_landmarks(p,[shot])[0]['spherical_shot']['yaw']==-33

def test_story_can_move_past_clip_edge_without_changing_speed(tmp_path,monkeypatch):
 import server.edit_timeline as module
 monkeypatch.setattr(module,'duration',lambda _:2)
 data={'moments':[{'start':0,'end':2,'pictures':[{'path':'a','start':0,'end':2}]},{'start':2,'end':4,'pictures':[{'path':'b','start':0,'end':2}]}]}
 roll(data,'story',1,2.5);left,right=[m['pictures'][0] for m in data['moments']]
 assert left['end']==2 and left['pad_after']==.5 and left['keep_speed'];assert right['start']==.5
 roll(data,'story',1,1.5);left,right=[m['pictures'][0] for m in data['moments']]
 assert left['end']==1.5 and left['pad_after']==0;assert right['start']==0 and right['pad_before']==.5

def test_story_held_edge_renders_requested_duration(tmp_path):
 import subprocess
 from core.show_story import _piece
 from core.ffmpeg import ffprobe,tool_status
 f=str(tool_status().get('ffmpeg_path') or 'ffmpeg');source=tmp_path/'red.mp4';out=tmp_path/'held.mp4'
 subprocess.run([f,'-v','error','-y','-f','lavfi','-i','color=red:s=160x90:r=30:d=1','-c:v','libx264','-threads','1',str(source)],check=True)
 _piece({'path':str(source),'kind':'video'},{'start':0,'end':1,'keep_speed':True,'pad_before':.5,'pad_after':.5},2,out,f,160,90,30)
 assert abs(float(ffprobe(str(out))['format']['duration'])-2)<.04

def test_music_camera_selection_is_scoped_and_persisted(tmp_path,monkeypatch):
 import server.edit_timeline as module
 from core.stages import cut
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));monkeypatch.setattr(module,'duration',lambda _:20)
 p=create_project('Music',str(tmp_path/'Music.zuckervid'));sources=[{'path':str(tmp_path/(name+'.mp4')),'source_path':str(tmp_path/(name+'.mp4')),'filename':name,'duration_sec':20,'clip_start_sec':1} for name in ['red','blue']]
 monkeypatch.setattr(cut,'load_coverage',lambda _:{'sources':sources,'platform':'reel'})
 s={'clip_path':sources[0]['path'],'source_path':sources[0]['path'],'filename':'red','clip_start_sec':1,'master_start_sec':0,'duration_sec':2}
 atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'reel','segments':[s]});c=create_app(str(p.folder),dev=True).test_client();doc=c.get('/api/v1/edit/shot?index=0').json
 assert len(doc['choices'])==2 and all('Moving' in v['label'] for v in doc['choices'])
 body={'project_id':str(p.folder),'revision':doc['revision'],'index':0,'choice':1};r=c.patch('/api/v1/edit/shot',json=body);assert r.status_code==200,r.json
 saved=json.loads((p.artifacts_dir/'edit_plan.json').read_text())['segments'][0];assert saved['filename']=='blue' and saved['duration_sec']==2 and saved['master_start_sec']==0
 assert c.patch('/api/v1/edit/shot',json=body).status_code==409


def test_stage_shot_speed_persists_without_changing_neighbor(tmp_path, monkeypatch):
    import server.edit_timeline as module
    from core.stages import cut
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setattr(module,'duration',lambda _:20)
    p=create_project('Isolated speed',str(tmp_path/'Isolated speed.zuckervid'))
    path=str(tmp_path/'camera.mp4')
    sources=[{'path':path,'source_path':path,'filename':'camera','duration_sec':20,'clip_start_sec':0}]
    monkeypatch.setattr(cut,'load_coverage',lambda _:{'sources':sources,'platform':'youtube'})
    s1={'clip_path':path,'source_path':path,'filename':'camera','clip_start_sec':0,'master_start_sec':0,'duration_sec':2}
    s2={**s1,'clip_start_sec':2,'master_start_sec':2,'playback_speed':1.0}
    atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'youtube','segments':[s1,s2]})
    c=create_app(str(p.folder),dev=True).test_client()
    doc=c.get('/api/v1/edit/shot?index=0').json
    assert doc['playback_speed']==1.0
    payload={'project_id':str(p.folder),'revision':doc['revision'],'index':0,
             'choice':0,'playback_speed':0.5,'edit_component':'framing'}
    response=c.patch('/api/v1/edit/shot',json=payload)
    assert response.status_code==200,response.json
    saved=json.loads((p.artifacts_dir/'edit_plan.json').read_text())['segments']
    assert saved[0]['playback_speed']==0.5
    assert saved[1]['playback_speed']==1.0
    assert saved[0]['duration_sec']==saved[1]['duration_sec']==2


def test_custom_360_view_survives_sanitizer_and_rotation():
    from server.api import _sanitize_spherical_landmarks
    from core.stages.edit import migrate_spherical_landmarks, _available_spherical_shots
    raw={"full_stage":{"yaw":0,"subject":"general","enabled":True},
         "custom_1":{"yaw":72,"pitch":-5,"fov":94,"label":"Trumpet solo",
                     "subject":"general","enabled":True,"weight":1},
         "custom_2":{"yaw":125,"pitch":10,"fov":80,"label":"Extra guitar",
                     "subject":"guitarist","enabled":True,"weight":1}}
    saved=_sanitize_spherical_landmarks(raw)
    assert saved["custom_1"]["label"]=="Trumpet solo"
    assert saved["custom_2"]["label"]=="Extra guitar"
    shots=_available_spherical_shots(migrate_spherical_landmarks(saved))
    custom={shot["shot_id"]:shot for shot in shots if shot["shot_id"].startswith("custom_")}
    assert custom["custom_1"]["label"]=="Trumpet solo"
    assert custom["custom_1"]["yaw"]==72.0
    assert custom["custom_2"]["subject"]=="guitarist"


def test_untrusted_custom_viewpoint_keys_are_not_saved():
    from server.api import _sanitize_spherical_landmarks
    saved=_sanitize_spherical_landmarks({"custom_1":{"yaw":20},
                                         "custom_17":{"yaw":30},
                                         "../../escape":{"yaw":40}})
    assert "custom_1" in saved
    assert "custom_17" not in saved
    assert "../../escape" not in saved
