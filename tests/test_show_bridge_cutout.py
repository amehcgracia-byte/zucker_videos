import numpy as np
import pytest
from PIL import Image
from core.show_bridge import studio_drawers


def event(**kw):
    return dict(start=0, end=1, polygon=[[320,180],[960,180],[960,540],[320,540]],
                background='library', backgroundAsset='own', x=.5, y=.5, **kw)


def test_saved_background_replaces_only_pixels_outside_the_original_subject(tmp_path):
    path = tmp_path / 'blue.png'
    Image.new('RGB', (160, 90), (12, 33, 211)).save(path)
    drawers = studio_drawers(lambda _: None, materials=lambda: [dict(kind='background', id='own', asset='sha')],
                             asset=lambda _: path)
    draw, detail = drawers['cutout'](event())
    frame = np.full((72, 128, 3), (201, 24, 66), np.uint8)
    draw(frame, .5, (0, 0, .1))
    assert tuple(frame[36,64]) == (201,24,66)  # exact source pixels, no generated subject
    assert tuple(frame[3,3]) == (12,33,211)
    assert 'Original-pixel' in detail


def test_polygon_respects_the_camera_crop():
    draw, _ = studio_drawers(lambda _: None)['cutout'](dict(
        start=0,end=1,polygon=[[640,0],[1280,0],[1280,720],[640,720]], background='dark',x=.5,y=.5))
    frame = np.full((72,128,3), (201,24,66),np.uint8)
    # Zoom on the right half: the whole visible source belongs to the polygon.
    draw(frame,.5,(640,0,.2))
    assert tuple(frame[30,64]) == (201,24,66)


def test_moving_mask_is_not_silently_reused_outside_its_reviewed_interval(tmp_path):
    atlas=tmp_path/'atlas.png';Image.new('RGBA',(20,10),(255,255,255,255)).save(atlas)
    drawers=studio_drawers(lambda _:dict(start=1,end=2,fps=1,count=1,width=20,height=10,columns=1),source=lambda _:atlas)
    with pytest.raises(ValueError,match='beyond its reviewed'):
        drawers['cutout'](dict(start=0,end=3,sample=True))


def test_browser_only_background_is_reported_instead_of_replaced_with_paper():
    with pytest.raises(ValueError,match='imported into the project'):
        studio_drawers(lambda _:None)['cutout'](dict(start=0,end=1,polygon=[[0,0],[1,0],[1,1]],background='photo'))


def test_real_video_export_keeps_source_pixels_audio_and_the_saved_background(tmp_path):
    import shutil
    import subprocess
    from core.gag_render import render_gags
    from core.show_bridge import plan_effects, StudioDrawings
    ffmpeg = shutil.which('ffmpeg')
    assert ffmpeg, 'This export test requires FFmpeg'
    video, output, backdrop = tmp_path/'source.mp4', tmp_path/'export.mp4', tmp_path/'backdrop.png'
    Image.new('RGB',(160,90),(12,33,211)).save(backdrop)
    subprocess.run([ffmpeg,'-v','error','-y','-f','lavfi','-i','color=c=0xc91842:s=320x180:r=25:d=1',
                    '-f','lavfi','-i','sine=duration=1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(video)],check=True)
    plan=dict(fps=25,shots=[dict(start_frame=0,end_frame=25,zoom=1,focus=[.5,.5])])
    recipe=dict(events=[dict(id='person',type='cutout',**event())])
    drawers=studio_drawers(lambda _:None, materials=lambda:[dict(kind='background',id='own',asset='sha')],asset=lambda _:backdrop)
    layers,report=plan_effects(recipe,section='opening',source_start=0,plan=plan,drawers=drawers)
    assert report[0]['status']=='applied'
    render_gags(video,[],output,ffmpeg=ffmpeg,drawings=StudioDrawings(plan,layers))
    raw=subprocess.run([ffmpeg,'-v','error','-ss','0.5','-i',str(output),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
    frame=np.frombuffer(raw,np.uint8).reshape(180,320,3)
    assert np.max(np.abs(frame[90,160].astype(int)-[201,24,66]))<12
    assert np.max(np.abs(frame[10,10].astype(int)-[12,33,211]))<12
    from core.ffmpeg import ffprobe
    assert any(s['codec_type']=='audio' for s in ffprobe(str(output))['streams'])
