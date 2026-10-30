"""Timeline authority, in-place eyes, keyed media and non-blocking cutout preparation."""
import json
import threading
import time
from pathlib import Path
import subprocess
import shutil
import numpy as np
import pytest
from PIL import Image
from core import show_proposals, show_scenes
from core.project import create_project
from server import api as api_module


@pytest.fixture
def workshop(tmp_path,monkeypatch):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'appdata'))
    project=create_project('Clip workshop',str(tmp_path/'clip.zuckervid'))
    source=project.folder/'source.mp4';source.write_bytes(b'placeholder')
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps({'source':str(source),'fps':30,'edited_duration':10,'shots':[{'start_frame':0,'end_frame':300}],'words':[]}))
    clip=show_proposals.proposal('clip',1,2,reason='test',clip=str(source),clip_length=8,clip_start=0,url='https://vimeo.com/123',matte='none')
    clip['status']='approved'
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[clip],source=str(source),resources=[{'path':str(source),'kind':'video'}])
    show_scenes.save(project.artifacts_dir/'show_scenes.json',{'identity':{},'scenes':[{'id':'scene-1','name':'Scene','start':0,'end':10,'text':'hello','effects':[clip['id']],'overlays':[],'decision':'pending'}]})
    client=api_module.create_app(project_path=str(project.folder)).test_client()
    return project,clip,client


def test_clip_timeline_duration_controls_picture_and_own_audio(workshop):
    project,clip,client=workshop
    assert show_proposals.render_inputs([clip])[1][0]['clip_length']==2
    response=client.patch('/api/v1/show/review/'+clip['id'],json={'duration':.3})
    assert response.status_code==200,response.get_json()
    assert show_proposals.render_inputs([response.get_json()])[1][0]['clip_length']==.3
    assert client.patch('/api/v1/show/review/'+clip['id'],json={'duration':9}).status_code==400


def test_settings_and_library_reuse_same_video_bytes(workshop):
    project,clip,client=workshop
    response=client.post('/api/v1/show/clips/'+clip['id']+'/settings',json={'clip_start':1.5,'matte':'green','clip_audio':False})
    assert response.status_code==200,response.get_json()
    changed=response.get_json();assert changed['id']==clip['id']
    rows=client.get('/api/v1/show/video-library').get_json()['items'];assert rows[0]['transparent']
    placed=client.post('/api/v1/show/video-library/'+clip['id']+'/place',json={'scene_id':'scene-1','at':5,'duration':1}).get_json()['proposal']
    assert placed['id']!=clip['id'] and placed['params']['clip']==clip['params']['clip']
    _,gags,_=show_proposals.render_inputs([changed]);assert gags[0]['video_layer']==clip['params']['clip']
    assert not any(g.get('insert') for g in gags)
    assert client.post('/api/v1/show/clips/'+clip['id']+'/settings',json={'clip_start':float('nan')}).status_code==400


def test_eye_rig_updates_existing_closeup_without_another_effect(workshop,monkeypatch):
    project,clip,client=workshop
    photo=project.folder/'photo.png';Image.new('RGB',(120,80),'orange').save(photo)
    record=dict(cutout=str(photo),photo=str(photo),crop=[0,0,120,80],eyes=[{'rect':[.3,.3,.3,.2]}],mouth=None)
    from core import show_characters
    monkeypatch.setattr(show_characters,'list_characters',lambda *args: [record])
    path=project.artifacts_dir/'show_proposals.json'
    cut=show_proposals.proposal('cutaway',1,2,reason='test',source=clip['params']['clip'],source_start=0,zoom=1,focus=[.5,.5]);cut['status']='approved'
    show_proposals.save(path,[cut],resources=[])
    for _ in range(2):
        response=client.post('/api/v1/show/review/'+cut['id']+'/parts',json={'asset':str(photo)})
        assert response.status_code==200,response.get_json()
    saved=show_proposals.load(path)['proposals']
    assert len(saved)==1 and saved[0]['id']==cut['id'] and saved[0]['kind']=='cutaway'
    assert saved[0]['params']['pixel_parts']['eyes']
    cuts,gags,_=show_proposals.render_inputs(saved)
    assert len(cuts)==1 and len(gags)==1 and gags[0]['pixel_parts']
    assert not show_scenes.load(project.artifacts_dir/'show_scenes.json')['scenes'][0]['overlays']


def test_eye_animation_changes_only_original_eye_pixels_after_framing():
    from core.show_pixel_layers import apply_video_eyes
    image=np.full((80,120,3),[225,180,160],np.uint8);image[30:48,25:80]=255;image[34:44,46:57]=[20,40,100]
    original=image.copy();raw=dict(at=0,duration=3,source_size=[120,80],eyes=[{'rect':[25/120,30/80,55/120,18/80]}],zoom=1,focus=[.5,.5])
    apply_video_eyes(image,raw,.9)
    assert not np.array_equal(image[30:48,25:80],original[30:48,25:80])
    assert np.array_equal(image[:25],original[:25])
    assert {tuple(p) for p in image.reshape(-1,3)}<={tuple(p) for p in original.reshape(-1,3)}


def test_cutout_editor_returns_progress_without_blocking_or_preparing_again(workshop,monkeypatch,tmp_path):
    from core import show_backgrounds
    project,clip,client=workshop
    entered,release=threading.Event(),threading.Event();calls=[]
    host=tmp_path/'host.mp4';host.write_bytes(b'video');alpha=tmp_path/'alpha.png';alpha.write_bytes(b'alpha')
    def prepare(*args,**kwargs):
        calls.append(1);entered.set();assert release.wait(5)
        return dict(host=host,alpha=alpha,frames=30,fps=30,duration=1)
    monkeypatch.setattr(show_backgrounds,'editor_sample',prepare)
    url='/api/v1/show/video-layers/'+clip['id']
    page=client.get(url+'/editor');assert b'data-host-src=' in page.data
    started=time.monotonic();response=client.get(url+'/sample');assert time.monotonic()-started<1
    assert response.status_code==202 and entered.wait(1)
    assert client.get(url+'/sample').status_code==202
    assert client.get(url+'/media/host.mp4').status_code==409
    release.set()
    for _ in range(100):
        result=client.get(url+'/sample')
        if result.status_code==200:break
        time.sleep(.01)
    assert result.status_code==200
    assert client.get(url+'/media/host.mp4').data==b'video'
    assert len(calls)==1


def test_green_cutout_is_automatic_but_green_clothes_are_not(tmp_path):
    from core.show_video_assets import detect_green,VideoLayer
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.fail('FFmpeg is required for video cutout verification')
    def video(name,background,foreground):
        path=tmp_path/name
        subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i',f'color={background}:size=160x90:rate=30:duration=1.2','-vf',f'drawbox=x=60:y=20:w=40:h=50:color={foreground}:t=fill','-c:v','libx264','-threads','1',str(path)],check=True)
        return path
    green=video('green.mp4','0x00ff00','blue');clothes=video('clothes.mp4','red','0x00ff00')
    assert detect_green(green) and not detect_green(clothes)
    layer=VideoLayer({'video_layer':str(green),'matte':'green','at':0,'duration':1},160,90,30)
    try:
        for t in (0,.8):
            frame=np.full((90,160,3),[220,30,20],np.uint8);layer.apply(frame,t)
            assert frame[0,0,0]>200 and frame[45,80,2]>200
    finally:layer.close()


def test_supported_portals_still_reject_non_https_and_local_links():
    from core.show_clips import check_link
    for url in ['https://www.dailymotion.com/video/x123','https://clips.twitch.tv/example','https://archive.org/details/example']:
        assert check_link(url)==url
    for url in ['https://127.0.0.1/video.mp4','http://vimeo.com/1','https://vimeo.com@localhost/1','https://example.com/v.mp4']:
        with pytest.raises(ValueError):check_link(url)


def test_keyed_video_survives_actual_render_and_keeps_host_audio(tmp_path):
    from core.gag_render import render_gags
    import cv2
    ffmpeg=shutil.which('ffmpeg');assert ffmpeg
    host=tmp_path/'host.mp4';key=tmp_path/'key.mp4';output=tmp_path/'result.mp4'
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','color=0xdd3322:size=160x90:rate=30:duration=1.2','-f','lavfi','-i','sine=frequency=777:duration=1.2',
                    '-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(host)],check=True)
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','color=0x00ff00:size=160x90:rate=30:duration=1.2','-vf','drawbox=x=60:y=20:w=40:h=50:color=blue:t=fill',
                    '-c:v','libx264','-threads','1',str(key)],check=True)
    render_gags(host,[{'video_layer':str(key),'matte':'green','at':0,'duration':1.2}],output,ffmpeg=ffmpeg,preset='veryfast',crf=18)
    cap=cv2.VideoCapture(str(output))
    try:
        assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT))==36
        cap.set(cv2.CAP_PROP_POS_FRAMES,15);ok,bgr=cap.read();assert ok
        assert bgr[0,0,2]>190 and bgr[45,80,0]>190
    finally:cap.release()
    def audio_digest(path):return subprocess.check_output([ffmpeg,'-v','error','-i',str(path),'-map','0:a:0','-c','copy','-f','hash','-hash','sha256','-'])
    assert audio_digest(output)==audio_digest(host)


def test_clip_media_is_range_readable_but_not_arbitrary_files(workshop,tmp_path):
    project,clip,client=workshop
    response=client.get('/api/v1/show/clips/'+clip['id']+'/media',headers={'Range':'bytes=0-3'})
    assert response.status_code==206 and response.data==b'plac'
    path=project.artifacts_dir/'show_proposals.json';stored=show_proposals.load(path)
    external=tmp_path/'outside.mp4';external.write_bytes(b'private')
    stored['proposals'][0]['params']['clip']=str(external);show_proposals.save(path,stored['proposals'])
    assert client.get('/api/v1/show/clips/'+clip['id']+'/media').status_code==404


def test_filmed_texture_replaces_same_effect_and_keeps_mark_timing_and_template(workshop,monkeypatch):
    from core import show_clips,show_video_assets
    project,clip,client=workshop
    effect=show_proposals.proposal('texture',2,6,reason='Fire on plant',texture='fire',point=[.8,.4],anchor=[.7,.4,.1,.2],follow_template='/marked-template.png')
    effect['status']='approved'
    path=project.artifacts_dir/'show_proposals.json'
    show_proposals.save(path,[effect],source=clip['params']['clip'],resources=[])
    document=show_scenes.load(project.artifacts_dir/'show_scenes.json');document['scenes'][0]['effects']=[effect['id']];show_scenes.save(project.artifacts_dir/'show_scenes.json',document)
    reference={'id':'fire','scene_id':'scene-1','url':'https://vimeo.com/123','start':0,'end':30,'at':2}
    (project.artifacts_dir/'show_references.json').write_text(json.dumps([reference]))
    monkeypatch.setattr(show_clips,'download',lambda *a:Path(clip['params']['clip']))
    monkeypatch.setattr(show_clips,'length',lambda *a:3.)
    monkeypatch.setattr(show_video_assets,'detect_green',lambda *a:True)
    response=client.post('/api/v1/show/references/video/fire/download',json={'texture':'fire','replace_texture':effect['id'],'duration':6})
    assert response.status_code==201,response.get_json()
    result=response.get_json()['proposal']
    assert result['id']==effect['id'] and result['at']==2 and result['duration']==6
    assert result['params']['follow_anchor']==effect['params']['anchor']
    assert result['params']['follow_template']=='/marked-template.png'
    assert result['params']['person_offset']==pytest.approx([.3,-.6])
    assert len(show_proposals.load(path)['proposals'])==1
    assert show_scenes.load(project.artifacts_dir/'show_scenes.json')['scenes'][0]['effects']==[effect['id']]
    assert show_proposals.render_inputs([result])[1][0]['video_layer']==clip['params']['clip']


def test_crop_keeps_retained_pixels_stationary_and_render_geometry_matches_mouse():
    from core.show_geometry import crop_alpha,validate_crop
    a=np.ones((10,20,4),np.float32)
    cropped=crop_alpha(a,[.25,.2,.1,.1])
    assert not cropped[:2].any() and not cropped[:,:5].any()
    assert np.array_equal(cropped[2:9,5:18],a[2:9,5:18])
    with pytest.raises(ValueError):validate_crop([.7,0,.5,0])
    js=Path('web/show_geometry.js').read_text()
    import shutil
    node=shutil.which('node')
    if node:
        subprocess.run([node,'-e',"global.window={};"+js+"const g=window.ShowGeometry;const assert=require('assert');assert.deepEqual(g.change({},'left',.2,0,false),{crop:[.2,0,0,0]});assert.equal(g.change({},null,0,0,true,0,Math.PI/2).rotate,90);assert.equal(g.change({},'right',.2,0,true).stretch_x,1.4);"],check=True)
