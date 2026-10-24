import json,subprocess
from dataclasses import asdict
import numpy as np
import pytest
from core.flat_framing import validate,filtergraph
from core.project import create_project,atomic_write_json
from server.api import create_app
from core import show_podcast as pod

@pytest.mark.parametrize('spec',[{'zoom':0},{'zoom':4},{'zoom':float('nan')},{'x':-1},{'y':float('inf')}])
def test_rejects_invalid_framing(spec):
 with pytest.raises(ValueError):validate(spec)

def source(tmp_path):
 from core.ffmpeg import tool_status
 f=str(tool_status().get('ffmpeg_path') or 'ffmpeg');v=tmp_path/'split.mp4'
 subprocess.run([f,'-v','error','-y','-f','lavfi','-i','color=red:s=160x180:r=30:d=1','-f','lavfi','-i','color=blue:s=160x180:r=30:d=1','-filter_complex','[0:v][1:v]hstack','-c:v','libx264','-threads','1',str(v)],check=True)
 return f,v

def pixel_means(f,v):
 raw=subprocess.check_output([f,'-v','error','-i',str(v),'-frames:v','1','-vf','scale=160:90','-pix_fmt','rgb24','-f','rawvideo','-']);return np.frombuffer(raw,np.uint8).reshape(90,160,3).mean(axis=(0,1))

def test_podcast_fixed_crop_changes_real_pixels(tmp_path):
 f,v=source(tmp_path);setup={'people':[{'name':'One'}],'cameras':[{'path':str(v),'kind':'flat','people':[0]}]};shot={'start':0,'end':1,'view':0,'flat_framing':{'zoom':2,'x':.75,'y':.5}}
 out=pod.render(setup,[shot],np.zeros(48000,np.float32),tmp_path/'programme.mp4',f,width=320,height=180);pixels=pixel_means(f,out);assert pixels[2]>pixels[0]+180

def test_music_preview_and_export_use_same_crop(tmp_path,monkeypatch):
 from core import review_previews
 from core.stages import export
 from core.shot_review import _thumbnail_asset
 from server.edit_timeline import revision
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Flat',str(tmp_path/'Flat.zuckervid'));f,v=source(tmp_path)
 s={'clip_path':str(v),'source_path':str(v),'clip_start_sec':0,'duration_sec':1,'master_start_sec':0,'flat_framing':{'zoom':2,'x':.75,'y':.5},'motion':{'type':'ken_burns','zoom_start':1.2,'zoom_end':1.8}}
 assert export._motion_filter(s,'youtube',1) is None
 before=review_previews.preview_path(p,0,{**s,'flat_framing':{'zoom':1,'x':.5,'y':.5}});assert before!=review_previews.preview_path(p,0,s)
 assert _thumbnail_asset(tmp_path,0,s)[2]!=_thumbnail_asset(tmp_path,0,{**s,'flat_framing':{'zoom':1,'x':.5,'y':.5}})[2]
 preview=review_previews.render_preview(p,0,s);pixels=pixel_means(f,preview);assert pixels[2]>pixels[0]+180
 graph=export._segment_filtergraph('youtube',1,{}, {},has_watermark=False,text_enabled=False,frame_count=30,segment=s)
 out=tmp_path/'export.mp4';subprocess.run([f,'-v','error','-y','-i',str(v),'-filter_complex',graph,'-map','[v]','-frames:v','30','-c:v','libx264','-threads','1',str(out)],check=True);pixels=pixel_means(f,out);assert pixels[2]>pixels[0]+180

def test_fixed_crop_api_is_scoped_saved_and_keeps_other_shots(tmp_path,monkeypatch):
 from core.stages import cut
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Edit',str(tmp_path/'Edit.zuckervid'));v=tmp_path/'a.mp4';v.touch()
 monkeypatch.setattr(cut,'load_coverage',lambda _: {'sources':[]})
 s={'clip_path':str(v),'source_path':str(v),'clip_start_sec':0,'duration_sec':1,'master_start_sec':0}
 atomic_write_json(p.artifacts_dir/'edit_plan.json',{'segments':[s,{**s,'master_start_sec':1}]})
 c=create_app(str(p.folder),dev=True).test_client();doc=c.get('/api/v1/edit/shot?index=0').json;body={'project_id':str(p.folder),'revision':doc['revision'],'index':0,'choice':0,'flat_framing':{'zoom':2,'x':.75,'y':.5}}
 # Snapshot probes source durations; make this focused HTTP test independent of decoding.
 import server.edit_timeline as timeline
 monkeypatch.setattr(timeline,'duration',lambda _:5)
 result=c.patch('/api/v1/edit/shot',json=body);assert result.status_code==200,result.json
 saved=json.loads((p.artifacts_dir/'edit_plan.json').read_text());assert saved['segments'][0]['flat_framing']['zoom']==2 and 'flat_framing' not in saved['segments'][1]
 assert c.get('/api/v1/edit/shot?index=0').json['framing']['x']==.75
 assert c.patch('/api/v1/edit/shot',json=body).status_code==409
 atomic_write_json(pod.setup_path(p),{'people':[{'name':'One'}],'cameras':[{'path':str(v),'kind':'flat','people':[0]}],'shots':[{'start':0,'end':1,'view':0},{'start':1,'end':2,'view':0}],'revision':0})
 result=c.patch('/api/v1/show/podcast/plan/0',json={'project_id':str(p.folder),'revision':0,'view':0,'flat_framing':{'zoom':2,'x':.75,'y':.5}});assert result.status_code==200;assert result.json['shots'][0]['flat_framing']['zoom']==2 and 'flat_framing' not in result.json['shots'][1]

def test_camera_types_save_and_control_projection(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Types',str(tmp_path/'Types.zuckervid'));paths=[str(tmp_path/(name+'.mp4')) for name in ('a','b','c')];p.data['inputs']['videos']=[{'path':v,'probe':{}} for v in paths];p.save();c=create_app(str(p.folder),dev=True).test_client()
 types=dict(zip(paths,['360','fixed','moving']));r=c.post('/api/v1/settings/camera-types',json={'project_id':str(p.folder),'camera_types':types});assert r.status_code==200,r.json
 from core.project import load_project
 saved=load_project(p.folder);assert saved.data['inputs']['videos'][0]['probe']['projection']=='equirect';assert saved.data['settings']['edit']['camera_roles']=={paths[1]:'fixed_rear',paths[2]:'handheld'}
 assert c.post('/api/v1/settings/camera-types',json={'project_id':'other','camera_types':types}).status_code==409

def test_flat_sources_are_never_offered_as_fake_360_views(tmp_path,monkeypatch):
 from core import shot_review
 from core.stages import cut
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'));p=create_project('Kinds',str(tmp_path/'Kinds.zuckervid'));v=tmp_path/'ordinary.mp4';v.touch();p.data['inputs']['videos']=[{'path':str(v),'probe':{'projection':None}}];p.save()
 segment={'clip_path':str(v),'source_path':str(v),'clip_start_sec':0,'duration_sec':1,'master_start_sec':0};atomic_write_json(p.artifacts_dir/'edit_plan.json',{'segments':[segment]})
 monkeypatch.setattr(cut,'load_coverage',lambda _: {})
 monkeypatch.setattr(shot_review,'_review_candidate_pool',lambda *args:([{'path':str(v),'source_path':str(v),'projection':'equirect','spherical_shot':{'type':'singer'}}],'test'))
 c=create_app(str(p.folder),dev=True).test_client();doc=c.get('/api/v1/edit/shot?index=0').json;assert len(doc['choices'])==1 and not doc['choices'][0]['spherical']
