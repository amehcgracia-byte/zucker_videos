import subprocess
from pathlib import Path
from server.api import _validate_composition_output
from core.stages.edit import _reel_quality_start


def make_video(path, source):
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',source,'-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-t','1','-c:v','libx264','-c:a','aac',str(path)],check=True)


def test_black_validation_compares_source_and_keeps_dark_detail(tmp_path):
    dark=tmp_path/'dark.mp4'
    black=tmp_path/'black.mp4'
    make_video(dark,'color=black:s=320x180:r=30,drawbox=x=150:y=80:w=20:h=20:color=gray:t=fill')
    make_video(black,'color=black:s=320x180:r=30')
    assert _validate_composition_output(dark,dark,{})['ok']
    assert _validate_composition_output(black,black,{})['ok']
    rejected=_validate_composition_output(dark,black,{})
    assert not rejected['ok']
    assert 'black_frame_at' in rejected['reason']


def test_reel_prefers_measured_quality_and_avoids_repeating():
    source={'duration_sec':30,'director_quality':{'windows':[
        {'clip_start_sec':0,'clip_end_sec':5,'master_start_sec':0,'master_end_sec':5,'score':.1},
        {'clip_start_sec':10,'clip_end_sec':15,'master_start_sec':10,'master_end_sec':15,'score':.9},
        {'clip_start_sec':20,'clip_end_sec':25,'master_start_sec':20,'master_end_sec':25,'score':.85},
    ]}}
    assert _reel_quality_start(source,3,[],0)==10
    assert _reel_quality_start(source,3,[(10,13)],0)==20
    assert _reel_quality_start({'duration_sec':10},3,[],15)==7
