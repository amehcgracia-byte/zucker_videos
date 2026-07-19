#!/usr/bin/env python3
"""Build the Zucker Editor light-blue macOS icon from assets/logo_mixer.png."""

from __future__ import annotations

import colorsys
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "logo_mixer.png"
BLUE_LOGO = ROOT / "assets" / "logo_editor_blue.png"
ICONSET = ROOT / "build" / "ZuckerEditor.iconset"
ICNS = ROOT / "assets" / "icon.icns"
TARGET_RGB = (0x7E, 0xC8, 0xE3)
ICON_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def recolor_logo() -> Image.Image:
    """Recolor non-transparent, saturated pixels toward Zucker Editor blue."""
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
    BLUE_LOGO.parent.mkdir(parents=True, exist_ok=True)
    out.save(BLUE_LOGO)
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


def main() -> None:
    image = recolor_logo()
    save_iconset(image)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(ICNS)


if __name__ == "__main__":
    main()
