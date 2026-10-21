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


def light_pixels(rgb):
    """Recover soft filmed light from a tinted green plate, retaining faint rain/smoke."""
    x=rgb.astype(np.float32)
    border=np.concatenate((x[:5].reshape(-1,3),x[-5:].reshape(-1,3),x[:,:5].reshape(-1,3),x[:,-5:].reshape(-1,3)))
    greens=border[border[:,1]-np.maximum(border[:,0],border[:,2])>30]
    bg=np.median(greens,axis=0) if len(greens) else np.array([0.,255.,0.])
    rb=(x[:,:,[0,2]]-bg[[0,2]])/np.maximum(1.,255-bg[[0,2]])
    alpha=np.clip(rb.max(axis=2),0,1)
    pixels=np.clip(x-bg[None,None,:]*(1-alpha[...,None]),0,255)
    pixels=np.minimum(pixels,alpha[...,None]*255)
    return np.rint(pixels).astype(np.uint8),alpha


def light_sample(source,target,ffmpeg):
    """Bounded two-second editor host; the screen blend then matches the export."""
    import subprocess,tempfile
    cap=cv2.VideoCapture(str(source));w=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH));h=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT));fps=cap.get(cv2.CAP_PROP_FPS) or 30
    pending=target.with_suffix('.pending.mp4')
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([ffmpeg,'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0','-an','-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart',str(pending)],stdin=subprocess.PIPE,stderr=errors)
        try:
            while True:
                ok,bgr=cap.read()
                if not ok:break
                pixels,_=light_pixels(cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB));process.stdin.write(pixels.tobytes())
            process.stdin.close()
            if process.wait(timeout=90):raise ValueError('Could not prepare the soft texture preview')
            pending.replace(target)
        finally:
            cap.release()
            if process.poll() is None:process.kill();process.wait()
            pending.unlink(missing_ok=True)
    return target


def black_alpha(rgb):
    # Black-backed photographed smoke/light is already premultiplied footage.
    return np.max(rgb.astype(np.float32)/255,axis=2)


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
        from core.show_follow import Follow
        self.follow=Follow(raw["follow_anchor"],width,height,raw.get("follow_template"),raw.get("follow_seconds")) if raw.get("follow_anchor") else None
        self.tracked_offset=(0.,0.);self.tracked_visible=True
        self.raw=raw;self.at=float(raw['at']);self.duration=float(raw['duration']);self.fps=fps
        self.video=VideoBackground(raw['video_layer'],width,height,contain=raw.get('matte')=='opaque')
        self.masks=np.load(raw['masks'],mmap_mode='r') if raw.get('masks') else None
        if raw.get('matte')=='person' and self.masks is None:
            self.video.capture.release()
            raise ValueError('Prepare this moving person cutout before rendering it.')
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
        if not self.tracked_visible:return
        local=t-self.at
        rgb=self.video.at(local+float(self.raw.get('clip_start') or 0)).astype(np.uint8)
        if self.raw.get("matte")=="green-light":rgb,_=light_pixels(rgb)
        h,w=frame.shape[:2]
        if self.masks is not None:
            i=min(len(self.masks)-1,max(0,int(round(local*self.fps))))
            mask=cv2.resize(np.asarray(self.masks[i],dtype=np.float32)/255,(w,h))[...,None]
        elif self.raw.get('matte')=='opaque':
            mask=np.ones((h,w,1),np.float32)
        else:
            mask=(black_alpha(rgb) if self.raw.get("matte") in ("black","green-light") else green_alpha(rgb))[...,None]
        behind=self.background.at(local) if hasattr(self.background,'at') else self.background
        if behind is None:behind=frame
        from core.show_follow import colour
        person=np.full_like(rgb,8) if self.raw.get('person_style')=='silhouette' else colour(rgb,self.raw.get('brightness',1),self.raw.get('saturation',1),self.raw.get('hue',0))
        screen=self.raw.get('matte') in ('black','green-light') and self.raw.get('person_style')!='silhouette'
        if screen:mask=person.astype(np.float32)/255
        foreground=person.astype(np.float32) if screen else person.astype(np.float32)*mask
        from core.show_geometry import transform_pixels
        geometry=np.concatenate((foreground,mask),axis=2)
        geometry=transform_pixels(geometry,self.raw.get('crop'),self.raw.get('pan'))
        foreground,mask=geometry[...,:3],geometry[...,3:]
        scale=float(self.raw.get('person_scale',1));offset=self.raw.get('person_offset') or (0.,0.)
        if self.follow and self.raw.get('follow_place'):
            alpha=mask if mask.ndim==2 else mask[...,0]
            ys,xs=np.nonzero(alpha>.1)
            if not len(xs):return
            # The subject may occupy either side of its own source frame.
            # Attach its visible bounding box, not the centre of the whole video.
            cx=(xs.min()+xs.max()+1)/2/w;cy=(ys.min()+ys.max()+1)/2/h
            offset=(offset[0]+self.follow.start[0]-(cx*scale+(1-scale)/2),
                    offset[1]+self.follow.start[1]-(cy*scale+1-scale))
        offset=(offset[0]+self.tracked_offset[0],offset[1]+self.tracked_offset[1])
        if scale!=1 or tuple(offset)!=(0.,0.) or self.raw.get('stretch_x',1)!=1 or self.raw.get('stretch',1)!=1 or self.raw.get('rotate',0):
            sx=scale*float(self.raw.get('stretch_x',1.));sy=scale*float(self.raw.get('stretch',1.))
            matrix=np.array([[sx,0,w*(1-sx)/2+offset[0]*w],[0,sy,h*(1-scale)+h*(scale-sy)/2+offset[1]*h]],np.float32)
            if self.raw.get('rotate'):
                center=(w*(.5+offset[0]),h*(1-scale/2+offset[1]))
                rotate=cv2.getRotationMatrix2D(center,-float(self.raw['rotate']),1.)
                matrix=(np.vstack([rotate,[0,0,1]])@np.vstack([matrix,[0,0,1]]))[:2].astype(np.float32)
            foreground=cv2.warpAffine(foreground,matrix,(w,h))
            mask=cv2.warpAffine(mask,matrix,(w,h))
            if mask.ndim==2:mask=mask[...,None]
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
        if not video and ext!='.mp3':
            from core.audio_import import mp3
            converted=pending.with_suffix('.normalized.mp3')
            try:
                mp3(pending,converted,ffmpeg)
                pending.unlink();converted.replace(pending)
                digest=hashlib.sha256(pending.read_bytes());ext='.mp3'
            finally:converted.unlink(missing_ok=True)
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
