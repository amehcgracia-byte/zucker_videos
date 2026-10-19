"""Gags on a finished video: animated stickers and picture warps.

FFmpeg decodes the video to raw RGB frames, every frame with something on it
is drawn here with numpy/OpenCV, and FFmpeg encodes the frames again with the
original audio copied. Frames with nothing on them pass straight through.

A sticker gag:
  image      RGBA PNG path
  at         seconds it appears; duration: seconds on screen
  x, y       its centre, shares of the frame
  width      share of the frame width
  effect     a fun effect of captions/effects.py (pop, spin, drop, fly_left,
             pulse, jelly, wobble, ...), the same curves the captions use
  from_box   optional [x, y, w, h] (shares): a cut-out of the scene peels off
             from there, growing and flying to x, y
  path       optional [[share of duration, x, y], ...]: it travels along
  hops       optional number of little jumps along the path
A warp:
  warp       "stretch" (chewing gum), "swirl" (whirlpool) or "wave" (jelly)
  at, duration, strength (0..1), box optional [x, y, w, h] limiting it
A talking mouth (on something filmed, with its own pixels):
  mouth      [x, y, w, h] (shares) of the mouth or jaw at ``at``
  at, duration, open (largest drop, share of the frame height)
  The patch moves down with the loudness of the voice (the video's own
  sound, or ``voice`` [path, start seconds]) and black shows behind it, like
  a puppet's jaw. Nothing is drawn: no outline, no new lips. It is loudness,
  not lip sync. The patch follows small camera moves by matching its
  surroundings frame to frame, and stops moving if it loses them.
Warps bend the picture first, mouths move next, stickers are drawn on top.
"""

from __future__ import annotations

import json
import math
import queue
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image

from captions.effects import FLY_EFFECTS, FLY_SEC, fun_state
from core.ffmpeg import ffprobe, h264_args

EXIT_SEC = 0.2
PEEL_SEC = 0.7
PREPARED_SCALE = 1.3   # stickers are resized once, a little larger than needed
HOP_SHARE = 0.06       # hop height, share of the frame height


@dataclass
class Sticker:
    pixels: np.ndarray   # RGBA float32 0..1, premultiplied, at PREPARED_SCALE x target width
    at: float
    duration: float
    x: float
    y: float
    width_px: float
    effect: str
    from_box: tuple[float, float, float, float] | None
    path: list[tuple[float, float, float]]
    hops: int
    pivot: tuple[float, float] | None = None       # sprite pixels it turns and scales around (default: centre)
    mouth: np.ndarray | None = None                # sprite-size bool mask of the mouth (its own pixels move)
    mouth_direction: str = "down"
    mouth_drop: float = 0.0                        # largest drop, sprite pixels
    envelope: np.ndarray | None = None             # 0..1 per frame of the sticker: the voice's loudness
    fps: float = 30.0
    painter: Any = None                            # draws itself: Claude's strokes in their order
    draw_sec: float = 0.0                          # how long it takes to draw itself (0: it does not)
    follow: Any = None
    tracked_offset: tuple = (0.,0.)
    tracked_visible: bool = True
    eyes: Any = None                              # original-pixel layers with one-off glances

    def active(self, t: float) -> bool:
        return self.at <= t < self.at + self.duration


@dataclass
class Warp:
    kind: str
    at: float
    duration: float
    strength: float
    box: tuple[float, float, float, float] | None

    def active(self, t: float) -> bool:
        return self.at <= t < self.at + self.duration


def _ease_out(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return 1 - (1 - k) ** 3


def _smooth(k: float) -> float:
    k = min(1.0, max(0.0, k))
    return k * k * (3 - 2 * k)


def load_sticker(raw: dict[str, Any], width: int, height: int) -> Sticker:
    image = Image.open(raw["image"]).convert("RGBA")
    target = max(8.0, float(raw.get("width") or 0.25) * width)
    prepared_w = max(8, int(round(target * PREPARED_SCALE)))
    prepared_h = max(8, int(round(image.height * prepared_w / image.width)))
    image = image.resize((prepared_w, prepared_h), Image.LANCZOS)
    if not raw.get("mouth"):
        # Placed pictures can be stretched, mirrored and turned (a talking one keeps its mouth where it is).
        stretch = max(0.25, min(4.0, float(raw.get("stretch") or 1.0)))
        if abs(stretch - 1.0) > 1e-3:
            image = image.resize((prepared_w, max(8, int(round(prepared_h * stretch)))), Image.LANCZOS)
        if raw.get("flip"):
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if float(raw.get("rotate") or 0.0):
            image = image.rotate(-float(raw["rotate"]), resample=Image.BICUBIC, expand=True)
    pixels = np.asarray(image).astype(np.float32) / 255.0
    pixels[..., 3] *= max(0.0, min(1.0, float(raw.get("opacity", 1.0))))
    pixels[..., :3] *= pixels[..., 3:4]
    box = raw.get("from_box")
    sticker = Sticker(
        pixels=pixels, at=float(raw["at"]), duration=max(0.3, float(raw["duration"])),
        x=float(raw.get("x", 0.5)), y=float(raw.get("y", 0.5)), width_px=target,
        effect=str(raw.get("effect") or "pop"),
        from_box=tuple(float(v) for v in box) if box else None,
        path=[(float(a), float(b), float(c)) for a, b, c in raw.get("path") or []],
        hops=int(raw.get("hops") or 0),
    )
    if sticker.effect == "draw":
        # It draws itself first: Claude's strokes in their order, or (a painted picture) as a crayon colouring it in.
        sticker.draw_sec = min(2.5, max(0.8, sticker.duration * 0.45))
        if raw.get("strokes") and Path(str(raw["strokes"])).is_file():
            from core.show_claude_drawing import Painter
            sticker.painter = Painter(json.loads(Path(str(raw["strokes"])).read_text()), pixels.shape[1])
    if raw.get("pivot"):
        sticker.pivot = (float(raw["pivot"][0]) * prepared_w, float(raw["pivot"][1]) * prepared_h)
    if raw.get("mouth"):
        from core.show_inserts import region_polygon
        polygon = np.array([[x * prepared_w, y * prepared_h] for x, y in region_polygon(raw["mouth"])], dtype=np.int32)
        mask = np.zeros((prepared_h, prepared_w), dtype=np.uint8)
        cv2.fillConvexPoly(mask, polygon, 1)
        sticker.mouth = (mask > 0) & (pixels[..., 3] > 0.05)
        sticker.mouth_drop = float(raw.get("mouth_open") or 0.08) * prepared_h
        sticker.mouth_direction = raw.get("mouth_direction","down")
    if raw.get("eyes"):
        from core.show_pixel_layers import LookingPixels
        from core.show_pixel_layers import EYE_GLANCE
        sticker.eyes = LookingPixels(pixels, raw["eyes"], float(raw.get("eye_movement", EYE_GLANCE)),raw.get("eye_direction","horizontal"))
    if raw.get("follow_anchor"):
        from core.show_follow import Follow
        sticker.follow=Follow(raw["follow_anchor"],width,height,raw.get("follow_template"))
    return sticker


def talking_pixels(sticker: Sticker, t: float) -> np.ndarray:
    """The sprite with its eyes glancing and its mouth's own pixels dropped by the voice's
    loudness, black showing behind (only inside the cut-out). Eyes and mouth move together."""
    if sticker.eyes is not None:
        from core.show_pixel_layers import EYE_CYCLE_SEC
        local = t - sticker.at
        # A short picture glances once; a long one (a character talking) every few seconds.
        share = local / sticker.duration if sticker.duration < EYE_CYCLE_SEC * 1.5 else (local % EYE_CYCLE_SEC) / EYE_CYCLE_SEC
        base = sticker.eyes.frame(share)
    else:
        base = sticker.pixels
    if sticker.mouth is None or sticker.envelope is None or not len(sticker.envelope):
        return base
    index = min(len(sticker.envelope) - 1, max(0, int((t - sticker.at) * sticker.fps)))
    drop = int(round(float(sticker.envelope[index]) * sticker.mouth_drop))
    if drop <= 0:
        return base
    pixels = base.copy()
    patch = np.where(sticker.mouth[..., None], base, 0.0)
    pixels[sticker.mouth, :3] = 0.0                    # the open mouth: black, the sprite's own alpha kept
    from core.show_pixel_layers import direction_vector
    dx,dy=direction_vector(sticker.mouth_direction)
    moved=cv2.warpAffine(patch,np.array([[1,0,round(drop*dx)],[0,1,round(drop*dy)]],np.float32),(patch.shape[1],patch.shape[0]),flags=cv2.INTER_NEAREST)
    inside = moved[..., 3:4] > 0
    pixels = np.where(inside, moved, pixels)
    return pixels


def sticker_pose(sticker: Sticker, t: float, width: int, height: int) -> tuple[float, float, float, float, float]:
    """Centre x, y (px), horizontal and vertical scale (of the target width)
    and rotation (degrees, counter-clockwise) at time ``t``."""
    local = t - sticker.at
    share = local / sticker.duration
    cx, cy = sticker.x * width, sticker.y * height
    size = 1.0
    state = fun_state(sticker.effect, local)
    if sticker.path:
        keys = sticker.path
        if share <= keys[0][0]:
            cx, cy = keys[0][1] * width, keys[0][2] * height
        elif share >= keys[-1][0]:
            cx, cy = keys[-1][1] * width, keys[-1][2] * height
        else:
            for (a, x1, y1), (b, x2, y2) in zip(keys, keys[1:]):
                if a <= share <= b:
                    k = _smooth((share - a) / max(1e-6, b - a))
                    cx, cy = (x1 + (x2 - x1) * k) * width, (y1 + (y2 - y1) * k) * height
                    break
        if sticker.hops:
            cy -= abs(math.sin(math.pi * share * sticker.hops)) * HOP_SHARE * height
    elif sticker.from_box:
        bx, by, bw, bh = sticker.from_box
        k = _ease_out(local / PEEL_SEC)
        start_x, start_y = (bx + bw / 2) * width, (by + bh / 2) * height
        cx, cy = start_x + (cx - start_x) * k, start_y + (cy - start_y) * k
        start_size = bw * width / sticker.width_px
        size = start_size + (1.0 - start_size) * k
        # A peeled photo flaps a little while it flies.
        state = {**state, "rot": state["rot"] + 12.0 * math.sin(k * math.pi) * (1 - k)}
    elif sticker.effect in FLY_EFFECTS:
        k = _ease_out(local / FLY_SEC)
        reach_x, reach_y = sticker.width_px * 0.8, sticker.width_px * 0.8
        start = {"drop": (cx, -reach_y), "rise": (cx, height + reach_y),
                 "fly_right": (width + reach_x, cy), "fly_left": (-reach_x, cy)}[sticker.effect]
        cx, cy = start[0] + (cx - start[0]) * k, start[1] + (cy - start[1]) * k
    from captions.effects import BOUNCE_SHARE
    cy += state["dy"] * BOUNCE_SHARE * height
    remaining = sticker.duration - local
    if remaining < EXIT_SEC:
        size *= max(0.0, remaining / EXIT_SEC) ** 0.5
    return cx, cy, size * state["sx"], size * state["sy"], state["rot"]


def drawing_pixels(sticker: Sticker, t: float) -> np.ndarray:
    """The sprite while it draws itself: its strokes so far, or the part a crayon has coloured in."""
    local = t - sticker.at
    if not sticker.draw_sec or local >= sticker.draw_sec:
        return sticker.pixels
    share = max(0.0, local / sticker.draw_sec)
    h, w = sticker.pixels.shape[:2]
    if sticker.painter is not None:
        painted = sticker.painter.at(share)
        if painted.shape[:2] != (h, w):
            painted = cv2.resize(painted, (w, h), interpolation=cv2.INTER_AREA)
        pixels = painted.astype(np.float32) / 255.0
        pixels[..., :3] *= pixels[..., 3:4]
        return pixels
    # A crayon going back and forth in six bands, top to bottom.
    bands = 6
    reach = share * bands
    mask = np.zeros((h, w), np.float32)
    band = h / bands
    for index in range(bands):
        if reach <= index:
            break
        y0, y1 = int(index * band), int(min(h, (index + 1) * band + band * 0.35))
        part = min(1.0, reach - index)
        edge = int(w * part)
        if index % 2 == 0:
            mask[y0:y1, :edge] = 1.0
        else:
            mask[y0:y1, w - edge:] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), max(1.0, band * 0.15))
    return sticker.pixels * mask[..., None]


def draw_sticker(frame: np.ndarray, sticker: Sticker, t: float) -> None:
    height, width = frame.shape[:2]
    if not sticker.tracked_visible:return
    cx, cy, sx, sy, rot = sticker_pose(sticker, t, width, height)
    cx+=sticker.tracked_offset[0]*width;cy+=sticker.tracked_offset[1]*height
    if sx <= 0.01 or sy <= 0.01:
        return
    scale = 1.0 / PREPARED_SCALE
    h, w = sticker.pixels.shape[:2]
    angle = math.radians(rot)
    cos, sin = math.cos(angle), math.sin(angle)
    # Sprite pixel -> frame pixel: position + R(counter-clockwise on screen) * S * (p - pivot).
    # The pivot (feet, base...) keeps its place while the sprite turns or grows;
    # without one it is the centre and x, y place the centre.
    px, py = sticker.pivot or (w / 2, h / 2)
    if sticker.pivot:
        cx += (px - w / 2) * scale
        cy += (py - h / 2) * scale
    a, b = cos * sx * scale, sin * sy * scale
    c, d = -sin * sx * scale, cos * sy * scale
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float32) - [px, py]
    xs = cx + corners[:, 0] * a + corners[:, 1] * b
    ys = cy + corners[:, 0] * c + corners[:, 1] * d
    x0, x1 = max(0, int(math.floor(xs.min()))), min(width, int(math.ceil(xs.max())) + 1)
    y0, y1 = max(0, int(math.floor(ys.min()))), min(height, int(math.ceil(ys.max())) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    matrix = np.array([[a, b, cx - x0 - (a * px + b * py)],
                       [c, d, cy - y0 - (c * px + d * py)]], dtype=np.float32)
    pixels = drawing_pixels(sticker, t) if sticker.draw_sec else talking_pixels(sticker, t)
    layer = cv2.warpAffine(pixels, matrix, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    region = frame[y0:y1, x0:x1].astype(np.float32) / 255.0
    alpha = layer[..., 3:4]
    region = region * (1.0 - alpha) + layer[..., :3]
    frame[y0:y1, x0:x1] = np.clip(region * 255.0 + 0.5, 0, 255).astype(np.uint8)


class Warper:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.xs, self.ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))

    def _mask(self, box: tuple[float, float, float, float] | None) -> np.ndarray | float:
        if not box:
            return 1.0
        bx, by, bw, bh = box
        x0, x1, y0, y1 = bx * self.width, (bx + bw) * self.width, by * self.height, (by + bh) * self.height
        feather = 0.15 * min(x1 - x0, y1 - y0) + 1
        inside_x = np.clip(np.minimum(self.xs - x0, x1 - self.xs) / feather, 0, 1)
        inside_y = np.clip(np.minimum(self.ys - y0, y1 - self.ys) / feather, 0, 1)
        return inside_x * inside_y

    def apply(self, frame: np.ndarray, warp: Warp, t: float) -> np.ndarray:
        local = (t - warp.at) / warp.duration
        envelope = math.sin(math.pi * min(1.0, max(0.0, local))) * warp.strength
        if envelope <= 0.001:
            return frame
        box = warp.box or (0.0, 0.0, 1.0, 1.0)
        cx, cy = (box[0] + box[2] / 2) * self.width, (box[1] + box[3] / 2) * self.height
        dx, dy = self.xs - cx, self.ys - cy
        if warp.kind == "stretch":
            # Chewing gum: wider and a little shorter, wobbling as it pulls.
            pull = 1.0 + 0.8 * envelope * (1 + 0.15 * math.sin(t * 18))
            map_x, map_y = cx + dx / pull, cy + dy * (1 + 0.25 * envelope)
        elif warp.kind == "swirl":
            radius = 0.6 * math.hypot(box[2] * self.width, box[3] * self.height)
            r = np.sqrt(dx * dx + dy * dy)
            turn = envelope * 3.0 * np.clip(1 - r / radius, 0, 1) ** 2
            cos, sin = np.cos(turn), np.sin(turn)
            map_x, map_y = cx + dx * cos - dy * sin, cy + dx * sin + dy * cos
        else:  # wave
            amplitude = 0.012 * self.width * envelope
            map_x = self.xs + amplitude * np.sin(self.ys / (0.05 * self.height) + t * 14)
            map_y = self.ys + amplitude * 0.6 * np.sin(self.xs / (0.07 * self.width) + t * 11)
        mask = self._mask(warp.box)
        if not isinstance(mask, float):
            map_x = self.xs + (map_x - self.xs) * mask
            map_y = self.ys + (map_y - self.ys) * mask
        return cv2.remap(frame, map_x.astype(np.float32), map_y.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


MOUTH_MARGIN = 0.6       # tracked area around the mouth, share of its size
MOUTH_SEARCH = 0.04      # how far it may move between frames, share of the frame width
MOUTH_MIN_MATCH = 0.55   # below this match the mouth is considered lost


@dataclass
class Mouth:
    at: float
    duration: float
    rect: list[float]               # x, y, w, h in pixels, follows the subject
    open_px: float
    envelope: np.ndarray            # 0..1 per output frame of the item
    template: np.ndarray | None = None
    lost: bool = False
    direction: str = "down"

    def active(self, t: float) -> bool:
        return self.at <= t < self.at + self.duration


def loudness_envelope(samples: np.ndarray, rate: int, fps: float, frames: int) -> np.ndarray:
    """0..1 per video frame: the voice's loudness, quick to open, slower to close."""
    hop = rate / fps
    values = np.zeros(frames, dtype=np.float32)
    for index in range(frames):
        chunk = samples[int(index * hop):int((index + 1) * hop)]
        values[index] = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
    loud = float(np.percentile(values, 95)) if frames else 0.0
    if loud <= 1e-5:
        return np.zeros(frames, dtype=np.float32)
    level = np.clip((values / loud - 0.15) / 0.85, 0.0, 1.0)
    smooth = np.zeros_like(level)
    state = 0.0
    for index, value in enumerate(level):
        state = value if value > state else state * 0.6 + value * 0.4
        smooth[index] = state
    return smooth


def _decode_mono(ffmpeg: str, path: str | Path, start: float, duration: float, rate: int = 16000) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(path),
                          "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def load_mouth(raw: dict[str, Any], video: Path, fps: float, width: int, height: int, ffmpeg: str) -> Mouth:
    at, duration = float(raw["at"]), max(0.2, float(raw["duration"]))
    frames = int(round(duration * fps))
    voice = raw.get("voice")
    if voice:
        samples = _decode_mono(ffmpeg, voice[0], float(voice[1]), duration)
    else:
        samples = _decode_mono(ffmpeg, video, at, duration)
    x, y, w, h = (float(v) for v in raw["mouth"])
    return Mouth(at=at, duration=duration, rect=[x * width, y * height, w * width, h * height],
                 open_px=float(raw.get("open") or 0.03) * height, envelope=loudness_envelope(samples, 16000, fps, frames),direction=raw.get("mouth_direction","down"))


def _track(mouth: Mouth, grey: np.ndarray) -> None:
    """Follow the mouth's surroundings from frame to frame (template matching)."""
    import cv2
    x, y, w, h = mouth.rect
    mx, my = w * MOUTH_MARGIN, h * MOUTH_MARGIN
    box = [int(round(x - mx)), int(round(y - my)), int(round(w + 2 * mx)), int(round(h + 2 * my))]
    height, width = grey.shape
    if box[0] < 0 or box[1] < 0 or box[0] + box[2] > width or box[1] + box[3] > height:
        return
    if mouth.template is None:
        mouth.template = grey[box[1]:box[1] + box[3], box[0]:box[0] + box[2]].copy()
        return
    reach = int(MOUTH_SEARCH * width)
    sx0, sy0 = max(0, box[0] - reach), max(0, box[1] - reach)
    sx1, sy1 = min(width, box[0] + box[2] + reach), min(height, box[1] + box[3] + reach)
    area = grey[sy0:sy1, sx0:sx1]
    if area.shape[0] < box[3] or area.shape[1] < box[2]:
        return
    scores = cv2.matchTemplate(area, mouth.template, cv2.TM_CCOEFF_NORMED)
    _, best, _, (bx, by) = cv2.minMaxLoc(scores)
    if best < MOUTH_MIN_MATCH:
        mouth.lost = True
        return
    mouth.rect[0] = sx0 + bx + mx
    mouth.rect[1] = sy0 + by + my


def move_mouth(frame: np.ndarray, mouth: Mouth, t: float, fps: float) -> None:
    import cv2
    _track(mouth, cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY))
    if mouth.lost:
        return
    index = min(len(mouth.envelope) - 1, max(0, int((t - mouth.at) * fps)))
    drop = int(round(float(mouth.envelope[index]) * mouth.open_px)) if len(mouth.envelope) else 0
    if drop <= 0:
        return
    height, width = frame.shape[:2]
    x, y, w, h = (int(round(v)) for v in mouth.rect)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(width, x + w), min(height, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    patch = frame[y0:y1, x0:x1].copy()
    frame[y0:y1, x0:x1] = 0                      # the open mouth: black behind the jaw
    from core.show_pixel_layers import direction_vector
    dx,dy=direction_vector(mouth.direction);dx,dy=round(dx*drop),round(dy*drop)
    left,top=max(0,x0+dx),max(0,y0+dy)
    right,bottom=min(width,x1+dx),min(height,y1+dy)
    if right>left and bottom>top:frame[top:bottom,left:right]=patch[top-y0-dy:bottom-y0-dy,left-x0-dx:right-x0-dx]


def sticker_voice(raw: dict[str, Any], video: Path, fps: float, ffmpeg: str) -> np.ndarray:
    """Loudness per frame of what a talking sticker says: its own voice, or the video's sound."""
    from core.show_inserts import decode_voice, loudness
    duration = float(raw["duration"])
    if raw.get("voice"):
        path, start, length = raw["voice"]
        samples = decode_voice(path, float(start), min(float(length), duration), ffmpeg)
    else:
        samples = decode_voice(video, float(raw["at"]), duration, ffmpeg)
    return loudness(samples, fps, int(round(duration * fps)))


def _video_info(path: Path) -> tuple[int, int, float, int]:
    probe = ffprobe(str(path))
    stream = next(s for s in probe.get("streams", []) if s.get("codec_type") == "video")
    num, _, den = str(stream.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)
    duration = float(stream.get("duration") or (probe.get("format") or {}).get("duration") or 0)
    frames = int(stream.get("nb_frames") or round(duration * fps))
    return int(stream["width"]), int(stream["height"]), fps, frames


def render_gags(video: str | Path, gags: list[dict[str, Any]], output: str | Path, *, ffmpeg: str = "ffmpeg",
                progress: Callable[[float], None] | None = None, cancel: Callable[[], None] | None = None,
                timeline: list[tuple[float, float]] | None = None, drawings: Any = None,
                preset: str = "veryfast", crf: int = 19, hardware: bool = False) -> Path:
    """Draw ``gags`` on ``video``. Character interventions lengthen it: their
    (at, duration) spans are appended to ``timeline`` for captions to follow.
    ``drawings`` (core.show_bridge.StudioDrawings) are Show Studio's effects,
    drawn on the edited clock under the stickers."""
    video, output = Path(video), Path(output)
    width, height, fps, total = _video_info(video)
    stickers = []
    for raw in gags:
        if not raw.get("image") or raw.get("insert_with"):
            continue
        sticker = load_sticker(raw, width, height)
        sticker.fps = fps
        if sticker.mouth is not None:
            sticker.envelope = sticker_voice(raw, video, fps, ffmpeg)
        stickers.append(sticker)
    warps = [Warp(str(raw["warp"]), float(raw["at"]), max(0.2, float(raw["duration"])), float(raw.get("strength") or 0.6),
                  tuple(float(v) for v in raw["box"]) if raw.get("box") else None) for raw in gags if raw.get("warp")]
    warper = Warper(width, height) if warps else None
    # A filmed mouth (the video's own pixels); a picture with a mouth is a sticker that talks, drawn above.
    mouths = [load_mouth(raw, video, fps, width, height, ffmpeg) for raw in gags if raw.get("mouth") and not raw.get("image") and not raw.get("pixel_parts")]
    from core.show_inserts import AUDIO_RATE, draw_steam, fit, insert_frame, load_insert, load_steam, splice_audio
    inserts = sorted((load_insert(raw, width, height, fps, ffmpeg, video) for raw in gags if raw.get("insert")), key=lambda item: item.at)
    steams = [load_steam(raw, width, height) for raw in gags if raw.get("steam")]
    texture_references = {}
    for raw, texture in zip((raw for raw in gags if raw.get("steam")), steams):
        # One marked object, including after an edited cut. Never learn the new
        # shot's wall as a new "cup" just because it occupies the old coordinates.
        texture.reference = texture_references.setdefault(raw.get("proposal") or id(texture), {})
    from core import show_backgrounds
    pixel_parts=[raw for raw in gags if raw.get("pixel_parts")]
    from core.show_pixel_layers import framed_regions
    for raw in pixel_parts:
        regions=framed_regions(raw,width,height,[raw['mouth']]) if raw.get('mouth') else []
        if regions:
            points=np.asarray(regions[0]['polygon']);lo=points.min(axis=0);hi=points.max(axis=0)
            mouths.append(load_mouth({**raw,'mouth':[*lo,*(hi-lo)],'amount':float(raw.get('amount',.02))*float(raw.get('intensity',1))},video,fps,width,height,ffmpeg))
    from core.show_video_assets import VideoLayer
    video_layers = [VideoLayer(raw,width,height,fps) for raw in gags if raw.get('video_layer')]
    # A new background behind the person (masks made beforehand by show_backgrounds.prepare).
    backgrounds = [layer for layer in (show_backgrounds.load(raw, width, height, fps) for raw in gags if raw.get("background")) if layer]
    total += sum(insert.frames for insert in inserts)
    if timeline is not None:
        timeline.extend((insert.at, insert.duration) for insert in inserts)
    frame_bytes = width * height * 3
    pending = output.with_name(f".{output.stem}.part{output.suffix}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log, tempfile.TemporaryDirectory() as scratch:
        if inserts:
            # The host pauses while a character speaks: the sound is rebuilt.
            import soundfile
            main_audio = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-vn", "-ac", "2", "-ar", str(AUDIO_RATE),
                                         "-f", "f32le", "-"], capture_output=True, check=True).stdout
            sound = splice_audio(np.frombuffer(main_audio, dtype=np.float32).reshape(-1, 2), inserts)
            audio_input = Path(scratch) / "sound.wav"
            soundfile.write(str(audio_input), np.clip(sound, -1, 1), AUDIO_RATE, subtype="PCM_16")
            audio_args = ["-i", str(audio_input), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k"]
        else:
            audio_args = ["-i", str(video), "-map", "0:v", "-map", "1:a?", "-c:a", "copy"]
        decoder = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                   stdout=subprocess.PIPE, stderr=decode_log)
        encoder = subprocess.Popen([
            ffmpeg, "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-r", f"{fps:.6f}", "-i", "pipe:0", *audio_args,
            *h264_args(ffmpeg, preset=preset, crf=crf, width=width, height=height, fps=fps, hardware=hardware), "-pix_fmt", "yuv420p",
            # Rawvideo's EOF can make -shortest discard the final buffered AAC
            # packets. The known frame count bounds both tracks without that race.
            "-t", f"{total / fps:.9f}", "-movflags", "+faststart", str(pending)], stdin=subprocess.PIPE, stderr=encode_log)
        pending_inserts = list(inserts)
        written = 0
        # Decoding, drawing and encoding overlap: one thread reads frames ahead from the
        # decoder and another feeds the encoder, so neither ffmpeg waits for the drawing.
        ahead: queue.Queue = queue.Queue(maxsize=16)
        behind: queue.Queue = queue.Queue(maxsize=16)
        halt = threading.Event()
        broken: list[BaseException] = []

        def hand(line: queue.Queue, item: Any) -> None:
            while not halt.is_set():
                try:
                    line.put(item, timeout=0.2)
                    return
                except queue.Full:
                    continue

        def read_frames() -> None:
            try:
                while not halt.is_set():
                    raw = decoder.stdout.read(frame_bytes)
                    if len(raw) < frame_bytes:
                        break
                    hand(ahead, raw)
            except (OSError, ValueError):
                pass
            finally:
                hand(ahead, None)

        def write_frames() -> None:
            try:
                while True:
                    try:
                        chunk = behind.get(timeout=0.2)
                    except queue.Empty:
                        if halt.is_set():
                            return
                        continue
                    if chunk is None:
                        return
                    encoder.stdin.write(chunk)
            except (OSError, ValueError) as exc:
                broken.append(exc)
                halt.set()

        def send(chunk: bytes) -> None:
            if broken:
                raise RuntimeError(f"Gag render failed: the encoder stopped ({broken[0]})")
            hand(behind, chunk)

        def write_inserts(before_frame: int | None) -> None:
            nonlocal written
            while pending_inserts and (before_frame is None or int(round(pending_inserts[0].at * fps)) <= before_frame):
                insert = pending_inserts.pop(0)
                fitted = fit(insert.still, width, height)
                for k in range(insert.frames):
                    send(insert_frame(insert, k, fitted).tobytes())
                    written += 1
                    if written % 30 == 0 and cancel:
                        cancel()
        reader = threading.Thread(target=read_frames, daemon=True, name="gag-render-read")
        writer = threading.Thread(target=write_frames, daemon=True, name="gag-render-write")
        try:
            assert decoder.stdout is not None and encoder.stdin is not None
            reader.start()
            writer.start()
            index = 0
            while True:
                try:
                    raw = ahead.get(timeout=0.5)
                except queue.Empty:
                    if broken:
                        raise RuntimeError(f"Gag render failed: the encoder stopped ({broken[0]})")
                    if reader.is_alive():
                        continue
                    raw = None
                if raw is None:
                    break
                write_inserts(index)
                t = index / fps
                live_warps = [warp for warp in warps if warp.active(t)]
                live_mouths = [mouth for mouth in mouths if mouth.active(t)]
                live_steams = [steam for steam in steams if steam.active(t)]
                live = [sticker for sticker in stickers if sticker.active(t)]
                studio = bool(drawings) and drawings.active(t)
                live_backgrounds = [layer for layer in backgrounds if layer.active(t)]
                live_parts=[raw for raw in pixel_parts if raw["at"]<=t<raw["at"]+raw["duration"]]
                live_videos=[layer for layer in video_layers if layer.active(t)]
                if live or live_warps or live_mouths or live_steams or studio or live_backgrounds or live_videos or live_parts:
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
                    for tracked in [*live,*live_videos]:
                        if tracked.follow:
                            delta=tracked.follow.update(frame)
                            tracked.tracked_visible=delta is not None
                            if delta is not None:tracked.tracked_offset=delta
                    texture_source = frame.copy() if any(steam.rect is not None for steam in live_steams) else None
                    for layer in live_backgrounds:
                        show_backgrounds.apply(frame, layer, t)
                    for raw_part in live_parts:
                        from core.show_pixel_layers import apply_video_eyes
                        apply_video_eyes(frame,raw_part,t)
                    for layer in live_videos:
                        layer.apply(frame,t)
                    for warp in live_warps:
                        frame = warper.apply(frame, warp, t)
                    for mouth in live_mouths:
                        move_mouth(frame, mouth, t, fps)
                    for steam in live_steams:
                        draw_steam(frame, steam, t, tracking_frame=texture_source, person_layer=live_backgrounds[-1] if live_backgrounds else None)
                    if studio:
                        drawings.draw(frame, t)
                    for sticker in live:
                        draw_sticker(frame, sticker, t)
                    raw = frame.tobytes()
                send(raw)
                index += 1
                written += 1
                if index % 30 == 0:
                    if cancel:
                        cancel()
                    if progress:
                        progress(min(1.0, written / max(1, total)))
            write_inserts(None)   # interventions after the last frame
            hand(behind, None)
            writer.join()
            if broken:
                raise RuntimeError(f"Gag render failed: the encoder stopped ({broken[0]})")
            encoder.stdin.close()
            decoder.wait()
            if encoder.wait() != 0 or decoder.returncode != 0:
                encode_log.seek(0)
                decode_log.seek(0)
                raise RuntimeError("Gag render failed: " + (encode_log.read() + decode_log.read()).decode(errors="replace")[-1500:])
            pending.replace(output)
        finally:
            halt.set()
            for layer in video_layers:
                layer.close()
            for layer in backgrounds:
                layer.close()
            for process in (decoder, encoder):
                if process.poll() is None:
                    process.kill()
            for thread in (reader, writer):
                if thread.is_alive():
                    thread.join(timeout=2)
            pending.unlink(missing_ok=True)
    return output
