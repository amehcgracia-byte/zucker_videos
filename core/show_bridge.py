"""Show Studio's edit compiled onto the automatic Show (the real render).

Studio keeps its effects on the clock of its source section (seconds from the
section's start in the original video) and in the pixels of its 1280x720
picture. The automatic Show has its own clock: the kept shots of the original
at 30 fps, reframed shot by shot, with close-ups over the host. This bridge
maps every effect through that edit: source seconds to the edited moments that
still show them (a moment that was cut has none), Studio pixels to the window
each output frame crops. The automatic montage, framing and captions stay as
they are; Studio effects are drawn on top of them.

Every effect gets a result, never a silent substitute:
  applied      drawn in the render
  disabled     switched off in Studio
  superseded   replaced on purpose by the automatic edit (camera, captions)
  failed       could not be drawn (its moment was cut, its data is missing...)
  unsupported  the render cannot draw this kind yet
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STUDIO_W, STUDIO_H = 1280, 720
STUDIO_FPS = 25.0
FACE_HEIGHT = 0.38            # the render's default vertical focus (core.show_render)
# Where each section of the pilot starts in the original video (Studio's ShowSource).
SECTION_STARTS = {"workshop": 466.0, "opening": 0.0}


# ---- Clocks ----------------------------------------------------------------------

def _shots(plan: dict[str, Any]):
    """(shot, first edited frame, frames) in edited order."""
    first = 0
    for shot in plan["shots"]:
        length = int(shot["end_frame"]) - int(shot["start_frame"])
        yield shot, first, length
        first += length


def source_pieces(plan: dict[str, Any], start: float, end: float) -> list[tuple[float, float, float, float]]:
    """(edited start, edited end, source start, source end) of the parts of the
    source interval the edit kept, in edited order."""
    fps = float(plan.get("fps") or 30)
    out = []
    for shot, first, length in _shots(plan):
        a, b = shot["start_frame"] / fps, shot["end_frame"] / fps
        lo, hi = max(start, a), min(end, b)
        if hi > lo:
            offset = first / fps
            out.append((offset + lo - a, offset + hi - a, lo, hi))
    return out


def edited_to_source(plan: dict[str, Any], seconds: float) -> float | None:
    fps = float(plan.get("fps") or 30)
    for shot, first, length in _shots(plan):
        if first <= seconds * fps < first + length:
            return shot["start_frame"] / fps + seconds - first / fps
    return None


def window_at(plan: dict[str, Any], seconds: float) -> tuple[float, float, float]:
    """(x0, y0, width) of the source picture an output frame shows, as shares,
    exactly as core.show_render's zoompan crops it."""
    fps = float(plan.get("fps") or 30)
    n = int(round(seconds * fps))
    for shot, first, length in _shots(plan):
        if first <= n < first + length:
            start = float(shot.get("zoom") or 1.0)
            end = float(shot.get("zoom_end") or start)
            zoom = start + (end - start) / max(1, length - 1) * (n - first)
            w = 1.0 / zoom
            cx, cy = shot.get("focus") or (0.5, FACE_HEIGHT)
            return (min(max(cx - w / 2, 0.0), 1.0 - w), min(max(cy - w / 2, 0.0), 1.0 - w), w)
    return (0.0, 0.0, 1.0)


def to_output(point: tuple[float, float], window: tuple[float, float, float], width: int, height: int) -> tuple[float, float, float]:
    """A Studio pixel on the output frame, and how much Studio sizes grow there."""
    x0, y0, w = window
    return ((point[0] / STUDIO_W - x0) / w * width, (point[1] / STUDIO_H - y0) / w * height, width / STUDIO_W / w)


def in_window(point: tuple[float, float], window: tuple[float, float, float]) -> bool:
    x0, y0, w = window
    u, v = point[0] / STUDIO_W, point[1] / STUDIO_H
    return x0 <= u <= x0 + w and y0 <= v <= y0 + w


# ---- Layers --------------------------------------------------------------------------

@dataclass
class Layer:
    """A Studio effect on the edit: the edited moments it is drawn at, each with
    its section time, so a gesture keeps its own clock across cuts."""
    effect_id: str
    kind: str
    pieces: list[tuple[float, float, float]] = field(default_factory=list)  # (edited a, edited b, section time at a)
    params: dict[str, Any] = field(default_factory=dict)
    drawer: Any = None
    hidden: list[tuple[float, float]] = field(default_factory=list)          # edited intervals showing another picture

    def section_time(self, t: float) -> float | None:
        if any(a <= t < b for a, b in self.hidden):
            return None
        for a, b, s in self.pieces:
            if a <= t < b:
                return s + (t - a)
        return None


def _result(event: dict[str, Any], section: str, status: str, detail: str) -> dict[str, Any]:
    return {"id": event["id"], "type": event["type"], "name": event.get("name") or event["type"], "section": section,
            "start": event.get("start"), "end": event.get("end"), "status": status, "detail": detail}


def plan_effects(recipe: dict[str, Any], *, section: str, source_start: float, plan: dict[str, Any],
                 hidden: list[tuple[float, float]] | None = None, drawers: dict[str, Any] | None = None) -> tuple[list[Layer], list[dict[str, Any]]]:
    """The layers this section's recipe adds to the render, and one result per effect.
    ``drawers`` maps an effect type to the function that prepares its drawing
    (it returns (drawer, detail) or raises ValueError with the reason it cannot)."""
    drawers = drawers or {}
    layers: list[Layer] = []
    report: list[dict[str, Any]] = []
    for event in recipe.get("events") or []:
        kind = event.get("type")
        if event.get("id") in ("base", "base-audio") or kind in ("video", "audio") and event.get("source") in ("original", "baseAudio"):
            continue                                                       # the section's own picture and voice
        if event.get("enabled") is False:
            report.append(_result(event, section, "disabled", "Switched off in Show Studio"))
            continue
        if kind in ("zoom", "cut"):
            report.append(_result(event, section, "superseded", "The automatic edit's framing is kept"))
            continue
        if kind == "caption":
            report.append(_result(event, section, "superseded", "The automatic captions are kept"))
            continue
        if event.get("maskAsset") is not None or event.get("faceTrack"):
            report.append(_result(event, section, "unsupported", "Photo masks are not drawn by the render yet"))
            continue
        prepare = drawers.get(kind)
        if prepare is None:
            report.append(_result(event, section, "unsupported", f"The render does not draw “{kind}” effects yet"))
            continue
        start = source_start + float(event.get("start") or 0)
        end = source_start + float(event.get("end") or 0)
        pieces = source_pieces(plan, start, end)
        if not pieces:
            report.append(_result(event, section, "failed", "Its moment was cut from the automatic edit"))
            continue
        try:
            drawer, detail = prepare(event)
        except ValueError as exc:
            report.append(_result(event, section, "failed", str(exc)))
            continue
        kept = sum(b - a for a, b, _, _ in pieces)
        layers.append(Layer(event["id"], kind, [(a, b, sa - source_start) for a, b, sa, _ in pieces], dict(event), drawer, list(hidden or [])))
        note = f"{len(pieces)} kept part{'s' if len(pieces) != 1 else ''}, {kept:.2f} of {end - start:.2f} s"
        report.append(_result(event, section, "applied", f"{note}{'; ' + detail if detail else ''}"))
    return layers, report
