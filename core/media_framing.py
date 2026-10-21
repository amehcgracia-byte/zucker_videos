"""Automatic framing for ordinary imported clips, never for keyed effect footage."""
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np


def active_rectangle(frames):
    """Remove only black edge strips present in every usable sampled frame."""
    usable = [f for f in frames if f is not None and np.mean(f) > 18]
    if len(usable) < 3:
        return None
    h,w=usable[0].shape[:2]
    edges=[]
    for f in usable:
        black=np.max(f,axis=2)<16
        rows=np.mean(black,axis=1)>.995
        cols=np.mean(black,axis=0)>.995
        def lead(a):
            indices=np.flatnonzero(~a)
            return int(indices[0]) if len(indices) else 0
        edges.append([lead(cols),lead(rows),lead(cols[::-1]),lead(rows[::-1])])
    l,t,r,b=np.min(edges,axis=0)
    # Dark shots and dramatic lighting must not cause large automatic crops.
    if l+r>=w*.65 or t+b>=h*.65:return None
    l,t,r,b=[int(v) if v>=2 else 0 for v in (l,t,r,b)]
    return (l,t,w-l-r,h-t-b)


@lru_cache(maxsize=128)
def _inspect(path, size, stamp):
    cap=cv2.VideoCapture(path)
    try:
        w,h=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if not cap.isOpened() or min(w,h,count)<=0:return None
        # Bounded reads, not a full decode; one stable crop for the entire clip.
        frames=[]
        ratio=min(1.,480/max(w,h))
        for index in np.linspace(0,max(0,count-1),9).astype(int):
            cap.set(cv2.CAP_PROP_POS_FRAMES,int(index));ok,frame=cap.read()
            if ok:frames.append(cv2.resize(frame,(max(1,round(w*ratio)),max(1,round(h*ratio)))))
        rectangle=active_rectangle(frames)
        if rectangle:
            x,y,cw,ch=rectangle
            # Even dimensions/offsets for chroma-subsampled decoding.
            x,y=int(x/ratio)//2*2,int(y/ratio)//2*2
            cw,ch=min(w-x,round(cw/ratio))//2*2,min(h-y,round(ch/ratio))//2*2
        else:x,y,cw,ch=0,0,w//2*2,h//2*2
        return dict(x=x,y=y,width=cw,height=ch,portrait=cw<ch)
    finally:cap.release()


def inspect(source):
    path=Path(source)
    try:
        stat=path.stat()
        return _inspect(str(path.resolve()),stat.st_size,stat.st_mtime_ns)
    except OSError:return None


def filters(input_label, output_label, source, width, height):
    info=inspect(source)
    crop=f"crop={info['width']}:{info['height']}:{info['x']}:{info['y']}," if info else ''
    cover=f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if info and info['portrait'] and width>height:
        key=output_label
        return [f"[{input_label}]{crop}split=2[{key}front][{key}back]",
                f"[{key}back]{cover},gblur=sigma=24[{key}blur]",
                f"[{key}front]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[{key}fit]",
                f"[{key}blur][{key}fit]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[{output_label}]"]
    return [f"[{input_label}]{crop}{cover},format=yuv420p[{output_label}]"]


def frame(rgb,width,height,info=None):
    """The equivalent framing for live/composited video readers (RGB)."""
    if info:
        rgb=rgb[info['y']:info['y']+info['height'],info['x']:info['x']+info['width']]
    h,w=rgb.shape[:2]
    scale=max(width/w,height/h)
    enlarged=cv2.resize(rgb,(max(width,round(w*scale)),max(height,round(h*scale))))
    eh,ew=enlarged.shape[:2]
    result=enlarged[(eh-height)//2:(eh-height)//2+height,(ew-width)//2:(ew-width)//2+width].copy()
    if w<h and width>height:
        result=cv2.GaussianBlur(result,(0,0),max(2,width/40))
        scale=min(width/w,height/h);small=cv2.resize(rgb,(max(1,round(w*scale)),max(1,round(h*scale))))
        sh,sw=small.shape[:2];result[(height-sh)//2:(height-sh)//2+sh,(width-sw)//2:(width-sw)//2+sw]=small
    return result
