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
    stamp = source.stat().st_mtime_ns if source.is_file() else 0
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
            source=Path(entry['video_layer'])
            begin=float(entry.get('clip_start') or 0)
            n=int(round((begin+entry['duration'])*fps))
            clip_plan={'source':str(source),'fps':fps,'cover':True,'shots':[{'start_frame':0,'end_frame':n}]}
            cut=[{'background':'clip-person','at':begin,'duration':entry['duration']}]
            warnings=prepare(source,cut,clip_plan,cache,ffmpeg=ffmpeg,progress=progress,cancel=cancel)
            if warnings:raise BackgroundUnavailable('; '.join(warnings))
            entry['masks']=cut[0]['masks']
    wanted = [entry for entry in entries if entry.get("background")]
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

    def active(self, t: float) -> bool:
        local = t - self.at
        return 0 <= local < self.duration and not any(a <= local < b for a, b in self.hidden)

    def close(self):
        if self.video is not None:
            self.video.capture.release()


VIDEO_SUFFIXES = {'.mp4', '.mov', '.m4v', '.webm', '.mkv'}


class VideoBackground:
    """Sequential reads; seek only on scrubs, rate changes or loops. Audio stays with the host."""
    def __init__(self, path, width, height):
        import cv2
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
                      scale=float(raw.get('person_scale', 1)), offset=tuple(raw.get('person_offset') or (0., 0.)))


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
    person = np.full_like(frame, 8) if background.style == 'silhouette' else frame
    if background.scale != 1 or background.offset != (0., 0.):
        scale = background.scale
        matrix = np.array([[scale, 0, width*(1-scale)/2+background.offset[0]*width],
                           [0, scale, height*(1-scale)+background.offset[1]*height]], np.float32)
        foreground = cv2.warpAffine(person.astype(np.float32)*mask, matrix, (width, height))
        mask = cv2.warpAffine(mask, matrix, (width, height))[..., None]
    else:
        foreground = person.astype(np.float32)*mask
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
            plan={'source':str(source),'fps':fps,'edited_duration':begin+duration,'shots':[{'start_frame':0,'end_frame':round((begin+duration)*fps)}]}
            start=begin
        else:start=float(item['at'])
        end = min(float(plan['edited_duration']), start+float(item['duration']), start+2)
        if end <= start:
            raise ValueError('Choose words for the moving video layer first')
        folder = project.cache_dir/'show-layer-editor'
        folder.mkdir(parents=True, exist_ok=True)
        part = window_plan(plan, start, end)
        if is_clip:
            stat=Path(plan['source']).stat()
            key=hashlib.sha256(json.dumps([str(plan['source']),stat.st_size,stat.st_mtime_ns,start,end]).encode()).hexdigest()[:20]
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
        notes = prepare(host, entries, plan, project.cache_dir/'show_masks', offset=start, ffmpeg=ffmpeg, progress=(lambda value,detail:progress(.3+value*.65,detail)) if progress else None)
        if notes:
            raise BackgroundUnavailable('; '.join(notes))
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
