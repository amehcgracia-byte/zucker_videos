"""Show Studio's drawings, the same in the render as in its canvas preview.

Ports of tools/qa/show_paint_tools.js (hash, resample, timedTrails, pigment) and
the editor's stroke() and coffee(), drawn on RGB frames. Sizes and positions
arrive already in output pixels (core.show_bridge.to_output); ``k`` is how much
a Studio pixel grows on this frame, so wobble and grain scale like the preview's
zoomed canvas.
"""
from __future__ import annotations

import colorsys
import math
from functools import lru_cache

import numpy as np

STAMP = 64
SUPER = 4                      # stamps are drawn 4x larger and averaged down (canvas antialiasing)


def js_hash(n: int) -> float:
    """show_paint_tools.js hash(): the same pseudo-random numbers as the preview."""
    def imul(a: int, b: int) -> int:
        return (a * b) & 0xFFFFFFFF
    v = (n & 0xFFFFFFFF) ^ 0x9E3779B9
    v = imul(v ^ (v >> 16), 0x21F0AAAD)
    v = imul(v ^ (v >> 15), 0x735A2D97)
    return ((v ^ (v >> 15)) & 0xFFFFFFFF) / 4294967296


def resample(points: list[tuple[float, float]], spacing: float = 1.5) -> list[tuple[float, float]]:
    if not points:
        return []
    out = [tuple(points[0])]
    left = spacing
    for i in range(1, len(points)):
        a = list(points[i - 1])
        b = points[i]
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        while distance >= left and distance > 0:
            u = left / distance
            a = [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u]
            out.append(tuple(a))
            distance = math.hypot(b[0] - a[0], b[1] - a[1])
            left = spacing
        left -= distance
    return out


def timed_trails(runs: list[list[list[float]]], frame: float, fps: float, width: int, height: int,
                 tail: float = 4.0) -> list[tuple[list[tuple[float, float]], float]]:
    """The detected fingertip runs as trails at ``frame`` (Studio pixels): the last
    ``tail`` seconds of each run, fading out after the hand is lost."""
    out = []
    for run in runs:
        if not run or frame < run[0][0] or frame > run[-1][0] + fps * tail:
            continue
        points = [(p[1] * 1280 / width, p[2] * 720 / height) for p in run if frame - fps * tail <= p[0] <= frame]
        if len(points) > 1:
            out.append((points, max(0.0, min(1.0, (run[-1][0] + fps * tail - frame) / fps))))
    return out


def _rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))


@lru_cache(maxsize=64)
def pigment_stamp(brush: str, color: str) -> np.ndarray:
    """The 64x64 grain stamp of show_paint_tools.js pigment(): premultiplied RGBA floats."""
    size = STAMP * SUPER
    alpha = np.zeros((size, size), dtype=np.float32)
    grain = 580 if brush == "marker" else 135 if brush == "pencil" else 95
    for i in range(grain):
        a = js_hash(i * 4) * math.pi * 2
        r = math.sqrt(js_hash(i * 4 + 1)) * 29
        x, y = 32 + math.cos(a) * r, 32 + math.sin(a) * r
        opacity = (0.22 if brush == "marker" else 0.3) + js_hash(i * 4 + 2) * 0.5
        w = 3 if brush == "marker" else 1 + js_hash(i * 4 + 3) * 2
        h = 1 if brush == "pencil" else 2
        x0, y0 = int(round(x * SUPER)), int(round(y * SUPER))
        x1, y1 = int(round((x + w) * SUPER)), int(round((y + h) * SUPER))
        region = alpha[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        region[...] = region + opacity * (1 - region)             # source-over of one rectangle
    alpha = alpha.reshape(STAMP, SUPER, STAMP, SUPER).mean(axis=(1, 3))
    rgb = np.array(_rgb(color), dtype=np.float32)
    return np.dstack([alpha[..., None] * rgb, alpha])              # premultiplied


def _composite(layer: np.ndarray, frame: np.ndarray) -> None:
    """A premultiplied float RGBA layer over an RGB uint8 frame, in place."""
    alpha = layer[..., 3:4]
    frame[...] = np.clip(frame * (1 - alpha) + layer[..., :3] * 255 + 0.5, 0, 255).astype(np.uint8)


def pigment(frame: np.ndarray, points: list[tuple[float, float]], *, brush: str, color: str, width: float,
            opacity: float = 1.0, k: float = 1.0) -> None:
    """Grain stamps along the path (show_paint_tools.js pigment()), ``width`` in Studio
    pixels and ``points`` already in output pixels."""
    import cv2
    if not points:
        return
    stamp = pigment_stamp(brush, color)
    height, frame_w = frame.shape[:2]
    layer = np.zeros((height, frame_w, 4), dtype=np.float32)
    strength = opacity * (0.7 if brush == "marker" else 0.8)
    spacing = max(0.7, width * 0.22) * k
    cache: dict[int, np.ndarray] = {}
    for x, y in resample(points, spacing):
        # Pressure from the Studio position, as in the preview (x/k, y/k are only a phase here).
        size = width * 2.3 * (0.82 + 0.18 * math.sin(x / k * 0.087 + y / k * 0.061)) * k
        side = max(1, int(round(size)))
        if side not in cache:
            cache[side] = cv2.resize(stamp, (side, side), interpolation=cv2.INTER_AREA if side < STAMP else cv2.INTER_LINEAR) * strength
        piece = cache[side]
        x0, y0 = int(round(x - side / 2)), int(round(y - side / 2))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        x0c, y0c = max(0, x0), max(0, y0)
        x1c, y1c = min(frame_w, x0 + side), min(height, y0 + side)
        if x1c <= x0c or y1c <= y0c:
            continue
        cut = piece[sy0:sy0 + y1c - y0c, sx0:sx0 + x1c - x0c]
        target = layer[y0c:y1c, x0c:x1c]
        target[...] = cut + target * (1 - cut[..., 3:4])
    _composite(layer, frame)


def stroke(frame: np.ndarray, points: list[tuple[float, float]], *, brush: str, color: str, width: float,
           opacity: float = 1.0, k: float = 1.0) -> None:
    """The editor's stroke(): a few wobbly strands (crayon 5, pencil 3, marker 1)."""
    import cv2
    if len(points) < 2:
        return
    strands = 1 if brush == "marker" else 5 if brush == "crayon" else 3
    line = max(1, int(round((width if brush == "marker" else width / strands) * k)))
    alpha = opacity * (0.8 if brush == "marker" else 0.48)
    rgb = np.array(_rgb(color), dtype=np.float32)
    height, frame_w = frame.shape[:2]
    for j in range(strands):
        mask = np.zeros((height, frame_w), dtype=np.uint8)
        path = []
        for i, (x, y) in enumerate(points):
            wobble = (math.sin(i * 0.6) * 0.4 if brush == "marker" else math.sin(i * 2.31 + j * 4) * 1.3) * k
            path.append((x + wobble, y + (j - 1) * k))
        cv2.polylines(mask, [np.round(np.array(path) * 16).astype(np.int32)], False, 255, line, cv2.LINE_AA, shift=4)
        a = mask.astype(np.float32)[..., None] / 255 * alpha
        frame[...] = np.clip(frame * (1 - a) + rgb * 255 * a + 0.5, 0, 255).astype(np.uint8)


def coffee(frame: np.ndarray, point: tuple[float, float], *, elapsed: float, radius: float, k: float) -> None:
    """The editor's coffee(): 30 rising rainbow puffs from the cup (radius as a share
    of the Studio picture)."""
    height, frame_w = frame.shape[:2]
    layer = np.zeros((height, frame_w, 4), dtype=np.float32)
    for i in range(30):
        age = (elapsed * 0.25 + i / 30) % 1
        x = point[0] + math.sin(age * 7 + i * 0.4) * age * radius * 1280 * k
        y = point[1] - age * 130 * k
        r = radius * 720 * (0.3 + age) * k
        peak = 0.1 * (1 - age)
        if r <= 0.5 or peak <= 0:
            continue
        x0, x1 = max(0, int(x - r)), min(frame_w, int(x + r) + 1)
        y0, y1 = max(0, int(y - r)), min(height, int(y + r) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        rr, gg, bb = colorsys.hls_to_rgb((i * 12 % 360) / 360, 0.78, 0.85)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        a = np.clip(1 - np.hypot(xx - x, yy - y) / r, 0, 1)[..., None] * peak   # canvas radial gradient to transparent
        target = layer[y0:y1, x0:x1]
        target[...] = np.dstack([a * rr, a * gg, a * bb, a]) + target * (1 - a)
    _composite(layer, frame)
