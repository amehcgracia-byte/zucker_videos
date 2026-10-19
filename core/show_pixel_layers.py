"""Animate selected photographic regions without replacing their original pixels.

The small exposed holes are filled once from the surrounding photo. The moving
patches themselves keep their RGB/alpha unchanged and move by integer pixels.
This is a still-photo layer, not video segmentation or automatic eye tracking.
"""
from __future__ import annotations

import math
import cv2
import numpy as np


def direction_vector(name):
    vectors={'horizontal':(1.,0.),'vertical':(0.,1.),'diagonal':(.70710678,.70710678),
             'diagonal-up':(.70710678,-.70710678),'down':(0.,1.),'up':(0.,-1.),'left':(-1.,0.),'right':(1.,0.)}
    if not isinstance(name,str) or name not in vectors:raise ValueError('Choose a valid movement direction')
    return vectors[name]


class LookingPixels:
    """Original eyes that glance: inside each marked eye the middle (the iris) slides sideways
    while the eye's outline stays put, like a liquify push. Pixels are moved, never redrawn
    or blended (nearest sampling), and everything outside the marked eyes stays exact."""

    def __init__(self, pixels: np.ndarray, regions: list[dict], amount: float = 0.05, direction: str = "horizontal"):
        self.direction = direction_vector(direction)
        from core.show_inserts import region_polygon
        if not 1 <= len(regions) <= 8 or not math.isfinite(amount) or not 0 <= amount <= 0.5:
            raise ValueError('Mark one to eight regions; movement must be between zero and 0.5')
        self.original = pixels
        h, w = pixels.shape[:2]
        self.patches = []
        for region in regions:
            points = np.asarray(region_polygon(region), dtype=float)
            if not np.isfinite(points).all() or np.any(points < 0) or np.any(points > 1):
                raise ValueError('Regions must be inside the image')
            mask = np.zeros((h, w), np.uint8)
            cv2.fillPoly(mask, [np.rint(points * [w-1, h-1]).astype(np.int32)], 1)
            mask &= (pixels[..., 3] > 0).astype(np.uint8)
            ys, xs = np.nonzero(mask)
            if not len(xs) or xs.max() - xs.min() < 2:
                raise ValueError('The selected region is too small or transparent')
            x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
            u = (np.arange(x0, x1) - x0 + 0.5) / (x1 - x0)
            v = (np.arange(y0, y1) - y0 + 0.5) / (y1 - y0)
            # Most at the centre, nothing at the edges: the corners and lids stay where they are.
            bump = np.sqrt(np.sin(np.pi * v))[:, None] * np.sin(np.pi * u)[None, :]
            reach = (x1 - x0) * amount
            self.patches.append(((y0, y1, x0, x1), mask[y0:y1, x0:x1].astype(bool), (bump * reach).astype(np.float32), reach))
        self._cache: dict[int, np.ndarray] = {}

    def frame(self, share: float) -> np.ndarray:
        # One glance left, hold, glance right, hold, then return. No time wrapping.
        phase = float(np.interp(np.clip(share, 0, 1), [0,.12,.25,.40,.55,.75,.9,1], [0,0,-1,-1,1,1,0,0]))
        step = int(round(phase * 16))                  # a held gaze is one picture, reused
        if not step or not any(reach >= 0.5 for *_, reach in self.patches):
            return self.original
        if step not in self._cache:
            result = self.original.copy()
            for (y0, y1, x0, x1), inside, push, _ in self.patches:
                gx, gy = np.meshgrid(np.arange(x0, x1, dtype=np.float32), np.arange(y0, y1, dtype=np.float32))
                dx,dy = self.direction
                source = np.clip(np.rint(gx - push * (step / 16)*dx), x0, x1 - 1).astype(np.int32)
                rows = np.clip(np.rint(gy - push * (step / 16)*dy),y0,y1-1).astype(np.int32)
                moved = self.original[rows, source]
                # Only inside the eye, and only from inside it: nothing slides in from the skin around.
                take = inside & inside[rows - y0, source - x0]
                block = result[y0:y1, x0:x1]
                block[take] = moved[take]
            if len(self._cache) > 40:
                self._cache.clear()
            self._cache[step] = result
        return self._cache[step]


# How far original eyes glance, as a share of each eye's width: 5 % was one or two pixels.
EYE_GLANCE = 0.22
EYE_CYCLE_SEC = 3.0      # a talking character glances left and right every three seconds


def render_preview(photo, regions, output, ffmpeg='ffmpeg', amount=EYE_GLANCE, direction='horizontal'):
    """Bounded three-second review clip; exported layers use the same compositor."""
    from pathlib import Path
    import subprocess
    from PIL import Image
    image = Image.open(photo).convert('RGBA')
    image.thumbnail((480, 480))
    # Keep even dimensions for H.264. No scaling is applied to stored source assets.
    w, h = image.size
    image = image.crop((0, 0, w-w%2, h-h%2))
    pixels = np.asarray(image).astype(np.float32) / 255
    pixels[..., :3] *= pixels[..., 3:4]
    layer = LookingPixels(pixels, regions, amount, direction)
    frames = []
    for i in range(37):
        frame = layer.frame(i / 36)
        rgb = frame[..., :3] + (1-frame[..., 3:4]) * np.array([23,32,25], np.float32)/255
        frames.append(np.clip(np.rint(rgb*255),0,255).astype(np.uint8).tobytes())
    output = Path(output)
    pending = output.with_name(output.stem+'.pending.mp4')
    try:
        subprocess.run([ffmpeg,'-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{image.width}x{image.height}',
                        '-r','12','-i','pipe:0','-an','-c:v','libx264','-preset','veryfast','-crf','18',
                        '-pix_fmt','yuv420p','-movflags','+faststart',str(pending)],
                       input=b''.join(frames), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=60)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return output


def rig_regions(record):
    """Map a saved original-pixel rig back into its uncropped source picture."""
    from PIL import Image
    from core.show_inserts import region_polygon
    w,h=Image.open(record['photo']).size
    x0,y0,x1,y1=record['crop']
    def mapped(region):
        return {'polygon':[[(x0+x*(x1-x0))/w,(y0+y*(y1-y0))/h] for x,y in region_polygon(region)]}
    return dict(eyes=[mapped(r) for r in record.get('eyes') or []],mouth=mapped(record['mouth']) if record.get('mouth') else None,
                source_size=[w,h],voice=record.get("voice"),eye_direction=record.get("eye_direction","horizontal"),mouth_direction=record.get("mouth_direction","down"))


def framed_regions(raw, width, height, regions):
    from core.show_inserts import region_polygon
    sw,sh=raw['source_size'];zoom=float(raw.get('zoom') or 1)
    fx,fy=raw.get('focus') or (.5,.5)
    cropw,croph=sw/zoom,sh/zoom
    x0=min(max(sw*fx-cropw/2,0),sw-cropw);y0=min(max(sh*fy-croph/2,0),sh-croph)
    k=max(width/cropw,height/croph);ox=(cropw*k-width)/2;oy=(croph*k-height)/2
    result=[]
    for region in regions:
        points=[[(x*sw-x0)*k/width-ox/width,(y*sh-y0)*k/height-oy/height] for x,y in region_polygon(region)]
        if all(0<=x<=1 and 0<=y<=1 for x,y in points):result.append({'polygon':points})
    return result


def apply_video_eyes(frame, raw, t):
    """The close-up remains one effect; move its own eye pixels after its framing."""
    h,w=frame.shape[:2]
    regions=framed_regions(raw,w,h,raw.get('eyes') or [])
    if not regions:return
    points=np.array([p for r in regions for p in r['polygon']])*[w-1,h-1]
    lo=np.maximum(0,np.floor(points.min(axis=0)).astype(int)-2)
    hi=np.minimum([w,h],np.ceil(points.max(axis=0)).astype(int)+3)
    x0,y0=lo;x1,y1=hi
    patch=frame[y0:y1,x0:x1];ph,pw=patch.shape[:2]
    if min(ph,pw)<3:return
    local=[{'polygon':[((np.array(p)*[w-1,h-1]-lo)/[pw-1,ph-1]).tolist() for p in r['polygon']]} for r in regions]
    rgba=np.dstack([patch.astype(np.float32)/255,np.ones((ph,pw),np.float32)])
    moved=LookingPixels(rgba,local,min(.5,EYE_GLANCE*float(raw.get("intensity",1))),raw.get("eye_direction","horizontal")).frame((t-raw['at'])/max(.001,raw['duration']))
    patch[:]=np.clip(moved[...,:3]*255+.5,0,255).astype(np.uint8)
