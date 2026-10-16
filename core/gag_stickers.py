"""Sticker pictures for gags: photos as stickers, speech bubbles, big words."""

from __future__ import annotations

import sys
import textwrap
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

WHITE = (255, 255, 255, 255)
INK = (17, 17, 17, 255)


@lru_cache(maxsize=8)
def _font_path(bold: bool = True) -> str | None:
    bundle = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "assets" / "fonts"
    names = ["Verdana Bold.ttf", "Arial.ttf"] if bold else ["Arial.ttf", "Verdana Bold.ttf"]
    for name in names:
        for folder in (bundle, Path("/System/Library/Fonts/Supplemental"), Path("C:/Windows/Fonts")):
            if (folder / name).is_file():
                return str(folder / name)
    return None


def _font(size: int) -> ImageFont.ImageFont:
    path = _font_path()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


# Lettering that looks written by hand: macOS faces first, then the ones every
# Windows has, then the app's own (Verdana) as the last resort.
HAND_FONTS: dict[str, tuple[tuple[str, int], ...]] = {
    "marker": (("/System/Library/Fonts/MarkerFelt.ttc", 1), ("C:/Windows/Fonts/segoeprb.ttf", 0), ("C:/Windows/Fonts/comicbd.ttf", 0)),
    "chalk": (("/System/Library/Fonts/Supplemental/Chalkduster.ttf", 0), ("C:/Windows/Fonts/Inkfree.ttf", 0), ("C:/Windows/Fonts/segoepr.ttf", 0)),
    "note": (("/System/Library/Fonts/Noteworthy.ttc", 1), ("C:/Windows/Fonts/segoepr.ttf", 0)),
    "board": (("/System/Library/Fonts/Supplemental/ChalkboardSE.ttc", 2), ("C:/Windows/Fonts/comicbd.ttf", 0)),
}


def hand_font(size: int, name: str = "marker") -> ImageFont.ImageFont:
    for path, index in HAND_FONTS.get(name, HAND_FONTS["marker"]):
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return _font(size)


def _outline(image: Image.Image, width: int, colour=WHITE) -> Image.Image:
    """A solid border around the picture's shape, sticker style."""
    alpha = image.getchannel("A")
    grown = alpha.filter(ImageFilter.MaxFilter(width * 2 + 1)) if width > 0 else alpha
    border = Image.new("RGBA", image.size, colour)
    border.putalpha(grown)
    border.alpha_composite(image)
    return border


def _shadowed(image: Image.Image) -> Image.Image:
    pad = max(6, image.width // 30)
    canvas = Image.new("RGBA", (image.width + pad * 3, image.height + pad * 3), (0, 0, 0, 0))
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow.putalpha(image.getchannel("A").point(lambda a: int(a * 0.5)))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(pad / 2)), (pad * 2, pad * 2))
    canvas.alpha_composite(image, (pad, pad))
    return canvas


def rounded_card(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    radius = max(12, min(image.size) // 9)
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def as_sticker(image: Image.Image) -> Image.Image:
    """A white border and a soft shadow around the picture's shape."""
    border = max(6, max(image.size) // 40)
    padded = Image.new("RGBA", (image.width + border * 2, image.height + border * 2), (0, 0, 0, 0))
    padded.alpha_composite(image, (border, border))
    return _shadowed(_outline(padded, border))


def photo_sticker(path: Path, size: int = 520) -> Image.Image:
    """A photo as a sticker. Cut-outs (with transparency) keep their shape;
    plain photos become a rounded card. Both get a white border."""
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
    image.thumbnail((size, size), Image.LANCZOS)
    if image.getchannel("A").getextrema()[0] >= 250:
        image = rounded_card(image)
    return as_sticker(image)


def _wobbly_ellipse(box: tuple[float, float, float, float], seed: int, points: int = 48) -> list[tuple[float, float]]:
    import math
    import random
    rng = random.Random(seed)
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    phase = rng.uniform(0, math.tau)
    shape = []
    for i in range(points):
        a = math.tau * i / points
        r = 1 + 0.025 * math.sin(3 * a + phase) + rng.uniform(-0.012, 0.012)
        shape.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return shape


def speech_bubble(text: str, *, width_chars: int = 16, font_size: int = 46, tail: str = "left", seed: int = 0) -> Image.Image:
    """A comic speech bubble drawn by hand, with ``text``; the tail points
    down-left or down-right towards whoever speaks."""
    font = hand_font(font_size)
    lines = textwrap.wrap(text.strip(), width=width_chars)[:4] or [""]
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    widths = [probe.textbbox((0, 0), line, font=font)[2] for line in lines]
    line_height = int(font_size * 1.18)
    text_w, text_h = max(widths), line_height * len(lines)
    # An ellipse around a text block needs ~1.45x its size.
    body_w, body_h = int(text_w * 1.42 + font_size), int(text_h * 1.5 + font_size * 0.8)
    tail_h = int(font_size * 1.3)
    pad = 10
    image = Image.new("RGBA", (body_w + pad * 2, body_h + tail_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    box = (pad, pad, pad + body_w, pad + body_h)
    shape = _wobbly_ellipse(box, seed)
    root_x = pad + body_w * (0.32 if tail == "left" else 0.68)
    tip = (root_x - body_w * 0.16 if tail == "left" else root_x + body_w * 0.16, pad + body_h + tail_h)
    tail_shape = [(root_x - font_size * 0.45, pad + body_h * 0.86), (root_x + font_size * 0.45, pad + body_h * 0.86), tip]
    outline = max(4, font_size // 9)
    draw.polygon(shape, fill=INK)
    draw.polygon(tail_shape, fill=INK)
    grown = image.getchannel("A").filter(ImageFilter.MaxFilter(outline * 2 + 1))
    ink = Image.new("RGBA", image.size, INK)
    ink.putalpha(grown)
    inner = Image.new("RGBA", image.size, (0, 0, 0, 0))
    inner_draw = ImageDraw.Draw(inner)
    inner_draw.polygon(shape, fill=WHITE)
    inner_draw.polygon(tail_shape, fill=WHITE)
    ink.alpha_composite(inner)
    draw = ImageDraw.Draw(ink)
    top = pad + (body_h - text_h) / 2 - font_size * 0.08
    for index, line in enumerate(lines):
        draw.text((pad + (body_w - widths[index]) / 2, top + index * line_height), line, font=font, fill=INK)
    return _shadowed(ink)


def character_with_bubble(character: Path | Image.Image, text: str, *, side: str = "left", size: int = 520, seed: int = 0) -> Image.Image:
    """A character saying ``text`` in a bubble above its head. ``side`` is
    where the character stands on screen: the bubble leans towards the middle."""
    character = photo_sticker(character, size) if isinstance(character, Path) else character
    bubble = speech_bubble(text, tail="left" if side == "left" else "right", seed=seed)
    scale = min(1.0, character.width * 1.3 / bubble.width)
    if scale < 1.0:
        bubble = bubble.resize((int(bubble.width * scale), int(bubble.height * scale)), Image.LANCZOS)
    width = max(character.width, bubble.width + character.width // 3)
    overlap = bubble.height // 6
    canvas = Image.new("RGBA", (width, character.height + bubble.height - overlap), (0, 0, 0, 0))
    char_x = 0 if side == "left" else width - character.width
    bubble_x = width - bubble.width if side == "left" else 0
    canvas.alpha_composite(character, (char_x, canvas.height - character.height))
    canvas.alpha_composite(bubble, (bubble_x, 0))
    return canvas


def word_sticker(text: str, *, colour: str = "#ffffff", font_size: int = 170, tilt: float = -6.0, font: str = "marker", handwritten: bool = False) -> Image.Image:
    """A big shouted word, hand lettered, thick dark outline, slightly tilted."""
    face = hand_font(font_size, font)
    words = (text.strip() if handwritten else text.strip().upper())[:28] or "!"
    stroke = 1 if handwritten else max(4, font_size // 10)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    left, top, right, bottom = probe.textbbox((0, 0), words, font=face, stroke_width=stroke)
    image = Image.new("RGBA", (right - left + 40, bottom - top + 40), (0, 0, 0, 0))
    raw = colour.lstrip("#")
    fill = tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4)) + (255,) if len(raw) == 6 else WHITE
    ImageDraw.Draw(image).text((20 - left, 20 - top), words, font=face, fill=fill, stroke_width=stroke, stroke_fill=INK)
    return _shadowed(image.rotate(tilt, resample=Image.BICUBIC, expand=True))
