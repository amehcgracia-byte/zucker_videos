"""Drawings by the person's own Claude, stroke by stroke.

Claude does not paint pixels: asked through the local Claude Code (the person's
own subscription, signed in with their Claude account), it answers with a crude
doodle as an ordered list of strokes on a 1000x1000 sheet: outlines, and areas
to scribble colour into. Here those strokes are drawn with the wax-crayon stamp
of Show Studio (core.show_bridge_draw.pigment) on a transparent picture. The
strokes are kept beside the picture, so the drawing can also draw itself on the
video, line by line, in the order Claude drew it.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

SHEET = 1000
SIZE = 1024
COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

SCHEMA = {
    "type": "object",
    "properties": {
        "strokes": {
            "type": "array", "minItems": 3, "maxItems": 90,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["line", "fill"]},
                    "color": {"type": "string"},
                    "width": {"type": "number"},
                    "points": {"type": "array", "minItems": 2, "maxItems": 80,
                               "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "number"}}},
                },
                "required": ["kind", "color", "width", "points"],
                "additionalProperties": False,
            },
        },
        "note": {"type": "string"},
    },
    "required": ["strokes"],
    "additionalProperties": False,
}

SYSTEM = ("You draw for a comedy video like an ordinary adult doodling quickly with wax crayons: crude, funny, wobbly and "
          "unfinished, like a child's drawing in technique (a lopsided guitar, a potato-shaped sofa), but the people keep the age "
          "and identity asked for. You answer only with strokes on a 1000x1000 sheet whose background stays empty (transparent). "
          "Draw the main shapes as 'line' strokes in a dark crayon first, then add colour as 'fill' strokes: a fill is a closed "
          "outline of an area that gets scribbled in with that colour, patchy and not neat. Use 3 to 5 flat colours, wobbly "
          "hand-made points (never perfect circles or straight ruler lines), thick lines (width 6-18), and keep the drawing "
          "inside 60..940 so nothing is cut. 12 to 60 strokes in the order a person would draw them. No text, no frame, no "
          "background scenery unless asked, no shading.")

STYLE_HINTS = {
    "crayon": "Rough wax crayons: thick, broken, patchy lines.",
    "pencil": "A messy pencil doodle: thinner grey lines (width 4-8), little colour.",
    "marker": "A clumsy felt-tip pen drawing: even lines, flat bright colour.",
}


def prompt_for(job: dict[str, Any], previous: dict[str, Any] | None, identity: str = "") -> str:
    parts = [f"Draw this for the video: {job['direction']}",
             f"Style: {STYLE_HINTS.get(job.get('style') or 'crayon', STYLE_HINTS['crayon'])}"]
    if job.get("selected_text"):
        parts.append(f"It appears while the presenter says: “{job['selected_text']}”")
    if job.get("use_face") and identity:
        parts.append(f"The presenter, if he appears, is: {identity}. Keep him an adult; draw him crudely with those marks.")
    if previous:
        parts.append("This is your earlier drawing of it; change it as asked and keep what was not mentioned:\n"
                     + json.dumps(previous, separators=(",", ":"))[:20000])
    return "\n".join(parts)


def ask(prompt: str, *, timeout: float = 300.0) -> dict[str, Any]:
    """Claude Code's strokes for ``prompt`` (raises RuntimeError with a plain reason)."""
    from core.gag_director import SIGN_IN_HELP, claude_cli
    cli = claude_cli()
    if not cli:
        raise RuntimeError("Claude Code is not installed on this computer. Install it and sign in once with `claude`.")
    command = [cli, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA),
               "--tools", "", "--no-session-persistence", "--system-prompt", SYSTEM]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()), check=False,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Claude took too long to draw it; try again.") from exc
    except OSError as exc:
        raise RuntimeError(f"Claude Code could not start: {exc}") from exc
    try:
        payload = json.loads(result.stdout or "{}")
    except ValueError as exc:
        raise RuntimeError((result.stderr or result.stdout or "Claude Code did not answer").strip()[-300:]) from exc
    if payload.get("is_error"):
        message = str(payload.get("result") or "Claude Code failed")
        if re.search(r"authenticat|oauth|log ?in|sign ?in|credential", message, re.I):
            message = SIGN_IN_HELP
        raise RuntimeError(message)
    drawing = payload.get("structured_output")
    if not isinstance(drawing, dict):
        match = re.search(r"\{.*\}", str(payload.get("result") or ""), re.S)
        drawing = json.loads(match.group(0)) if match else {}
    return clean(drawing)


def clean(drawing: Any) -> dict[str, Any]:
    """Only well-formed strokes, inside the sheet."""
    strokes = []
    for raw in (drawing or {}).get("strokes") or []:
        try:
            points = [(min(SHEET, max(0.0, float(x))), min(SHEET, max(0.0, float(y)))) for x, y in raw["points"]]
        except (KeyError, TypeError, ValueError):
            continue
        if len(points) < 2:
            continue
        colour = str(raw.get("color") or "#2b2118")
        strokes.append({"kind": "fill" if raw.get("kind") == "fill" else "line",
                        "color": colour if COLOUR.match(colour) else "#2b2118",
                        "width": min(30.0, max(2.0, float(raw.get("width") or 8))), "points": points})
    if len(strokes) < 3:
        raise RuntimeError("Claude's drawing came back empty; try saying it another way.")
    return {"strokes": strokes}


def _scribble(points: list[tuple[float, float]], width: float) -> list[list[tuple[float, float]]]:
    """Back-and-forth crayon strokes filling a closed outline, a little untidy: one
    pass across, then a second one on the slant, so the colour is dense but patchy."""
    import cv2
    polygon = np.array(points, dtype=np.float32)
    mask = np.zeros((SHEET, SHEET), np.uint8)
    cv2.fillPoly(mask, [np.round(polygon).astype(np.int32)], 255)
    if not mask.any():
        return []
    gap = max(5.0, width * 0.5)
    paths = []
    for angle in (0.0, 0.6):
        turned = mask if not angle else cv2.warpAffine(mask, cv2.getRotationMatrix2D((SHEET / 2, SHEET / 2), math.degrees(angle), 1.0), (SHEET, SHEET))
        back = cv2.getRotationMatrix2D((SHEET / 2, SHEET / 2), -math.degrees(angle), 1.0)
        ys = np.nonzero(turned.any(axis=1))[0]
        if not len(ys):
            continue
        y, row = float(ys.min()) + gap / 2, 0
        while y <= ys.max():
            line = np.nonzero(turned[int(y)])[0]
            if len(line):
                a, b = float(line.min()), float(line.max())
                wobble = math.sin(row * 1.7 + angle * 5) * gap * 0.3
                segment = [(a + 3, y + wobble), (b - 3, y - wobble)]
                if row % 2:
                    segment = segment[::-1]
                paths.append([tuple(back @ np.array([x, yy, 1.0])) for x, yy in segment])
            y += gap
            row += 1
    return paths


class Painter:
    """The drawing being drawn: ``at(share)`` gives it with that share of its strokes,
    in Claude's order (fills stay under the lines). Shares only grow from frame to
    frame, so each finished stroke is drawn once."""

    def __init__(self, drawing: dict[str, Any], size: int = SIZE):
        self.strokes = drawing["strokes"]
        self.size = size
        self.k = size / SHEET
        self.layers = [np.zeros((size, size, 3), np.uint8) for _ in range(4)]    # lines, line ink, fills, fill ink
        self.done = 0

    def _draw(self, stroke: dict[str, Any], part: float, layers: list[np.ndarray]) -> None:
        from core import show_bridge_draw as draw
        fill = stroke["kind"] == "fill"
        paths = _scribble(stroke["points"], stroke["width"]) if fill else [stroke["points"]]
        if part < 1.0:                                                        # the stroke being drawn right now
            flat = draw.resample([point for path in paths for point in path], 2.0)
            paths = [flat[:max(2, int(len(flat) * part))]]
        colour, ink = (layers[2], layers[3]) if fill else (layers[0], layers[1])
        # Lines go over twice, a hair apart, as a pressed crayon does; fills are scribbled wide.
        passes = [(0.0, 0.0)] if fill else [(0.0, 0.0), (1.6, 1.1)]
        width = stroke["width"] * (0.9 if fill else 0.75)
        for path in paths:
            for dx, dy in passes:
                moved = [(x + dx, y + dy) for x, y in path]
                # The colour, and the same strokes in white on black to know where the crayon went.
                draw.pigment(colour, moved, (0.0, 0.0, self.k), brush="crayon", color=stroke["color"], width=width)
                draw.pigment(ink, moved, (0.0, 0.0, self.k), brush="crayon", color="#ffffff", width=width)

    def at(self, share: float = 1.0) -> np.ndarray:
        """RGBA uint8, transparent around the drawing."""
        shown = max(0.0, min(1.0, share)) * len(self.strokes)
        while self.done < len(self.strokes) and self.done + 1 <= shown + 1e-9:
            self._draw(self.strokes[self.done], 1.0, self.layers)
            self.done += 1
        layers = self.layers
        if self.done < len(self.strokes) and shown - self.done > 0.02:
            layers = [layer.copy() for layer in self.layers]
            self._draw(self.strokes[self.done], shown - self.done, layers)
        lines, line_ink, fills, fill_ink = layers
        a_line = line_ink[..., 0].astype(np.float32) / 255
        a_fill = fill_ink[..., 0].astype(np.float32) / 255
        # Each layer was drawn over black: its colour is what it holds over how much ink is there.
        line_rgb = lines / np.maximum(a_line[..., None], 1e-6)
        fill_rgb = fills / np.maximum(a_fill[..., None], 1e-6)
        a = a_line + a_fill * (1 - a_line)
        rgb = (line_rgb * a_line[..., None] + fill_rgb * (a_fill * (1 - a_line))[..., None]) / np.maximum(a[..., None], 1e-6)
        return np.dstack([np.clip(rgb, 0, 255), a * 255]).astype(np.uint8)


def render(drawing: dict[str, Any], *, size: int = SIZE, upto: float | None = None) -> np.ndarray:
    """The drawing as RGBA uint8; ``upto`` in 0..1 draws only that share of it."""
    return Painter(drawing, size).at(1.0 if upto is None else upto)


def identity_hint(project) -> str:
    """How the presenter looks, from the approved clay character when the project has one."""
    try:
        from core.show_clay import ClayStore
        for character in ClayStore(project.folder).load().get("characters") or []:
            features = character.get("record", {}).get("identity") or {}
            words = ", ".join(str(v) for v in features.values() if v)
            if words:
                return words
    except Exception:  # no clay records: the description stays generic
        pass
    return "an adult man"
