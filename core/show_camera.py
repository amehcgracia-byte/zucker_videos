"""The camera of a Show edit, frame by frame.

Each shot frames the talking video with a zoom (``zoom``, moving to ``zoom_end`` when it has one)
around a centre (``focus``, shares of the picture). Between two shots the framing used to change
only by cutting: on footage that runs on (a shot split only for pace) that reads as a quick zoom
in or out. A shot marked ``"move": "glide"`` instead starts where the previous one ended and the
camera travels to its own framing in ``MOVE_SEC``, eased in and out like a camera operator.

The render (core.show_render), the stickers that follow the face (core.show_bridge,
core.show_framing) and the editor all read the camera from here, so they agree frame by frame.
"""
from __future__ import annotations

from typing import Any

MOVE_SEC = 0.5            # how long a camera move between two framings takes
FACE_HEIGHT = 0.42        # the default framing centre, share of the picture height
MOVES = ("cut", "glide")


def _length(shot: dict[str, Any]) -> int:
    return int(shot["end_frame"]) - int(shot["start_frame"])


def end_state(shot: dict[str, Any]) -> tuple[float, float, float]:
    """(zoom, cx, cy) on a shot's last frame."""
    zoom = float(shot.get("zoom") or 1.0)
    cx, cy = shot.get("focus") or (0.5, FACE_HEIGHT)
    return float(shot.get("zoom_end") or zoom), float(cx), float(cy)


def start_state(shot: dict[str, Any]) -> tuple[float, float, float]:
    cx, cy = shot.get("focus") or (0.5, FACE_HEIGHT)
    return float(shot.get("zoom") or 1.0), float(cx), float(cy)


def glide(plan: dict[str, Any], index: int) -> tuple[tuple[float, float, float], int, int] | None:
    """(from (zoom, cx, cy), frames, frames already gone) of the camera move opening shot
    ``index``, or None. A part of the edit cut out on its own (a scene preview) carries the
    move resolved on the whole edit (``glide_from``, ``glide_frames``, ``glide_skip``)."""
    shots = plan["shots"]
    shot = shots[index]
    if shot.get("move") != "glide":
        return None
    if shot.get("glide_from"):
        origin = tuple(float(v) for v in shot["glide_from"])
    elif index > 0:
        origin = end_state(shots[index - 1])
    else:
        return None
    target = start_state(shot)
    if max(abs(a - b) for a, b in zip(origin, target)) < 1e-4:
        return None
    fps = float(plan.get("fps") or 30)
    frames = int(shot.get("glide_frames") or max(2, min(round(MOVE_SEC * fps), int(_length(shot) * 0.6))))
    return origin, frames, int(shot.get("glide_skip") or 0)


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def at_frame(plan: dict[str, Any], index: int, i: int) -> tuple[float, float, float]:
    """(zoom, cx, cy) on frame ``i`` of shot ``index``."""
    shot = plan["shots"][index]
    length = _length(shot)
    zoom, cx, cy = start_state(shot)
    zoom += (float(shot.get("zoom_end") or zoom) - zoom) / max(1, length - 1) * i
    move = glide(plan, index)
    if move:
        (z0, x0, y0), frames, skip = move
        s = ease((i + skip) / frames)
        zoom, cx, cy = z0 + (zoom - z0) * s, x0 + (cx - x0) * s, y0 + (cy - y0) * s
    return zoom, cx, cy


def camera_at(plan: dict[str, Any], n: int) -> tuple[float, float, float]:
    """(zoom, cx, cy) on output frame ``n`` of the edit."""
    first = 0
    for index, shot in enumerate(plan["shots"]):
        length = _length(shot)
        if n < first + length:
            return at_frame(plan, index, max(0, n - first))
        first += length
    return 1.0, 0.5, FACE_HEIGHT


def window(zoom: float, cx: float, cy: float) -> tuple[float, float, float]:
    """(x0, y0, width) of the source picture shown, as shares, as zoompan crops it."""
    w = 1.0 / max(1.0, zoom)
    return min(max(cx - w / 2, 0.0), 1.0 - w), min(max(cy - w / 2, 0.0), 1.0 - w), w


def resolved(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """The shots with every camera move written out in full, so a part of the edit cut out
    on its own keeps them exactly (see ``trim``)."""
    shots = []
    for index, shot in enumerate(plan["shots"]):
        move = glide(plan, index)
        if move:
            origin, frames, skip = move
            shot = {**shot, "glide_from": [round(v, 5) for v in origin], "glide_frames": frames, "glide_skip": skip}
        shots.append(shot)
    return shots


def trim(shot: dict[str, Any], skip: int, length: int) -> dict[str, Any]:
    """A resolved shot starting ``skip`` frames later and lasting ``length``: its zoom
    still moves at the same pace and a camera move opening it is not restarted."""
    full = _length(shot)
    zoom = float(shot.get("zoom") or 1.0)
    end = float(shot.get("zoom_end") or zoom)
    out = {**shot, "start_frame": int(shot["start_frame"]) + skip, "end_frame": int(shot["start_frame"]) + skip + length}
    if end != zoom:
        step = (end - zoom) / max(1, full - 1)
        out["zoom"], out["zoom_end"] = round(zoom + step * skip, 5), round(zoom + step * (skip + length - 1), 5)
    if shot.get("glide_frames"):
        out["glide_skip"] = int(shot.get("glide_skip") or 0) + skip
    return out


def default_moves(plan: dict[str, Any], every: int = 2) -> int:
    """Camera moves instead of cuts where the picture runs on (a shot split only for pace):
    every ``every``-th change of framing there glides, the others stay quick cuts so the
    rhythm keeps both. Shots the person chose (``move`` already set) are kept. Returns how
    many shots now glide."""
    changes = 0
    previous: dict[str, Any] | None = None
    for shot in plan["shots"]:
        if previous is not None and "move" not in shot and shot.get("cut_before") == "pace" \
                and max(abs(a - b) for a, b in zip(end_state(previous), start_state(shot))) > 1e-4:
            changes += 1
            if every and changes % every == 0 and _length(shot) >= int(MOVE_SEC * float(plan.get("fps") or 30)) * 2:
                shot["move"] = "glide"
        previous = shot
    return sum(1 for shot in plan["shots"] if shot.get("move") == "glide")
