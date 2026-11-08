import copy
import shutil
import subprocess
import numpy as np
import pytest
from PIL import Image
from core.shot_continuity import continuous_views
from core.spherical_motion import motion_pose


def test_same_view_uses_one_motion_clock_without_changing_source_time():
    shots = [{'source_path':'camera.mp4', 'master_start_sec':i*4., 'clip_start_sec':10+i*4., 'duration_sec':4.,
              'spherical_shot':{'type':'piano','yaw':25.,'pitch':-10.,'fov':80.,'runtime_motion_enabled':True,
                                'movement':'pan_right' if i==0 else 'push_in'}} for i in range(2)]
    original=copy.deepcopy(shots);result=continuous_views(shots)
    assert shots==original
    assert result[1]['clip_start_sec']==14
    assert motion_pose(result[0]['spherical_shot'],4,4)==motion_pose(result[1]['spherical_shot'],4,0)
    # A separately aimed view must remain a deliberate camera change.
    shots[1]['spherical_shot']['yaw']=50
    assert 'continuity_offset_sec' not in continuous_views(shots)[1]['spherical_shot']
    shots[1]['spherical_shot']['yaw']=25;shots[1]['clip_start_sec']=50
    assert 'continuity_offset_sec' not in continuous_views(shots)[1]['spherical_shot']


@pytest.mark.parametrize('opaque',[False,True])
def test_real_logo_fades_footage_under_logo_without_adding_time(tmp_path,monkeypatch,opaque):
    if not shutil.which('ffmpeg'):pytest.skip('ffmpeg required')
    import core.stages.export as export
    logo=tmp_path/'logo.png'
    image=Image.new('RGBA',(80,80),(255,255,255,255) if opaque else (0,0,0,0))
    for x in range(25,55):
        for y in range(25,55):image.putpixel((x,y),(255,0,0,255))
    image.save(logo)
    monkeypatch.setattr(export,'_target_size',lambda p:(320,180))
    monkeypatch.setattr(export,'_personal_logo_path',lambda:logo)
    monkeypatch.setattr(export,'_watermark_path',lambda:logo)
    graph=export._within_content_logo_graph('[0:v]format=yuv420p[base]','base','youtube',4,{'offset':0,'total':4})
    video=tmp_path/'fade.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:s=320x180:r=30:d=4','-loop','1','-i',str(logo),'-filter_complex_threads','1','-filter_complex',graph,'-map','[v]','-t','4','-c:v','libx264','-pix_fmt','yuv420p',str(video)],check=True,capture_output=True)
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-f','rawvideo','-pix_fmt','rgb24','-threads','1','-'])
    frames=np.frombuffer(raw,dtype=np.uint8).reshape(-1,180,320,3)
    assert len(frames)==120
    start,mid,end=frames[0,5,5],frames[60,5,5],frames[-1,5,5]
    assert mid[2]>220 and mid[0]<20
    if opaque:assert min(start)>230 and min(end)>220
    else:assert max(start)<20 and max(end)<30
    # The logo is present while the footage is already appearing behind it.
    assert frames[18,90,160,0]>150
    assert frames[18,5,5,2]>30
