#!/usr/bin/env python3
"""Build Zucker Editor icon and export logo assets."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "logo_mixer.png"
SOURCE_DIR = ROOT / "assets" / "source"
FULL_CIRCLE_SOURCE = SOURCE_DIR / "logo_full_circle.png"
SYMBOL_A_SOURCE = SOURCE_DIR / "logo_symbol_a.png"
SYMBOL_B_SOURCE = SOURCE_DIR / "logo_symbol_b.png"
INTRO_BLACK_SOURCE = SOURCE_DIR / "logo_intro_black.jpg"
INTRO_BLACK_PNG_SOURCE = SOURCE_DIR / "logo_intro_black.png"
GREEN_LOGO = ROOT / "assets" / "logo_editor_green.png"
WEB_LOGO = ROOT / "web" / "logo_editor_green.png"
WHITE_LOGO = ROOT / "assets" / "logo_editor_white.png"
WEB_WHITE_LOGO = ROOT / "web" / "logo_editor_white.png"
ICON_SOURCE = ROOT / "assets" / "logo_icon_source.png"
WATERMARK = ROOT / "assets" / "logo_watermark.png"
WEB_WATERMARK = ROOT / "web" / "logo_watermark.png"
INTRO_BLACK = ROOT / "assets" / "logo_intro_black.png"
WEB_INTRO_BLACK = ROOT / "web" / "logo_intro_black.png"
ICONSET = ROOT / "build" / "ZuckerEditor.iconset"
ICNS = ROOT / "assets" / "icon.icns"
DMG_BACKGROUND = ROOT / "build" / "dmg_background.png"
TARGET_RGB = (0x7F, 0xBF, 0x62)
GREEN_DARK = (0x1E, 0x3F, 0x17)
GREEN_MID = TARGET_RGB
GREEN_LIGHT = (0xD7, 0xF1, 0xCE)
ICON_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def recolor_logo() -> Image.Image:
    """Colorize non-transparent logo pixels into the Zucker Editor green ramp."""
    icon_source = FULL_CIRCLE_SOURCE if FULL_CIRCLE_SOURCE.exists() else SOURCE
    if not icon_source.exists():
        raise FileNotFoundError(f"Missing {SOURCE}. Replace this swap point with the Mixer logo.")
    src = Image.open(icon_source).convert("RGBA")
    ICON_SOURCE.parent.mkdir(parents=True, exist_ok=True)
    src.save(ICON_SOURCE)
    pixels = []
    for r, g, b, a in src.getdata():
        if a == 0:
            pixels.append((r, g, b, a))
            continue
        luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
        if luminance < 0.55:
            nr, ng, nb = _lerp_rgb(GREEN_DARK, GREEN_MID, luminance / 0.55)
        else:
            nr, ng, nb = _lerp_rgb(GREEN_MID, GREEN_LIGHT, (luminance - 0.55) / 0.45)
        pixels.append((nr, ng, nb, a))
    out = Image.new("RGBA", src.size)
    out.putdata(pixels)
    GREEN_LOGO.parent.mkdir(parents=True, exist_ok=True)
    out.save(GREEN_LOGO)
    WEB_LOGO.parent.mkdir(parents=True, exist_ok=True)
    out.save(WEB_LOGO)
    white = white_logo(src)
    white.save(WHITE_LOGO)
    WEB_WHITE_LOGO.parent.mkdir(parents=True, exist_ok=True)
    white.save(WEB_WHITE_LOGO)
    prepare_intro_logo()
    prepare_watermark()
    return out


def _lerp_rgb(start: tuple[int, int, int], end: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(int(round(a + (b - a) * amount)) for a, b in zip(start, end))


def white_logo(src: Image.Image) -> Image.Image:
    """Return a transparent logo with all visible pixels white."""
    out = Image.new("RGBA", src.size)
    out.putdata([(255, 255, 255, a) for _r, _g, _b, a in src.getdata()])
    WHITE_LOGO.parent.mkdir(parents=True, exist_ok=True)
    return out


def prepare_intro_logo() -> Path | None:
    """Copy the user-provided black-background intro/outro logo when present."""
    source = INTRO_BLACK_PNG_SOURCE if INTRO_BLACK_PNG_SOURCE.exists() else INTRO_BLACK_SOURCE
    if not source.exists():
        return None
    image = Image.open(source).convert("RGB")
    INTRO_BLACK.parent.mkdir(parents=True, exist_ok=True)
    image.save(INTRO_BLACK)
    WEB_INTRO_BLACK.parent.mkdir(parents=True, exist_ok=True)
    image.save(WEB_INTRO_BLACK)
    return INTRO_BLACK


def prepare_watermark() -> Path | None:
    """Choose and validate a transparent watermark symbol, or keep the existing fallback."""
    chosen = choose_symbol_source()
    if chosen:
        image = Image.open(chosen).convert("RGBA")
        assert_real_alpha(image, chosen)
        WATERMARK.parent.mkdir(parents=True, exist_ok=True)
        image.save(WATERMARK)
        WEB_WATERMARK.parent.mkdir(parents=True, exist_ok=True)
        image.save(WEB_WATERMARK)
        return WATERMARK
    fallback = WHITE_LOGO if WHITE_LOGO.exists() else WEB_WHITE_LOGO
    if not fallback.exists():
        return None
    image = Image.open(fallback).convert("RGBA")
    try:
        assert_real_alpha(image, fallback)
    except ValueError:
        WATERMARK.unlink(missing_ok=True)
        WEB_WATERMARK.unlink(missing_ok=True)
        print("No verified transparent watermark asset; corner watermark will be disabled until assets/source/logo_symbol_a.png or logo_symbol_b.png is provided.")
        return None
    WATERMARK.parent.mkdir(parents=True, exist_ok=True)
    image.save(WATERMARK)
    WEB_WATERMARK.parent.mkdir(parents=True, exist_ok=True)
    image.save(WEB_WATERMARK)
    return WATERMARK


def choose_symbol_source() -> Path | None:
    """Pick the cleaner small watermark symbol when both variants are present."""
    candidates = [path for path in (SYMBOL_A_SOURCE, SYMBOL_B_SOURCE) if path.exists()]
    if not candidates:
        return None
    scored = [(symbol_legibility_score(path), path) for path in candidates]
    return sorted(scored, key=lambda item: (-item[0], item[1].name))[0][1]


def symbol_legibility_score(path: Path) -> float:
    """Score visible opaque area after downscaling; higher tends to read better small."""
    image = Image.open(path).convert("RGBA")
    assert_real_alpha(image, path)
    score = 0.0
    for size, weight in ((64, 2.0), (256, 1.0)):
        resized = image.resize((size, size), Image.Resampling.LANCZOS)
        alpha = list(resized.getchannel("A").getdata())
        opaque = sum(1 for value in alpha if value >= 96)
        varied = len(set(alpha)) > 2
        score += weight * opaque / max(1, len(alpha))
        if varied:
            score += 0.05 * weight
    return score


def assert_real_alpha(image: Image.Image, path: Path) -> None:
    """Fail for fully opaque assets pretending to be transparent watermarks."""
    if image.mode != "RGBA":
        raise ValueError(f"{path} must be RGBA for watermark use")
    alpha_values = set(image.getchannel("A").getdata())
    if len(alpha_values) < 2 or min(alpha_values) >= 250:
        raise ValueError(f"{path} has no real alpha transparency; refusing to ship a boxed watermark")


def save_iconset(image: Image.Image) -> None:
    """Write all required iconset PNG sizes."""
    ICONSET.mkdir(parents=True, exist_ok=True)
    for size in ICON_SIZES:
        resized = image.resize((size, size), Image.Resampling.LANCZOS)
        if size <= 512:
            resized.save(ICONSET / f"icon_{size}x{size}.png")
        if size >= 32:
            base = size // 2
            resized.save(ICONSET / f"icon_{base}x{base}@2x.png")


def save_dmg_background(image: Image.Image) -> None:
    """Create a simple branded DMG background."""
    width, height = 640, 400
    bg = Image.new("RGBA", (width, height), (245, 251, 242, 255))
    logo = image.copy()
    logo.thumbnail((220, 220), Image.Resampling.LANCZOS)
    logo.putalpha(logo.getchannel("A").point(lambda value: int(value * 0.16)))
    bg.alpha_composite(logo, ((width - logo.width) // 2, 34))
    DMG_BACKGROUND.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(DMG_BACKGROUND)


def main() -> None:
    image = recolor_logo()
    save_iconset(image)
    save_dmg_background(image)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(ICNS)


if __name__ == "__main__":
    main()
