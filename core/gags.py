"""Show gags, start to finish: plan them, make their pictures, draw them on.

  1. face_side      where the host sits, so pictures go on the free side
  2. plan_gags      the director (your Claude Code, or keywords offline)
  3. make_items     every gag becomes a sticker: painted on this computer
                    (kept in the gag library as "Painted/..." to reuse), a
                    picture from the library, or hand lettering
  4. core.gag_render draws them, animated, on the edited video
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from core import gag_director, gag_paint
from core.gag_stickers import _shadowed, character_with_bubble, photo_sticker, word_sticker

# Centre of a picture for each position, when the free side is the left.
CENTRES = {"": (0.21, 0.42), "top": (0.2, 0.24), "bottom": (0.22, 0.72)}
WORD_COLOURS = ("#ffd23f", "#5ce1ff", "#ff5ca8", "#7dff6a", "#ffffff", "#ff8a3d")
WORD_FONTS = ("marker", "marker", "chalk", "board")
FORMULAS = ("E=mc²", "π", "∞", "√2", "x²+y²", "1+1=3", "∑", "?", "a²+b²=c²", "÷0")
MATH_ORBIT = (0.15, 0.13)     # orbit radii around the face, shares of the frame
Progress = Callable[[float, str], None]


def face_side(video: str | Path, ffmpeg: str = "ffmpeg", samples: int = 9) -> str:
    """"left" or "right": the side of the frame where faces mostly are."""
    import subprocess

    import cv2
    import numpy as np
    from core.ffmpeg import ffprobe
    from core.show_framing import face_detectors
    detectors = face_detectors()
    if not detectors:
        return "right"
    cascade = detectors[0][0]
    duration = float((ffprobe(str(video)).get("format") or {}).get("duration") or 0)
    centres = []
    for index in range(samples):
        at = duration * (index + 0.5) / samples
        raw = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=480:-2",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=False).stdout
        if not raw:
            continue
        height = len(raw) // 480
        frame = np.frombuffer(raw[:480 * height], dtype=np.uint8).reshape(height, 480)
        faces = cascade.detectMultiScale(frame, 1.1, 5, minSize=(24, 24))
        if len(faces):
            x, _, w, _ = max(faces, key=lambda face: face[2] * face[3])
            centres.append((x + w / 2) / 480)
    if not centres:
        return "right"
    centres.sort()
    return "right" if centres[len(centres) // 2] >= 0.5 else "left"


def centre(position: str, face: str) -> tuple[float, float]:
    """Frame centre of a position name, mirrored when the face is on the left."""
    vertical = "top" if position.startswith("top") else "bottom" if position.startswith("bottom") else ""
    x, y = CENTRES[vertical]
    on_face_side = position.endswith(face)
    left = (not on_face_side) if face == "right" else on_face_side
    if on_face_side:
        x = 0.1  # small pictures beside the face, near the edge
    return (x if left else 1 - x), y


def plan_gags(words: list[tuple[str, float, float]], duration: float, *, director: str = "claude", language: str = "",
              face: str = "right", library: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], str, str]:
    """(gags, director used, note for the user)."""
    timed = [{"text": text, "start": start, "end": end} for text, start, end in words]
    library = library or []
    note = ""
    if director == "claude":
        try:
            raw = gag_director.ask_claude(gag_director.build_prompt(timed, library, face_side=face))
            return gag_director.clean_gags(raw, duration, face_side=face), "claude", ""
        except gag_director.DirectorError as exc:
            note = f"{exc} Used the offline keyword director instead."
    raw = gag_director.keyword_gags(timed, library, language=language or "es", face_side=face)
    return gag_director.clean_gags(raw, duration, face_side=face), "keywords", note


def _slug(text: str) -> str:
    return re.sub(r"[^\w -]+", "", text).strip()[:60] or "gag"


def painted_picture(subject: str, style: str, *, seed: int = 3, library_root: Path | None = None,
                    step: Callable[[int, int], None] | None = None, prompt: str | None = None) -> Image.Image:
    """A painted sticker, reused from the gag library when it was painted before
    (a drawing with the person's own prompt is another drawing)."""
    from core.gag_library import library_dir
    style = style if style in gag_paint.STYLES else "pencil"
    folder = (library_root or library_dir()) / "Painted" / gag_paint.STYLES[style].label
    key = f"{subject}|{style}|{seed}" + (f"|{prompt}" if prompt else "")
    target = folder / f"{_slug(subject)} {hashlib.sha1(key.encode()).hexdigest()[:6]}.png"
    if target.is_file():
        kept = Image.open(target).convert("RGBA")
        if not gag_paint.mostly_black(kept):          # painted before the blob check: paint it again
            return kept
    # The drawing itself, its paper removed: no white sticker rim, only a soft shadow to read on video.
    sticker = _shadowed(gag_paint.cut_out(gag_paint.paint_clean(subject, style, seed=seed, step=step, prompt=prompt)))
    folder.mkdir(parents=True, exist_ok=True)
    sticker.save(target)
    return sticker


def math_items(gag: dict[str, Any], folder: Path, index: int, centre: tuple[float, float]) -> list[dict[str, Any]]:
    """Hand-written formulas orbiting the host's head, each on its own phase."""
    import math
    items = []
    count = 5
    for k in range(count):
        formula = FORMULAS[(index + k) % len(FORMULAS)]
        # Chalkduster has no "²": those formulas are always in Marker Felt.
        image = word_sticker(formula, colour=WORD_COLOURS[(index + k) % len(WORD_COLOURS)], font="marker" if "²" in formula else ("chalk", "marker")[k % 2],
                             font_size=110, tilt=(-8, 6, -4, 9, -6)[k])
        path = folder / f"gag-{index:03d}-math{k}.png"
        image.save(path)
        phase = k / count
        keys = []
        for step in range(9):
            share = step / 8
            angle = 2 * math.pi * (phase + share * 0.75)
            keys.append([round(share, 3), round(centre[0] + MATH_ORBIT[0] * math.cos(angle), 4),
                         round(centre[1] + MATH_ORBIT[1] * math.sin(angle), 4)])
        items.append({"image": str(path), "at": round(gag["at"] + 0.12 * k, 3), "duration": round(gag["duration"] - 0.12 * k, 3),
                      "width": 0.075 if len(formula) <= 3 else 0.12, "effect": "pop", "path": keys, "kind": "math"})
    return items


def make_items(gags: list[dict[str, Any]], folder: Path, *, face: str = "right", library: list[dict[str, Any]] | None = None,
               progress: Progress | None = None, cancel: Callable[[], None] | None = None,
               frame_size: tuple[int, int] = (1920, 1080),
               face_at: Callable[[float], tuple[float, float] | None] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Render items for core.gag_render, and notes about gags that were skipped."""
    from core.gag_library import search
    folder.mkdir(parents=True, exist_ok=True)
    painter = gag_paint.can_paint()
    notes: list[str] = []
    items: list[dict[str, Any]] = []
    to_paint = sum(1 for gag in gags if gag["kind"] in ("drawing", "character"))
    painted = 0
    unusable: list[str] = []
    width, height = frame_size
    for index, gag in enumerate(gags):
        if cancel:
            cancel()
        kind = gag["kind"]
        if kind == "warp":
            items.append({"warp": gag["warp"], "at": gag["at"], "duration": gag["duration"], "strength": 0.8})
            continue
        if kind == "math":
            where = face_at(gag["at"] + gag["duration"] / 2) if face_at else None
            items.extend(math_items(gag, folder, index, where or (0.62 if face == "right" else 0.38, 0.32)))
            continue
        position = gag.get("position") or ("left" if face == "right" else "right")
        side = "left" if centre(position, face)[0] < 0.5 else "right"
        image: Image.Image | None = None
        if kind in ("drawing", "character"):
            if not painter:
                continue
            painted += 1
            style = gag.get("style") if gag.get("style") in gag_paint.STYLES else "pencil"
            if progress:
                progress(painted / max(1, to_paint), f"Painting {painted}/{to_paint}: {gag['subject']} ({gag_paint.STYLES[style].label})")
            try:
                picture = painted_picture(gag["subject"], style, seed=3 + index % 2, prompt=gag.get("prompt") or None)
            except gag_paint.UnusableDrawing:
                unusable.append(gag["subject"])
                continue
            image = character_with_bubble(picture, gag["says"], side=side, seed=index) if kind == "character" and gag.get("says") else picture
        elif kind == "word":
            image = word_sticker(gag["text"], colour=WORD_COLOURS[index % len(WORD_COLOURS)], font=WORD_FONTS[index % len(WORD_FONTS)],
                                 tilt=(-6, 5, -3, 7)[index % 4])
        elif kind == "photo":
            found = search(gag["query"], library or [])
            if found:
                image = photo_sticker(Path(found["path"]))
        if image is None:
            continue
        path = folder / f"gag-{index:03d}.png"
        image.save(path)
        size = float(gag.get("size") or 0.25)
        x, y = centre(position, face)
        half_w, half_h = size / 2, size * width * image.height / image.width / height / 2
        if half_h > 0.45:  # very tall picture: narrower so it fits the height
            size *= 0.45 / half_h
            half_w, half_h = size / 2, 0.45
        items.append({"image": str(path), "at": gag["at"], "duration": gag["duration"], "width": round(size, 4),
                      "x": round(min(max(x, half_w + 0.01), 1 - half_w - 0.01), 4),
                      "y": round(min(max(y, half_h + 0.01), 1 - half_h - 0.01), 4),
                      "effect": gag.get("effect") or "pop", "kind": kind})
    if not painter and to_paint:
        notes.append(f"{to_paint} drawings were skipped: download the drawing painter (2.1 GB, once) to get them.")
    if unusable:
        notes.append(f"Left out, the painter could not draw them on a clean background: {', '.join(unusable)}.")
    (folder / "gags.json").write_text(json.dumps({"gags": gags, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return items, notes
