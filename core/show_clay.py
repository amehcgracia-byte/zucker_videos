"""Clay memories inside the automatic Show.

A clay story (the clay workflow's story-v3.json) re-enacts a stretch of what the
host says with the approved clay character: shots on the story's own clock,
which is the source clock from ``source_start``. Each pose is cued once by the
words and held until the next cue (tools/qa/show_clay_pose_model.js); nothing
loops. Each shot shows one tile of a 3x2 pose sheet, as the clay scene tool
draws it.

Placements put parts of the story in the montage, on the story clock, so they
follow the host's voice through the edit's cuts (a moment the edit removed is
simply not shown). The host's voice and captions are never touched. Modes:

  full    the clay picture fills the frame
  pip     a small clay picture over the host
  bubble  a thought bubble whose small circles lead to the host's head
  blend   the clay picture laid translucently over the host

Gaps between placements return to the host. Every placement has its position,
size, opacity, fade and in/out points, all editable.

Stories, their pose sheets and the character reference live in the project
(the sheets in the project library, by SHA-256); show_studio/clay.json keeps
the records and placements, with each replaced version in history/.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

STORE = "clay.json"
STUDIO_W = 1280
MODES = ("full", "pip", "bubble", "blend")
DEFAULTS = {
    "full": {"x": 0.5, "y": 0.5, "size": 1.0, "opacity": 1.0},
    "pip": {"x": 0.21, "y": 0.27, "size": 0.34, "opacity": 1.0},
    "bubble": {"x": 0.21, "y": 0.24, "size": 0.32, "opacity": 1.0},
    "blend": {"x": 0.5, "y": 0.5, "size": 1.0, "opacity": 0.5},
}
PAPER = "#f4eedf"
INK = "#3b3127"


# ---- The story ---------------------------------------------------------------------

def validate_story(story: dict[str, Any]) -> None:
    """The same checks as tools/qa/build_clay_pose_preview.py, plus the source clock."""
    duration = float(story["duration"])
    start, end = float(story["source_start"]), float(story["source_end"])
    if not all(map(math.isfinite, (duration, start, end))) or abs(end - start - duration) > 0.01:
        raise ValueError("The story's source interval must match its duration")
    previous = 0.0
    for shot in story["shots"]:
        a, b = float(shot["start"]), float(shot["end"])
        if not all(map(math.isfinite, (a, b))) or abs(a - previous) > 0.001 or b <= a:
            raise ValueError("Shots must be finite, positive and contiguous from zero")
        if not shot.get("sheet"):
            raise ValueError("Each shot needs a pose sheet")
        poses = shot.get("poses") or []
        if not poses or any(type(p) is not int or not 0 <= p < 6 for p in poses):
            raise ValueError("Each shot needs grid pose indices from zero to five")
        keys = shot.get("poseKeys") or []
        if any(not math.isfinite(float(k["time"])) or type(k["pose"]) is not int or not 0 <= k["pose"] < 6 for k in keys) \
                or any(x["time"] > y["time"] for x, y in zip(keys, keys[1:])):
            raise ValueError("Pose cues must be finite, ordered and use indices zero to five")
        previous = b
    if not story["shots"] or abs(previous - duration) > 0.001:
        raise ValueError("Shot duration must match the story")


def pose_at(shot: dict[str, Any], t: float) -> int:
    """show_clay_pose_model.js poseAt(): the last cue at or before ``t``, held."""
    keys = shot.get("poseKeys") or []
    pose = keys[0]["pose"] if keys else (shot.get("poses") or [0])[0]
    for key in keys:
        if key["time"] > t:
            break
        pose = key["pose"]
    return pose


def shot_at(story: dict[str, Any], t: float) -> dict[str, Any]:
    t = min(t, float(story["duration"]) - 0.001)
    for shot in story["shots"]:
        if shot["start"] <= t < shot["end"]:
            return shot
    return story["shots"][0]


def tile(width: int, height: int, pose: int, close: bool) -> tuple[float, float, float, float]:
    """The clay scene tool's crop of one pose: (x, y, w, h) on the sheet."""
    w, h, inset = width / 3, height / 2, 2
    x, y, cw, ch = pose % 3 * w + inset, pose // 3 * h + inset, w - 2 * inset, h - 2 * inset
    if close:
        x, y, cw, ch = x + cw * 0.04, y + ch * 0.1, cw * 0.8, ch * 0.8
    return x, y, cw, ch


def crop_to(sheet: np.ndarray, rect: tuple[float, float, float, float], width: int, height: int) -> np.ndarray:
    """drawImage(sheet, x, y, w, h, 0, 0, width, height): bilinear, pixel centres."""
    import cv2
    x, y, cw, ch = rect
    sx, sy = cw / width, ch / height
    matrix = np.float32([[sx, 0, x + 0.5 * sx - 0.5], [0, sy, y + 0.5 * sy - 0.5]])
    return cv2.warpAffine(sheet, matrix, (width, height), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


class ClayStory:
    """A validated story with its pose sheets loaded."""

    def __init__(self, record: dict[str, Any], sheets: dict[str, np.ndarray]):
        validate_story(record)
        missing = sorted({shot["sheet"] for shot in record["shots"]} - set(sheets))
        if missing:
            raise ValueError(f"The pose sheet {missing[0]} is missing from the project")
        self.record = record
        self.sheets = sheets
        self._cache: dict[tuple, np.ndarray] = {}

    @property
    def source_start(self) -> float:
        return float(self.record["source_start"])

    def picture(self, t: float, width: int, height: int) -> np.ndarray:
        shot = shot_at(self.record, t)
        sheet = self.sheets[shot["sheet"]]
        key = (shot["sheet"], pose_at(shot, t), bool(shot.get("close")), width, height, shot.get("fit"))
        if key not in self._cache:
            if len(self._cache) > 48:
                self._cache.clear()
            rect = tile(sheet.shape[1], sheet.shape[0], key[1], key[2])
            if shot.get("fit") == "contain":
                # Generated sheets need not have 16:9 cells. Preserve the puppet's
                # proportions instead of stretching its head across the frame.
                scale = min(width / rect[2], height / rect[3])
                w, h = max(1, round(rect[2] * scale)), max(1, round(rect[3] * scale))
                picture = np.full((height, width, 3), (23, 32, 25), dtype=np.uint8)
                x, y = (width - w) // 2, (height - h) // 2
                picture[y:y+h, x:x+w] = crop_to(sheet, rect, w, h)
                self._cache[key] = picture
            else:
                self._cache[key] = crop_to(sheet, rect, width, height)
        return self._cache[key]


# ---- Placements -------------------------------------------------------------------------

def default_placements(story: dict[str, Any]) -> list[dict[str, Any]]:
    """A first proposal that uses every mode once, following the story's shots: the
    dream in full, the neighbours in a thought bubble, the question to camera back
    on the host, the city in a small window, a brief blend into the village, the
    village in full. Every value is editable."""
    shots = story["shots"]

    def placement(index: int, mode: str, start: float, end: float, **extra: Any) -> dict[str, Any]:
        return {"id": f"clay-{index}", "story": story["id"], "mode": mode, "start": round(start, 3), "end": round(end, 3),
                **DEFAULTS[mode], "fade": 0.3, "enabled": True, **extra}
    if len(shots) < 4:
        return [placement(1, "full", 0.0, float(story["duration"]))]
    question = next((key["time"] for key in shots[2].get("poseKeys") or [] if key["time"] > shots[2]["start"]), shots[2]["start"])
    village = shots[3]["start"]
    return [
        placement(1, "full", shots[0]["start"], shots[0]["end"]),
        placement(2, "bubble", shots[1]["start"], shots[1]["end"]),
        placement(3, "pip", question, shots[2]["end"]),
        placement(4, "blend", village, village + 1.0, fade=0.4),
        placement(5, "full", village + 1.0, shots[3]["end"]),
    ]


def check_placement(raw: dict[str, Any], story: dict[str, Any]) -> dict[str, Any]:
    if raw.get("mode") not in MODES:
        raise ValueError("Choose full, pip, bubble or blend")
    out = {**DEFAULTS[raw["mode"]], **raw}
    for key in ("start", "end", "x", "y", "size", "opacity", "fade"):
        out[key] = float(out.get(key, 0.3 if key == "fade" else 0))
        if not math.isfinite(out[key]):
            raise ValueError(f"{key} must be a number")
    out["start"] = max(0.0, out["start"])
    out["end"] = min(float(story["duration"]), out["end"])
    if out["end"] - out["start"] < 0.1:
        raise ValueError("A clay placement lasts at least 0.1 s inside its story")
    out["x"], out["y"] = min(1.0, max(0.0, out["x"])), min(1.0, max(0.0, out["y"]))
    out["size"] = min(1.0, max(0.1, out["size"]))
    out["opacity"] = min(1.0, max(0.0, out["opacity"]))
    out["fade"] = min(2.0, max(0.0, out["fade"]))
    out["enabled"] = bool(out.get("enabled", True))
    out["avoidFace"] = bool(out.get("avoidFace", True))
    out["id"] = str(out.get("id") or f"clay-{int(time.time() * 1000)}")
    out["story"] = story["id"]
    return out


# ---- Storage ----------------------------------------------------------------------------

class ClayStore:
    """show_studio/clay.json: characters, stories (with their sheets in the library) and placements."""

    def __init__(self, project_folder: Path):
        from core.show_studio_store import StudioStore
        self.studio = StudioStore(Path(project_folder))
        self.path = self.studio.base / STORE

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": 1, "revision": 0, "characters": [], "stories": [], "placements": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any], *, revision: int) -> int:
        from core.show_studio_store import StaleRevision
        from core.project import atomic_write_json
        current = self.load()
        if int(current.get("revision") or 0) != int(revision):
            raise StaleRevision("The clay placements were changed elsewhere; reload them first")
        if self.path.is_file():
            history = self.studio.base / "history"
            history.mkdir(parents=True, exist_ok=True)
            (history / f"clay-r{current.get('revision') or 0}-{time.time_ns()}.json").write_bytes(self.path.read_bytes())
        data = {**data, "version": 1, "revision": int(revision) + 1}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, data)
        return data["revision"]

    def story(self, story_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        entry = next((item for item in self.load()["stories"] if item["id"] == story_id), None)
        if entry is None:
            raise ValueError(f"The clay story {story_id} is not in this project")
        return entry, entry["story"]

    def load_story(self, story_id: str) -> ClayStory:
        from PIL import Image
        entry, record = self.story(story_id)
        sheets = {}
        for name, asset in (entry.get("sheets") or {}).items():
            try:
                with Image.open(self.studio.asset_path(asset)) as image:
                    sheets[name] = np.asarray(image.convert("RGB"))
            except (ValueError, OSError):
                continue                                                     # reported as a missing sheet
        return ClayStory(record, sheets)

    def import_story(self, story_path: Path, character_path: Path | None = None, *, placements: bool = True) -> dict[str, Any]:
        """Copy a clay story, its pose sheets and the character reference into the
        project (sheets and reference into the library). The originals stay where they are."""
        story_path = Path(story_path)
        record = json.loads(story_path.read_text(encoding="utf-8"))
        validate_story(record)
        library = self.studio.library
        sheets = {}
        for name in sorted({shot["sheet"] for shot in record["shots"]}):
            with (story_path.parent / name).open("rb") as stream:
                sheets[name] = library.import_stream(stream, name, origin="clay story")["id"]
        characters = []
        if character_path:
            character = json.loads(Path(character_path).read_text(encoding="utf-8"))
            if character.get("id") != record.get("character_id") or int(character.get("version") or 0) != int(record.get("character_version") or 0):
                raise ValueError("The character record is not the version this story uses")
            with (Path(character_path).parent / character["reference"]).open("rb") as stream:
                reference = library.import_stream(stream, character["reference"], origin="clay character")["id"]
            characters.append({"id": character["id"], "version": character["version"], "record": character, "reference": reference})
        data = self.load()
        revision = int(data.get("revision") or 0)
        data["stories"] = [item for item in data["stories"] if item["id"] != record["id"]] + [
            {"id": record["id"], "story": record, "sheets": sheets, "file": story_path.name}]
        for character in characters:
            data["characters"] = [item for item in data["characters"]
                                  if (item["id"], item["version"]) != (character["id"], character["version"])] + [character]
        if placements and not any(item["story"] == record["id"] for item in data["placements"]):
            data["placements"] += default_placements(record)
        self.save(data, revision=revision)
        return self.load()


# ---- Drawing ------------------------------------------------------------------------------

def _fade(placement: dict[str, Any], t: float) -> float:
    fade = float(placement.get("fade") or 0)
    if fade <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - placement["start"]) / fade, (placement["end"] - t) / fade))


def _over(frame: np.ndarray, picture: np.ndarray, alpha: np.ndarray | float, x0: int, y0: int) -> None:
    """``picture`` (with per-pixel or uniform alpha) over the frame at (x0, y0), clipped."""
    height, width = frame.shape[:2]
    ph, pw = picture.shape[:2]
    fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(width, x0 + pw), min(height, y0 + ph)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    part = picture[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0].astype(np.float32)
    a = alpha[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0, None] if isinstance(alpha, np.ndarray) else alpha
    target = frame[fy0:fy1, fx0:fx1]
    target[...] = np.clip(target * (1 - a) + part * a + 0.5, 0, 255).astype(np.uint8)


def _wobbly(cx: float, cy: float, rx: float, ry: float, seed: int, points: int = 72) -> list[tuple[float, float]]:
    """A hand-drawn ellipse: the radius breathes a little, the same every frame."""
    from core.show_bridge_draw import js_hash
    out = []
    for i in range(points + 1):
        a = i / points * 2 * math.pi
        wobble = 1 + 0.025 * math.sin(a * 3 + js_hash(seed) * 6.3) + 0.015 * math.sin(a * 7 + js_hash(seed + 1) * 6.3)
        out.append((cx + math.cos(a) * rx * wobble, cy + math.sin(a) * ry * wobble))
    return out


def _bubble(frame: np.ndarray, picture: np.ndarray, placement: dict[str, Any], head: tuple[float, float] | None, alpha: float) -> None:
    import cv2

    from core import show_bridge_draw as draw
    height, width = frame.shape[:2]
    bw, bh = picture.shape[1], picture.shape[0]
    cx, cy = placement["x"] * width, placement["y"] * height
    unit = width / 1280
    x0, y0 = int(round(cx - bw / 2)), int(round(cy - bh / 2))
    # The cloud: an oval with scalloped puffs around it, paper inside, the clay in the oval.
    pad = int(0.22 * bh)
    canvas_w, canvas_h = bw + 2 * pad, bh + 2 * pad
    cloud = np.zeros((canvas_h, canvas_w), np.uint8)
    rx, ry = bw * 0.56, bh * 0.6
    ox, oy = canvas_w / 2, canvas_h / 2
    cv2.ellipse(cloud, (int(ox), int(oy)), (int(rx), int(ry)), 0, 0, 360, 255, -1, cv2.LINE_AA)
    puffs = 12
    for i in range(puffs):
        a = i / puffs * 2 * math.pi + 0.2
        r = bh * (0.15 + 0.04 * draw.js_hash(i + 31))
        cv2.circle(cloud, (int(ox + math.cos(a) * rx * 0.98), int(oy + math.sin(a) * ry * 0.98)), int(r), 255, -1, cv2.LINE_AA)
    window = np.zeros_like(cloud)
    cv2.ellipse(window, (int(ox), int(oy)), (int(bw * 0.5), int(bh * 0.5)), 0, 0, 360, 255, -1, cv2.LINE_AA)
    paper = np.empty((canvas_h, canvas_w, 3), np.uint8)
    paper[...] = [int(PAPER[i:i + 2], 16) for i in (1, 3, 5)]
    paper[pad:pad + bh, pad:pad + bw] = np.where(window[pad:pad + bh, pad:pad + bw, None] > 0, picture, paper[pad:pad + bh, pad:pad + bw])
    soft = cv2.GaussianBlur(window, (0, 0), 1.2).astype(np.float32) / 255
    paper[pad:pad + bh, pad:pad + bw] = (picture * soft[pad:pad + bh, pad:pad + bw, None]
                                         + paper[pad:pad + bh, pad:pad + bw] * (1 - soft[pad:pad + bh, pad:pad + bw, None])).astype(np.uint8)
    _over(frame, paper, cloud.astype(np.float32) / 255 * alpha, x0 - pad, y0 - pad)
    # Its outline in crayon, and the clay window's rim.
    contours, _ = cv2.findContours(cloud, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    origin = (-(x0 - pad), -(y0 - pad), 1.0)
    for contour in contours:
        path = [(float(p[0][0]), float(p[0][1])) for p in contour[::3]] + [(float(contour[0][0][0]), float(contour[0][0][1]))]
        draw.stroke(frame, path, origin, brush="crayon", color=INK, width=4.5 * unit, opacity=alpha)
    draw.stroke(frame, _wobbly(cx, cy, bw * 0.5, bh * 0.5, 7), (0.0, 0.0, 1.0), brush="pencil", color=INK, width=2.5 * unit, opacity=0.6 * alpha)
    # Little circles from the cloud towards the head.
    if head is None:
        return
    hx, hy = head[0] * width, head[1] * height
    dx, dy = hx - cx, hy - cy
    distance = math.hypot(dx, dy)
    if distance < 1:
        return
    ux, uy = dx / distance, dy / distance
    edge = 1 / math.sqrt((ux / rx) ** 2 + (uy / ry) ** 2) + 0.16 * bh         # past the oval and its puffs
    stop = max(edge + 0.3 * bh, distance - 0.1 * height)                        # short of the head
    for step, (share, radius) in enumerate(((0.12, 0.085), (0.5, 0.06), (0.88, 0.04))):
        along = edge + (stop - edge) * share
        px, py, r = cx + ux * along, cy + uy * along, radius * bh
        if along + r > distance - 0.05 * height:
            break                                                               # never on the face itself
        spot = np.zeros((int(2 * r) + 6, int(2 * r) + 6), np.uint8)
        cv2.circle(spot, (int(r) + 3, int(r) + 3), int(r), 255, -1, cv2.LINE_AA)
        disc = np.empty(spot.shape + (3,), np.uint8)
        disc[...] = [int(PAPER[i:i + 2], 16) for i in (1, 3, 5)]
        _over(frame, disc, spot.astype(np.float32) / 255 * alpha, int(px - r) - 3, int(py - r) - 3)
        draw.stroke(frame, _wobbly(px, py, r, r, 11 + step, 36), (0.0, 0.0, 1.0), brush="crayon", color=INK, width=3.5 * unit, opacity=alpha)


def clear_of_face(placement: dict[str, Any], head: tuple[float, float] | None, zoom: float, width: int, height: int,
                  bw: float, bh: float, pad: float) -> dict[str, Any]:
    """The placement moved sideways (or up) just enough not to cover the host's face,
    when it would; it stays where it was put otherwise."""
    if head is None or placement.get("avoidFace") is False:
        return placement
    half = 0.085 * width * zoom                                                 # the face (and cap) on this framing
    fx, fy = head[0] * width, head[1] * height
    face = (fx - half, fy - 1.5 * half, fx + half, fy + 1.4 * half)
    cx, cy = placement["x"] * width, placement["y"] * height
    box = (cx - bw / 2 - pad, cy - bh / 2 - pad, cx + bw / 2 + pad, cy + bh / 2 + pad)
    if box[2] <= face[0] or box[0] >= face[2] or box[3] <= face[1] or box[1] >= face[3]:
        return placement
    margin = 0.01 * width
    options = []
    left = face[0] - (bw / 2 + pad)                                             # its right edge at the face's left
    if left - bw / 2 - pad >= margin:
        options.append((abs(cx - left), left, cy))
    right = face[2] + (bw / 2 + pad)
    if right + bw / 2 + pad <= width - margin:
        options.append((abs(right - cx), right, cy))
    up = face[1] - (bh / 2 + pad)
    if up - bh / 2 - pad >= 0:
        options.append((abs(cy - up), cx, up))
    if not options:
        return placement
    _, x, y = min(options)
    return {**placement, "x": x / width, "y": y / height}


def draw_placement(frame: np.ndarray, story: ClayStory, placement: dict[str, Any], t: float,
                   head: tuple[float, float] | None = None, zoom: float = 1.0) -> None:
    """One placement at story time ``t`` on an RGB output frame; ``head`` is where the
    host's face is on it (shares) and ``zoom`` how close the framing is."""
    from core import show_bridge_draw as draw
    height, width = frame.shape[:2]
    alpha = float(placement.get("opacity", 1.0)) * _fade(placement, t)
    if alpha <= 0:
        return
    mode = placement["mode"]
    if mode in ("full", "blend"):
        _over(frame, story.picture(t, width, height), alpha, 0, 0)
        return
    bw = max(16, int(round(placement["size"] * width)))
    bh = max(9, int(round(bw * 9 / 16)))
    picture = story.picture(t, bw, bh)
    if mode == "bubble":
        _bubble(frame, picture, clear_of_face(placement, head, zoom, width, height, bw * 1.12, bh * 1.2, 0.2 * bh), head, alpha)
        return
    placement = clear_of_face(placement, head, zoom, width, height, bw, bh, 7 * width / 1280)
    # pip: the clay picture on a paper mount, outlined in crayon.
    unit = width / 1280
    border = max(2, int(round(7 * unit)))
    mount = np.empty((bh + 2 * border, bw + 2 * border, 3), np.uint8)
    mount[...] = [int(PAPER[i:i + 2], 16) for i in (1, 3, 5)]
    mount[border:border + bh, border:border + bw] = picture
    x0, y0 = int(round(placement["x"] * width - mount.shape[1] / 2)), int(round(placement["y"] * height - mount.shape[0] / 2))
    _over(frame, mount, alpha, x0, y0)
    corners = [(x0, y0), (x0 + mount.shape[1], y0), (x0 + mount.shape[1], y0 + mount.shape[0]), (x0, y0 + mount.shape[0]), (x0, y0)]
    path = draw.resample([(float(x), float(y)) for x, y in corners], 6 * unit)
    draw.stroke(frame, path, (0.0, 0.0, 1.0), brush="crayon", color=INK, width=4.5 * unit, opacity=alpha)


def clay_layers(project_folder: str | Path, plan: dict[str, Any], *, hidden: list[tuple[float, float]] | None = None):
    """The project's clay placements as bridge layers, and one result per placement."""
    from core.show_bridge import Layer, _result, source_pieces
    from core.show_framing import face_on_screen
    store_path = Path(project_folder) / "show_studio" / STORE
    if not store_path.is_file():
        return [], []
    store = ClayStore(Path(project_folder))
    data = store.load()
    layers, report = [], []
    stories: dict[str, ClayStory | str] = {}
    source_name = Path(str(plan.get("source") or "")).name
    for raw in data.get("placements") or []:
        mode = raw.get("mode", "?")
        event = {"id": raw.get("id", "?"), "type": "clay", "name": f"Clay · {mode} · {raw.get('name') or raw.get('story', '')}",
                 "start": raw.get("start"), "end": raw.get("end")}
        if raw.get("enabled") is False:
            report.append(_result(event, "clay", "disabled", "Switched off"))
            continue
        story_id = str(raw.get("story") or "")
        if story_id not in stories:
            try:
                stories[story_id] = store.load_story(story_id)
            except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
                stories[story_id] = str(exc)
        story = stories[story_id]
        if isinstance(story, str):
            report.append(_result(event, "clay", "failed", story))
            continue
        recorded = str(story.record.get("source") or "")
        if recorded and source_name and recorded != source_name:
            report.append(_result(event, "clay", "failed", f"This story was made for {recorded}, not {source_name}"))
            continue
        try:
            placement = check_placement(raw, story.record)
        except (ValueError, TypeError) as exc:
            report.append(_result(event, "clay", "failed", str(exc)))
            continue
        a, b = story.source_start + placement["start"], story.source_start + placement["end"]
        pieces = source_pieces(plan, a, b)
        if not pieces:
            report.append(_result(event, "clay", "failed", "Its moment was cut from the automatic edit"))
            continue

        def drawer(frame, at, view, t=None, story=story, placement=placement):
            head = face_on_screen(plan, t) if placement["mode"] in ("bubble", "pip") and t is not None else None
            draw_placement(frame, story, placement, at, head, view[2] * STUDIO_W / frame.shape[1])
        layers.append(Layer(placement["id"], "clay", [(x, y, sa - story.source_start) for x, y, sa, _ in pieces], placement,
                            drawer, list(hidden or []), [], "clay"))
        kept = sum(y - x for x, y, _, _ in pieces)
        report.append(_result(event, "clay", "applied",
                              f"{mode}, {len(pieces)} kept part{'s' if len(pieces) != 1 else ''}, {kept:.2f} of {b - a:.2f} s; "
                              "poses held between word cues; the host's voice and captions stay"))
    return layers, report


def place_bundled_sample(project, plan: dict, at: float, end: float, mode: str, root: Path) -> dict:
    """Explicit reuse of Simón's sample; align to one kept source shot, never old timestamps."""
    import tempfile
    import shutil
    import uuid
    from core.show_bridge import edited_to_source, _shots
    if mode not in MODES or not all(map(math.isfinite, (at, end))) or end <= at:
        raise ValueError('Choose a positive word interval and a clay display mode')
    source = edited_to_source(plan, at)
    if source is None:
        raise ValueError('Choose words inside the current edit')
    fps = float(plan.get('fps') or 30)
    remaining = next((length / fps - (at - first / fps) for _, first, length in _shots(plan)
                      if first / fps <= at < (first + length) / fps), 0)
    duration = min(end - at, remaining)
    if duration < .04:
        raise ValueError('Choose at least one frame inside a source shot')
    sample = root / 'assets' / 'clay' / 'simon'
    record = json.loads((sample / 'story-simon-v1.json').read_text())
    factor = duration / float(record['duration'])
    record.update(id='simon-sample-' + uuid.uuid4().hex, source=Path(plan['source']).name, source_start=source,
                  source_end=source + duration, duration=duration, quote='Reusable sample placed on selected words',
                  evidence='User chose the bundled countryside sample; motion cues scaled, not new semantic alignment')
    for shot in record['shots']:
        shot['start'] *= factor
        shot['end'] *= factor
        for key in shot.get('poseKeys', []):
            key['time'] *= factor
    store = ClayStore(project.folder)
    with tempfile.TemporaryDirectory(prefix='zucker-clay-') as temporary:
        temp = Path(temporary)
        for name in {shot['sheet'] for shot in record['shots']}:
            shutil.copyfile(sample / name, temp / name)
        (temp / 'story.json').write_text(json.dumps(record))
        data = store.import_story(temp / 'story.json', placements=False)
    placement = check_placement(dict(id=record['id'], story=record['id'], mode=mode, start=0, end=duration,
                                     enabled=True, fade=min(.2, duration / 4), **DEFAULTS[mode]), record)
    store.save({**data, 'placements': [*data['placements'], placement]}, revision=data['revision'])
    return store.load()
