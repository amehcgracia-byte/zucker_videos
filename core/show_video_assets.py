"""Non-destructive video cutouts. Originals stay intact; recipes live with project assets."""
from __future__ import annotations
import json
import threading
import functools

_catalog_lock = threading.RLock()
_thumbnail_lock = threading.Lock()

def _catalog_locked(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        with _catalog_lock:
            return fn(*args, **kwargs)
    return wrapped

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


@_catalog_locked
def remember(project, item):
    """Recipes reference an existing project-owned clip; never duplicate its bytes."""
    from core.project import atomic_write_json
    path = project.folder/'assets/video-library/catalog.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    records = json.loads(path.read_text()) if path.is_file() else {}
    p = item['params']
    old=records.get(item['id'])
    if old and old['path']!=p['clip']:
        import hashlib
        old_id=item['id']+'-'+hashlib.sha256(old['path'].encode()).hexdigest()[:12]
        records[old_id]={**old,'id':old_id}
    records[item['id']] = dict(id=item['id'],path=p['clip'],name=p.get('name') or Path(p['clip']).stem,
                              matte=p.get('matte','none'),transparent=p.get('matte','none')!='none',params=dict(p),
                              media_kind=p.get('media_kind','video'),face_swapped=bool(p.get('clip_original') or p.get('face_swapped')),
                              url=p.get('url',''),duration=p.get('clip_length',item['duration']))
    if not path.is_file() or json.loads(path.read_text()) != records:
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


@_catalog_locked
def catalog(project):
    path=project.folder/'assets/video-library/catalog.json'
    return json.loads(path.read_text()) if path.is_file() else {}


def import_media(project, stream, filename, ffprobe, ffmpeg):
    """Copy a selected local file once, by content, and keep original bytes intact."""
    import hashlib,uuid,subprocess,math
    ext=Path(filename).suffix.lower()
    video_exts={'.mp4','.mov','.m4v','.mkv','.webm','.avi'}
    audio_exts={'.mp3','.wav','.m4a','.aac','.flac','.ogg','.opus'}
    if ext not in video_exts|audio_exts:raise ValueError('Choose a video or audio file')
    folder=project.folder/'assets/clips';folder.mkdir(parents=True,exist_ok=True)
    pending=folder/('import-'+uuid.uuid4().hex+ext)
    try:
        digest=hashlib.sha256()
        with pending.open('wb') as output:
            for chunk in iter(lambda:stream.read(8*1024*1024),b''):
                digest.update(chunk);output.write(chunk)
        meta=json.loads(subprocess.check_output([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(pending)],timeout=25))
        duration=float(meta['format']['duration'])
        if not math.isfinite(duration) or duration<=0:raise ValueError('This file has no playable duration')
        video=next((s for s in meta['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')),None)
        audio=next((s for s in meta['streams'] if s['codec_type']=='audio'),None)
        if video is None and audio is None:raise ValueError('No video or audio stream was found')
        kind='video' if video else 'audio';sha=digest.hexdigest();target=folder/('local-'+sha+ext)
        if target.exists():pending.unlink()
        else:pending.replace(target)
        preview=target
        if video and (video['codec_name']!='h264' or ext not in {'.mp4','.m4v','.mov'} or (audio and audio['codec_name'] not in {'aac','mp3','alac'})):
            preview=folder/('preview-'+sha+'.mp4')
            if not preview.exists():
                temp=preview.with_name(preview.stem+'.'+uuid.uuid4().hex+'.pending.mp4')
                try:
                    command=[ffmpeg,'-v','error','-y','-nostdin','-i',str(target),'-map','0:v:0','-map','0:a:0?']
                    command+=['-c:v','copy'] if video['codec_name']=='h264' else ['-c:v','libx264','-crf','18','-preset','fast','-threads','2','-pix_fmt','yuv420p']
                    subprocess.run(command+['-c:a','aac','-movflags','+faststart',str(temp)],check=True,capture_output=True,timeout=180)
                    temp.replace(preview)
                finally:temp.unlink(missing_ok=True)
        if not video and ext not in {'.mp3','.m4a','.wav'}:
            preview=folder/('preview-'+sha+'.m4a')
            if not preview.exists():
                temp=preview.with_name(preview.stem+'.'+uuid.uuid4().hex+'.pending.m4a')
                try:
                    subprocess.run([ffmpeg,'-v','error','-y','-nostdin','-i',str(target),'-vn','-c:a','aac','-threads','2',str(temp)],check=True,capture_output=True,timeout=180)
                    temp.replace(preview)
                finally:temp.unlink(missing_ok=True)
        item={'id':'local-'+sha[:24],'kind':'clip' if video else 'jingle','duration':duration,'params':{
            'clip':str(target),'preview':str(preview),'clip_start':0,'clip_length':duration,'clip_audio':bool(audio),
            'name':Path(filename).stem,'media_kind':kind,'sha256':sha,'matte':'none'}}
        remember(project,item)
        return catalog(project)[item['id']]
    finally:pending.unlink(missing_ok=True)


def thumbnail(project, record, ffmpeg):
    with _thumbnail_lock:
        return _thumbnail(project, record, ffmpeg)

def _thumbnail(project, record, ffmpeg):
    """Lazy project-local thumbnail; immutable source IDs avoid rereading big videos."""
    import hashlib,subprocess
    path=Path(record['path']).resolve()
    if not path.is_relative_to(project.folder.resolve()) or not path.is_file():raise ValueError('This video is missing from the project')
    token=hashlib.sha256((str(path)+record.get('thumbnail_revision','')).encode()).hexdigest()[:24]
    out=project.cache_dir/'video-thumbnails'/(token+'.jpg');out.parent.mkdir(parents=True,exist_ok=True)
    if not out.exists():
        pending=out.with_suffix('.pending.jpg')
        try:
            subprocess.run([ffmpeg,'-v','error','-y','-nostdin','-ss',str(min(.3,record.get('duration',1)/4)),'-i',str(path),
                '-frames:v','1','-vf','scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2','-threads','1',str(pending)],check=True,capture_output=True,timeout=20)
            pending.replace(out)
        finally:pending.unlink(missing_ok=True)
    return out
