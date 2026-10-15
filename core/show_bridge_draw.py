"""Show Studio's drawings, the same in the render as in its canvas preview.

Ports of tools/qa/show_paint_tools.js (hash, resample, timedTrails, pigment) and
of the editor's stroke(), pointAt(), finger(), spiral() and coffee(). Like the
canvas, every drawing is laid out in Studio pixels (the 1280x720 picture) and
only then mapped to the output frame through a ``view``: (Studio x at the
frame's left edge, Studio y at its top, output pixels per Studio pixel). Grain,
pressure and wobble therefore follow the Studio coordinates, exactly as in the
preview, whatever the framing of the automatic edit.
"""
from __future__ import annotations

import colorsys
import math
from functools import lru_cache
from typing import Any

import numpy as np

View = tuple[float, float, float]

STAMP = 64
SUPER = 4                      # stamps are drawn 4x larger and averaged down (canvas antialiasing)
FINGER_FPS = 25.0              # Studio's fingertip tracks count 25 fps frames


def js_round(value: float) -> int:
    """JavaScript's Math.round (halves go up), not Python's banker's rounding."""
    return math.floor(value + 0.5)


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


def reveal(path: list[tuple[float, float]], progress: float) -> list[tuple[float, float]]:
    """show_paint_tools.js reveal(): the first part of a stroke, as if being drawn."""
    points = resample(path, 1.4)
    return points[:max(1, math.ceil(len(points) * max(0.0, min(1.0, progress))))]


def timed_trails(runs: list[list[list[float]]], frame: float, fps: float, width: int, height: int,
                 tail: float = 4.0) -> list[tuple[list[tuple[float, float]], float]]:
    """The detected fingertip runs as trails at ``frame`` (Studio pixels): the last
    ``tail`` seconds of each run, fading out after the hand is lost. A lost
    detection ends a run, so the gap stays a gap."""
    out = []
    for run in runs:
        if not run or frame < run[0][0] or frame > run[-1][0] + fps * tail:
            continue
        points = [(p[1] * 1280 / width, p[2] * 720 / height) for p in run if frame - fps * tail <= p[0] <= frame]
        if len(points) > 1:
            out.append((points, max(0.0, min(1.0, (run[-1][0] + fps * tail - frame) / fps))))
    return out


def point_at(points: list[list[float]], n: float) -> tuple[float, float] | None:
    """The editor's pointAt(): a keyed path ([key, x, y], ...) at ``n``, held at both ends."""
    if not points:
        return None
    for i in range(1, len(points)):
        if n <= points[i][0]:
            a, b = points[i - 1], points[i]
            u = max(0.0, (n - a[0]) / ((b[0] - a[0]) or 1))
            return (a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u)
    return (points[-1][1], points[-1][2])


def _rgb(color: str) -> tuple[float, float, float]:
    color = str(color or "#e7b64c").lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    try:
        return tuple(int(color[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return (0.906, 0.714, 0.298)


def _map(points: list[tuple[float, float]], view: View) -> list[tuple[float, float]]:
    ox, oy, k = view
    return [((x - ox) * k, (y - oy) * k) for x, y in points]


def _box(frame: np.ndarray, points: list[tuple[float, float]], margin: float) -> tuple[int, int, int, int] | None:
    """The part of the frame a drawing can touch, or None when it is all outside."""
    height, width = frame.shape[:2]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = max(0, int(math.floor(min(xs) - margin))), max(0, int(math.floor(min(ys) - margin)))
    x1, y1 = min(width, int(math.ceil(max(xs) + margin)) + 1), min(height, int(math.ceil(max(ys) + margin)) + 1)
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _composite(layer: np.ndarray, frame: np.ndarray) -> None:
    """A premultiplied float RGBA layer over an RGB uint8 frame (or part of one), in place."""
    alpha = layer[..., 3:4]
    frame[...] = np.clip(frame * (1 - alpha) + layer[..., :3] * 255 + 0.5, 0, 255).astype(np.uint8)


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


def pigment(frame: np.ndarray, points: list[tuple[float, float]], view: View, *, brush: str = "crayon",
            color: str = "#e7b64c", width: float = 5, opacity: float = 1.0) -> None:
    """show_paint_tools.js pigment(): grain stamps along a Studio path."""
    import cv2
    if not points:
        return
    width = max(1.0, float(width or 5))
    ox, oy, k = view
    stamp = pigment_stamp(brush, color)
    strength = opacity * (0.7 if brush == "marker" else 0.8)
    stamps = []
    for x, y in resample(points, max(0.7, width * 0.22)):
        size = width * 2.3 * (0.82 + 0.18 * math.sin(x * 0.087 + y * 0.061)) * k
        stamps.append(((x - ox) * k - size / 2, (y - oy) * k - size / 2, size))
    box = _box(frame, [(x + size / 2, y + size / 2) for x, y, size in stamps], width * 2.3 * k)
    if box is None:
        return
    bx0, by0, bx1, by1 = box
    # Every stamp has the same colour, so stamping one over another (source-over) only
    # multiplies what shows through: the ink's alpha is 1 - the product of (1 - each
    # stamp's alpha), and its colour stays the stamp colour. Only alpha is warped.
    through = np.ones((by1 - by0, bx1 - bx0), dtype=np.float32)
    alpha = np.ascontiguousarray(stamp[..., 3]) * strength
    for x, y, size in stamps:
        # drawImage(stamp, x, y, size, size) as Chrome draws it: bilinear samples of the
        # 64 px stamp at each covered pixel's centre (no averaging, so the grain stays crisp).
        x0c, y0c = max(0, int(math.floor(x)) - bx0), max(0, int(math.floor(y)) - by0)
        x1c, y1c = min(bx1 - bx0, int(math.ceil(x + size)) - bx0), min(by1 - by0, int(math.ceil(y + size)) - by0)
        if x1c <= x0c or y1c <= y0c:
            continue
        s = STAMP / size
        matrix = np.float32([[s, 0, (bx0 + x0c + 0.5 - x) * s - 0.5], [0, s, (by0 + y0c + 0.5 - y) * s - 0.5]])
        cut = cv2.warpAffine(alpha, matrix, (x1c - x0c, y1c - y0c), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                             borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        through[y0c:y1c, x0c:x1c] *= 1 - cut
    ink = (1 - through)[..., None]
    rgb = np.array(_rgb(color), dtype=np.float32)
    _composite(np.concatenate([ink * rgb, ink], axis=2), frame[by0:by1, bx0:bx1])


def stroke(frame: np.ndarray, points: list[tuple[float, float]], view: View, *, brush: str = "pencil",
           color: str = "#f4cc77", width: float = 4, opacity: float = 1.0) -> None:
    """The editor's stroke(): a few wobbly strands (crayon 5, pencil 3, marker 1)."""
    import cv2
    if len(points) < 2:
        return
    width = float(width or 4)
    k = view[2]
    strands = 1 if brush == "marker" else 5 if brush == "crayon" else 3
    line = max(1, int(round((width if brush == "marker" else width / strands) * k)))
    alpha = opacity * (0.8 if brush == "marker" else 0.48)
    rgb = np.array(_rgb(color), dtype=np.float32) * 255
    box = _box(frame, _map(points, view), (width + 3) * k)
    if box is None:
        return
    x0, y0, x1, y1 = box
    part = frame[y0:y1, x0:x1]
    for j in range(strands):
        path = []
        for i, (x, y) in enumerate(points):
            wobble = math.sin(i * 0.6) * 0.4 if brush == "marker" else math.sin(i * 2.31 + j * 4) * 1.3
            path.append((x + wobble, y + j - 1))
        mapped = np.array(_map(path, view)) - (x0, y0)
        mask = np.zeros(part.shape[:2], dtype=np.uint8)
        cv2.polylines(mask, [np.round(mapped * 16).astype(np.int32)], False, 255, line, cv2.LINE_AA, shift=4)
        a = mask.astype(np.float32)[..., None] / 255 * alpha
        part[...] = np.clip(part * (1 - a) + rgb * a + 0.5, 0, 255).astype(np.uint8)


def dot(frame: np.ndarray, point: tuple[float, float], view: View, *, color: str, radius: float) -> None:
    """A filled ink dot (a fingertip path of a single mark)."""
    import cv2
    (x, y), = _map([point], view)
    r, g, b = (int(round(v * 255)) for v in _rgb(color))
    cv2.circle(frame, (int(round(x * 16)), int(round(y * 16))), max(1, int(round(radius * view[2] * 16))), (r, g, b), -1, cv2.LINE_AA, shift=4)


def visible(frame: np.ndarray, point: tuple[float, float], view: View) -> bool:
    """Whether a Studio point is on the output frame (the editor's cupInFrame)."""
    (x, y), = _map([point], view)
    height, width = frame.shape[:2]
    return 0 <= x <= width and 0 <= y <= height


def coffee(frame: np.ndarray, point: tuple[float, float], view: View, *, elapsed: float, radius: float) -> None:
    """The editor's coffee(): 30 rising rainbow puffs from the cup (``radius`` as a
    share of the Studio picture)."""
    ox, oy, k = view
    height, width = frame.shape[:2]
    for i in range(30):
        age = (elapsed * 0.25 + i / 30) % 1
        sx = point[0] + math.sin(age * 7 + i * 0.4) * age * radius * 1280
        sy = point[1] - age * 130
        x, y, r = (sx - ox) * k, (sy - oy) * k, radius * 720 * (0.3 + age) * k
        peak = 0.1 * (1 - age)
        if r <= 0.5 or peak <= 0:
            continue
        x0, x1 = max(0, int(x - r)), min(width, int(x + r) + 1)
        y0, y1 = max(0, int(y - r)), min(height, int(y + r) + 1)
        if x1 <= x0 or y1 <= y0:
            continue
        rr, gg, bb = colorsys.hls_to_rgb((i * 12 % 360) / 360, 0.78, 0.85)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        # The preview's gradient runs to 'transparent' (black at alpha 0) and Chrome blends
        # unpremultiplied: colour and alpha both fade, so each puff darkens towards its edge.
        fade = np.clip(1 - np.hypot(xx - x, yy - y) / r, 0, 1)[..., None]
        a, tint = fade * peak, fade * fade * peak
        _composite(np.dstack([tint * rr, tint * gg, tint * bb, a]).astype(np.float32), frame[y0:y1, x0:x1])


# ---- The editor's effects -----------------------------------------------------------

def spiral_points(event: dict[str, Any], t: float) -> list[tuple[float, float]]:
    """The editor's spiral(): an inward or outward spiral revealed over 1.8 s, turning slowly."""
    elapsed = t - float(event["start"]) + float(event.get("effectOffset") or 0)
    u = min(1.0, elapsed / 1.8)
    radius, turns = float(event.get("radius") or 0.14), float(event.get("turns") or 3)
    cx, cy = float(event.get("x", 0.6)) * 1280, float(event.get("y", 0.36)) * 720
    out = []
    for i in range(js_round(220 * u) + 1):
        q = i / 220
        r = radius * 720 * (q if event.get("direction") == "out" else 1 - q)
        a = q * turns * 2 * math.pi + elapsed * 0.5
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def spiral_opacity(event: dict[str, Any], t: float) -> float:
    return min(1.0, (float(event["end"]) - t) / 0.35)


def finger_drawing(event: dict[str, Any], t: float, tracks: list[list[list[float]]]):
    """The editor's finger() for a reviewed or own fingertip path: ("stroke", points),
    ("dot", point) or None. ``tracks`` are the reviewed runs (25 fps frames)."""
    if event.get("trackUntil") is not None and t > float(event["trackUntil"]) + 0.12:
        return None
    shift = float(event.get("contentShift") or 0)
    reviewed = event.get("pathMode") == "reviewed"
    raw = t + shift
    if reviewed:
        source = next((run for run in tracks if run and run[0][0] <= raw * FINGER_FPS and run[-1][0] >= raw * FINGER_FPS), None)
    else:
        source = event.get("points") or []
    if not source:
        return None
    offset = shift * FINGER_FPS if reviewed else 0.0
    tip = point_at(source, t * FINGER_FPS + offset)
    mode = event.get("drawMode")
    if mode in ("circle", "spiral"):
        elapsed = t - float(event["start"]) + float(event.get("effectOffset") or 0)
        progress = min(1.0, max(0.0, elapsed / 1.4))
        radius = float(event.get("radius") or 0.08)
        points = []
        for i in range(js_round(180 * progress) + 1):
            q = i / 180
            a = q * 2 * math.pi * (2.5 if mode == "spiral" else 1)
            r = radius * 720 * (q if mode == "spiral" else 1)
            points.append((tip[0] + r * math.cos(a), tip[1] + r * math.sin(a)))
        return ("stroke", points)
    if len(source) == 1:
        return ("dot", tip)
    points = []
    n = max(float(event["start"]) * FINGER_FPS, t * FINGER_FPS - 38)
    while n <= t * FINGER_FPS:
        p = point_at(source, n + offset)
        if p:
            points.append(p)
        n += 1
    return ("stroke", points)


def auto_ink_trails(event: dict[str, Any], t: float, hands: dict[str, Any]) -> list[tuple[list[tuple[float, float]], float]]:
    """The scene workshop's automatic finger ink: the detected index runs around
    section time ``t``, moved by the effect's offset."""
    fps = float(hands.get("fps") or 25)
    frame = (t + float(event.get("contentShift") or 0)) * fps
    dx = (float(0.5 if event.get("x") is None else event["x"]) - 0.5) * 1280
    dy = (float(0.5 if event.get("y") is None else event["y"]) - 0.5) * 720
    out = []
    for points, opacity in timed_trails(hands.get("runs") or [], frame, fps, int(hands.get("width") or 1280), int(hands.get("height") or 720)):
        out.append(([(x + dx, y + dy) for x, y in points], opacity))
    return out
