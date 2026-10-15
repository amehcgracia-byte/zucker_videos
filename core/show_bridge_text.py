"""Show Studio's words and formulas, as its preview's canvas writes them.

The editor's formula(): centred text in Georgia (or a cursive face for the chalk
style), a soft dark outline (lineWidth 3, rgba(10,18,14,.5)) under a white or
chalk fill. Like the browser, a character the main font lacks (∇, →, ⁱ...) is
taken from a fallback font instead of showing a missing-glyph box.
"""
from __future__ import annotations

import math
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

FORMULAS = ["E = mc²", "∑ n = n(n+1)/2", "x² + y² = r²", "f′(x) = lim Δf/Δx", "∫ x dx = x²/2 + C", "sin²θ + cos²θ = 1",
            "P(A|B) = ?", "eⁱπ + 1 = 0", "t → ∞", "∇ × F", "√(x² + y²)", "a² + b² = c²", "Δt = t₂ − t₁",
            "x = (−b ± √Δ)/2a", "π ≈ 3.14159", "∫₀¹ f(x) dx"]

if sys.platform == "win32":
    _FONTS = Path("C:/Windows/Fonts")
    FAMILIES = {"serif": [_FONTS / "georgia.ttf"], "serif-italic": [_FONTS / "georgiai.ttf"], "cursive": [_FONTS / "comic.ttf"],
                "fallback": [_FONTS / "seguisym.ttf", _FONTS / "cambria.ttc", _FONTS / "arial.ttf"]}
else:
    _SUPPLEMENTAL = Path("/System/Library/Fonts/Supplemental")
    FAMILIES = {"serif": [_SUPPLEMENTAL / "Georgia.ttf"], "serif-italic": [_SUPPLEMENTAL / "Georgia Italic.ttf"],
                "cursive": [_SUPPLEMENTAL / "Apple Chancery.ttf"],
                "fallback": [_SUPPLEMENTAL / "Arial Unicode.ttf", Path("/Library/Fonts/Arial Unicode.ttf"),
                             Path("/System/Library/Fonts/Apple Symbols.ttf"), _SUPPLEMENTAL / "STIXTwoMath-Regular.otf",
                             Path("/System/Library/Fonts/Supplemental/Arial.ttf")]}


@lru_cache(maxsize=32)
def _font(path: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(path, size)


def fonts_for(style: str, size: int):
    """The main font for the style and the fallbacks present on this computer."""
    main = next((p for p in FAMILIES["cursive" if style == "chalk" else "serif"] if p.is_file()), None)
    fallbacks = [p for p in FAMILIES["fallback"] if p.is_file()]
    if main is None and fallbacks:
        main = fallbacks[0]
    if main is None:
        return None, []
    return _font(str(main), size), [_font(str(p), size) for p in fallbacks if p != main]


@lru_cache(maxsize=4096)
def _has(font, char: str) -> bool:
    """Whether ``font`` draws ``char`` itself (not its missing-glyph box)."""
    if char.isspace():
        return True
    missing = font.getmask("\ue000")                                            # a private-use code point: the box
    glyph = font.getmask(char)
    return not (glyph.size == missing.size and bytes(glyph) == bytes(missing))


def runs(text: str, main, fallbacks) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    for char in text:
        font = main if _has(main, char) else next((f for f in fallbacks if _has(f, char)), main)
        if out and out[-1][1] is font:
            out[-1] = (out[-1][0] + char, font)
        else:
            out.append((char, font))
    return out


def formula(frame: np.ndarray, text: str, point: tuple[float, float], size: float, view: tuple[float, float, float], *,
            style: str = "classic", opacity: float = 1.0, alpha: float = 1.0) -> None:
    """The editor's formula() at Studio ``point`` with a Studio font ``size``."""
    from PIL import Image, ImageDraw
    if not text or alpha <= 0 or opacity <= 0:
        return
    ox, oy, k = view
    px = max(4, int(round(size * k)))
    main, fallbacks = fonts_for(style, px)
    if main is None:
        return
    pieces = runs(text, main, fallbacks)
    widths = [draw_font.getlength(part) for part, draw_font in pieces]
    ascent, descent = main.getmetrics()
    pad = int(math.ceil(3 * k)) + 2
    width, height = int(math.ceil(sum(widths))) + 2 * pad, ascent + descent + 2 * pad
    stroke = Image.new("L", (width, height), 0)
    fill = Image.new("L", (width, height), 0)
    line = max(1, int(round(1.5 * k)))
    x = pad
    for (part, font), w in zip(pieces, widths):
        ImageDraw.Draw(stroke).text((x, pad + ascent), part, font=font, fill=255, anchor="ls", stroke_width=line, stroke_fill=255)
        ImageDraw.Draw(fill).text((x, pad + ascent), part, font=font, fill=255, anchor="ls")
        x += w
    a_stroke = np.asarray(stroke, np.float32) / 255 * 0.5
    a_fill = np.asarray(fill, np.float32) / 255
    colour = np.array((244, 233, 198) if style == "chalk" else (255, 255, 255), np.float32) / 255
    dark = np.array((10, 18, 14), np.float32) / 255
    # The fill over its outline (premultiplied), then the whole word at the event's opacity.
    rgb = colour * a_fill[..., None] + dark * a_stroke[..., None] * (1 - a_fill[..., None])
    a = (a_fill + a_stroke * (1 - a_fill)) * opacity * alpha
    rgb = rgb * opacity * alpha
    # textAlign centre, textBaseline middle: the middle of the em box sits on the point.
    cx, cy = (point[0] - ox) * k, (point[1] - oy) * k
    x0 = int(round(cx - sum(widths) / 2 - pad))
    y0 = int(round(cy + (ascent - descent) / 2 - ascent - pad))                  # baseline below the middle by half the glyph height
    frame_h, frame_w = frame.shape[:2]
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(frame_w, x0 + width), min(frame_h, y0 + height)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    part_rgb = rgb[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    part_a = a[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0, None]
    target = frame[fy0:fy1, fx0:fx1]
    target[...] = np.clip(target * (1 - part_a) + part_rgb * 255 + 0.5, 0, 255).astype(np.uint8)


def thinking(frame: np.ndarray, e: dict, t: float, view: tuple[float, float, float]) -> None:
    """The editor's thinking(): formulas appearing one by one around a point, drifting."""
    elapsed = t - e["start"] + (e.get("effectOffset") or 0)
    for i in range(int(e.get("count") or 10)):
        appear = i * 0.16
        if elapsed < appear:
            continue
        angle = i * 2.399963
        rad = float(e.get("radius") or 0.21) * 720 * (1 + (i % 3) * 0.25)
        x = float(e.get("x", 0.64)) * 1280 + math.cos(angle) * rad * 1.45
        y = float(e.get("y", 0.3)) * 720 + math.sin(angle) * rad * 0.95 + math.sin(elapsed * 0.65 + i) * float(e.get("motion") or 0) * 12
        fade = min(1.0, (elapsed - appear) / 0.35, (e["end"] - t) / 0.4)
        formula(frame, FORMULAS[i % len(FORMULAS)], (x, y), float(e.get("fontSize") or 33) * (0.72 + (i % 3) * 0.15), view,
                style=e.get("style") or "classic", opacity=float(e.get("opacity", 0.85)), alpha=fade)


def words(frame: np.ndarray, e: dict, t: float, view: tuple[float, float, float]) -> None:
    """The editor's words(): the person's own text, fading in and out over 0.2 s."""
    formula(frame, e.get("text") or "", (float(e.get("x", 0.64)) * 1280, float(e.get("y", 0.3)) * 720), float(e.get("fontSize") or 34), view,
            style=e.get("style") or "classic", opacity=float(e.get("opacity", 0.85)), alpha=min(1.0, (t - e["start"]) / 0.2, (e["end"] - t) / 0.2))
