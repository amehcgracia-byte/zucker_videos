"""Non-destructive video cutouts. Originals stay intact; recipes live with project assets."""
from __future__ import annotations
import json
from pathlib import Path
import cv2
import numpy as np


def green_alpha(rgb):
    """Soft green-screen key. Green clothing alone never enables automatic keying."""
    pixels = rgb.astype(np.float32)
    r, g, b = [pixels[..., i] for i in range(3)]
    dominance = g - np.maximum(r, b)
    return 1 - np.clip((dominance - 22) / 45, 0, 1) * np.clip((g - 65) / 50, 0, 1)


def detect_green(path):
    cap = cv2.VideoCapture(str(path))
    votes = []
    try:
        fps=cap.get(cv2.CAP_PROP_FPS);count=cap.get(cv2.CAP_PROP_FRAME_COUNT)
        duration=count/fps if fps>0 and count>0 else 1.
        for second in (0., min(.5,duration*.35), min(1.,duration*.75)):
            cap.set(cv2.CAP_PROP_POS_MSEC, second*1000)
            ok, bgr = cap.read()
            if not ok: continue
            rgb = cv2.cvtColor(cv2.resize(bgr,(160,90)),cv2.COLOR_BGR2RGB)
            green = green_alpha(rgb) < .2
            border = np.concatenate([green[:5].ravel(),green[-5:].ravel(),green[:, :5].ravel(),green[:, -5:].ravel()])
            votes.append(border.mean() > .8 and green.mean() > .3)
    finally:
        cap.release()
    return len(votes) >= 2 and sum(votes) >= 2


def remember(project, item):
    """Recipes reference an existing project-owned clip; never duplicate its bytes."""
    from core.project import atomic_write_json
    path = project.folder/'assets/video-library/catalog.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    records = json.loads(path.read_text()) if path.is_file() else {}
    p = item['params']
    records[item['id']] = dict(id=item['id'],path=p['clip'],name=Path(p['clip']).stem,
                              matte=p.get('matte','none'),transparent=p.get('matte','none')!='none',
                              url=p.get('url',''),duration=p.get('clip_length',item['duration']))
    atomic_write_json(path,records)


class VideoLayer:
    def __init__(self, raw, width, height, fps):
        from core.show_backgrounds import VideoBackground
        self.raw=raw;self.at=float(raw['at']);self.duration=float(raw['duration']);self.fps=fps
        self.video=VideoBackground(raw['video_layer'],width,height)
        self.masks=np.load(raw['masks'],mmap_mode='r') if raw.get('masks') else None
        self.background=None
        if raw.get('picture'):
            from core.show_backgrounds import VIDEO_SUFFIXES, cover
            path=Path(raw['picture'])
            self.background=VideoBackground(path,width,height) if path.suffix.lower() in VIDEO_SUFFIXES else cover(path,width,height)
    def active(self,t):return self.at<=t<self.at+self.duration
    def close(self):
        self.video.capture.release()
        if hasattr(self.background,'capture'):self.background.capture.release()
    def apply(self,frame,t):
        local=t-self.at
        rgb=self.video.at(local+float(self.raw.get('clip_start') or 0)).astype(np.uint8)
        h,w=frame.shape[:2]
        if self.masks is not None:
            i=min(len(self.masks)-1,max(0,int(round(local*self.fps))))
            mask=cv2.resize(np.asarray(self.masks[i],dtype=np.float32)/255,(w,h))[...,None]
        else:
            mask=green_alpha(rgb)[...,None]
        behind=self.background.at(local) if hasattr(self.background,'at') else self.background
        if behind is None:behind=frame
        person=np.full_like(rgb,8) if self.raw.get('person_style')=='silhouette' else rgb
        foreground=person.astype(np.float32)*mask
        scale=float(self.raw.get('person_scale',1));offset=self.raw.get('person_offset') or (0.,0.)
        if scale!=1 or tuple(offset)!=(0.,0.):
            matrix=np.array([[scale,0,w*(1-scale)/2+offset[0]*w],[0,scale,h*(1-scale)+offset[1]*h]],np.float32)
            foreground=cv2.warpAffine(foreground,matrix,(w,h))
            mask=cv2.warpAffine(mask,matrix,(w,h))[...,None]
        frame[:]=np.clip(foreground+behind*(1-mask)+.5,0,255).astype(np.uint8)
