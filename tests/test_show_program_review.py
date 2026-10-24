import subprocess
from dataclasses import asdict
import numpy as np
import pytest
from core.project import create_project,atomic_write_json
from core import show_podcast as pod,show_caption_layout as cap,show_story_manual as story
from server.api import create_app

def test_camera_changes_are_scoped_versioned_and_recorded(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Review',str(tmp_path/'Review.zuckervid'));v=tmp_path/'c.mp4';v.touch()
 atomic_write_json(pod.setup_path(p),{'people':[{'name':'One','mic':'one.mp3'}],'cameras':[{'path':str(v),'kind':'360','seats':[{'person':0,'yaw':20,'pitch':0}]}],'shots':[{'start':0,'end':2,'view':0}],'revision':0})
 c=create_app(str(p.folder),dev=True).test_client();body={'project_id':str(p.folder),'revision':0,'view':0,'yaw':-25,'pitch':8,'fov':65,'reason':'Wrong speaker'}
 result=c.patch('/api/v1/show/podcast/plan/0',json=body);assert result.status_code==200;assert result.json['shots'][0]['override']['yaw']==-25 and result.json['revision']==1
 assert c.patch('/api/v1/show/podcast/plan/0',json=body).status_code==409
 assert c.patch('/api/v1/show/podcast/plan/0',json={**body,'project_id':'other','revision':1}).status_code>=400
 assert c.patch('/api/v1/show/podcast/plan/0',json={**body,'view':999,'revision':1}).status_code==400
 events=c.get('/api/v1/show/decisions').json['items'];assert len(events)==1 and events[0]['before']['view']==0 and events[0]['after']['override']['yaw']==-25

def test_caption_style_and_drag_survive_reopen(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Caps',str(tmp_path/'Caps.zuckervid'));data=cap.save_defaults(p,{'font':'Impact','size':88,'fun_effect':'pop'},0)
 cue=cap.apply([{'lines':['Hello'],'start':0,'end':1,'style_override':{'size':58}}],data)[0];assert cue['style_override']['size']==88
 cap.save(p,cue['id'],{'horizontal':22,'vertical':43},1,[cue]);cue=cap.apply([{'lines':['Hello'],'start':0,'end':1}],cap.load(p))[0];assert cue['style_override']['horizontal']==22 and cue['style_override']['font']=='Impact' and cue['style_override']['fun_effect']=='pop'
 with pytest.raises(ValueError):cap.save_defaults(p,{'size':float('nan')},2)

def test_manual_story_rejects_foreign_clips_gaps_and_wrong_duration():
 plan={'moments':[{'start':0,'end':2,'pictures':[{'path':'a.mp4'}]}]};assert story.validate(plan,['a.mp4'],2)==plan
 with pytest.raises(ValueError):story.validate(plan,['other.mp4'],2)
 with pytest.raises(ValueError):story.validate({'moments':[{'start':1,'end':2,'pictures':[{'path':'a.mp4'}]}]},['a.mp4'])
 with pytest.raises(ValueError):story.validate(plan,['a.mp4'],3)

def test_render_uses_corrected_camera(tmp_path):
 from core.ffmpeg import tool_status
 f=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
 for colour in ['red','blue']:subprocess.run([f,'-v','error','-y','-f','lavfi','-i',f'color={colour}:s=160x90:r=30:d=1','-c:v','libx264','-threads','1',str(tmp_path/(colour+'.mp4'))],check=True)
 setup={'people':[{'name':'One'}],'cameras':[{'path':str(tmp_path/'red.mp4'),'kind':'flat','people':[0]},{'path':str(tmp_path/'blue.mp4'),'kind':'flat','people':[0]}]};view=next(v for v in pod.views(setup['cameras'],1) if v.camera==1)
 output=pod.render(setup,[{'start':0,'end':1,'view':0,'override':asdict(view)}],np.zeros(48000,np.float32),tmp_path/'program.mp4',f,width=160,height=90)
 raw=subprocess.check_output([f,'-v','error','-i',str(output),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-']);img=np.frombuffer(raw,np.uint8).reshape(90,160,3);assert img[:,:,2].mean()>img[:,:,0].mean()+100


def test_scene_preview_changes_when_the_camera_program_changes(tmp_path,monkeypatch):
 from core import show,show_proposals,show_scenes
 import os
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Version',str(tmp_path/'Version.zuckervid'));v=tmp_path/'program.mp4';v.write_bytes(b'camera-one')
 plan={'source':str(v),'edited_duration':1,'fps':30,'shots':[{'start_frame':0,'end_frame':30,'zoom':1,'focus':[.5,.5],'words':[]}],'words':[],'style':'podcast'}
 atomic_write_json(p.artifacts_dir/'show_plan.json',plan);show_proposals.save(p.artifacts_dir/'show_proposals.json',[],source=str(v),resources=[])
 show_scenes.save(p.artifacts_dir/'show_scenes.json',{'identity':{},'scenes':[{'id':'one','name':'One','start':0,'end':1,'text':'','effects':[],'overlays':[],'decision':'modified'}]})
 before=show.scene_preview_path(p,'one');stat=v.stat();os.utime(v,ns=(stat.st_atime_ns,stat.st_mtime_ns+10000000));after=show.scene_preview_path(p,'one');assert before!=after
