import subprocess
import numpy as np
from PIL import Image
from core.media_framing import active_rectangle, frame, inspect
from core.show_inserts import load_clip
from core.show_geometry import place_frame


def test_borders_must_be_black_across_all_samples():
    a=np.zeros((100,160,3),np.uint8);a[10:90,20:140]=[80,140,200]
    assert active_rectangle([a]*3)==(20,10,120,80)
    b=a.copy();b[:]=80
    assert active_rectangle([a,a,b])==(0,0,160,100)
    assert active_rectangle([np.zeros_like(a)]*4) is None


def test_portrait_keeps_center_and_fills_sides():
    source=np.full((160,90,3),[60,120,200],np.uint8)
    source[40:120,30:60]=[220,20,30]
    fitted=frame(source,320,180)
    assert np.mean(fitted[:,:50])>30
    assert fitted[90,160,0]>180
    assert fitted.shape==(180,320,3)


def test_real_clip_decode_has_blurred_sides_and_applies_geometry(tmp_path):
    source=tmp_path/'portrait.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=orange:s=90x160:r=10:d=0.5','-c:v','libx264','-threads','1',str(source)],check=True)
    assert inspect(source)['portrait']
    raw={'source':str(source),'at':0,'clip_length':.4}
    insert=load_clip(raw,160,90,10,'ffmpeg')
    assert insert.clip.shape[0]==4
    assert insert.clip[0,:,0].mean()>25
    transformed=load_clip({**raw,'person_scale':.5,'person_offset':[0,0]},160,90,10,'ffmpeg')
    assert transformed.clip[0,0].max()==0
    assert transformed.clip[0,70,80].mean()>25


def test_huge_scale_keeps_fixed_canvas():
    pixels=np.full((90,160,3),80,np.uint8)
    assert place_frame(pixels,{'person_scale':30}).shape==pixels.shape
