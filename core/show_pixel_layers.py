"""Animate selected photographic regions without replacing their original pixels.

The small exposed holes are filled once from the surrounding photo. The moving
patches themselves keep their RGB/alpha unchanged and move by integer pixels.
This is a still-photo layer, not video segmentation or automatic eye tracking.
"""
from __future__ import annotations

import math
import cv2
import numpy as np


class LookingPixels:
    def __init__(self, pixels: np.ndarray, regions: list[dict], amount: float = 0.05):
        from core.show_inserts import region_polygon
        if not 1 <= len(regions) <= 8 or not math.isfinite(amount) or not 0 <= amount <= 0.5:
            raise ValueError('Mark one to eight regions; movement must be between zero and 0.5')
        self.original = pixels
        h, w = pixels.shape[:2]
        self.patches = []
        union = np.zeros((h, w), np.uint8)
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
            self.patches.append((mask.astype(bool), max(1, round((xs.max()-xs.min()) * amount)) if amount else 0))
            union |= mask
        # Inpaint just the vacated holes. All pixels outside the selection stay exact.
        rgb = np.clip(pixels[..., :3] * 255, 0, 255).astype(np.uint8)
        filled = cv2.inpaint(rgb, union * 255, 3, cv2.INPAINT_TELEA).astype(np.float32) / 255
        self.base = pixels.copy()
        self.base[union > 0, :3] = filled[union > 0]

    def frame(self, share: float) -> np.ndarray:
        # One glance left, hold, glance right, hold, then return. No time wrapping.
        phase = float(np.interp(np.clip(share, 0, 1), [0,.12,.25,.40,.55,.75,.9,1], [0,0,-1,-1,1,1,0,0]))
        shifts = [round(phase * amount) for _, amount in self.patches]
        if not any(shifts):
            return self.original
        result = self.base.copy()
        h, w = result.shape[:2]
        for (mask, _), dx in zip(self.patches, shifts):
            ys, xs = np.nonzero(mask)
            tx = xs + dx
            keep = (tx >= 0) & (tx < w)
            ys, xs, tx = ys[keep], xs[keep], tx[keep]
            # Do not move selected pixels into transparent space outside the cutout.
            inside = self.original[ys, tx, 3] > 0
            ys, xs, tx = ys[inside], xs[inside], tx[inside]
            result[ys, tx] = self.original[ys, xs]
        return result


def render_preview(photo, regions, output, ffmpeg='ffmpeg', amount=0.05):
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
    layer = LookingPixels(pixels, regions, amount)
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
