#!/usr/bin/env python3
"""Build the Zucker Editor green macOS icon from assets/logo_mixer.png."""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "logo_mixer.png"
GREEN_LOGO = ROOT / "assets" / "logo_editor_green.png"
WEB_LOGO = ROOT / "web" / "logo_editor_green.png"
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
    if not SOURCE.exists():
        raise FileNotFoundError(f"Missing {SOURCE}. Replace this swap point with the Mixer logo.")
    src = Image.open(SOURCE).convert("RGBA")
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
    return out


def _lerp_rgb(start: tuple[int, int, int], end: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, amount))
    return tuple(int(round(a + (b - a) * amount)) for a, b in zip(start, end))


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
