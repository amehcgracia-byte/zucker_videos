import json
import shutil
import subprocess
from pathlib import Path
import numpy as np
import pytest
from PIL import Image


def test_eye_directions_preserve_original_pixels_and_move_on_the_chosen_axes():
    from core.show_pixel_layers import LookingPixels
    pixels=np.ones((100,100,4),np.float32);pixels[45:55,45:55,:3]=0
    region=[{'rect':[.25,.25,.5,.5]}]
    def center(direction):
        moved=LookingPixels(pixels,region,.22,direction).frame(.6)
        ys,xs=np.nonzero(moved[:,:,:3].sum(axis=2)==0)
        assert np.array_equal(moved[:24],pixels[:24])
        return xs.mean(),ys.mean()
    x,y=center('horizontal');assert x>52 and y==pytest.approx(49.5)
    x,y=center('vertical');assert x==pytest.approx(49.5) and y>52
    x,y=center('diagonal');assert x>52 and y>52
    with pytest.raises(ValueError):LookingPixels(pixels,region,.22,'unknown')


def test_follow_disappears_on_loss_and_recovers_without_learning_another_wall():
    from core.show_follow import Follow
    rng=np.random.default_rng(4);patch=rng.integers(10,245,(24,24,3),dtype=np.uint8)
    def frame(x):
        out=np.zeros((180,320,3),np.uint8)
        if x is not None:out[60:84,x:x+24]=patch
        return out
    follow=Follow([20/320,60/180,24/320,24/180],320,180)
    assert follow.update(frame(20))==pytest.approx((0,0))
    assert follow.update(frame(28))[0]==pytest.approx(8/320,abs=.008)
    for _ in range(5):result=follow.update(frame(None))
    assert result is None
    for _ in range(12):result=follow.update(frame(120))
    assert result is not None and result[0]==pytest.approx(100/320,abs=.02)


def test_follow_anchor_survives_scene_save_and_render_recipe(tmp_path,monkeypatch):
    from core.project import create_project
    from server.api import create_app
    from core import show_scenes,show_proposals
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    p=create_project('Follow',str(tmp_path/'follow.zuckervid'))
    picture=p.folder/'assets/show-library/test.png';picture.parent.mkdir(parents=True);Image.new('RGBA',(20,20),'red').save(picture)
    plan={'source':str(picture),'fps':30,'shots':[{'start_frame':0,'end_frame':90}],'edited_duration':3,'words':[],'cutaways':[]}
    (p.artifacts_dir/'show_plan.json').write_text(json.dumps(plan));show_proposals.save(p.artifacts_dir/'show_proposals.json',[])
    doc=show_scenes.build(plan,[],[],[],suggested_title='Test');scene=doc['scenes'][0]
    show_scenes.save(p.artifacts_dir/'show_scenes.json',doc)
    client=create_app(str(p.folder)).test_client();anchor=[.2,.3,.1,.1]
    response=client.post(f"/api/v1/show/scenes/{scene['id']}/overlays",json={'overlays':[{'id':'guitar','asset':str(picture),'at':0,'duration':2,'follow_anchor':anchor}]})
    assert response.status_code==200,response.get_json()
    result=show_scenes.overlay_items(show_scenes.load(p.artifacts_dir/'show_scenes.json'))
    assert result[0]['follow_anchor']==anchor
    assert client.post(f"/api/v1/show/scenes/{scene['id']}/overlays",json={'overlays':[{'asset':str(picture),'follow_anchor':[1,1,1,1]}]}).status_code==400


def test_real_render_follows_the_clean_video_not_the_added_picture(tmp_path):
    from core.gag_render import render_gags
    exe=shutil.which('ffmpeg');assert exe
    w,h,fps=320,180,10;rng=np.random.default_rng(8);patch=rng.integers(0,255,(24,24,3),dtype=np.uint8)
    frames=[]
    for i in range(20):
        frame=np.zeros((h,w,3),np.uint8);frame[60:84,20+i*3:44+i*3]=patch;frames.append(frame.tobytes())
    source=tmp_path/'source.mp4';output=tmp_path/'out.mp4';asset=tmp_path/'guitar.png';Image.new('RGBA',(20,20),'red').save(asset)
    subprocess.run([exe,'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s','320x180','-r','10','-i','pipe:0','-c:v','libx264','-crf','12',str(source)],input=b''.join(frames),check=True)
    render_gags(source,[{'image':str(asset),'at':0,'duration':2,'x':.1,'y':.7,'width':.08,'effect':'none','follow_anchor':[20/w,60/h,24/w,24/h]}],output,ffmpeg=exe)
    import cv2
    cap=cv2.VideoCapture(str(output));centers=[]
    for number in (2,15):
        cap.set(cv2.CAP_PROP_POS_FRAMES,number);ok,bgr=cap.read();assert ok
        red=(bgr[:,:,2]>180)&(bgr[:,:,1]<60)&(bgr[:,:,0]<60);red[:100]=False
        ys,xs=np.nonzero(red);assert len(xs)>100;centers.append(xs.mean())
    cap.release();assert centers[1]-centers[0]==pytest.approx(39,abs=4)


def test_browser_access_is_explicit_and_errors_explain_verification(tmp_path,monkeypatch):
    from core import show_clips
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path))
    assert show_clips.browser_setting() is None
    show_clips.set_browser('firefox');assert show_clips.browser_setting()=='firefox'
    assert json.loads((tmp_path/'download_access.json').read_text())=={'browser':'firefox'}
    with pytest.raises(ValueError):show_clips.set_browser('/arbitrary/profile')
    text=show_clips.download_error("Sign in to confirm you’re not a bot --cookies")
    assert 'verification' in text and 'not more credits' in text and '--cookies' not in text
    show_clips.set_browser('');assert show_clips.browser_setting() is None


def test_black_footage_keeps_the_host_visible_and_foreground_colour_is_isolated(tmp_path,monkeypatch):
    from core import show_backgrounds,show_video_assets
    class Video:
        def __init__(self,*args):self.capture=self
        def release(self):pass
        def at(self,t):
            out=np.zeros((18,32,3),np.uint8);out[5:12,10:20]=255;return out
    monkeypatch.setattr(show_backgrounds,'VideoBackground',Video)
    layer=show_video_assets.VideoLayer({'video_layer':'test','at':0,'duration':2,'matte':'black'},32,18,30)
    frame=np.full((18,32,3),[20,60,100],np.uint8);layer.apply(frame,.5)
    assert np.array_equal(frame[0,0],[20,60,100]);assert np.array_equal(frame[7,15],[255,255,255])
    from core.show_follow import colour
    assert np.array_equal(colour(np.array([[[100,40,20]]],np.uint8),0),np.zeros((1,1,3),np.uint8))


def test_native_reveal_uses_the_os_and_reports_failed_commands(tmp_path,monkeypatch):
    import app
    from types import SimpleNamespace
    file=tmp_path/'result.mp4';file.write_bytes(b'x');calls=[]
    monkeypatch.setattr(app.sys,'platform','win32')
    monkeypatch.setattr(app.subprocess,'run',lambda args,**kwargs:(calls.append(args) or SimpleNamespace(returncode=0)))
    bridge=app.DesktopApi()
    assert bridge.reveal_in_finder(str(file))
    assert calls[-1]==['explorer','/select,'+str(file)]
    monkeypatch.setattr(app.sys,'platform','linux');assert bridge.reveal_in_finder(str(file))
    assert calls[-1]==['xdg-open',str(tmp_path)]
    monkeypatch.setattr(app.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=1))
    assert not bridge.reveal_in_finder(str(file))
    assert not bridge.reveal_in_finder(str(tmp_path/'missing'))


def test_automatic_references_are_bounded_drafts_and_do_not_generate_on_export(tmp_path,monkeypatch):
    from core.project import create_project
    from core import show_proposals,show_research,show_clips,show_video_assets
    p=create_project('Research',str(tmp_path/'research.zuckervid'))
    entries=show_proposals.from_gags([{'kind':'video_reference','at':n*10.,'duration':2.,'query':'specific reaction '+str(n)} for n in range(5)])
    calls=[]
    monkeypatch.setattr(show_research,'search_videos',lambda query:[{'url':'https://www.youtube.com/watch?v=BaW_jenozKc','title':query}])
    def fetch(project,ref,ffmpeg):
        calls.append(ref);path=project.folder/(ref['id']+'.mp4');path.write_bytes(b'mocked-video');return path
    monkeypatch.setattr(show_clips,'download',fetch)
    resources,reports=show_research.prepare(p,entries,ffmpeg='ffmpeg')
    assert len(calls)==len(resources)==len(reports)==3
    assert all(e['status']=='proposed' for e in entries)
    assert all(not e['needs'] for e in entries[:3])
    assert all(e['needs'] for e in entries[3:])
    assert entries[0]['params']['url'].startswith('https://www.youtube.com/')
    # No reference network call in the rendering path: prepared bytes remain a recipe.
    approved=[{**entries[0],'status':'approved'}];_,layers,_=show_proposals.render_inputs(approved)
    assert layers[0]['source']==entries[0]['params']['clip']


def test_failed_reference_picture_requires_review_instead_of_falling_back_to_a_painter(tmp_path,monkeypatch):
    from core.project import create_project
    from core import show_proposals,show_research,show_references,gag_director
    p=create_project('Research',str(tmp_path/'reference.zuckervid'))
    planned=gag_director.clean_gags([{'kind':'internet_photo','at':2.,'duration':2.,'query':'a specific person'}],10)
    entries=show_proposals.from_gags(planned)
    monkeypatch.setattr(show_references,'search',lambda query:(_ for _ in ()).throw(OSError('offline')))
    resources,report=show_research.prepare(p,entries,ffmpeg='ffmpeg')
    assert not resources and report[0]['status']=='unavailable'
    assert 'internet reference' in entries[0]['needs'][0]
    assert entries[0]['params']['research_error']=='offline'


def test_saved_target_template_can_seed_tracking_after_camera_zoom(tmp_path):
    import cv2
    from core.show_follow import Follow
    rng=np.random.default_rng(23);patch=rng.integers(0,255,(24,24,3),dtype=np.uint8)
    template=tmp_path/'target.png';Image.fromarray(patch).save(template)
    frame=np.zeros((180,320,3),np.uint8);frame[70:118,140:188]=cv2.resize(patch,(48,48))
    follow=Follow([20/320,60/180,24/320,24/180],320,180,str(template))
    for _ in range(3):delta=follow.update(frame)
    assert delta is not None
    assert delta==pytest.approx(((164-32)/320,(94-72)/180),abs=.015)


def test_soft_texture_key_preserves_faint_rain_against_a_tinted_green_plate():
    from core.show_video_assets import green_alpha,light_pixels
    pixels=np.full((80,120,3),[20,255,0],np.uint8)
    pixels[20:40,40:42]=[40,255,20]
    assert green_alpha(pixels)[25,40]==0  # opaque-object key used to erase it completely
    light,alpha=light_pixels(pixels)
    assert alpha[0,0]==0 and not light[0,0].any()
    assert alpha[25,40]>.05 and light[25,40].min()>0


def test_background_picker_accepts_library_images_and_rejects_unknown_files(tmp_path,monkeypatch):
    from core.project import create_project
    from core import show_proposals
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    p=create_project('Layers',str(tmp_path/'layers.zuckervid'))
    picture=p.folder/'assets/show-library/sky.png';picture.parent.mkdir(parents=True);Image.new('RGB',(32,18),'blue').save(picture)
    item=show_proposals.proposal('clip',0,2,reason='Fixture',clip=str(picture),clip_length=2,clip_start=0,matte='green')
    show_proposals.save(p.artifacts_dir/'show_proposals.json',[item])
    client=create_app(str(p.folder)).test_client();prefix='/api/v1/show/video-layers/'+item['id']
    library=client.get(prefix+'/library').get_json()['items'];assert any(v['path']==str(picture) for v in library)
    assert client.get(prefix+'/library-media',query_string={'path':str(picture)}).status_code==200
    assert client.get(prefix+'/library-media',query_string={'path':'/etc/passwd'}).status_code==404
    r=client.post(prefix+'/save',data={'settings':json.dumps({'person_style':'person','person_scale':1,'person_offset':[0,0],'library_path':str(picture),'brightness':1.4})})
    assert r.status_code==200,r.get_json()
    assert r.get_json()['params']['picture']==str(picture)
    assert r.get_json()['params']['matte']=='green'  # editing a key must not silently switch to person segmentation
    assert r.get_json()['params']['brightness']==1.4
    assert client.post(prefix+'/save',data={'settings':json.dumps({'library_path':'/etc/passwd'})}).status_code==400


def test_tracking_jumps_to_the_marked_object_on_the_first_cut_frame():
    from core.show_textures import ObjectTracker
    rng=np.random.default_rng(121);patch=rng.integers(0,255,(24,24),dtype=np.uint8)
    def frame(x):
        grey=np.full((180,320),35,np.uint8)
        if x is not None:grey[70:94,x:x+24]=patch
        return grey
    tracker=ObjectTracker([20,70,24,24])
    assert tracker.update(frame(20))[:2]==[20,70]
    assert tracker.update(frame(240))[:2]==pytest.approx([240,70],abs=1)
    assert tracker.update(frame(None)) is None
    assert tracker.update(frame(80))[:2]==pytest.approx([80,70],abs=1)


def test_texture_uses_the_saved_mark_not_pixels_at_effect_start(tmp_path):
    from core.show_inserts import load_steam,draw_steam
    rng=np.random.default_rng(100);patch=rng.integers(0,255,(24,24),dtype=np.uint8)
    Image.fromarray(patch).save(tmp_path/'mark.png')
    steam=load_steam({'steam':[.8,.4],'anchor':[.75,.4,24/320,24/180],
                      'follow_template':str(tmp_path/'mark.png'),'at':0,'duration':4},320,180)
    clean=np.full((180,320,3),35,np.uint8);clean[30:54,40:64]=patch[...,None]
    draw_steam(clean.copy(),steam,1,tracking_frame=clean)
    assert not steam.lost
    assert steam.x==pytest.approx(40+(.8-.75)*320,abs=1)
    assert steam.y==pytest.approx(30,abs=1)


def test_marked_screen_texture_is_not_reprojected_as_source_coordinates():
    from core.show import framed_layer
    entry={'steam':[.8,.6],'anchor':[.75,.6,.1,.1],'follow_template':'marked-frame.png','at':0,'duration':4}
    plan={'fps':25,'shots':[{'start_frame':0,'end_frame':50,'zoom':2,'focus':[.3,.4]},
                            {'start_frame':50,'end_frame':100,'zoom':1,'focus':[.5,.5]}]}
    assert framed_layer(plan,[entry])==[entry]


def test_release_leaves_the_object_at_last_position_instead_of_following_hand_back():
    from core.show_follow import Follow
    patch=np.random.default_rng(42).integers(0,255,(24,24,3),dtype=np.uint8)
    def picture(x):
        frame=np.zeros((180,320,3),np.uint8);frame[60:84,x:x+24]=patch;return frame
    follow=Follow([20/320,60/180,24/320,24/180],320,180,until=1.)
    assert follow.update(picture(20),0)==pytest.approx((0,0))
    placed=follow.update(picture(35),.9)
    assert placed[0]==pytest.approx(15/320,abs=.01)
    assert follow.update(picture(20),1.1)==placed


def test_full_hd_reacquisition_refines_coarse_search_coordinates():
    from core.show_textures import ObjectTracker
    patch=np.random.default_rng(12).integers(0,255,(78,93),dtype=np.uint8)
    def frame(x,y):
        out=np.full((1080,1920),35,np.uint8);out[y:y+78,x:x+93]=patch;return out
    tracker=ObjectTracker([1451,608,93,78])
    tracker.update(frame(1451,608))
    found=tracker.update(frame(233,107))
    assert found is not None
    assert found[:2]==pytest.approx([233,107],abs=1)
