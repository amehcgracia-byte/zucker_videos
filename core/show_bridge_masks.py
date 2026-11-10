"""Show Studio's photo masks and pictures, the same in the render as in its preview.

Ports of tools/qa/show_mask_geometry.js (faceAt, placement, mouthOpen, bounds),
show_motion_model.js (at), show_voice_envelope.js (build, at) and the mask
library's drawing: the photo clipped to its outline, placed on the followed
face (the selected-face track, or the section's face track), turned with it,
and its mouth region dropped by the voice like a jaw.
"""
from __future__ import annotations

import json
import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

STUDIO_W, STUDIO_H = 1280, 720
FACE_FPS = 25.0
MOTION_KEYS = ("x", "y", "scale", "rotation", "radius", "opacity", "maskScale", "maskRotation", "maskOffsetX", "maskOffsetY")


def studio_files() -> Path:
    """tools/qa, beside the code or inside the packaged app."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "tools" / "qa"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _or(value: Any, default: Any) -> Any:
    """JavaScript's ``value ?? default``."""
    return default if value is None else value


# ---- Geometry (show_mask_geometry.js) -----------------------------------------------

def face_at(points: list[list[float]], frame: float, hold: bool = False) -> list[float] | None:
    if not points:
        return None
    lo, hi = 0, len(points) - 1
    while lo < hi:
        mid = math.ceil((lo + hi) / 2)
        if points[mid][0] <= frame:
            lo = mid
        else:
            hi = mid - 1
    a = points[lo]
    b = points[lo + 1] if lo + 1 < len(points) else None
    if abs(a[0] - frame) <= 1:
        return a
    if b is not None and abs(b[0] - frame) <= 1:
        return b
    return a if hold else None


def placement(e: dict[str, Any], face: list[float] | None) -> dict[str, float]:
    offset_x, offset_y = e.get("maskOffsetX") or 0, e.get("maskOffsetY") or 0
    if e.get("followFace") is not False:
        if not face:
            return {"x": _or(e.get("x"), 0.5) * STUDIO_W + offset_x, "y": _or(e.get("y"), 0.5) * STUDIO_H + offset_y,
                    "width": (e.get("radius") or 0.12) * STUDIO_W * _or(e.get("maskScale"), 1),
                    "angle": (e.get("maskRotation") or 0) * math.pi / 180, "mouth": 0}
        return {"x": face[1] + offset_x, "y": face[2] + offset_y, "width": face[3] * (e.get("maskScale") or 1.6),
                "angle": face[5] + (e.get("maskRotation") or 0) * math.pi / 180, "mouth": face[6]}
    return {"x": e["x"] * STUDIO_W + offset_x, "y": e["y"] * STUDIO_H + offset_y,
            "width": (e.get("radius") or 0.12) * STUDIO_W * (e.get("maskScale") or 1),
            "angle": ((e.get("maskRotation") or 0) + (e.get("rotation") or 0)) * math.pi / 180, "mouth": 0}


def mouth_open(e: dict[str, Any], t: float, detected: float = 0.0) -> float:
    if e.get("animateMouth") is False:
        return 0.0
    mode = e.get("mouthMode") or "continuous"
    if mode == "manual":
        return max(0.0, min(1.0, _or(e.get("mouthOpening"), 0.5)))
    if mode == "continuous":
        value = (1 + math.sin((t - e["start"] + (e.get("effectOffset") or 0)) * 17)) * 0.5
    else:
        value = detected
    return max(0.0, min(1.0, value * _or(e.get("mouthOpening"), 1)))


def bounds(polygon: list[list[float]] | None) -> tuple[float, float, float, float]:
    if not polygon:
        return (0.0, 0.0, 1.0, 1.0)
    xs, ys = [p[0] for p in polygon], [p[1] for p in polygon]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def motion_at(e: dict[str, Any], t: float) -> dict[str, Any]:
    """show_motion_model.js at(): keyframed position, size, turn and opacity."""
    keys = sorted(e.get("motionKeys") or [], key=lambda k: k["time"])
    if not keys:
        return e
    a = b = keys[0]
    for p in keys:
        if p["time"] <= t:
            a = p
        if p["time"] >= t:
            b = p
            break
        b = p
    u = 0.0 if a is b else max(0.0, min(1.0, (t - a["time"]) / (b["time"] - a["time"])))
    if e.get("motionEase") != "linear":
        u = u * u * (3 - 2 * u)
    out = dict(e)
    for k in MOTION_KEYS:
        x = _or(a.get(k), e.get(k))
        y = _or(b.get(k), x)
        x, y = _num(x), _num(y)
        if x is not None and y is not None:
            out[k] = x + (y - x) * u
    return out


# ---- Voice (show_voice_envelope.js) ---------------------------------------------------

def envelope(samples: np.ndarray, rate: int, fps: float = FACE_FPS) -> dict[str, Any]:
    step = max(1, round(rate / fps))
    width = round(rate / fps)
    rms = []
    for i in range(0, len(samples), step):
        part = samples[i:min(len(samples), i + width)].astype(np.float64)
        rms.append(math.sqrt(float(np.dot(part, part)) / max(1, len(part))))
    ordered = sorted(rms)
    floor = min(0.012, ordered[int(len(ordered) * 0.2)] if ordered else 0)
    ceiling = max(floor + 0.015, ordered[int(len(ordered) * 0.95)] if ordered else 0.08)
    return {"fps": fps, "rms": rms, "floor": floor, "ceiling": ceiling}


def voice_at(data: dict[str, Any] | None, t: float) -> float:
    if not data or t < 0:
        return 0.0
    rms = data["rms"]
    i = math.floor(t * data["fps"])
    get = lambda j: rms[j] if 0 <= j < len(rms) else 0.0
    v = get(i) * 0.6 + get(i - 1) * 0.25 + get(i + 1) * 0.15
    return max(0.0, min(1.0, (v - data["floor"]) / (data["ceiling"] - data["floor"])))


def decode_first_channel(path: Path, ffmpeg: str = "ffmpeg", rate: int = 48000) -> np.ndarray:
    """The first channel, as the browser's getChannelData(0) gives it."""
    import subprocess
    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-vn", "-af", "pan=mono|c0=c0", "-ar", str(rate),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


# ---- The mask's picture ----------------------------------------------------------------

def head_picture(image: np.ndarray, polygon: list[list[float]] | None, mouth: list[float] | None):
    """The mask library's head(): the photo clipped to its outline (RGBA floats,
    premultiplied) and the mouth rectangle in its pixels."""
    import cv2
    ih, iw = image.shape[:2]
    b = bounds(polygon)
    width, height = max(1, round(b[2] * iw)), max(1, round(b[3] * ih))
    x0, y0 = b[0] * iw, b[1] * ih
    matrix = np.float32([[1, 0, x0], [0, 1, y0]])
    crop = cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0)).astype(np.float32) / 255
    alpha = np.zeros((height, width), np.uint8)
    outline = polygon or [[0, 0], [1, 0], [1, 1], [0, 1]]
    points = np.array([[(x - b[0]) * iw, (y - b[1]) * ih] for x, y in outline]) * 16
    cv2.fillPoly(alpha, [np.round(points).astype(np.int32)], 255, cv2.LINE_AA, shift=4)
    a = alpha.astype(np.float32)[..., None] / 255
    if crop.shape[2] == 4:
        a = a * crop[..., 3:4]                                                  # a transparent photo keeps its own alpha
    rgba = np.dstack([crop[..., :3] * a, a])
    box = None
    if mouth:
        box = ((mouth[0] - b[0]) * iw, (mouth[1] - b[1]) * ih, mouth[2] * iw, mouth[3] * ih)
    return rgba, box


def with_jaw(rgba: np.ndarray, box: tuple[float, float, float, float], drop: float) -> tuple[np.ndarray, int]:
    """The preview's jaw: a dark gap the size of the mouth box, and the mouth region
    drawn ``drop`` pixels lower (it may hang below the photo). Returns the picture
    and how many rows it grew by."""
    import cv2
    x, y, w, h = box
    extra = int(math.ceil(drop)) + 2
    out = np.zeros((rgba.shape[0] + extra, rgba.shape[1], 4), np.float32)
    out[:rgba.shape[0]] = rgba
    x0, x1 = max(0, int(math.floor(x))), min(rgba.shape[1], int(math.ceil(x + w)))
    y0, y1 = max(0, int(math.floor(y))), min(out.shape[0], int(math.ceil(y + drop + 1)))
    if x1 <= x0 or y1 <= y0:
        return out, extra
    out[y0:y1, x0:x1] = (16 / 255, 13 / 255, 10 / 255, 1.0)                     # #100d0a, opaque
    patch_y0, patch_y1 = max(0, int(math.floor(y))), min(rgba.shape[0], int(math.ceil(y + h)))
    patch = np.zeros((patch_y1 - patch_y0 + extra, x1 - x0, 4), np.float32)
    patch[:patch_y1 - patch_y0] = rgba[patch_y0:patch_y1, x0:x1]
    moved = cv2.warpAffine(patch, np.float32([[1, 0, 0], [0, 1, drop]]), (patch.shape[1], patch.shape[0]),
                           flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    target = out[patch_y0:patch_y0 + moved.shape[0], x0:x1]
    moved = moved[:target.shape[0]]
    target[...] = moved + target * (1 - moved[..., 3:4])
    return out, extra


def place(frame: np.ndarray, rgba: np.ndarray, center: tuple[float, float], anchor: tuple[float, float], scale: float,
          angle: float, view: tuple[float, float, float], opacity: float) -> None:
    """drawImage of ``rgba`` after translate(center) rotate(angle) scale(scale)
    translate(-anchor), all in Studio pixels, then through the frame's view."""
    import cv2
    ox, oy, k = view
    c, s = math.cos(angle) * scale * k, math.sin(angle) * scale * k
    tx = (center[0] - ox) * k - (c * anchor[0] - s * anchor[1])
    ty = (center[1] - oy) * k - (s * anchor[0] + c * anchor[1])
    h, w = rgba.shape[:2]
    corners = [(c * x - s * y + tx, s * x + c * y + ty) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    height, width = frame.shape[:2]
    x0, y0 = max(0, int(math.floor(min(p[0] for p in corners)))), max(0, int(math.floor(min(p[1] for p in corners))))
    x1, y1 = min(width, int(math.ceil(max(p[0] for p in corners)))), min(height, int(math.ceil(max(p[1] for p in corners))))
    if x1 <= x0 or y1 <= y0:
        return
    # Output pixel centre -> picture coordinates (pixel centres): the inverse of the transform.
    det = c * c + s * s
    ia, ib, ic, id_ = c / det, s / det, -s / det, c / det
    px, py = x0 + 0.5 - tx, y0 + 0.5 - ty
    matrix = np.float32([[ia, ib, ia * px + ib * py - 0.5], [ic, id_, ic * px + id_ * py - 0.5]])
    warped = cv2.warpAffine(rgba, matrix, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0)) * opacity
    target = frame[y0:y1, x0:x1]
    target[...] = np.clip(target * (1 - warped[..., 3:4]) + warped[..., :3] * 255 + 0.5, 0, 255).astype(np.uint8)


@lru_cache(maxsize=1)
def builtin_masks() -> dict[str, dict[str, Any]]:
    path = studio_files() / "show-masks" / "manifest.json"
    try:
        return {item["id"]: item for item in json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError):
        return {}


def load_rgb(path: Path) -> np.ndarray:
    from PIL import Image
    with Image.open(path) as image:
        return np.asarray(image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB"))
