"""A plain background removed automatically: green, blue or any strong colour, or black.

Found from the picture's edges: when nearly the whole border (on every side) is one colour and
something else is inside, that colour is the background. Only background connected to the
edges is removed, so the same colour inside the subject (a green shirt in front of green, a
blue logo) stays. White is not removed automatically (documents and screenshots have white
backgrounds), only when the person asks for it ("Remove background").
"""
from __future__ import annotations

from typing import Any

import cv2
import numpy as np

EDGE = 0.04            # the border strip read, as a share of the picture's size


def _lab(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2LAB).astype(np.float32)


def find(frames: list[np.ndarray], *, allow_white: bool = False) -> dict[str, Any] | None:
    """{"color": [r, g, b], "kind": "green"|"blue"|"colour"|"black"|"white", "spread"} of a plain
    background in ``frames`` (RGB, same size), or None."""
    usable = [frame for frame in frames if frame is not None and frame.size]
    if not usable:
        return None
    h, w = usable[0].shape[:2]
    bx, by = max(2, int(w * EDGE)), max(2, int(h * EDGE))
    sides = {"top": [], "bottom": [], "left": [], "right": []}
    for frame in usable:
        sides["top"].append(frame[:by].reshape(-1, 3))
        sides["bottom"].append(frame[-by:].reshape(-1, 3))
        sides["left"].append(frame[:, :bx].reshape(-1, 3))
        sides["right"].append(frame[:, -bx:].reshape(-1, 3))
    # The subject usually cuts through one side (a person at the bottom): three sides of the
    # same colour make a screen; two (a letterbox, pillarbox bars) do not.
    strips = {name: np.concatenate(parts) for name, parts in sides.items()}
    every = np.concatenate(list(strips.values()))
    centre = np.median(_lab(every[None])[0], axis=0)
    covered = {name: float((np.linalg.norm(_lab(strip[None])[0] - centre, axis=1) < 18).mean()) for name, strip in strips.items()}
    plain_sides = sorted(covered, key=covered.get, reverse=True)[:3]
    if min(covered[name] for name in plain_sides) < 0.8:
        return None
    border = np.concatenate([strips[name] for name in plain_sides])
    lab = _lab(border[None])[0]
    centre = np.median(lab, axis=0)
    distance = np.linalg.norm(lab - centre, axis=1)
    if (distance < 18).mean() < 0.85:
        return None
    lightness, chroma = centre[0] * 100 / 255, float(np.hypot(centre[1] - 128, centre[2] - 128))
    even = float(np.percentile(distance[distance < 18], 90))
    # A chroma screen is intense and even; a clear sky is paler and has a gradient (only
    # removed when the person asks for it).
    if chroma > (30 if allow_white else 45) and (allow_white or even < 12):
        rgb = np.median(border[distance < 18], axis=0)
        hue = cv2.cvtColor(rgb.reshape(1, 1, 3).astype(np.uint8), cv2.COLOR_RGB2HSV)[0, 0, 0] * 2
        kind = "green" if 70 <= hue <= 170 else "blue" if 180 <= hue <= 260 else "colour"
    elif lightness < 12:
        kind = "black"
    elif lightness > 92 and chroma < 8 and allow_white:
        kind = "white"
    else:
        return None                                    # a grey or neutral wall is scenery, not a screen
    key = {"color": [int(v) for v in np.median(border[distance < 18], axis=0)], "kind": kind,
           "spread": round(float(np.percentile(distance[distance < 18], 90)), 2)}
    subject = np.mean([(alpha(frame, key, connected=False) > 0.5).mean() for frame in usable])
    if not 0.02 < subject < 0.92:
        return None                                    # nothing in front of it, or no background at all
    return key


def alpha(rgb: np.ndarray, key: dict[str, Any], *, connected: bool = True) -> np.ndarray:
    """0 where the plain background is, 1 on the subject, soft at the edges (float32)."""
    if key["kind"] in ("black", "white"):
        luma = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        if key["kind"] == "black":
            soft = np.clip((luma - 18) / 40, 0, 1)
        else:
            soft = np.clip((235 - luma) / 40, 0, 1)
    else:
        lab = _lab(rgb)
        target = _lab(np.asarray(key["color"], np.uint8).reshape(1, 1, 3))[0, 0]
        # Colour distance, mostly in hue and saturation (the screen's shading changes its lightness).
        distance = np.sqrt(((lab[..., 1:] - target[1:]) ** 2).sum(axis=2) + 0.15 * (lab[..., 0] - target[0]) ** 2)
        near = max(12.0, float(key.get("spread") or 12) * 1.2)
        soft = np.clip((distance - near) / 22, 0, 1)
    if not connected:
        return soft.astype(np.float32)
    background = (soft < 0.5).astype(np.uint8)
    count, labels = cv2.connectedComponents(background, connectivity=8)
    edges = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    enclosed = (background > 0) & ~np.isin(labels, edges[edges != 0])
    soft = soft.copy()
    soft[enclosed] = 1.0                                # the same colour inside the subject stays
    return soft.astype(np.float32)


def despill(rgb: np.ndarray, matte: np.ndarray, key: dict[str, Any]) -> np.ndarray:
    """The screen's colour taken off the subject's soft edges (no green or blue halo)."""
    if key["kind"] in ("black", "white"):
        return rgb
    pixels = rgb.astype(np.float32)
    grey = pixels.mean(axis=2, keepdims=True)
    edge = np.clip(1 - matte, 0, 1)[..., None] * 0.7
    return np.clip(pixels * (1 - edge) + grey * edge, 0, 255).astype(np.uint8)


def sample_frames(path: str, count: int = 5, size: tuple[int, int] = (192, 108)) -> list[np.ndarray]:
    """A few RGB frames spread over a video (small; enough to recognise its background)."""
    capture = cv2.VideoCapture(str(path))
    frames = []
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        for index in np.linspace(0, max(0, total - 1), count).astype(int):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, bgr = capture.read()
            if ok:
                frames.append(cv2.cvtColor(cv2.resize(bgr, size), cv2.COLOR_BGR2RGB))
    finally:
        capture.release()
    return frames
