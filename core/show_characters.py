"""Characters from photos: a thing cut out of a photo with its own pixels,
rigged once (mouth, pivot, voice) and kept in the library to reuse.

Cut-out: macOS's own subject lifting (Vision's foreground instance mask) when
it is there, else OpenCV's GrabCut inside a box; the person's keep/erase brush
strokes then fix the edge. Nothing is redrawn: only the alpha mask changes.
A character is a folder in the gag library's "Characters":
  cutout.png      the photo's pixels inside the mask, cropped
  mask.png        the mask on the whole photo (to edit it again)
  character.json  name, where it comes from (photo, video, second), how it was
                  cut, crop box, mouth (shares of the cut-out), pivot, voice
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

CHARACTER_VERSION = 1
PHOTO_EVERY = 4.0      # seconds between library photos of one steady stretch
PHOTO_LIMIT = 60       # per video


def library_photos(resource: dict[str, Any], folder: Path, ffmpeg: str, *, every: float = PHOTO_EVERY,
                   limit: int = PHOTO_LIMIT) -> list[dict[str, Any]]:
    """Photos to cut characters from: the video's steady, sharp stretches (as the
    resource analysis found them; the whole video if it was not analysed), one
    every ``every`` seconds and at least one per stretch, at full size. A photo
    already taken is kept, not taken again."""
    import subprocess
    source = Path(resource["path"])
    stretches = [(float(item["start"]), float(item["end"])) for item in resource.get("intervals") or []]
    if not stretches:
        from core.ffmpeg import ffprobe
        stretches = [(0.0, float(ffprobe(str(source))["format"]["duration"]))]
    times: list[float] = []
    for start, end in stretches:
        count = max(1, int((end - start) // every))
        step = (end - start) / count
        times += [round(start + step * (k + 0.5), 2) for k in range(count)]
    folder.mkdir(parents=True, exist_ok=True)
    photos = []
    for second in sorted(set(times))[:limit]:
        target = folder / f"{source.stem}-{second:06.1f}s.jpg"
        if not target.is_file():
            subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-ss", f"{second:.3f}", "-i", str(source), "-frames:v", "1",
                            "-q:v", "2", str(target)], capture_output=True, check=False)
        if target.is_file():
            minutes, seconds = divmod(int(second), 60)
            photos.append({"name": f"{source.stem} {minutes}:{seconds:02d}", "photo": str(target), "video": str(source),
                           "second": second, "chosen": "library"})
    return photos



def vision_mask(photo: str | Path) -> np.ndarray | None:
    """macOS subject lifting: a 0..1 mask at the photo's size, or None."""
    if sys.platform != "darwin":
        return None
    try:
        import objc
        import Quartz
        from Foundation import NSDictionary, NSURL
        bundle: dict[str, Any] = {}
        objc.loadBundle("Vision", bundle, bundle_path="/System/Library/Frameworks/Vision.framework")
        if "VNGenerateForegroundInstanceMaskRequest" not in bundle:
            return None
        handler = bundle["VNImageRequestHandler"].alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(photo)), NSDictionary.dictionary())
        request = bundle["VNGenerateForegroundInstanceMaskRequest"].alloc().init()
        if not handler.performRequests_error_([request], None) or not request.results():
            return None
        observation = request.results()[0]
        buffer = observation.generateScaledMaskForImageForInstances_fromRequestHandler_error_(observation.allInstances(), handler, None)
        buffer = buffer[0] if isinstance(buffer, tuple) else buffer
        if buffer is None:
            return None
        Quartz.CVPixelBufferLockBaseAddress(buffer, 1)
        try:
            width, height = Quartz.CVPixelBufferGetWidth(buffer), Quartz.CVPixelBufferGetHeight(buffer)
            row = Quartz.CVPixelBufferGetBytesPerRow(buffer)
            float_mask = Quartz.CVPixelBufferGetPixelFormatType(buffer) == 1278226534   # 'L00f'
            raw = bytes(Quartz.CVPixelBufferGetBaseAddress(buffer).as_buffer(row * height))
        finally:
            Quartz.CVPixelBufferUnlockBaseAddress(buffer, 1)
        dtype = np.float32 if float_mask else np.uint8
        mask = np.frombuffer(raw, dtype=dtype).reshape(height, row // np.dtype(dtype).itemsize)[:, :width].astype(np.float32)
        return np.clip(mask if float_mask else mask / 255.0, 0.0, 1.0)
    except Exception:  # any Vision trouble: GrabCut instead
        return None


def grabcut_mask(photo: str | Path, box: list[float] | None = None) -> np.ndarray:
    """OpenCV GrabCut inside ``box`` (shares x, y, w, h; default: the middle)."""
    import cv2
    image = np.asarray(Image.open(photo).convert("RGB"))[:, :, ::-1].copy()
    height, width = image.shape[:2]
    x, y, w, h = box or (0.15, 0.1, 0.7, 0.85)
    rect = (int(x * width), int(y * height), max(2, int(w * width)), max(2, int(h * height)))
    mask = np.zeros((height, width), np.uint8)
    background, foreground = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(image, mask, rect, background, foreground, 5, cv2.GC_INIT_WITH_RECT)
    return np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)


def auto_mask(photo: str | Path, box: list[float] | None = None) -> tuple[np.ndarray, str]:
    """The subject's mask: macOS subject lifting first (unless a box is given), else GrabCut."""
    if box is None:
        mask = vision_mask(photo)
        if mask is not None and float((mask > 0.5).mean()) > 0.002:
            return mask, "macos-vision"
    return grabcut_mask(photo, box), "opencv-grabcut"


def apply_strokes(mask: np.ndarray, strokes: list[dict[str, Any]]) -> np.ndarray:
    """Keep/erase brush strokes ({mode, points [[x, y] shares], radius share of width})."""
    height, width = mask.shape
    layer = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8))
    draw = ImageDraw.Draw(layer)
    for stroke in strokes:
        value = 255 if stroke.get("mode") == "keep" else 0
        radius = max(1, int(float(stroke.get("radius") or 0.01) * width))
        points = [(float(x) * width, float(y) * height) for x, y in stroke.get("points") or []]
        for (x0, y0), (x1, y1) in zip(points, points[1:] or points):
            draw.line([(x0, y0), (x1, y1)], fill=value, width=radius * 2)
        for x0, y0 in points:
            draw.ellipse([x0 - radius, y0 - radius, x0 + radius, y0 + radius], fill=value)
    return np.asarray(layer).astype(np.float32) / 255.0


def cutout(photo: str | Path, mask: np.ndarray) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """The photo's own pixels inside the mask, cropped to them (box in photo pixels)."""
    image = Image.open(photo).convert("RGBA")
    alpha = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8)).resize(image.size)
    image.putalpha(alpha)
    box = image.getbbox()
    if box is None:
        raise ValueError("The selection is empty: keep part of the picture")
    return image.crop(box), box


def _slug(name: str) -> str:
    return re.sub(r"[^\w -]+", "", name).strip()[:60] or "character"


def characters_dir() -> Path:
    from core.gag_library import library_dir
    return library_dir() / "Characters"


def save_character(name: str, photo: str | Path, mask: np.ndarray, *, method: str, mouth: dict[str, Any] | None = None,
                   pivot: list[float] | None = None, voice: list[Any] | None = None, provenance: dict[str, Any] | None = None,
                   eye_direction: str = "horizontal", mouth_direction: str = "down",
                   root: Path | None = None, eyes: list[dict[str, Any]] | None = None) -> Path:
    folder = (root or characters_dir()) / _slug(name)
    folder.mkdir(parents=True, exist_ok=True)
    picture, box = cutout(photo, mask)
    picture.save(folder / "cutout.png")
    Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8)).save(folder / "mask.png")
    record = {"version": CHARACTER_VERSION, "name": name, "photo": str(photo), "method": method, "crop": list(box),
              "mouth": mouth, "eyes": eyes or [], "eye_direction": eye_direction, "mouth_direction": mouth_direction, "pivot": pivot or [0.5, 1.0], "voice": voice, "provenance": provenance or {},
              "size": list(picture.size), "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (folder / "character.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder


def rig(folder: Path, **changes: Any) -> dict[str, Any]:
    """Mark the mouth (rect or polygon, shares of the cut-out), pivot or default voice once."""
    path = folder / "character.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    for key in ("mouth", "eyes", "pivot", "voice", "name", "eye_direction", "mouth_direction"):
        if key in changes:
            record[key] = changes[key]
    record["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def list_characters(root: Path | None = None) -> list[dict[str, Any]]:
    base = root or characters_dir()
    out = []
    for path in sorted(base.glob("*/character.json")) if base.is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({**record, "folder": str(path.parent), "cutout": str(path.parent / "cutout.png")})
    return out
