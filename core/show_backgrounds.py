"""A new background behind the person while they keep talking: the kitchen, outside, the
atelier… The person is cut out of every frame of the edited video (macOS Vision person
segmentation, on this computer; nothing is uploaded) and the chosen picture goes behind.

Masks are made once per background span from the edited cut, every other frame (blended
between), and kept in the project's cache keyed by the source frames and their framing,
so a scene preview and the final render (any size) share them. Each frame's mask is then
refined against that frame's own edges, so hair and shoulders stay clean at full size.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

MASK_SIZE = (384, 216)          # stored masks (width, height): 16:9, shared by every render size
SEGMENT_SIZE = (640, 360)       # frames given to Vision
STEP = 2                        # a mask every other frame; the ones between are blended
FADE_SEC = 0.25                 # the new background fades in and out
VERSION = "person-v1"
_vision_lock = threading.Lock()
_editor_lock = threading.Lock()


class BackgroundUnavailable(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "darwin" and _vision() is not None


_bundle: dict[str, Any] | None = None


def _vision() -> dict[str, Any] | None:
    global _bundle
    if _bundle is None:
        try:
            import objc
            bundle: dict[str, Any] = {}
            objc.loadBundle("Vision", bundle, bundle_path="/System/Library/Frameworks/Vision.framework")
            _bundle = bundle if "VNGeneratePersonSegmentationRequest" in bundle else {}
        except Exception:  # not a Mac, or no Vision
            _bundle = {}
    return _bundle or None


class Segmenter:
    """Person masks for consecutive frames (Vision keeps them steady from one frame to the next)."""

    def __init__(self) -> None:
        bundle = _vision()
        if bundle is None:
            raise BackgroundUnavailable("Changing the background needs macOS 12 or later (person cut-out by the Mac)")
        import Quartz
        self.quartz = Quartz
        self.sequence = bundle["VNSequenceRequestHandler"].alloc().init()
        self.request = bundle["VNGeneratePersonSegmentationRequest"].alloc().init()
        self.request.setQualityLevel_(1)                 # balanced: clean enough, twice as fast as accurate
        self.request.setOutputPixelFormat_(1278226488)   # 'L008': one byte per pixel
        self.space = Quartz.CGColorSpaceCreateDeviceRGB()

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        # Flask/editor workers do not own Cocoa's main-thread autorelease pool.
        # Drain Vision's temporary Objective-C objects after each copied mask.
        import objc
        with objc.autorelease_pool():
            return self._segment(rgb)

    def _segment(self, rgb: np.ndarray) -> np.ndarray:
        Quartz = self.quartz
        height, width = rgb.shape[:2]
        bgra = np.dstack([rgb[..., ::-1], np.full((height, width, 1), 255, np.uint8)]).tobytes()
        provider = Quartz.CGDataProviderCreateWithData(None, bgra, len(bgra), None)
        image = Quartz.CGImageCreate(width, height, 8, 32, width * 4, self.space,
                                     Quartz.kCGBitmapByteOrder32Little | Quartz.kCGImageAlphaPremultipliedFirst,
                                     provider, None, False, Quartz.kCGRenderingIntentDefault)
        done = self.sequence.performRequests_onCGImage_error_([self.request], image, None)
        if not (done[0] if isinstance(done, tuple) else done) or not self.request.results():
            raise BackgroundUnavailable("The Mac could not find a person in this picture")
        buffer = self.request.results()[0].pixelBuffer()
        Quartz.CVPixelBufferLockBaseAddress(buffer, 1)
        try:
            w, h = Quartz.CVPixelBufferGetWidth(buffer), Quartz.CVPixelBufferGetHeight(buffer)
            row = Quartz.CVPixelBufferGetBytesPerRow(buffer)
            raw = bytes(Quartz.CVPixelBufferGetBaseAddress(buffer).as_buffer(row * h))
        finally:
            Quartz.CVPixelBufferUnlockBaseAddress(buffer, 1)
        import cv2
        mask = np.frombuffer(raw, np.uint8).reshape(h, row)[:, :w]
        return cv2.resize(mask, MASK_SIZE, interpolation=cv2.INTER_LINEAR)


def _frames_key(plan: dict[str, Any], start: float, count: int) -> str:
    """What the masks of ``count`` edited frames from ``start`` (edited seconds) depend on:
    the source frames shown and how each shot is framed (not the render size)."""
    fps = int(plan.get("fps") or 30)
    first = int(round(start * fps))
    frames: list[Any] = []
    offset = 0
    for shot in plan["shots"]:
        length = int(shot["end_frame"]) - int(shot["start_frame"])
        lo, hi = max(first, offset), min(first + count, offset + length)
        if hi > lo:
            frames.append([int(shot["start_frame"]) + lo - offset, hi - lo, shot.get("zoom"), shot.get("focus")])
        offset += length
    source = Path(str(plan.get("source") or ""))
    stamp = plan.get("_mask_source_hash") or (file_digest(source) if source.is_file() else '')
    payload = [VERSION, str(source), stamp, plan.get("framing"), frames, list(MASK_SIZE), STEP, plan.get("cover")]
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def prepare(cut: Path, entries: list[dict[str, Any]], plan: dict[str, Any], cache: Path, *, offset: float = 0.0,
            hidden: list[tuple[float, float]] | None = None, ffmpeg: str = "ffmpeg",
            progress: Callable[[float, str], None] | None = None, cancel: Callable[[], None] | None = None) -> list[str]:
    """Person masks for each background entry (``at`` on the cut's clock; ``offset`` is where
    the cut starts in the edit), written to the cache and set as ``entry["masks"]``. Frames
    during a close-up (``hidden``) keep their own picture. Returns notes for those left out."""
    import cv2
    notes: list[str] = []
    fps = float(plan.get("fps") or 30)
    for entry in entries:
        if entry.get('video_layer') and entry.get('matte')=='person':
            entry['masks']=str(source_masks(Path(entry['video_layer']),float(entry.get('clip_start') or 0),
                float(entry['duration']),fps,cache,ffmpeg=ffmpeg,progress=progress,cancel=cancel))
    wanted = [entry for entry in entries if entry.get("background")]
    if wanted:
        source=Path(str(plan.get("source") or ""))
        plan={**plan,"_mask_source_hash":file_digest(source) if source.is_file() else ""}
    total = sum(max(1, int(round(float(entry["duration"]) * fps))) for entry in wanted)
    done = 0
    cache.mkdir(parents=True, exist_ok=True)
    for entry in wanted:
        count = max(1, int(round(float(entry["duration"]) * fps)))
        target = cache / f"{_frames_key(plan, float(entry['at']) + offset, count)}.npy"
        if not target.is_file():
            try:
                segment = Segmenter()
            except BackgroundUnavailable as exc:
                notes.append(f"Background at {float(entry['at']) + offset:.1f} s: {exc}")
                entry["background"] = None
                continue
            width, height = SEGMENT_SIZE
            reader = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{float(entry['at']):.3f}", "-i", str(cut),
                                       "-frames:v", str(count), "-vf", (f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}" if plan.get("cover") else f"scale={width}:{height}"), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            masks = np.zeros((count, MASK_SIZE[1], MASK_SIZE[0]), np.uint8)
            made: list[int] = []
            # A decoder stuck in read() must not block every editor queued behind it.
            deadline=threading.Timer(90,reader.kill);deadline.daemon=True;deadline.start()
            try:
                with _vision_lock:
                    for index in range(count):
                        raw = reader.stdout.read(width * height * 3)
                        if len(raw) < width * height * 3:
                            masks = masks[:index]
                            break
                        if index % STEP == 0 or index == count - 1:
                            masks[index] = segment(np.frombuffer(raw, np.uint8).reshape(height, width, 3))
                            made.append(index)
                        done += 1
                        if index % 15 == 0:
                            if cancel:
                                cancel()
                            if progress:
                                progress(done / max(1, total), f"Cutting you out for the new background — {done / max(1, total):.0%}")
            finally:
                deadline.cancel()
                reader.kill()
                reader.wait()
            if not made or len(masks)!=count:
                raise BackgroundUnavailable('The cutout source stopped decoding. Try a shorter span or another source video.')
            # The frames between two masks: blended (a person moves little in a 15th of a second).
            for a, b in zip(made, made[1:]):
                for index in range(a + 1, b):
                    share = (index - a) / (b - a)
                    masks[index] = np.rint(masks[a] * (1 - share) + masks[b] * share).astype(np.uint8)
            pending = target.with_suffix(".pending.npy")
            np.save(pending, masks)
            pending.replace(target)
        else:
            done += count
        entry["masks"] = str(target)
        entry["hidden"] = [[a - float(entry["at"]), b - float(entry["at"])] for a, b in (hidden or [])
                           if b > float(entry["at"]) and a < float(entry["at"]) + float(entry["duration"])]
    return notes


@dataclass
class Background:
    at: float
    duration: float
    picture: np.ndarray            # the new background, at the frame's size (float32 0..255 RGB)
    masks: np.ndarray              # (frames, h, w) uint8, memory-mapped
    hidden: list[list[float]]
    fps: float
    video: Any = None
    style: str = "person"
    scale: float = 1.0
    offset: tuple[float, float] = (0., 0.)
    brightness: float = 1.
    saturation: float = 1.
    hue: float = 0.
    geometry: dict | None = None

    def active(self, t: float) -> bool:
        local = t - self.at
        return 0 <= local < self.duration and not any(a <= local < b for a, b in self.hidden)

    def close(self):
        if self.video is not None:
            self.video.capture.release()


VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}


class VideoBackground:
    """Sequential reads; seek only on scrubs, rate changes or loops. Audio stays with the host."""
    def __init__(self, path, width, height, contain=False):
        import cv2
        self.contain = contain
        from core.media_framing import inspect
        self.framing = inspect(path) if contain else None
        self.capture = cv2.VideoCapture(str(path))
        self.fps = self.capture.get(cv2.CAP_PROP_FPS)
        self.count = int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width, self.height, self.index, self.frame = width, height, -1, None
        if not self.capture.isOpened() or self.fps <= 0 or self.count <= 0:
            self.capture.release()
            raise BackgroundUnavailable('This background video could not be decoded')

    def at(self, seconds):
        import cv2
        index = max(0, int(seconds*self.fps)) % self.count
        if index == self.index:
            return self.frame
        if index != self.index+1:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if not ok:
            raise BackgroundUnavailable('The background video stopped decoding')
        h, w = frame.shape[:2]
        if self.contain:
            from core.media_framing import frame as fit_frame
            self.frame=fit_frame(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB),self.width,self.height,self.framing).astype(np.float32)
            self.index=index
            return self.frame
        factor = max(self.width/w, self.height/h)
        frame = cv2.resize(frame, (max(self.width, round(w*factor)), max(self.height, round(h*factor))))
        h, w = frame.shape[:2]
        self.frame = cv2.cvtColor(frame[(h-self.height)//2:(h-self.height)//2+self.height,
                                      (w-self.width)//2:(w-self.width)//2+self.width], cv2.COLOR_BGR2RGB).astype(np.float32)
        self.index = index
        return self.frame


def cover(path: str | Path, width: int, height: int) -> np.ndarray:
    """The picture filling the frame (cropped to its shape, never stretched)."""
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    flat = Image.new("RGB", image.size, (24, 24, 24))
    flat.paste(image, mask=image.getchannel("A"))
    return np.asarray(ImageOps.fit(flat, (width, height), Image.LANCZOS)).astype(np.float32)


def load(raw: dict[str, Any], width: int, height: int, fps: float) -> Background | None:
    if not raw.get("background") or not raw.get("masks"):
        return None
    masks = np.load(raw["masks"], mmap_mode="r")
    video = VideoBackground(raw['background'], width, height) if Path(raw['background']).suffix.lower() in VIDEO_SUFFIXES else None
    return Background(float(raw["at"]), float(raw["duration"]), video.at(0) if video else cover(raw["background"], width, height), masks,
                      [list(span) for span in raw.get("hidden") or []], fps, video=video, style=raw.get('person_style', 'person'),
                      scale=float(raw.get('person_scale', 1)), offset=tuple(raw.get('person_offset') or (0., 0.)),brightness=raw.get('brightness',1),saturation=raw.get('saturation',1),hue=raw.get('hue',0), geometry=raw)


def _guided(guide: np.ndarray, source: np.ndarray, radius: int, eps: float = 1e-3) -> np.ndarray:
    """He's guided filter: the soft mask follows the frame's own edges (hair, shoulders)."""
    import cv2
    size = (2 * radius + 1, 2 * radius + 1)
    mean = lambda image: cv2.boxFilter(image, cv2.CV_32F, size)  # noqa: E731
    mean_i, mean_p = mean(guide), mean(source)
    cov = mean(guide * source) - mean_i * mean_p
    var = mean(guide * guide) - mean_i * mean_i
    a = cov / (var + eps)
    b = mean_p - a * mean_i
    return mean(a) * guide + mean(b)


def apply(frame: np.ndarray, background: Background, t: float) -> None:
    """Put the new background behind the person in ``frame`` (RGB uint8, changed in place)."""
    import cv2
    local = t - background.at
    index = min(len(background.masks) - 1, max(0, int(round(local * background.fps))))
    height, width = frame.shape[:2]
    small = np.asarray(background.masks[index], dtype=np.float32) / 255.0
    guide = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    mask = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    mask = np.clip(_guided(guide, mask, max(2, width // 240)), 0.0, 1.0)[..., None]
    fade = min(1.0, local / FADE_SEC, (background.duration - local) / FADE_SEC)
    behind = background.video.at(local) if background.video else background.picture
    from core.show_follow import colour
    person = np.full_like(frame, 8) if background.style == 'silhouette' else colour(frame,background.brightness,background.saturation,background.hue)
    from core.show_geometry import place_frame
    geometry={**(background.geometry or {}), 'person_scale':background.scale,'person_offset':background.offset}
    pixels=place_frame(np.concatenate((person.astype(np.float32)*mask,mask),axis=2),geometry)
    foreground,mask=pixels[...,:3],pixels[...,3:]
    composite = foreground+behind*(1-mask)
    frame[:] = np.clip(composite*fade+frame.astype(np.float32)*(1-fade)+.5, 0, 255).astype(np.uint8)


def editor_sample(project, item, *, ffmpeg='ffmpeg', progress=None):
    """Prepare a bounded live preview. Saved placement applies to the full effect span.

    The actual cut and production person masks are reused; no new image is generated.
    Private media and its alpha atlas stay in this project's cache.
    """
    from PIL import Image
    from core.show import window_plan, _cached_cut
    with _editor_lock:
        plan = json.loads((project.artifacts_dir/'show_plan.json').read_text())
        if progress:progress(.02,'Preparing the source video…')
        is_clip=item['kind']=='clip'
        if is_clip:
            source=Path(item['params']['clip']);begin=float(item['params'].get('clip_start') or 0)
            duration=min(2.,float(item['duration']),float(item['params']['clip_length'])-begin)
            fps=30
            plan={'source':str(source),'fps':fps,'cover':True,'edited_duration':begin+duration,'shots':[{'start_frame':0,'end_frame':round((begin+duration)*fps)}]}
            start=begin
        else:start=float(item['at'])
        end = min(float(plan['edited_duration']), start+float(item['duration']), start+2)
        if end <= start:
            raise ValueError('Choose words for the moving video layer first')
        folder = project.cache_dir/'show-layer-editor'
        folder.mkdir(parents=True, exist_ok=True)
        part = window_plan(plan, start, end)
        if is_clip:
            key=hashlib.sha256(json.dumps([file_digest(Path(plan['source'])),start,end]).encode()).hexdigest()[:20]
            host=folder/f'clip-{key}.mp4'
            if not host.is_file():
                pending=host.with_suffix('.pending.mp4')
                try:
                    subprocess.run([ffmpeg,'-v','error','-y','-nostdin','-ss',str(start),'-i',plan['source'],'-t',str(end-start),
                                    '-map','0:v:0','-map','0:a:0?','-vf','scale=960:540:force_original_aspect_ratio=increase,crop=960:540,fps=30',
                                    '-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac','-movflags','+faststart',str(pending)],
                                   check=True,capture_output=True,timeout=90)
                    pending.replace(host)
                finally:pending.unlink(missing_ok=True)
        else:
            host = _cached_cut(Path(plan['source']), part, folder, 'person', ffmpeg=ffmpeg, width=960, height=540, cancel=None,
                               progress=(lambda value:progress(.05+value*.002,'Preparing the source video…')) if progress else None)
        if progress:progress(.3,'Finding the moving person…')
        entries = [{'background':'live-editor', 'at':0., 'duration':end-start}]
        if is_clip:
            entries=[{'video_layer':str(source),'matte':'person','clip_start':start,'duration':end-start}]
        matte=item['params'].get('matte') if is_clip else None
        if matte=='green-light':
            from core.show_video_assets import light_sample
            light_host=host.with_name(host.stem+'-soft-key.mp4')
            if not light_host.is_file():light_sample(host,light_host,ffmpeg)
            host=light_host
        if matte in ('green','black','green-light','plain'):
            from core.show_video_assets import green_alpha,black_alpha
            from core import plain_key
            plain=item['params'].get('key') if matte=='plain' else None
            if matte=='plain' and not plain:raise ValueError('Choose how to remove this clip\'s background again (Clip settings)')
            key=hashlib.sha256((file_digest(host)+matte+json.dumps(plain,sort_keys=True)).encode()).hexdigest()[:24]
            mask_path=folder/('key-'+key+'.npy')
            if not mask_path.is_file():
                import cv2
                capture=cv2.VideoCapture(str(host));made=[]
                try:
                    while True:
                        ok,bgr=capture.read()
                        if not ok:break
                        rgb=cv2.cvtColor(cv2.resize(bgr,MASK_SIZE),cv2.COLOR_BGR2RGB)
                        made.append(np.rint((plain_key.alpha(rgb,plain) if plain else black_alpha(rgb) if matte in ('black','green-light') else green_alpha(rgb))*255).astype(np.uint8))
                finally:capture.release()
                if not made:raise ValueError('The texture has no decodable frames')
                pending=mask_path.with_suffix('.pending.npy');np.save(pending,np.asarray(made));pending.replace(mask_path)
            entries[0]['masks']=str(mask_path)
        else:
            notes = prepare(host, entries, plan, project.cache_dir/'show_masks', offset=start, ffmpeg=ffmpeg, progress=(lambda value,detail:progress(.3+value*.65,detail)) if progress else None)
            if notes:raise BackgroundUnavailable('; '.join(notes))
        masks = np.load(entries[0]['masks'], mmap_mode='r')
        atlas = folder/(Path(entries[0]['masks']).stem+'.png')
        if not atlas.is_file():
            w, h = MASK_SIZE
            pixels = np.zeros((((len(masks)+9)//10)*h, 10*w, 4), np.uint8)
            pixels[..., :3] = 255
            for i, mask in enumerate(masks):
                pixels[(i//10)*h:(i//10+1)*h,(i%10)*w:(i%10+1)*w,3] = mask
            pending = atlas.with_suffix('.pending.png')
            Image.fromarray(pixels).save(pending)
            pending.replace(atlas)
        return {'host':host, 'alpha':atlas, 'fps':float(plan.get('fps') or 30), 'frames':len(masks), 'duration':end-start}


_source_mask_lock=threading.Lock()

def file_digest(source: Path) -> str:
    """Content identity, never timestamp identity."""
    digest=hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def source_masks(source: Path, begin: float, duration: float, fps: float, cache: Path, *, ffmpeg: str,
                 progress=None, cancel=None) -> Path:
    """Persistent two-second source chunks survive re-timing and changing the background.

    Only an uncovered source chunk is segmented. Render-size-independent masks use
    the same fps/cover geometry in the editor, scene preview and final render.
    """
    import uuid
    import cv2
    fps=round(float(fps),6);out_count=max(1,int(round(duration*fps)))
    capture=cv2.VideoCapture(str(source));native_fps=float(capture.get(cv2.CAP_PROP_FPS));capture.release()
    native_fps=round(native_fps,6) if np.isfinite(native_fps) and native_fps>0 else fps
    indices=np.rint((begin+np.arange(out_count)/fps)*native_fps).astype(np.int64)
    first=int(indices[0]);count=int(indices[-1])-first+1
    if cancel:cancel()
    recipe=[VERSION,'source-chunks-v2',file_digest(source),native_fps,SEGMENT_SIZE,MASK_SIZE,STEP,'cover']
    token=hashlib.sha256(json.dumps(recipe,sort_keys=True).encode()).hexdigest()[:24]
    root=(cache.parent.parent/'assets/video-cutouts' if cache.parent.name=='cache' else cache/'source-cutouts')/token
    root.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True)
    view=hashlib.sha256(indices.tobytes()).hexdigest()[:16]
    output=cache/f'source-{token}-{view}.npy'
    if output.is_file():return output
    chunk_size=max(1,int(round(native_fps*2)))
    pieces=[]
    with _source_mask_lock:
        if output.is_file():return output
        for number in range(first//chunk_size,(first+count-1)//chunk_size+1):
            if cancel:cancel()
            target=root/f'{number:06d}.npz';base=number*chunk_size
            if not target.is_file():
                segment=Segmenter();w,h=SEGMENT_SIZE
                reader=subprocess.Popen([ffmpeg,'-v','error','-nostdin','-ss',f'{base/native_fps:.6f}','-i',str(source),
                    '-frames:v',str(chunk_size),'-vf',f'fps={native_fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}',
                    '-f','rawvideo','-pix_fmt','rgb24','-'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                timer=threading.Timer(90,reader.kill);timer.daemon=True;timer.start();frames=[];made=[]
                try:
                    with _vision_lock:
                        for index in range(chunk_size):
                            raw=reader.stdout.read(w*h*3)
                            if len(raw)!=w*h*3:break
                            last_rgb=np.frombuffer(raw,np.uint8).reshape(h,w,3)
                            frames.append(segment(last_rgb) if index%STEP==0 else None)
                            if index%STEP==0:made.append(index)
                            if cancel:cancel()
                        if frames and frames[-1] is None:frames[-1]=segment(last_rgb);made.append(len(frames)-1)
                finally:
                    timer.cancel();reader.kill();reader.wait();reader.stdout.close()
                if not frames:raise BackgroundUnavailable('No frames remain in this cutout source.')
                for a,b in zip(made,made[1:]):
                    for i in range(a+1,b):frames[i]=np.rint(frames[a]*(1-(i-a)/(b-a))+frames[b]*((i-a)/(b-a))).astype(np.uint8)
                pending=target.with_name(target.stem+'.'+uuid.uuid4().hex+'.npz')
                try:np.savez_compressed(pending,masks=np.asarray(frames,dtype=np.uint8));pending.replace(target)
                finally:pending.unlink(missing_ok=True)
            with np.load(target) as stored:masks=stored['masks']
            lo=max(first,base)-base;hi=min(first+count,base+len(masks))-base
            if hi>lo:pieces.append(masks[lo:hi])
            if progress:progress(min(1.,sum(len(piece) for piece in pieces)/count),'Reusing the saved video cutout…')
        result=np.concatenate(pieces) if pieces else np.empty((0,*MASK_SIZE[::-1]),np.uint8)
        if len(result)!=count:raise BackgroundUnavailable('The cutout ends before this timeline interval. Shorten the clip.')
        result=result[indices-first]
        pending=output.with_name(output.stem+'.'+uuid.uuid4().hex+'.npy')
        try:np.save(pending,result);pending.replace(output)
        finally:pending.unlink(missing_ok=True)
    return output
