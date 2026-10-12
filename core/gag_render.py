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
Warps bend the picture underneath; stickers are drawn on top.
"""

from __future__ import annotations

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PIL import Image

from captions.effects import FLY_EFFECTS, FLY_SEC, fun_state
from core.ffmpeg import ffprobe

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
    pixels = np.asarray(image).astype(np.float32) / 255.0
    pixels[..., :3] *= pixels[..., 3:4]
    box = raw.get("from_box")
    return Sticker(
        pixels=pixels, at=float(raw["at"]), duration=max(0.3, float(raw["duration"])),
        x=float(raw.get("x", 0.5)), y=float(raw.get("y", 0.5)), width_px=target,
        effect=str(raw.get("effect") or "pop"),
        from_box=tuple(float(v) for v in box) if box else None,
        path=[(float(a), float(b), float(c)) for a, b, c in raw.get("path") or []],
        hops=int(raw.get("hops") or 0),
    )


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


def draw_sticker(frame: np.ndarray, sticker: Sticker, t: float) -> None:
    height, width = frame.shape[:2]
    cx, cy, sx, sy, rot = sticker_pose(sticker, t, width, height)
    if sx <= 0.01 or sy <= 0.01:
        return
    scale = 1.0 / PREPARED_SCALE
    h, w = sticker.pixels.shape[:2]
    angle = math.radians(rot)
    cos, sin = math.cos(angle), math.sin(angle)
    # Sprite pixel -> frame pixel: centre + R(counter-clockwise on screen) * S * (p - sprite centre)
    a, b = cos * sx * scale, sin * sy * scale
    c, d = -sin * sx * scale, cos * sy * scale
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], dtype=np.float32) - [w / 2, h / 2]
    xs = cx + corners[:, 0] * a + corners[:, 1] * b
    ys = cy + corners[:, 0] * c + corners[:, 1] * d
    x0, x1 = max(0, int(math.floor(xs.min()))), min(width, int(math.ceil(xs.max())) + 1)
    y0, y1 = max(0, int(math.floor(ys.min()))), min(height, int(math.ceil(ys.max())) + 1)
    if x1 <= x0 or y1 <= y0:
        return
    matrix = np.array([[a, b, cx - x0 - (a * w / 2 + b * h / 2)],
                       [c, d, cy - y0 - (c * w / 2 + d * h / 2)]], dtype=np.float32)
    layer = cv2.warpAffine(sticker.pixels, matrix, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
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


def _video_info(path: Path) -> tuple[int, int, float, int]:
    probe = ffprobe(str(path))
    stream = next(s for s in probe.get("streams", []) if s.get("codec_type") == "video")
    num, _, den = str(stream.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)
    duration = float(stream.get("duration") or (probe.get("format") or {}).get("duration") or 0)
    frames = int(stream.get("nb_frames") or round(duration * fps))
    return int(stream["width"]), int(stream["height"]), fps, frames


def render_gags(video: str | Path, gags: list[dict[str, Any]], output: str | Path, *, ffmpeg: str = "ffmpeg",
                progress: Callable[[float], None] | None = None, cancel: Callable[[], None] | None = None) -> Path:
    video, output = Path(video), Path(output)
    width, height, fps, total = _video_info(video)
    stickers = [load_sticker(raw, width, height) for raw in gags if raw.get("image")]
    warps = [Warp(str(raw["warp"]), float(raw["at"]), max(0.2, float(raw["duration"])), float(raw.get("strength") or 0.6),
                  tuple(float(v) for v in raw["box"]) if raw.get("box") else None) for raw in gags if raw.get("warp")]
    warper = Warper(width, height) if warps else None
    frame_bytes = width * height * 3
    pending = output.with_name(f".{output.stem}.part{output.suffix}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as decode_log, tempfile.TemporaryFile() as encode_log:
        decoder = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                   stdout=subprocess.PIPE, stderr=decode_log)
        encoder = subprocess.Popen([
            ffmpeg, "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-r", f"{fps:.6f}", "-i", "pipe:0", "-i", str(video), "-map", "0:v", "-map", "1:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-shortest", "-movflags", "+faststart", str(pending)], stdin=subprocess.PIPE, stderr=encode_log)
        try:
            assert decoder.stdout is not None and encoder.stdin is not None
            index = 0
            while True:
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                t = index / fps
                live_warps = [warp for warp in warps if warp.active(t)]
                live = [sticker for sticker in stickers if sticker.active(t)]
                if live or live_warps:
                    frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).copy()
                    for warp in live_warps:
                        frame = warper.apply(frame, warp, t)
                    for sticker in live:
                        draw_sticker(frame, sticker, t)
                    raw = frame.tobytes()
                encoder.stdin.write(raw)
                index += 1
                if index % 30 == 0:
                    if cancel:
                        cancel()
                    if progress:
                        progress(min(1.0, index / max(1, total)))
            encoder.stdin.close()
            decoder.wait()
            if encoder.wait() != 0 or decoder.returncode != 0:
                encode_log.seek(0)
                decode_log.seek(0)
                raise RuntimeError("Gag render failed: " + (encode_log.read() + decode_log.read()).decode(errors="replace")[-1500:])
            pending.replace(output)
        finally:
            for process in (decoder, encoder):
                if process.poll() is None:
                    process.kill()
            pending.unlink(missing_ok=True)
    return output
