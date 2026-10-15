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

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

STUDIO_W, STUDIO_H = 1280, 720
STUDIO_FPS = 25.0
FACE_HEIGHT = 0.38            # the render's default vertical focus (core.show_render)
# Where each section of the pilot starts in the original video (Studio's ShowSource:
# "Previous workshop · 7:46–8:30" and "Beginning · 0:00–1:28").
SECTION_STARTS = {"workshop": 466.0, "opening": 0.0}
DRAWINGS = ("finger", "spiral", "paint", "math", "coffee")     # what Studio's serious mode hides
NOT_YET = {
    "cutout": "Cut-out people and their background are not drawn by the render yet",
    "part": "Moving body parts are not drawn by the render yet",
    "video": "Added videos are not composited by the render yet",
    "audio": "Added music and sounds are not mixed into the render yet",
}


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
    muted: list[tuple[float, float]] = field(default_factory=list)           # section intervals Studio hides it in
    section: str = ""

    def section_time(self, t: float) -> float | None:
        if any(a <= t < b for a, b in self.hidden):
            return None
        for a, b, s in self.pieces:
            if a <= t < b:
                at = s + (t - a)
                return None if any(m0 <= at < m1 for m0, m1 in self.muted) else at
        return None


def _result(event: dict[str, Any], section: str, status: str, detail: str) -> dict[str, Any]:
    return {"id": event["id"], "type": event["type"], "name": event.get("name") or event["type"], "section": section,
            "start": event.get("start"), "end": event.get("end"), "status": status, "detail": detail}


def _span(event: dict[str, Any]) -> tuple[float, float]:
    return float(event.get("start") or 0), float(event.get("end") or 0)


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if hi > lo else None


def plan_effects(recipe: dict[str, Any], *, section: str, source_start: float, plan: dict[str, Any],
                 hidden: list[tuple[float, float]] | None = None, drawers: dict[str, Any] | None = None) -> tuple[list[Layer], list[dict[str, Any]]]:
    """The layers this section's recipe adds to the render, and one result per effect.
    ``drawers`` maps an effect type to the function that prepares its drawing
    (it returns (drawer, detail) or raises ValueError with the reason it cannot)."""
    drawers = drawers or {}
    layers: list[Layer] = []
    report: list[dict[str, Any]] = []
    events = recipe.get("events") or []
    serious = recipe.get("mode") == "serious"
    # Studio's preview hides hand-placed finger drawings and spirals while the detected finger ink is on.
    inks = [_span(e) for e in events if e.get("autoHands") and e.get("enabled") is not False and not serious]
    for event in events:
        kind = event.get("type")
        if (event.get("id") == "base-audio" or kind == "audio" and event.get("source") == "baseAudio") and event.get("variant", "mix") == "mix":
            report.append(_result(event, section, "superseded", "The automatic edit's own sound is kept; Studio's speech + music mix is not used"))
            continue
        if event.get("id") in ("base", "base-audio") or kind in ("video", "audio") and event.get("source") in ("original", "baseAudio"):
            continue                                                       # the section's own picture and voice
        if event.get("enabled") is False:
            report.append(_result(event, section, "disabled", "Switched off in Show Studio"))
            continue
        if serious and kind in DRAWINGS:
            report.append(_result(event, section, "disabled", "Serious mode in Show Studio hides drawings"))
            continue
        if kind in ("zoom", "cut"):
            report.append(_result(event, section, "superseded", "The automatic edit's framing is kept"))
            continue
        if kind == "caption":
            report.append(_result(event, section, "superseded", "The automatic captions are kept"))
            continue
        if event.get("parent") not in (None, "base"):
            report.append(_result(event, section, "unsupported", "It sits on an added video, which the render does not show yet"))
            continue
        prepare = drawers.get(kind)
        if prepare is None:
            report.append(_result(event, section, "unsupported", NOT_YET.get(kind, f"The render does not draw “{kind}” effects yet")))
            continue
        muted: list[tuple[float, float]] = []
        if kind in ("finger", "spiral") and not event.get("autoHands"):
            muted = [span for span in (_overlap(_span(event), ink) for ink in inks) if span]
            if sum(b - a for a, b in muted) >= _span(event)[1] - _span(event)[0] - 1e-6:
                report.append(_result(event, section, "disabled", "Hidden by the automatic finger ink, as in Show Studio's preview"))
                continue
        start = source_start + float(event.get("start") or 0)
        end = source_start + float(event.get("end") or 0)
        pieces = source_pieces(plan, start, end)
        if not pieces:
            report.append(_result(event, section, "failed", "Its moment was cut from the automatic edit"))
            continue
        kept = sum(b - a for a, b, _, _ in pieces)
        shown = kept - sum(max(0.0, min(hi, m1) - max(lo, m0)) for _, _, lo, hi in pieces
                           for m0, m1 in ((m0 + source_start, m1 + source_start) for m0, m1 in muted))
        if shown <= 1e-6:
            report.append(_result(event, section, "disabled", "Hidden by the automatic finger ink wherever the edit keeps it, as in Show Studio's preview"))
            continue
        try:
            prepared = prepare(event)
        except ValueError as exc:
            report.append(_result(event, section, "failed", str(exc)))
            continue
        drawer, detail = prepared[0], prepared[1]
        warning = len(prepared) > 2 and bool(prepared[2])
        layers.append(Layer(event["id"], kind, [(a, b, sa - source_start) for a, b, sa, _ in pieces], dict(event), drawer,
                            list(hidden or []), muted, section))
        note = f"{len(pieces)} kept part{'s' if len(pieces) != 1 else ''}, {kept:.2f} of {end - start:.2f} s"
        if shown < kept - 1e-6:
            note += f"; hidden for {kept - shown:.2f} s under the automatic finger ink"
        report.append({**_result(event, section, "applied", f"{note}{'; ' + detail if detail else ''}"), **({"warning": True} if warning else {})})
    return layers, report


# ---- Drawing ---------------------------------------------------------------------------

def studio_drawers(analysis: Callable[[str], Any], source: Callable[[str], Path | None] = lambda name: None,
                   materials: Callable[[], list[dict[str, Any]]] = lambda: [],
                   asset: Callable[[str], Path] | None = None) -> dict[str, Callable[[dict[str, Any]], tuple]]:
    """How each Studio effect is drawn: ``analysis(name)`` reads the section's
    tracking data (None when it is missing), ``source(name)`` finds its source
    files (the voice), ``materials()`` the project's own masks and ``asset(id)``
    a library file. A drawer is draw(frame, section seconds, view, edited seconds)."""
    import numpy as np

    from core import show_bridge_draw as draw
    from core import show_bridge_masks as masks

    def style(event: dict[str, Any], brush: str, color: str, width: float) -> dict[str, Any]:
        return {"brush": event.get("brush") or brush, "color": event.get("color") or color, "width": float(event.get("width") or width)}

    def finger(event: dict[str, Any]):
        if event.get("autoHands"):
            hands = analysis("automatic-hands")
            if not hands or not hands.get("runs"):
                raise ValueError("The detected fingertip data (automatic-hands) is missing from the project")
            ink = style(event, "crayon", "#e7b64c", 5)

            def auto(frame, t, view, *_):
                for points, opacity in draw.auto_ink_trails(event, t, hands):
                    draw.pigment(frame, points, view, opacity=opacity, **ink)
            return auto, (f"detected index tip in {hands.get('detected_frames', '?')} of {hands.get('frames', '?')} frames, "
                          f"{len(hands['runs'])} separate strokes where detection was lost")
        tracks = analysis("finger-track") or []
        if event.get("pathMode") == "reviewed":
            if not tracks:
                raise ValueError("The reviewed fingertip path (finger-track) is missing from the project")
            a, b = _span(event)
            shift = float(event.get("contentShift") or 0)
            covered = sum(max(0.0, min(b, run[-1][0] / draw.FINGER_FPS - shift) - max(a, run[0][0] / draw.FINGER_FPS - shift))
                          for run in tracks if run)
            if covered <= 0:
                raise ValueError("The reviewed fingertip path does not reach this moment")
            detail = f"reviewed fingertip path for {covered:.2f} of {b - a:.2f} s"
        elif event.get("points"):
            detail = f"{len(event['points'])} fingertip marks"
        else:
            raise ValueError("No fingertip was marked for this drawing")
        if event.get("drawMode") in ("circle", "spiral"):
            detail += f", revealing a {event['drawMode']} at the fingertip"
        lines = style(event, "pencil", "#f4cc77", 4)

        def hand(frame, t, view, *_):
            shape = draw.finger_drawing(event, t, tracks)
            if shape is None:
                return
            kind, value = shape
            if kind == "dot":
                draw.dot(frame, value, view, color=lines["color"], radius=max(3.0, lines["width"]))
            else:
                draw.stroke(frame, value, view, **lines)
        return hand, detail

    def spiral(event: dict[str, Any]):
        lines = style(event, "crayon", "#f6ba82", 5)

        def turn(frame, t, view, *_):
            draw.stroke(frame, draw.spiral_points(event, t), view, opacity=draw.spiral_opacity(event, t), **lines)
        return turn, f"{event.get('turns', 3)} turns {'outward' if event.get('direction') == 'out' else 'inward'}"

    def coffee(event: dict[str, Any]):
        keys = event.get("positionKeys") or []
        seen = event.get("visibleKeys") or []
        if not any(on for _, on in seen):
            raise ValueError("The cup is not marked visible anywhere in Show Studio")
        start, radius = float(event.get("start") or 0), float(event.get("radius") or 0.035)
        fixed = (float(event.get("x", 0.66)) * 1280, float(event.get("y", 0.54)) * 720)

        def steam(frame, t, view, *_):
            on = False
            for time, state in seen:
                if time <= t:
                    on = bool(state)
            if not on:
                return
            cup = draw.point_at(keys, t) if keys else fixed
            if not draw.visible(frame, cup, view):
                return                                                     # the framing leaves the cup out
            draw.coffee(frame, cup, view, elapsed=t - start + float(event.get("effectOffset") or 0), radius=radius)
        return steam, f"{len(keys)} cup positions; drawn only while the cup is marked visible and in the frame"

    voice: dict[str, Any] = {}

    def envelope():
        if "data" not in voice:
            path = source("voice")
            try:
                voice["data"] = masks.envelope(masks.decode_first_channel(path), 48000) if path else None
            except (OSError, RuntimeError, ValueError, Exception):   # an unreadable voice leaves the jaw still, reported
                voice["data"] = None
        return voice["data"]

    def picture_of(mask_id: str):
        own = next((item for item in materials() if item.get("id") == mask_id and item.get("kind") == "mask"), None)
        record = own or masks.builtin_masks().get(mask_id)
        if record is None:
            raise ValueError("Its mask photo is not in the project or the built-in masks")
        if own and own.get("asset"):
            if asset is None:
                raise ValueError("Its mask photo is in a project library this render cannot open")
            path = asset(own["asset"])
        else:
            path = masks.studio_files() / "show-masks" / str((own or {}).get("builtin") or record.get("file") or "")
        if not path.is_file():
            raise ValueError(f"Its mask photo {path.name} is missing")
        return masks.head_picture(masks.load_rgb(path), record.get("polygon"), record.get("mouth")), record

    def image(event: dict[str, Any]):
        if event.get("maskAsset") is not None:
            (rgba, box), record = picture_of(str(event["maskAsset"]))
            height, width = rgba.shape[:2]
            faces = analysis("face-track") or {}
            track = event.get("faceTrack") or faces.get("points") or []
            fps = float(faces.get("fps") or masks.FACE_FPS)
            speech = event.get("mouthMode") in ("speech", None)
            jaw = bool(event.get("animateMouth") and box)
            warn = jaw and speech and envelope() is None

            def mask(frame, t, view, *_):
                e = masks.motion_at(event, t)
                face = masks.face_at(track, t * fps, True) if track else None
                p = masks.placement(e, face)
                picture = rgba
                if jaw:
                    value = masks.voice_at(envelope(), t) if speech else p["mouth"]
                    opened = masks.mouth_open({**e, "mouthMode": "detected" if speech else e.get("mouthMode")}, t, value)
                    drop = opened * min(box[3] * 0.8, height * 0.08)
                    if drop > 0.5:
                        picture, _ = masks.with_jaw(rgba, box, drop)
                masks.place(frame, picture, (p["x"], p["y"]), (width / 2, height / 2), p["width"] / width, p["angle"], view,
                            float(masks._or(e.get("opacity"), 1)))
            follows = (f"follows the selected face ({len(event['faceTrack'])} samples)" if event.get("faceTrack")
                       else f"follows the section's face track ({len(track)} samples)" if track and event.get("followFace") is not False
                       else "placed where it was put")
            moves = f", {len(event['motionKeys'])} keyframes" if event.get("motionKeys") else ""
            mouth = ("" if not jaw else "; the voice track is missing, so the jaw stays still" if warn
                     else "; the jaw moves with the voice" if speech else f"; the mouth is {event.get('mouthMode')}")
            return mask, f"{record.get('title') or event['maskAsset']} mask, {follows}{moves}{mouth}", warn
        if event.get("libraryAsset") == "flying-bull":
            path = masks.studio_files() / "show-elements" / "flying-bull-clean.png"
        else:
            raise ValueError("Its picture was attached in the browser only; attach it again in Show Studio inside Zucker")
        if not path.is_file():
            raise ValueError(f"Its picture {path.name} is missing")
        rgb = masks.load_rgb(path).astype(np.float32) / 255
        rgba = rgb if rgb.shape[2] == 4 else np.dstack([rgb, np.ones(rgb.shape[:2], np.float32)])
        rgba = np.dstack([rgba[..., :3] * rgba[..., 3:4], rgba[..., 3:4]])

        def picture(frame, t, view, *_):
            e = masks.motion_at(event, t)
            w = float(e.get("radius") or 0.12) * STUDIO_W
            masks.place(frame, rgba, (float(e.get("x", 0.5)) * STUDIO_W, float(e.get("y", 0.5)) * STUDIO_H),
                        (rgba.shape[1] / 2, rgba.shape[0] / 2), w / rgba.shape[1], (e.get("rotation") or 0) * math.pi / 180, view,
                        float(masks._or(e.get("opacity"), 1)))
        return picture, f"{path.stem}{', ' + str(len(event['motionKeys'])) + ' keyframes' if event.get('motionKeys') else ''}"

    def paint(event: dict[str, Any]):
        paths = [path for path in event.get("paths") or [] if path]
        if not paths:
            raise ValueError("It has no strokes")
        ink = style(event, "crayon", "#e7b64c", 5)
        start = float(event.get("start") or 0)

        def strokes(frame, t, view, *_):
            for index, path in enumerate(paths):
                progress = max(0.0, min(1.0, (t - start - index * 0.25) / 1.1))
                draw.pigment(frame, draw.reveal([tuple(p) for p in path], progress), view, **ink)
        return strokes, f"{len(paths)} hand-drawn stroke{'s' if len(paths) != 1 else ''}, {ink['brush']}, revealed in order"

    def lettering(kind: str):
        def prepare(event: dict[str, Any]):
            from core import show_bridge_text as text
            main, _ = text.fonts_for(event.get("style") or "classic", 40)
            if main is None:
                raise ValueError("No font for its words on this computer")
            if kind == "text" and not str(event.get("text") or "").strip():
                raise ValueError("It has no words")
            font = Path(main.path).stem

            def letters(frame, t, view, *_):
                (text.thinking if kind == "math" else text.words)(frame, event, t, view)
            what = f"{int(event.get('count') or 10)} formulas" if kind == "math" else f"“{str(event.get('text'))[:40]}”"
            return letters, f"{what} in {font}"
        return prepare

    return {"finger": finger, "spiral": spiral, "coffee": coffee, "image": image, "paint": paint,
            "math": lettering("math"), "text": lettering("text")}


class StudioDrawings:
    """Studio layers drawn on the automatic edit's frames (edited seconds)."""

    def __init__(self, plan: dict[str, Any], layers: list[Layer]):
        self.plan = plan
        self.layers = layers

    def __bool__(self) -> bool:
        return bool(self.layers)

    def active(self, t: float) -> bool:
        return any(layer.section_time(t) is not None for layer in self.layers)

    def draw(self, frame, t: float) -> None:
        x0, y0, w = window_at(self.plan, t)
        view = (x0 * STUDIO_W, y0 * STUDIO_H, frame.shape[1] / STUDIO_W / w)
        for layer in self.layers:
            at = layer.section_time(t)
            if at is not None:
                layer.drawer(frame, at, view, t)


def studio_layers(project_folder: str | Path, plan: dict[str, Any], *,
                  hidden: list[tuple[float, float]] | None = None) -> tuple[StudioDrawings, list[dict[str, Any]]]:
    """Every saved Studio section of the project, compiled onto ``plan``: what to
    draw, and one result per effect."""
    from core.show_studio_store import DEFAULT_SECTION, StudioStore
    base = Path(project_folder) / "show_studio"
    names = ([DEFAULT_SECTION] if (base / "composition.json").is_file() else []) + \
        sorted(path.parent.name for path in (base / "sections").glob("*/composition.json"))
    layers: list[Layer] = []
    report: list[dict[str, Any]] = []
    for name in names:
        try:
            store = StudioStore(Path(project_folder), name)
            recipe = store.state()["composition"] or {}
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            report.append({"id": "*", "type": "section", "name": name, "section": name, "start": None, "end": None,
                           "status": "failed", "detail": f"Its saved edit could not be read: {exc}"})
            continue
        if name not in SECTION_STARTS:
            for event in recipe.get("events") or []:
                if event.get("id") not in ("base", "base-audio"):
                    report.append(_result(event, name, "failed", "Where this section starts in the original video is unknown"))
            continue

        def analysis(kind: str, store=store) -> Any:
            try:
                return store.load_analysis(kind)
            except (ValueError, OSError, json.JSONDecodeError):
                return None

        def source(kind: str, store=store) -> Path | None:
            try:
                found = store.state()["sources"].get(kind)
                return store.asset_path(found) if found else None
            except (ValueError, OSError):
                return None

        def materials(store=store) -> list[dict[str, Any]]:
            try:
                return store.state()["materials"]
            except (ValueError, OSError, json.JSONDecodeError):
                return []
        found, results = plan_effects(recipe, section=name, source_start=SECTION_STARTS[name], plan=plan, hidden=hidden,
                                      drawers=studio_drawers(analysis, source, materials, store.asset_path))
        layers += found
        report += results
    from core.show_clay import clay_layers
    clay, results = clay_layers(project_folder, plan, hidden=hidden)
    return StudioDrawings(plan, layers + clay), report + results


def studio_warnings(report: list[dict[str, Any]]) -> list[str]:
    """One plain line per Studio effect the render could not draw."""
    return [f"Show Studio · {item['name']} ({item['section']}): {item['detail']}"
            for item in report if item["status"] in ("failed", "unsupported") or item.get("warning")]


def studio_fingerprint(project_folder: str | Path) -> str:
    """Changes whenever a saved Studio edit or its tracking data changes (preview caches)."""
    import hashlib
    digest = hashlib.sha1()
    base = Path(project_folder) / "show_studio"
    for path in sorted([*base.glob("composition.json"), *base.glob("clay.json"), *base.glob("analysis/*.json"),
                        *base.glob("sections/*/composition.json"), *base.glob("sections/*/analysis/*.json")]):
        stat = path.stat()
        digest.update(f"{path.relative_to(base)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:12]
