import json,subprocess
import numpy as np
import pytest
from core.video_light import validate,filtergraph,profile
from core.project import create_project,atomic_write_json
from core.stages import export,cut
from core import review_previews
from core.shot_review import _thumbnail_asset
from server.api import create_app

@pytest.mark.parametrize('value',[{'auto':1},{'light':float('nan')},{'shadows':float('inf')},{'light':2},{'light':True},{'unknown':1}])
def test_invalid_controls(value):
 with pytest.raises(ValueError):validate(value)

def make_video(tmp_path,level=45):
 tmp_path.mkdir(parents=True,exist_ok=True)
 from core.ffmpeg import tool_status
 ff=str(tool_status().get('ffmpeg_path') or 'ffmpeg')
 # Dark, recoverable midtones alongside black and an already bright lamp.
 data=np.tile(np.array([0]*80+[level]*230+[245]*10,dtype=np.uint8),(180,1))
 image=tmp_path/'source.pgm';image.write_bytes(b'P5\n320 180\n255\n'+data.tobytes())
 video=tmp_path/'source.mp4'
 subprocess.run([ff,'-v','error','-y','-loop','1','-i',str(image),'-t','1','-r','30','-pix_fmt','yuv420p','-c:v','libx264','-threads','1',str(video)],check=True)
 return ff,video

def pixels(ff,path):
 raw=subprocess.check_output([ff,'-v','error','-i',str(path),'-frames:v','1','-vf','scale=320:180','-pix_fmt','gray','-f','rawvideo','-'])
 return np.frombuffer(raw,np.uint8).reshape(180,320)

def test_auto_lifts_dark_reference_and_preview_matches_export(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'state'))
 p=create_project('Lighting',str(tmp_path/'edit.zuckervid'));ff,v=make_video(tmp_path)
 p.data['inputs']['videos']=[{'path':str(v),'probe':{'width':320,'height':180,'duration':1}}]
 p.data['settings']['edit']['video_light']={'enabled':True}
 s={'clip_path':str(v),'source_path':str(v),'clip_start_sec':0,'duration_sec':1,'master_start_sec':0}
 atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'youtube','segments':[s]});p.save()
 colors=export._color_profiles_for_segments(p,[s],[]);color=colors[str(v)]
 # The sole reference camera used to remain entirely uncorrected.
 assert color.get('video_light') and color['light_target']>color['light_anchor']
 preview=review_previews.render_preview(p,0,s)
 graph=export._segment_filtergraph('youtube',1,{},color,has_watermark=False,text_enabled=False,frame_count=30,segment=s)
 final=tmp_path/'export.mp4';subprocess.run([ff,'-v','error','-y','-i',str(v),'-filter_complex',graph,'-map','[v]','-frames:v','30','-c:v','libx264','-threads','1',str(final)],check=True)
 a,b,c=pixels(ff,v),pixels(ff,preview),pixels(ff,final)
 assert c[:,100:220].mean()>a[:,100:220].mean()+8
 assert c[:,:50].mean()<4
 assert abs(c[:,315:].mean()-a[:,315:].mean())<12
 assert abs(b[:,100:220].mean()-c[:,100:220].mean())<5
 from core.shot_review import review_items
 rendered=review_items(p,render_missing=True)
 polled=review_items(p,render_missing=False)
 assert rendered[0]['thumbnail'] and rendered[0]['thumbnail']==polled[0]['thumbnail']
 old=review_previews.preview_path(p,0,s)
 p.data['settings']['edit']['video_light']['cameras']={str(v):{'auto':False,'light':-.2,'shadows':0}}
 assert old!=review_previews.preview_path(p,0,s)
 assert _thumbnail_asset(tmp_path,0,s)[2]!=_thumbnail_asset(tmp_path,0,{**s,'video_light':{'auto':False,'light':-.2,'shadows':0}})[2]

def test_light_api_scopes_camera_vs_shot_and_validates_before_saving(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'state'))
 p=create_project('Lighting',str(tmp_path/'edit.zuckervid'));ff,v=make_video(tmp_path)
 p.data['inputs']['videos']=[{'path':str(v),'probe':{'width':320,'height':180,'duration':1}}];p.save()
 s={'clip_path':str(v),'source_path':str(v),'clip_start_sec':0,'duration_sec':.4,'master_start_sec':0}
 atomic_write_json(p.artifacts_dir/'edit_plan.json',{'platform':'youtube','segments':[s,{**s,'master_start_sec':.4,'clip_start_sec':.4}]})
 monkeypatch.setattr(cut,'load_coverage',lambda _: {'sources':[]})
 c=create_app(str(p.folder),dev=True).test_client()
 assert c.post('/api/v1/settings/video-light',json={'project_id':'wrong','enabled':True}).status_code==409
 assert c.post('/api/v1/settings/video-light',json={'project_id':str(p.folder),'enabled':True}).status_code==200
 spec={'auto':False,'light':.2,'shadows':.1}
 doc=c.get('/api/v1/edit/shot?index=0').json
 body={'project_id':str(p.folder),'revision':doc['revision'],'index':0,'choice':0,'video_light':spec}
 r=c.patch('/api/v1/edit/shot',json=body);assert r.status_code==200,r.json
 saved=json.loads((p.artifacts_dir/'edit_plan.json').read_text())
 assert saved['segments'][0]['video_light']==spec and 'video_light' not in saved['segments'][1]
 doc=c.get('/api/v1/edit/shot?index=0').json
 body.update(revision=doc['revision'],light_scope='camera')
 r=c.patch('/api/v1/edit/shot',json=body);assert r.status_code==200,r.json
 assert c.get('/api/v1/edit/shot?index=1').json['light']==spec
 doc=c.get('/api/v1/edit/shot?index=0').json
 before=(p.artifacts_dir/'edit_plan.json').read_bytes()
 body.update(revision=doc['revision'],video_light={'light':float('nan')})
 assert c.patch('/api/v1/edit/shot',json=body).status_code==400
 assert (p.artifacts_dir/'edit_plan.json').read_bytes()==before
 frame=c.get('/api/v1/edit/shot/frame',query_string={'index':0,'revision':doc['revision'],'light':json.dumps(spec)})
 assert frame.status_code==200 and frame.mimetype=='image/jpeg'
 recipe=c.get('/api/v1/edit/native-recipe',query_string={'project_id':str(p.folder)})
 assert recipe.status_code==200,recipe.json
 assert len(recipe.json['sources'])==1 and len(recipe.json['segments'])==2
 assert all(s['source']==0 and s['video_light']==spec for s in recipe.json['segments'])
 assert c.get('/api/v1/edit/native-recipe',query_string={'project_id':'wrong'}).status_code==409
 assert c.get('/api/v1/edit/native-source/0',query_string={'project_id':str(p.folder),'revision':'old'}).status_code==409
 assert c.get('/api/v1/edit/native-source/0',query_string={'project_id':str(p.folder),'revision':recipe.json['revision']}).status_code==200


def test_two_dark_cameras_converge_without_matching_to_darkest(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'state'))
 p=create_project('Pair',str(tmp_path/'edit.zuckervid'))
 ff,a=make_video(tmp_path/'a',45);_,b=make_video(tmp_path/'b',70)
 p.data['inputs']['videos']=[{'path':str(v),'probe':{'width':320,'height':180,'duration':1}} for v in (a,b)]
 p.data['settings']['edit']['video_light']={'enabled':True}
 segments=[{'clip_path':str(v),'source_path':str(v),'clip_start_sec':0,'duration_sec':1,'master_start_sec':i} for i,v in enumerate((a,b))]
 colors=export._color_profiles_for_segments(p,segments,[])
 corrected=[]
 for i,v in enumerate((a,b)):
  target=tmp_path/f'corrected-{i}.mp4'
  subprocess.run([ff,'-v','error','-y','-i',str(v),'-vf',export._color_filter(colors[str(v)]),'-c:v','libx264','-threads','1',str(target)],check=True)
  corrected.append(pixels(ff,target)[:,100:220].mean())
 assert abs(corrected[0]-corrected[1])<10
 assert corrected[0]>55 and corrected[1]>65


def test_very_dark_anchor_cannot_blow_out_middle_tones():
 # Real dark stage anchors exposed an unbounded gain in an earlier prototype.
 # Already lit instruments must keep their detail when shadows are recovered.
 graph=filtergraph({'video_light':{'auto':True,'light':0,'shadows':0},'light_anchor':.05,'light_target':.23})
 points=[tuple(map(float,p.split('/'))) for p in graph.split("'")[1].split()]
 assert next(y for x,y in points if x==.35)<.65
 assert next(y for x,y in points if x==.65)<.85
 assert points[0]==(0.,0.) and points[-1]==(1.,1.)


def test_auto_anchor_survives_first_shot_auto_off(tmp_path,monkeypatch):
 monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'state'))
 p=create_project('Mixed auto',str(tmp_path/'edit.zuckervid'))
 s={'clip_path':'camera.mp4','video_light':{'auto':False,'light':0,'shadows':0}}
 color=profile(p,s,{'luma':35,'luma_high':57})
 assert not filtergraph(color)
 assert filtergraph(color,{'auto':True,'light':0,'shadows':0})
