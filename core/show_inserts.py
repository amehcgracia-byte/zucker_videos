"""Character interventions and rainbow steam for the Show.

An intervention stops the host for a moment: a frame of a close-up video (or
a photo) is frozen, the camera pushes in on it for ZOOM_SEC, then holds still
while the thing speaks with its own recorded voice. The host's narration is
paused meanwhile and resumes after it. The talking is done with the thing's
own pixels: the selected region (rectangle, or polygon reduced to its convex
outline) moves - jaw drop with the voice's loudness, nod, sideways or turn -
and black shows behind it. Nothing is drawn or reconstructed; the loudness
drives the jaw, it is not lip sync. A photo has no voice of its own.

Regions are stored in the still's own coordinates (shares of the source
picture), so changing the zoom or the focus never misplaces them.

Rainbow steam: translucent coloured puffs rising from a point, optionally
anchored to a tracked mug (it stops when the mug is lost).

The loudness mapping, timings and steam motion follow the approved browser
prototype (tools/qa/show_direction_demo.html on the audit branch). The steam
colours are stronger than the prototype's (6.5 % pastel puffs), which could not
be seen over a light mug against a window in the real footage.
"""

from __future__ import annotations

import colorsys
import math
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ZOOM_SEC = 1.2
SILENCE_RMS = 0.008       # below this the jaw stays closed
LOUDNESS_GAIN = 14.0      # rms 0.008 + 1/14 opens it fully
BEAT = 5.0                # nod / sideways / turn rhythm (radians per second)
TURN_RAD = 0.15
AUDIO_RATE = 48000
ACTIONS = ("mouth", "nod", "side", "turn")
ENTRANCES = ("cut", "zoom", "bounce")   # straight cut, push-in, or the photo bouncing in over the frozen host
BOUNCE_SEC = 0.65         # the photo rises from below with a little jump (the storyboard prototype's timing)
BOUNCE_SCALE = 0.78
BOUNCE_EXIT_SEC = 0.25    # and drops away as its voice ends
CARD_RADIUS = 14 / 960    # its rounded corners, share of the width
STEAM_OPACITY = 0.3       # each puff at its densest; 0.065 in the prototype was invisible on light mugs


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Outer convex outline (monotone chain): interior points and crossings go."""
    unique = sorted(set((float(x), float(y)) for x, y in points))
    if len(unique) < 3:
        return unique

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower: list[tuple[float, float]] = []
    for p in unique:
        while len(lower) > 1 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(unique):
        while len(upper) > 1 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def region_polygon(region: dict[str, Any]) -> list[tuple[float, float]]:
    """A rectangle [x, y, w, h] or polygon points, as a convex polygon (shares)."""
    if region.get("rect"):
        x, y, w, h = (float(v) for v in region["rect"])
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    hull = convex_hull([tuple(point) for point in region.get("polygon") or []])
    if len(hull) < 3:
        raise ValueError("A region needs three points that enclose an area")
    return hull


def loudness(samples: np.ndarray, fps: float, frames: int, rate: int = AUDIO_RATE) -> np.ndarray:
    """0..1 jaw opening per frame from the voice's loudness (quick open, softer close)."""
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    hop = rate / fps
    out = np.zeros(frames, dtype=np.float32)
    state = 0.0
    for index in range(frames):
        chunk = mono[int(index * hop):int((index + 1) * hop)]
        rms = float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0
        level = min(1.0, max(0.0, (rms - SILENCE_RMS) * LOUDNESS_GAIN))
        state = level if level > state else state * 0.55 + level * 0.45
        out[index] = state
    return out


def fit(picture: np.ndarray, width: int, height: int) -> np.ndarray:
    """The whole picture inside width x height, black around it (nothing cropped)."""
    import cv2
    h, w = picture.shape[:2]
    k = min(width / w, height / h)
    new_w, new_h = max(1, int(round(w * k))), max(1, int(round(h * k)))
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    x, y = (width - new_w) // 2, (height - new_h) // 2
    canvas[y:y + new_h, x:x + new_w] = cv2.resize(picture, (new_w, new_h), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_LINEAR)
    return canvas


def load_still(source: str | Path, at: float | None, ffmpeg: str, *, allow_dark: bool = False) -> np.ndarray:
    """RGB still: a photo, or the frame of a video at ``at`` seconds."""
    from PIL import Image, ImageOps
    source = Path(source)
    if at is None:
        with Image.open(source) as image:
            picture = np.asarray(ImageOps.exif_transpose(image).convert("RGB"))
    else:
        from core.ffmpeg import ffprobe
        stream = next(s for s in ffprobe(str(source)).get("streams", []) if s.get("codec_type") == "video")
        w, h = int(stream["width"]), int(stream["height"])
        raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{float(at):.3f}", "-i", str(source), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        if len(raw) < w * h * 3:
            raise ValueError(f"No frame at {at:.1f} s of {source.name}")
        picture = np.frombuffer(raw[:w * h * 3], dtype=np.uint8).reshape(h, w, 3)
    if not allow_dark and float(picture.mean()) < 4.0:
        raise ValueError("That frame is black or not decoded yet: choose another moment (or allow a dark frame)")
    return picture


def decode_voice(source: str | Path, start: float, length: float, ffmpeg: str) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(source),
                          "-vn", "-ac", "2", "-ar", str(AUDIO_RATE), "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


@dataclass
class Insert:
    at: float
    still: np.ndarray                 # source picture, RGB
    width: int
    height: int
    fps: float
    zoom: float = 1.25
    focus: tuple[float, float] = (0.5, 0.5)
    zoom_frames: int = 36
    hold_frames: int = 0
    voice: np.ndarray | None = None   # stereo float at AUDIO_RATE, hold length
    polygon: list[tuple[float, float]] = field(default_factory=list)   # shares of the still
    action: str = "mouth"
    amount: float = 0.02              # share of the output height
    offset: tuple[float, float] = (0.0, 0.0)
    envelope: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    overlays: list[Any] = field(default_factory=list)   # stickers drawn on it (a cut-out character talking)
    entrance: str = "zoom"
    backdrop: np.ndarray | None = None   # the frozen host behind a photo that bounces in (fitted, RGB)
    clip: np.ndarray | None = None       # a video clip's frames at the output size (memory-mapped), played whole

    @property
    def frames(self) -> int:
        return self.zoom_frames + self.hold_frames

    @property
    def duration(self) -> float:
        return self.frames / self.fps


def load_clip(raw: dict[str, Any], width: int, height: int, fps: float, ffmpeg: str) -> Insert:
    """A video clip (``insert`` "clip") played whole with its own sound while the host
    pauses, as a meme cut does: ``source`` from ``clip_start`` for ``clip_length`` seconds,
    letterboxed into the frame. Its frames wait in a memory-mapped scratch file, not in RAM."""
    import os
    import tempfile
    source, start, length = str(raw["source"]), float(raw.get("clip_start") or 0.0), float(raw["clip_length"])
    if not Path(source).is_file():
        raise ValueError(f"The clip {Path(source).name} is missing")
    from core.media_framing import filters
    framing = ";".join(filters("0:v", "fitted", source, width, height)+[f"[fitted]fps={fps}[output]"])
    size = width * height * 3
    scratch = tempfile.NamedTemporaryFile(prefix="zucker-clip-", suffix=".rgb", delete=False)
    try:
        with scratch, subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", source, "-an",
                                        "-filter_complex", framing, "-map", "[output]",
                                        "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], stdout=subprocess.PIPE) as decoder:
            assert decoder.stdout is not None
            from core.show_geometry import PLACEMENT_FIELDS, place_frame
            transformed = any(key in raw for key in PLACEMENT_FIELDS)
            while chunk := decoder.stdout.read(size):
                if len(chunk) != size:
                    raise ValueError("The imported clip ended with an incomplete frame")
                if transformed:
                    chunk = place_frame(np.frombuffer(chunk, np.uint8).reshape(height,width,3), raw).tobytes()
                scratch.write(chunk)
        frames = os.path.getsize(scratch.name) // size
        if frames == 0:
            raise ValueError(f"The clip {Path(source).name} has no picture to play")
        clip = np.memmap(scratch.name, dtype=np.uint8, mode="r", shape=(frames, height, width, 3))
    finally:
        os.unlink(scratch.name)            # the mapping stays readable; nothing is left behind
    try:
        voice = decode_voice(source, start, frames / fps, ffmpeg)
    except subprocess.CalledProcessError:
        voice = None                       # a clip without sound plays silent
    insert = Insert(at=float(raw["at"]), still=np.asarray(clip[0]), width=width, height=height, fps=fps, zoom=1.0,
                    zoom_frames=0, hold_frames=frames, voice=voice, entrance="cut")
    if insert.voice is not None:insert.voice*=float(raw.get('audio_volume',1.))
    insert.clip = clip
    insert.envelope = np.zeros(frames, dtype=np.float32)
    return insert


def load_insert(raw: dict[str, Any], width: int, height: int, fps: float, ffmpeg: str, video: str | Path | None = None) -> Insert:
    """``source`` "host" freezes the show's own picture at ``at`` (a character
    then talks over it); otherwise a close-up's frame or a photo. A clip plays whole."""
    if raw.get("insert") == "clip":
        return load_clip(raw, width, height, fps, ffmpeg)
    source = raw["source"]
    if source == "host":
        if video is None:
            raise ValueError("A frozen host frame needs the show video")
        still = load_still(video, float(raw["at"]), ffmpeg, allow_dark=True)
    else:
        photo = raw.get("still_time") is None
        still = load_still(source, None if photo else float(raw["still_time"]), ffmpeg, allow_dark=bool(raw.get("allow_dark")))
    entrance = raw.get("entrance") if raw.get("entrance") in ENTRANCES else "zoom"
    # A cut or a bounce speaks at once; a push-in is silent while the camera moves.
    zoom_frames = 0 if entrance != "zoom" else int(round(float(raw.get("zoom_sec", ZOOM_SEC)) * fps))
    voice = None
    if raw.get("voice"):
        path, start, length = raw["voice"]
        voice = decode_voice(path, float(start), float(length), ffmpeg)
        hold_frames = max(1, int(round(len(voice) / AUDIO_RATE * fps)))
    else:
        hold_frames = max(1, int(round(float(raw.get("hold") or 2.0) * fps)))
    insert = Insert(at=float(raw["at"]), still=still, width=width, height=height, fps=fps,
                    zoom=max(1.0, float(raw.get("zoom") or 1.25)), focus=tuple(raw.get("focus") or (0.5, 0.5)),
                    zoom_frames=zoom_frames, hold_frames=hold_frames, voice=voice,
                    polygon=region_polygon(raw["region"]) if raw.get("region") else [],
                    action=raw.get("action") if raw.get("action") in ACTIONS else "mouth",
                    amount=float(raw.get("amount") if raw.get("amount") is not None else 0.02),
                    offset=tuple(raw.get("offset") or (0.0, 0.0)), entrance=entrance)
    if entrance == "bounce" and source != "host":
        if video is None:
            raise ValueError("A photo that bounces in needs the show video behind it")
        insert.backdrop = fit(load_still(video, float(raw["at"]), ffmpeg, allow_dark=True), width, height)
    insert.envelope = loudness(voice, fps, hold_frames) if voice is not None else np.zeros(hold_frames, dtype=np.float32)
    if raw.get("overlays"):
        from core.gag_render import load_sticker
        speaking = np.concatenate([np.zeros(zoom_frames, dtype=np.float32), insert.envelope])   # silent while pushing in
        for overlay in raw["overlays"]:
            sticker = load_sticker({**overlay, "at": 0.0, "duration": insert.frames / fps}, width, height)
            sticker.fps = fps
            if sticker.mouth is not None:
                sticker.envelope = speaking
            insert.overlays.append(sticker)
    if insert.voice is not None:insert.voice*=float(raw.get('volume',1.))
    return insert


def _window(insert: Insert, zoom: float) -> tuple[float, float, float, float]:
    """Crop window (x, y, w, h) in pixels of the fitted still for ``zoom``."""
    w, h = insert.width / zoom, insert.height / zoom
    x = min(max(insert.focus[0] * insert.width - w / 2, 0), insert.width - w)
    y = min(max(insert.focus[1] * insert.height - h / 2, 0), insert.height - h)
    return x, y, w, h


def insert_frame(insert: Insert, index: int, fitted: np.ndarray | None = None) -> np.ndarray:
    """Frame ``index`` of the intervention, RGB at the output size: the still
    with any character drawn on it (so a push-in brings both closer), its region
    moving; a photo that bounces in is a card over the frozen host."""
    if insert.clip is not None:
        return np.asarray(insert.clip[min(index, len(insert.clip) - 1)])
    fitted = fitted if fitted is not None else fit(insert.still, insert.width, insert.height)
    if insert.overlays:
        from core.gag_render import draw_sticker
        fitted = fitted.copy()
        for sticker in insert.overlays:
            draw_sticker(fitted, sticker, index / insert.fps)
    frame = _still_frame(insert, index, fitted)
    if insert.backdrop is not None:
        frame = _card(insert, frame, index)
    return frame


def _card(insert: Insert, picture: np.ndarray, index: int) -> np.ndarray:
    """The picture as a rounded card over the backdrop: it rises from below with
    a little jump, stays, and drops away at the end."""
    import cv2
    width, height = insert.width, insert.height
    p = min(1.0, index / insert.fps / BOUNCE_SEC)
    rise = (1 - (1 - p) ** 3) * 1.10 + math.sin(p * math.pi) * 0.08          # share of the height travelled
    left = (insert.frames - 1 - index) / insert.fps                           # seconds still to show
    leave = max(0.0, 1 - left / BOUNCE_EXIT_SEC)
    dy = (1.10 - rise + leave * leave * 1.10) * height
    cw, ch = max(1, int(round(width * BOUNCE_SCALE))), max(1, int(round(height * BOUNCE_SCALE)))
    card = cv2.resize(picture, (cw, ch), interpolation=cv2.INTER_AREA)
    mask = np.zeros((ch, cw), dtype=np.uint8)
    r = max(1, int(round(CARD_RADIUS * cw)))
    cv2.rectangle(mask, (r, 0), (cw - 1 - r, ch - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (cw - 1, ch - 1 - r), 255, -1)
    for cx, cy in ((r, r), (cw - 1 - r, r), (r, ch - 1 - r), (cw - 1 - r, ch - 1 - r)):
        cv2.circle(mask, (cx, cy), r, 255, -1, lineType=cv2.LINE_AA)
    frame = insert.backdrop.copy()
    x0, y0 = (width - cw) // 2, (height - ch) // 2 + int(round(dy))
    top, bottom = max(0, y0), min(height, y0 + ch)
    if bottom <= top:
        return frame
    alpha = mask[top - y0:bottom - y0, :, None].astype(np.float32) / 255.0
    region = frame[top:bottom, x0:x0 + cw].astype(np.float32)
    frame[top:bottom, x0:x0 + cw] = (region * (1 - alpha) + card[top - y0:bottom - y0] * alpha + 0.5).astype(np.uint8)
    return frame


def _still_frame(insert: Insert, index: int, fitted: np.ndarray | None = None) -> np.ndarray:
    import cv2
    fitted = fitted if fitted is not None else fit(insert.still, insert.width, insert.height)
    zoom = insert.zoom if insert.zoom_frames == 0 else 1 + (insert.zoom - 1) * min(1.0, index / insert.zoom_frames)
    x, y, w, h = _window(insert, zoom)
    scale = insert.width / w
    matrix = np.array([[scale, 0, -x * scale], [0, scale, -y * scale]], dtype=np.float32)
    frame = cv2.warpAffine(fitted, matrix, (insert.width, insert.height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    if not insert.polygon:
        return frame
    # The region, from still shares to this frame's pixels.
    sh, sw = insert.still.shape[:2]
    k = min(insert.width / sw, insert.height / sh)
    ox, oy = (insert.width - sw * k) / 2, (insert.height - sh * k) / 2
    points = np.array([[((px * sw * k + ox) - x) * scale, ((py * sh * k + oy) - y) * scale] for px, py in insert.polygon], dtype=np.float32)
    dx, dy = insert.offset[0] * insert.width, insert.offset[1] * insert.height
    angle = 0.0
    hold = index - insert.zoom_frames
    if hold >= 0:
        local = hold / insert.fps
        beat = math.sin(local * BEAT)
        amount = insert.amount * insert.height
        if insert.action == "mouth":
            dy += float(insert.envelope[min(hold, len(insert.envelope) - 1)]) * amount if len(insert.envelope) else 0.0
        elif insert.action == "nod":
            dy += beat * amount
        elif insert.action == "side":
            dx += beat * amount
        else:
            angle = beat * TURN_RAD
    if abs(dx) < 0.5 and abs(dy) < 0.5 and abs(angle) < 1e-4:
        return frame
    mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, np.round(points).astype(np.int32), 255)
    patch = np.dstack([frame, mask])
    cx, cy = points.mean(axis=0)
    cos, sin = math.cos(angle), math.sin(angle)
    move = np.array([[cos, -sin, cx - cos * cx + sin * cy + dx], [sin, cos, cy - sin * cx - cos * cy + dy]], dtype=np.float32)
    moved = cv2.warpAffine(patch, move, (insert.width, insert.height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    frame[mask > 0] = 0                                   # black behind the moved pixels
    alpha = moved[..., 3:4].astype(np.float32) / 255.0
    return (frame * (1 - alpha) + moved[..., :3] * alpha + 0.5).astype(np.uint8)


def splice_audio(main: np.ndarray, inserts: list[Insert]) -> np.ndarray:
    """The host's sound with each intervention spliced in: silence during the
    push-in, then the voice (or silence for a photo); the host pauses meanwhile."""
    pieces: list[np.ndarray] = []
    cursor = 0
    for insert in sorted(inserts, key=lambda item: item.at):
        cut = min(len(main), int(round(insert.at * AUDIO_RATE)))
        pieces.append(main[cursor:cut])
        zoom_samples = int(round(insert.zoom_frames / insert.fps * AUDIO_RATE))
        hold_samples = int(round(insert.hold_frames / insert.fps * AUDIO_RATE))
        pieces.append(np.zeros((zoom_samples, 2), dtype=np.float32))
        voice = insert.voice if insert.voice is not None else np.zeros((0, 2), dtype=np.float32)
        voice = voice[:hold_samples]
        pieces.append(np.vstack([voice, np.zeros((hold_samples - len(voice), 2), dtype=np.float32)]))
        cursor = cut
    pieces.append(main[cursor:])
    return np.vstack(pieces) if pieces else main


def shift(seconds: float, spans: list[tuple[float, float]]) -> float:
    """Where a moment of the video lands once interventions (at, duration) are spliced in."""
    return seconds + sum(duration for at, duration in spans if at <= seconds)


@dataclass
class Steam:
    at: float
    duration: float
    x: float                       # pixels: where the steam rises from
    y: float
    scale: float
    rect: list[float] | None = None   # the mug (x, y, w, h px) when anchored
    template: np.ndarray | None = None
    lost: bool = False
    puffs: int = 35                    # density: how many puffs rise at once
    strength: float = STEAM_OPACITY    # how thick each puff is at its densest
    rise: float = 1.0                  # how high it goes
    colour: str = "rainbow"            # "rainbow", "white" or "#rrggbb"
    texture: str = "rainbow"          # legacy steam is the rainbow smoke preset
    tracker: Any = None
    reference: Any = None              # shared by the same effect across shot framings
    point_in_box: Any = None
    original_size: Any = None
    fade_in: bool = True
    fade_out: bool = True

    def active(self, t: float) -> bool:
        return self.at <= t < self.at + self.duration


def _tracking_reference(path, rect):
    if not path or not rect:return None
    import cv2
    template=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
    if template is None:raise ValueError('The saved tracking template is missing')
    return {'template':cv2.resize(template,(max(6,round(rect[2])),max(6,round(rect[3])))), 'scale':1.}


def load_steam(raw: dict[str, Any], width: int, height: int) -> Steam:
    x, y = (float(v) for v in raw["steam"])
    anchor = raw.get("anchor")
    rect = [anchor[0] * width, anchor[1] * height, anchor[2] * width, anchor[3] * height] if anchor else None
    colour = str(raw.get("colour") or "rainbow")
    if colour not in ("rainbow", "white") and not (len(colour) == 7 and colour.startswith("#")):
        colour = "rainbow"
    return Steam(at=float(raw["at"]), duration=max(0.2, float(raw["duration"])), x=x * width, y=y * height,
                 scale=float(raw.get("scale") or 1.0), rect=rect, puffs=int(min(120, max(5, float(raw.get("density") or 35)))),
                 strength=min(0.9, max(0.05, float(raw.get("strength") or STEAM_OPACITY))), rise=min(3.0, max(0.3, float(raw.get("rise") or 1.0))),
                 colour=colour, texture=str(raw.get("texture") or "rainbow"),
                 reference=_tracking_reference(raw.get("follow_template"), rect),
                 fade_in=bool(raw.get('fade_in', True)), fade_out=bool(raw.get('fade_out', True)))


def draw_steam(frame: np.ndarray, steam: Steam, t: float, *, tracking_frame: np.ndarray | None = None, person_layer: Any = None) -> None:
    """35 translucent rainbow puffs rising and swaying (the prototype's motion, stronger colours)."""
    import cv2

    height, width = frame.shape[:2]
    if steam.rect is not None:
        from core.show_textures import ObjectTracker
        if steam.tracker is None:
            x, y, w, h = steam.rect
            steam.point_in_box = ((steam.x-x)/max(1, w), (steam.y-y)/max(1, h))
            steam.original_size = (w, steam.scale)
            steam.tracker = ObjectTracker(list(steam.rect), reference=steam.reference if steam.reference is not None else {}, scale=steam.scale)
        box = steam.tracker.update(cv2.cvtColor(tracking_frame if tracking_frame is not None else frame, cv2.COLOR_RGB2GRAY))
        steam.lost = box is None
        if steam.lost:
            return
        steam.rect = list(box)
        steam.x = box[0] + steam.point_in_box[0]*box[2]
        steam.y = box[1] + steam.point_in_box[1]*box[3]
        steam.scale = steam.original_size[1] * box[2] / max(1, steam.original_size[0])
    if person_layer is not None and steam.rect is not None:
        from dataclasses import replace
        factor = person_layer.scale
        steam = replace(steam, x=steam.x*factor+width*(1-factor)/2+person_layer.offset[0]*width,
                        y=steam.y*factor+height*(1-factor)+person_layer.offset[1]*height, scale=steam.scale*factor)
    if steam.texture not in ('rainbow', 'smoke', 'fog'):
        from core.show_textures import draw_nature
        draw_nature(frame, steam, t)
        return
    local = t - steam.at
    fade = min(1.0, local / 0.5 if steam.fade_in else 1.0, (steam.duration - local) / 0.5 if steam.fade_out else 1.0)
    if fade <= 0:
        return
    s = width / 960 * steam.scale
    count = steam.puffs
    for i in range(count):
        age = (t * 0.28 + i / count) % 1
        cx = steam.x + math.sin(age * 8 + i * 0.4) * age * (70 if steam.texture == 'fog' else 28) * s
        cy = steam.y - age * (65 if steam.texture == 'fog' else 145) * s * steam.rise
        size = (9 + age * 20) * s
        peak = steam.strength * (1 - age) * fade
        x0, x1 = max(0, int(cx - size)), min(width, int(cx + size) + 1)
        y0, y1 = max(0, int(cy - size)), min(height, int(cy + size) + 1)
        if x1 <= x0 or y1 <= y0 or peak <= 0:
            continue
        if steam.colour == "rainbow" and steam.texture == "rainbow":
            r, g, b = colorsys.hls_to_rgb(i / count, 0.55, 0.95)
        elif steam.colour == "white" or steam.texture in ("smoke", "fog"):
            r, g, b = 0.96, 0.96, 0.96
        else:
            r, g, b = (int(steam.colour[k:k + 2], 16) / 255 for k in (1, 3, 5))
        yy, xx = np.mgrid[y0:y1, x0:x1]
        alpha = np.clip(1 - np.hypot(xx - cx, yy - cy) / size, 0, 1)[..., None] * peak
        region = frame[y0:y1, x0:x1].astype(np.float32)
        frame[y0:y1, x0:x1] = (region * (1 - alpha) + np.array([r, g, b], dtype=np.float32) * 255 * alpha + 0.5).astype(np.uint8)
