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
import threading
import zlib
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


def placement(gag: dict[str, Any], image_size: tuple[int, int], face: str, frame_size: tuple[int, int] = (1920, 1080)) -> tuple[float, float, float]:
    """Where a picture is drawn (centre x, y and width, shares of the frame): where the person
    put it, or the free side of the frame kept inside it. The live view shows it there too."""
    width, height = frame_size
    size = float(gag.get("size") or 0.25)
    position = gag.get("position") or ("left" if face == "right" else "right")
    x, y = centre(position, face)
    explicit = gag.get("image_center")
    if explicit is not None:
        x, y = map(float, explicit)
    image_w, image_h = image_size
    half_w, half_h = size / 2, size * width * image_h / max(1, image_w) / height / 2
    if half_h > 0.45 and explicit is None:  # automatic placement fits; explicit placement may leave the frame
        size *= 0.45 / half_h
        half_w, half_h = size / 2, 0.45
    if explicit is None:
        x, y = min(max(x, half_w + 0.01), 1 - half_w - 0.01), min(max(y, half_h + 0.01), 1 - half_h - 0.01)
    return round(x, 4), round(y, 4), round(size, 4)


def plan_gags(words: list[tuple[str, float, float]], duration: float, *, director: str = "claude", language: str = "",
              face: str = "right", library: list[dict[str, Any]] | None = None, idea: str = "") -> tuple[list[dict[str, Any]], str, str]:
    """(gags, director used, note for the user)."""
    timed = [{"text": text, "start": start, "end": end} for text, start, end in words]
    library = library or []
    note = ""
    if director == "claude":
        try:
            raw = gag_director.ask_claude(gag_director.build_prompt(timed, library, face_side=face, idea=idea))
            return gag_director.clean_gags(raw, duration, face_side=face), "claude", ""
        except gag_director.DirectorError as exc:
            note = f"{exc} Used the offline keyword director instead" + (" (it cannot read your idea)." if idea else ".")
    raw = gag_director.keyword_gags(timed, library, language=language or "es", face_side=face)
    return gag_director.clean_gags(raw, duration, face_side=face), "keywords", note


def _slug(text: str) -> str:
    return re.sub(r"[^\w -]+", "", text).strip()[:60] or "gag"


def paint_seed(gag: dict[str, Any], index: int) -> int:
    """The painting's seed: fixed by the storyboard effect it belongs to, so its own
    preview, the scene's preview and the final render show one and the same picture
    (by its place in the list only for gags that are not storyboard effects)."""
    if gag.get("proposal"):
        return 3 + zlib.crc32(str(gag["proposal"]).encode()) % 2
    return 3 + index % 2


_painting: dict[str, threading.Lock] = {}
_painting_guard = threading.Lock()


def painted_picture(subject: str, style: str, *, seed: int = 3, library_root: Path | None = None,
                    step: Callable[[int, int], None] | None = None, prompt: str | None = None) -> Image.Image:
    """A painted sticker, reused from the gag library when it was painted before
    (a drawing with the person's own prompt is another drawing). Two previews that
    need the same painting at once paint it once: the second waits and reuses it."""
    from core.gag_library import library_dir
    style = style if style in gag_paint.STYLES else "pencil"
    folder = (library_root or library_dir()) / "Painted" / gag_paint.STYLES[style].label
    key = f"{subject}|{style}|{seed}|rough-art-v2|{gag_paint.STYLES[style].prompt}" + (f"|{prompt}" if prompt else "")
    target = folder / f"{_slug(subject)} {hashlib.sha1(key.encode()).hexdigest()[:6]}.png"
    with _painting_guard:
        lock = _painting.setdefault(str(target), threading.Lock())
    with lock:
        return _paint_once(target, subject, style, seed, step, prompt)


def _paint_once(target: Path, subject: str, style: str, seed: int, step: Callable[[int, int], None] | None,
                prompt: str | None) -> Image.Image:
    folder = target.parent
    if target.is_file():
        kept = Image.open(target).convert("RGBA")
        if not gag_paint.mostly_black(kept):          # painted before the blob check: paint it again
            return kept
    # A drawing the painter could not make cleanly is not tried again by every preview.
    failed = target.with_name(f".{target.stem}.unusable")
    if failed.is_file():
        raise gag_paint.UnusableDrawing(f"No clean drawing of {subject} (tried before)")
    # The drawing itself, its paper removed: no white sticker rim, only a soft shadow to read on video.
    try:
        painting = gag_paint.paint_clean(subject, style, seed=seed, step=step, prompt=prompt, tries=1)
    except gag_paint.UnusableDrawing:
        folder.mkdir(parents=True, exist_ok=True)
        failed.write_text(subject, encoding="utf-8")
        raise
    sticker = _shadowed(gag_paint.cut_out(painting))
    folder.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(f".{target.stem}.part.png")
    sticker.save(pending)
    pending.replace(target)                           # never a half-written painting in the library
    return sticker


def drawing_engine() -> str:
    """Who draws the storyboard's drawings: the person's own Claude (crude crayon doodles
    drawn stroke by stroke; nothing heavy runs on this computer) when Claude Code is
    here, else the local painter. ZUCKER_DRAWINGS=claude|painter chooses."""
    import os
    chosen = os.environ.get("ZUCKER_DRAWINGS", "").strip().lower()
    if chosen in ("claude", "painter"):
        return chosen
    return "claude" if gag_director.claude_cli() else "painter"


class DrawingFailed(RuntimeError):
    """Claude could not draw it (a sign-in, usage limit or empty answer)."""


_claude_failed: dict[str, tuple[float, str]] = {}


def claude_picture(subject: str, style: str, *, seed: int = 3, prompt: str | None = None,
                   library_root: Path | None = None) -> Image.Image:
    """A drawing by the person's own Claude, kept in the gag library (drawn once per
    storyboard effect, like the painter's), with its strokes beside it."""
    import time
    from core import show_claude_drawing as claude
    from core.gag_library import library_dir
    style = style if style in gag_paint.STYLES else "crayon"
    folder = (library_root or library_dir()) / "Claude drawings" / gag_paint.STYLES[style].label
    key = f"{subject}|{style}|{seed}|claude-crayon-v1" + (f"|{prompt}" if prompt else "")
    target = folder / f"{_slug(subject)} {hashlib.sha1(key.encode()).hexdigest()[:6]}.png"
    with _painting_guard:
        lock = _painting.setdefault(str(target), threading.Lock())
    with lock:
        if target.is_file():
            return Image.open(target).convert("RGBA")
        failed = _claude_failed.get(str(target))
        if failed and time.time() - failed[0] < 600:            # not asked again for ten minutes
            raise DrawingFailed(failed[1])
        job = {"direction": subject + (f". {prompt}" if prompt else ""), "style": style, "selected_text": "", "use_face": False}
        try:
            drawing = claude.ask(claude.prompt_for(job, None))
        except RuntimeError as exc:
            _claude_failed[str(target)] = (time.time(), str(exc))
            raise DrawingFailed(str(exc)) from exc
        picture = Image.fromarray(claude.render(drawing), "RGBA")
        box = picture.getchannel("A").getbbox()
        if box:                                                  # the doodle, not the empty sheet around it
            pad = 24
            picture = picture.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(picture.width, box[2] + pad), min(picture.height, box[3] + pad)))
        sticker = _shadowed(picture)
        folder.mkdir(parents=True, exist_ok=True)
        target.with_suffix(".json").write_text(json.dumps(drawing), encoding="utf-8")
        pending = target.with_name(f".{target.stem}.part.png")
        sticker.save(pending)
        pending.replace(target)
        return sticker


def suggestion_card(subject: str, says: str = "") -> Image.Image:
    """A suggested drawing in a preview: a card saying what would be drawn, drawn for
    real only once the person accepts it."""
    from PIL import ImageDraw
    from core.gag_stickers import _font
    import textwrap
    title, body = _font(34), _font(42)
    lines = textwrap.wrap(subject, 22)[:4] + ([f"“{says}”"] if says else [])
    width = 560
    height = 96 + 54 * len(lines)
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((4, 4, width - 4, height - 4), radius=28, fill=(255, 255, 255, 215), outline=(40, 40, 40, 255), width=4)
    for x in range(20, width - 20, 28):                         # a dashed top edge: not final art
        draw.line((x, 4, x + 14, 4), fill=(255, 255, 255, 255), width=4)
    draw.text((28, 22), "SUGGESTED DRAWING", font=title, fill=(110, 110, 110, 255))
    for row, line in enumerate(lines):
        draw.text((28, 72 + row * 54), line, font=body, fill=(30, 30, 30, 255))
    return _shadowed(card)


def math_items(gag: dict[str, Any], folder: Path, index: int, centre: tuple[float, float]) -> list[dict[str, Any]]:
    """Dense monochrome calculation field, using Studio's actual formula repertoire.

    Keep a clear centre for the host; no rainbow toy glyphs or orbit across the face.
    Each formula is rasterised once, then moved by the existing compositor.
    """
    from PIL import ImageDraw
    from core.show_bridge_text import FORMULAS, fonts_for, runs
    items = []
    main, fallback = fonts_for("classic", 76)
    if main is None:
        raise RuntimeError("No mathematical lettering font is available")
    for k, text in enumerate(FORMULAS):
        pieces = runs(text, main, fallback)
        widths = [font.getlength(part) for part, font in pieces]
        ascent, descent = main.getmetrics()
        image = Image.new("RGBA", (int(sum(widths)) + 16, ascent + descent + 16))
        draw = ImageDraw.Draw(image)
        x = 8.0
        for (part, font), w in zip(pieces, widths):
            draw.text((x, 8 + ascent), part, font=font, anchor="ls", fill=(255, 255, 255, 235),
                      stroke_width=1, stroke_fill=(12, 12, 12, 160))
            x += w
        path = folder / f"gag-{index:03d}-math{k}.png"
        image.save(path)
        # Top/bottom bands and two outer columns leave the host's centre clear.
        positions = [(x, y) for y in (0.10, 0.90) for x in (0.14, 0.38, 0.62, 0.86)]
        positions += [(x, y) for y in (0.27, 0.44, 0.61, 0.76) for x in (0.13, 0.87)]
        cx, cy = positions[k]
        delay = min(k * 0.045, float(gag["duration"]) * 0.2)
        items.append({"image": str(path), "at": round(gag["at"] + delay, 3),
                      "duration": round(gag["duration"] - delay, 3), "width": min(0.22, image.width / 1280 * 0.5),
                      "effect": "none", "path": [[0, cx, cy], [1, cx, cy - 0.015]], "kind": "math"})
    return items


def make_items(gags: list[dict[str, Any]], folder: Path, *, face: str = "right", library: list[dict[str, Any]] | None = None,
               progress: Progress | None = None, cancel: Callable[[], None] | None = None,
               frame_size: tuple[int, int] = (1920, 1080),
               face_at: Callable[[float], tuple[float, float] | None] | None = None,
               outcomes: dict[str, tuple[str, str]] | None = None, allow_generation: bool = True) -> tuple[list[dict[str, Any]], list[str]]:
    """Render items for core.gag_render, and notes about gags that were skipped.
    ``outcomes`` receives, for each gag from a storyboard proposal, whether it was
    pictured ("applied") or why not ("failed")."""
    outcomes = {} if outcomes is None else outcomes

    def result(gag: dict[str, Any], status: str, detail: str) -> None:
        if gag.get("proposal"):
            outcomes[gag["proposal"]] = (status, detail)
    from core.gag_library import named_picture, search
    folder.mkdir(parents=True, exist_ok=True)
    engine = drawing_engine()
    painter = engine == "claude" or gag_paint.can_paint()
    notes: list[str] = []
    items: list[dict[str, Any]] = []
    to_paint = sum(1 for gag in gags if gag["kind"] in ("drawing", "character") and not gag.get("picture") and not gag.get("suggested"))
    drawings: dict[int, Any] = {}
    pool = None
    if allow_generation and engine == "claude" and to_paint:
        # Claude draws on its side, so up to three drawings are asked at once. A reviewed
        # preview or render (allow_generation=False) asks nobody: it uses linked pictures only.
        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="zucker-claude-drawing")
        for index, gag in enumerate(gags):
            if gag["kind"] in ("drawing", "character") and not gag.get("picture") and not gag.get("suggested"):
                style = gag.get("style") if gag.get("style") in gag_paint.STYLES else "crayon"
                drawings[index] = pool.submit(claude_picture, gag["subject"], style, seed=paint_seed(gag, index), prompt=gag.get("prompt") or None)
        pool.shutdown(wait=False)
    painted = 0
    unusable: list[str] = []
    undrawn: list[str] = []
    width, height = frame_size
    for index, gag in enumerate(gags):
        if cancel:
            cancel()
        kind = gag["kind"]
        if kind == "background":
            continue                                    # drawn by core.show_backgrounds, behind the person
        if kind == "warp":
            items.append({"warp": gag["warp"], "at": gag["at"], "duration": gag["duration"], "strength": 0.8})
            result(gag, "applied", f"{gag['warp']} warp of the whole picture")
            continue
        if kind == "math":
            where = face_at(gag["at"] + gag["duration"] / 2) if face_at else None
            items.extend(math_items(gag, folder, index, where or (0.62 if face == "right" else 0.38, 0.32)))
            result(gag, "applied", "formulas around your head")
            continue
        position = gag.get("position") or ("left" if face == "right" else "right")
        side = "left" if centre(position, face)[0] < 0.5 else "right"
        image: Image.Image | None = None
        if kind == 'word' and gag.get('lettering_text') and not gag.get('picture'):
            detail = 'This caption needs its saved lettering image; no font was substituted'
            result(gag, 'failed', detail)
            notes.append(detail)
            continue
        if kind in ("drawing", "character", "word", "photo") and gag.get("picture"):
            if kind == 'word' and gag.get('lettering_text') and gag.get('text') != gag['lettering_text']:
                detail = 'Caption text changed: create and approve new lettering first'
                result(gag, 'failed', detail)
                notes.append(detail)
                continue
            # The person chose exact artwork instead of a new painting or font.
            try:
                with Image.open(gag["picture"]) as chosen:
                    image = chosen.convert("RGBA")
            except OSError:
                result(gag, "failed", f"Your chosen picture {Path(str(gag['picture'])).name} is missing")
                continue
            if kind == "character" and gag.get("says"):
                image = character_with_bubble(image, gag["says"], side=side, seed=index)
        elif kind in ("drawing", "character") and gag.get("suggested"):
            # Suggested, not accepted yet: shown as what would be drawn, drawn on acceptance.
            image = suggestion_card(gag["subject"], gag.get("says") or "")
        elif kind in ("drawing", "character") and index in drawings:
            painted += 1
            if progress:
                progress((painted - 1) / max(1, to_paint), f"Claude is drawing {painted}/{to_paint}: {gag['subject']}")
            try:
                picture = drawings[index].result()
            except DrawingFailed as exc:
                undrawn.append(f"{gag['subject']} ({str(exc)[:120]})")
                result(gag, "failed", f"Claude could not draw it: {exc}")
                continue
            image = character_with_bubble(picture, gag["says"], side=side, seed=index) if kind == "character" and gag.get("says") else picture
        elif kind in ("drawing", "character"):
            if not allow_generation:
                detail = f'Choose or generate and approve a picture for “{gag["subject"]}” first; no replacement was painted during rendering'
                result(gag, "failed", detail)
                notes.append(detail)
                continue
            if not painter:
                result(gag, "failed", "The drawing painter is not installed")
                continue
            painted += 1
            style = gag.get("style") if gag.get("style") in gag_paint.STYLES else "pencil"
            label = {"pencil": "Pencil", "crayon": "Wax crayons", "marker": "Felt-tip marker"}.get(style, style)
            message = f"Painting {painted}/{to_paint}: {gag['subject']} ({label})"
            if progress:
                progress((painted - 1) / max(1, to_paint), message)
            def paint_step(done, total):
                if cancel:
                    cancel()
                if progress:
                    progress((painted - 1 + done / max(1, total)) / max(1, to_paint), f"{message} — step {done}/{total}")
            try:
                picture = painted_picture(gag["subject"], style, seed=paint_seed(gag, index), prompt=gag.get("prompt") or None, step=paint_step)
            except gag_paint.UnusableDrawing:
                unusable.append(gag["subject"])
                result(gag, "failed", "The painter could not draw it on a clean background")
                continue
            image = character_with_bubble(picture, gag["says"], side=side, seed=index) if kind == "character" and gag.get("says") else picture
        elif kind == "word":
            image = word_sticker(gag["text"], colour=gag.get("colour") if str(gag.get("colour") or "").startswith("#") else "#fff4d6",
                                 font=gag.get("font") or "note", handwritten=True, tilt=(-3, 2, -2, 3)[index % 4],
                                 bold=bool(gag.get("bold")), italic=bool(gag.get("italic")))
        elif kind == "photo":
            # The picture the director named in your library (the one you chose wins, above);
            # previews and the reviewed render pass no index, so the name is looked up exactly.
            found = search(gag["query"], library) if library else None
            named = Path(found["path"]) if found else named_picture(gag["query"])
            if named:
                image = photo_sticker(named)
        if image is None:
            detail = f"No picture in your library is called “{gag.get('query', '')}”" if kind == "photo" else "Nothing to show"
            result(gag, "failed", detail)
            if kind == "photo":
                notes.append(detail)
            continue
        result(gag, "applied", f"your picture {Path(str(gag['picture'])).name}" if kind in ("drawing", "character", "word", "photo") and gag.get("picture")
               else f"{kind}: {gag.get('subject') or gag.get('text') or gag.get('query') or ''}".rstrip(": "))
        path = folder / f"gag-{index:03d}.png"
        image.save(path)
        x, y, size = placement({**gag, "position": position}, image.size, face, frame_size)
        item = {"image": str(path), "at": gag["at"], "duration": gag["duration"], "width": size, "x": x, "y": y,
                "effect": gag.get("effect") or "pop", "kind": kind,
                **{k: gag[k] for k in ("crop", "pan", "stretch", "stretch_x", "rotate", "flip") if k in gag},
                **({k: gag[k] for k in ("follow_anchor", "follow_template", "follow_seconds") if k in gag} if gag.get("follow_anchor") else {}),
                **({"strokes": gag["strokes"]} if gag.get("strokes") else {})}
        # The person's "Movement" (move up, jump, grow, swing…), the same as for placed stickers.
        from core.show_scenes import ACTIONS
        if gag.get("action") in ACTIONS and gag["action"] not in ("talk", "look", "path") and (gag["action"] != "draw" or gag.get("strokes")):
            effect, preset = ACTIONS[gag["action"]]
            item["effect"] = effect
            if preset in ("up", "down"):
                shift = 0.15 * float(gag.get("intensity") or 1.0) * (-1 if preset == "up" else 1)
                item["path"] = [[0.0, item["x"], item["y"]], [1.0, item["x"], item["y"] + shift]]
        items.append(item)
    if allow_generation and not painter and to_paint:
        notes.append(f"{to_paint} drawings were skipped: download the drawing painter (2.1 GB, once) to get them.")
    if unusable:
        notes.append(f"Left out, the painter could not draw them on a clean background: {', '.join(unusable)}.")
    if undrawn:
        notes.append(f"Left out, Claude could not draw them: {'; '.join(undrawn)}.")
    (folder / "gags.json").write_text(json.dumps({"gags": gags, "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return items, notes
