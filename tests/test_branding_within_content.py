"""Real export: branding changes pictures, never the chosen audio clock/duration."""
import json
import subprocess
import numpy as np
import pytest
from PIL import Image
from core.project import create_project
from core.stages import export

@pytest.mark.parametrize('personal,alpha,background',[(False,True,'black'),(True,True,'black'),(True,False,'white'),(True,False,'black')])
def test_branding_keeps_duration_audio_and_real_transparency(tmp_path,monkeypatch,personal,alpha,background):
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    logo=tmp_path/'logo.png'
    image=Image.new('RGBA' if alpha else 'RGB',(96,96),(0,0,0,0) if alpha else background)
    image.paste((255,0,0,255) if alpha else (255,0,0),(24,24,72,72));image.save(logo)
    config={'branding':{'mode':'custom' if personal else 'default','watermark_path':str(logo),'bookends':'within-content'}}
    if personal:config['personal_logo_path']=str(logo)
    monkeypatch.setattr(export,'_global_config',lambda:config)
    def command(args):return subprocess.check_output(args,stderr=subprocess.STDOUT)
    source=tmp_path/'camera.mp4';master=tmp_path/'master.wav'
    command(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:s=320x180:r=30:d=4','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
    command(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=330:duration=4','-ar','48000',str(master)])
    p=create_project('Logo clock',str(tmp_path/'edit.zuckervid'))
    p.data['inputs']['videos']=[{'path':str(source),'probe':{'width':320,'height':180,'duration':4,'fps':30,'video_codec':'h264'}}]
    p.data['settings']['export']['verify_motion']=False
    segment={'source_path':str(source),'clip_path':str(source),'clip_start_sec':0,'duration_sec':4,'master_start_sec':0}
    monkeypatch.setattr(export,'_color_profiles_for_segments',lambda *a:{})
    monkeypatch.setattr(export,'_render_logo_clip',lambda *a,**kw:pytest.fail('Added standalone seconds'))
    output=tmp_path/'out.mp4'
    export._render_plan(p,[segment],str(master),output,'youtube',1_000_000,[],lambda *a:None)
    info=json.loads(command(['ffprobe','-v','error','-show_format','-of','json',str(output)]))
    assert abs(float(info['format']['duration'])-4)<.08
    # Audio is present beneath both opening and closing graphics.
    for start in (.15,3.5):
        raw=command(['ffmpeg','-v','error','-ss',str(start),'-t','0.15','-i',str(output),'-vn','-f','f32le','-ac','1','-'])
        assert np.sqrt(np.mean(np.frombuffer(raw,dtype='<f4')**2))>.001
    raw=command(['ffmpeg','-v','error','-ss','0.6','-i',str(output),'-frames:v','1','-vf','scale=192:108','-f','rawvideo','-pix_fmt','rgb24','-'])
    frame=np.frombuffer(raw,dtype=np.uint8).reshape(108,192,3)
    # Transparent art reveals footage during the opening fade, with the logo still visible.
    if alpha:assert frame[10,10,2]>80
    elif background=='black':assert frame[10,96].max()<15
    else:assert frame[10,96].min()>180  # white background remains white
    assert frame[54,96,0]>150  # mark is visible, not lost by alpha detection
