#!/usr/bin/env python3
"""Build the Zucker Editor green macOS icon from assets/logo_mixer.png."""

from __future__ import annotations

import colorsys
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
ICON_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def recolor_logo() -> Image.Image:
    """Recolor non-transparent, saturated pixels toward Zucker Editor green."""
    if not SOURCE.exists():
        raise FileNotFoundError(f"Missing {SOURCE}. Replace this swap point with the Mixer logo.")
    src = Image.open(SOURCE).convert("RGBA")
    target_h, target_s, _target_v = colorsys.rgb_to_hsv(*(channel / 255 for channel in TARGET_RGB))
    pixels = []
    for r, g, b, a in src.getdata():
        if a == 0:
            pixels.append((r, g, b, a))
            continue
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s > 0.08 and v < 0.98:
            nr, ng, nb = colorsys.hsv_to_rgb(target_h, max(s, target_s), v)
            pixels.append((int(nr * 255), int(ng * 255), int(nb * 255), a))
        else:
            pixels.append((r, g, b, a))
    out = Image.new("RGBA", src.size)
    out.putdata(pixels)
    GREEN_LOGO.parent.mkdir(parents=True, exist_ok=True)
    out.save(GREEN_LOGO)
    WEB_LOGO.parent.mkdir(parents=True, exist_ok=True)
    out.save(WEB_LOGO)
    return out


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
