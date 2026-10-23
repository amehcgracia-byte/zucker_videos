"""The narrator's track: the talking video as the edit shows it, and the editor's changes to it.

The track lists what the edit did to the talking video: where it cut (a pause, a hesitation, a
repeated take taken out) and how each moment is framed (wide, a zoom on the face, a slow push,
a camera move from the previous framing). The editor changes the framing of any stretch of the
edit (zoom, where it aims, cut or camera move into it, pushes) and moves, extends or removes a
zoom. Framing never changes the timing: a stretch starting inside a shot splits that shot where
it starts and ends (the picture runs on, so nothing is cut there) and every effect stays put.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from core.show_camera import at_frame, end_state, glide, start_state, window
from core.show_framing import DEFAULT_FOCUS, frame_centre

ZOOMS = {"wide": 1.0, "medium": 1.3, "close": 1.65, "closer": 2.0}
UNDO_LIMIT = 40


def _fps(plan: dict[str, Any]) -> int:
    return int(plan.get("fps") or 30)


def _length(shot: dict[str, Any]) -> int:
    return int(shot["end_frame"]) - int(shot["start_frame"])


def _firsts(plan: dict[str, Any]) -> list[int]:
    out, first = [], 0
    for shot in plan["shots"]:
        out.append(first)
        first += _length(shot)
    return out


def _words(plan: dict[str, Any], shot: dict[str, Any]) -> str:
    words = {int(word["index"]): word for word in plan.get("words") or [] if "index" in word}
    return " ".join(str(words[i]["text"]).strip() for i in shot.get("words") or [] if i in words)


def track(plan: dict[str, Any], scenes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """What the editor shows: every shot (edited time, framing, camera move), the cuts with
    what they took out, the framing segments (runs of shots framed alike: one zoom each) and
    the scenes."""
    fps = _fps(plan)
    shots, cuts = [], []
    firsts = _firsts(plan)
    for index, shot in enumerate(plan["shots"]):
        first, length = firsts[index], _length(shot)
        move = glide(plan, index)
        zoom = float(shot.get("zoom") or 1.0)
        shots.append({
            "index": index, "first": first, "frames": length,
            "at": round(first / fps, 3), "duration": round(length / fps, 3),
            "source": [round(int(shot["start_frame"]) / fps, 3), round(int(shot["end_frame"]) / fps, 3)],
            "zoom": zoom, "zoom_end": float(shot["zoom_end"]) if shot.get("zoom_end") else None,
            "focus": list(shot.get("focus") or DEFAULT_FOCUS), "face": shot.get("face"),
            "move": shot.get("move") or "cut", "aimed": bool(shot.get("aimed")), "cut_before": shot.get("cut_before"),
            "glide": {"from": [round(v, 5) for v in move[0]], "frames": move[1], "skip": move[2]} if move else None,
            "text": _words(plan, shot),
        })
        if index and int(shot["start_frame"]) != int(plan["shots"][index - 1]["end_frame"]):
            gap = (int(shot["start_frame"]) - int(plan["shots"][index - 1]["end_frame"])) / fps
            cuts.append({"at": round(first / fps, 3), "removed": round(gap, 3), "reason": shot.get("cut_before") or "cut", "shot": index})
    return {"fps": fps, "duration": round(sum(_length(s) for s in plan["shots"]) / fps, 3), "shots": shots, "cuts": cuts,
            "segments": segments(plan), "zooms": ZOOMS, "source": plan.get("source"),
            "scenes": [{"id": scene["id"], "start": scene["start"], "end": scene["end"], "title": scene.get("name") or scene.get("title") or str(scene.get("text") or "")[:60]}
                       for scene in scenes or []]}


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """b goes on with a's zoom (one zoom over both, each shot aimed at its own face)."""
    if a.get("zoom_end") or b.get("zoom_end") or b.get("move") == "glide":
        return False
    return abs(end_state(a)[0] - start_state(b)[0]) < 1e-4


def segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Runs of shots framed alike, in edited seconds: the zooms the editor moves and resizes."""
    fps = _fps(plan)
    out: list[dict[str, Any]] = []
    firsts = _firsts(plan)
    for index, shot in enumerate(plan["shots"]):
        first, length = firsts[index], _length(shot)
        if out and _same(plan["shots"][index - 1], shot):
            out[-1]["end"] = round((first + length) / fps, 3)
            out[-1]["shots"].append(index)
            continue
        zoom = float(shot.get("zoom") or 1.0)
        out.append({"start": round(first / fps, 3), "end": round((first + length) / fps, 3), "zoom": zoom,
                    "zoom_end": float(shot["zoom_end"]) if shot.get("zoom_end") else None,
                    "focus": list(shot.get("focus") or DEFAULT_FOCUS), "face": shot.get("face"),
                    "move": shot.get("move") or "cut", "aimed": bool(shot.get("aimed")), "shots": [index]})
    return out


def _split(plan: dict[str, Any], n: int) -> None:
    """Split the shot holding output frame ``n`` there (nothing happens on a shot edge): two
    shots of the same picture, running on, framed as before."""
    firsts = _firsts(plan)
    for index, shot in enumerate(plan["shots"]):
        first, length = firsts[index], _length(shot)
        if first < n < first + length:
            i = n - first
            fps = _fps(plan)
            z_end = at_frame(plan, index, i - 1)[0] if shot.get("zoom_end") else None
            z_next = at_frame(plan, index, i)[0] if shot.get("zoom_end") else None
            move = glide(plan, index)
            cut = int(shot["start_frame"]) + i
            words = {int(w["index"]): w for w in plan.get("words") or [] if "index" in w}
            before = [w for w in shot.get("words") or [] if w in words and float(words[w]["start"]) < cut / fps]
            after = [w for w in shot.get("words") or [] if w not in before]
            one = {**shot, "end_frame": cut, "end": round(cut / fps, 6), "words": before}
            two = {**shot, "start_frame": cut, "start": round(cut / fps, 6), "words": after, "cut_before": "pace"}
            for key in ("move", "glide_from", "glide_frames", "glide_skip"):
                two.pop(key, None)
            if z_end is not None:
                one["zoom_end"], two["zoom"] = round(z_end, 5), round(z_next, 5)
            if move and move[2] + i < move[1]:
                # Split inside a camera move: the second part goes on with the same move.
                two.update(move="glide", glide_from=[round(v, 5) for v in move[0]], glide_frames=move[1], glide_skip=move[2] + i)
            plan["shots"][index:index + 1] = [one, two]
            return


def _inside(plan: dict[str, Any], a: int, b: int) -> list[int]:
    firsts = _firsts(plan)
    return [index for index, shot in enumerate(plan["shots"]) if firsts[index] >= a and firsts[index] + _length(shot) <= b]


def frame(plan: dict[str, Any], start: float, end: float, *, zoom: float | None = None, zoom_end: float | None = None,
          face: list[float] | None = None, move: str | None = None, aimed: bool | None = None,
          fallback_face: list[float] | None = None) -> dict[str, Any]:
    """Frame the edit between ``start`` and ``end`` (edited seconds), in place: its zoom (with
    ``zoom_end``, a push or pull over the stretch), where it aims (``face``, the point on the
    picture to keep in frame; chosen by hand unless ``aimed`` is False) and how it begins
    (``move``: "cut" or "glide"). A shot with no face of its own aims at ``fallback_face``.
    Returns the plan."""
    fps = _fps(plan)
    total = sum(_length(s) for s in plan["shots"])
    a, b = max(0, int(round(start * fps))), min(total, int(round(end * fps)))
    if b - a < 1:
        raise ValueError("Choose a stretch of at least one frame")
    if move is not None and move not in ("cut", "glide"):
        raise ValueError("A stretch starts with a cut or a camera move")
    _split(plan, a)
    _split(plan, b)
    chosen = _inside(plan, a, b)
    firsts = _firsts(plan)
    top = zoom_end if zoom_end is not None else zoom
    for position, index in enumerate(chosen):
        shot = plan["shots"][index]
        if zoom is not None:
            z0 = float(zoom)
            if zoom_end is not None and abs(zoom_end - zoom) > 1e-4:
                # A push or pull over the whole stretch, shot after shot at one pace.
                share = lambda frame_number: (frame_number - a) / max(1, b - a - 1)
                first, last = firsts[index], firsts[index] + _length(shot) - 1
                shot["zoom"] = round(zoom + (zoom_end - zoom) * share(first), 5)
                shot["zoom_end"] = round(zoom + (zoom_end - zoom) * share(last), 5)
            else:
                shot["zoom"] = round(z0, 5)
                shot.pop("zoom_end", None)
        if face is not None:
            shot["face"] = [round(float(v), 4) for v in face]
        elif fallback_face is not None and not shot.get("face"):
            shot["face"] = [round(float(v), 4) for v in fallback_face]
        if aimed is False:
            shot.pop("aimed", None)
        elif aimed or face is not None:
            shot["aimed"] = True
        reach = max(float(shot.get("zoom") or 1.0), float(shot.get("zoom_end") or 0.0), float(top or 0.0))
        target = face if face is not None else shot.get("face")
        shot["focus"] = [round(v, 4) for v in (frame_centre(tuple(target), reach) if target and reach > 1.0 else DEFAULT_FOCUS)]
        if position == 0 and move is not None:
            if move == "glide":
                shot["move"] = "glide"
            else:
                shot.pop("move", None)
            for key in ("glide_from", "glide_frames", "glide_skip"):
                shot.pop(key, None)
        elif position > 0:
            for key in ("move", "glide_from", "glide_frames", "glide_skip"):
                shot.pop(key, None)                    # inside the stretch: one framing running on
    return plan


def framing_at(plan: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    """The framing a stretch has now (to move or extend it as it is)."""
    fps = _fps(plan)
    a, b = int(round(start * fps)), int(round(end * fps))
    firsts = _firsts(plan)
    hit = [index for index, shot in enumerate(plan["shots"]) if firsts[index] < b and firsts[index] + _length(shot) > a]
    if not hit:
        raise ValueError("Nothing of the edit is there")
    first, last = plan["shots"][hit[0]], plan["shots"][hit[-1]]
    return {"zoom": float(first.get("zoom") or 1.0), "zoom_end": float(last["zoom_end"]) if last.get("zoom_end") else None,
            "face": first.get("face"), "move": first.get("move") or "cut", "aimed": bool(first.get("aimed"))}


def move(plan: dict[str, Any], old: tuple[float, float], new: tuple[float, float]) -> dict[str, Any]:
    """A zoom moved, extended or shortened: the stretch it left goes wide, the new one takes
    its framing."""
    keep = framing_at(plan, *old)
    frame(plan, old[0], old[1], zoom=1.0, move="cut")
    # Aimed by hand: the same point. Otherwise each moment aims at its own face (the person
    # moves), or at the moved zoom's where none was found.
    frame(plan, new[0], new[1], zoom=keep["zoom"], zoom_end=keep["zoom_end"], move=keep["move"],
          face=keep["face"] if keep["aimed"] else None, aimed=keep["aimed"], fallback_face=keep["face"])
    return plan


def all_moves(plan: dict[str, Any], kind: str) -> int:
    """Every change of framing where the picture runs on becomes a camera move ("glide")
    or a cut ("cut"). Returns how many shots glide."""
    if kind not in ("glide", "cut"):
        raise ValueError("Camera moves or cuts")
    for index, shot in enumerate(plan["shots"]):
        if index and shot.get("cut_before") == "pace" and max(abs(x - y) for x, y in zip(end_state(plan["shots"][index - 1]), start_state(shot))) > 1e-4:
            if kind == "glide":
                shot["move"] = "glide"
            else:
                shot["move"] = "cut"
    return sum(1 for shot in plan["shots"] if shot.get("move") == "glide")


def cameras(plan: dict[str, Any]) -> np.ndarray:
    """(frames, 3): zoom, cx, cy of every output frame."""
    rows = []
    for index, shot in enumerate(plan["shots"]):
        rows.extend(at_frame(plan, index, i) for i in range(_length(shot)))
    return np.asarray(rows, np.float64).reshape(-1, 3)


def changed(before: dict[str, Any], after: dict[str, Any]) -> tuple[float, float] | None:
    """The stretch (edited seconds) whose picture differs between two plans, or None."""
    a, b = cameras(before), cameras(after)
    if a.shape != b.shape:
        return 0.0, max(len(a), len(b)) / _fps(after)
    differ = np.nonzero(np.abs(a - b).max(axis=1) > 1e-5)[0]
    if not len(differ):
        return None
    return float(differ[0]) / _fps(after), float(differ[-1] + 1) / _fps(after)


# ---- Undo -----------------------------------------------------------------------------

def remember(stack: Path, plan: dict[str, Any]) -> None:
    """The plan's shots before an edit, for Undo (the newest few)."""
    try:
        data = json.loads(stack.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"undo": [], "redo": []}
    data["undo"] = [*data.get("undo", []), plan["shots"]][-UNDO_LIMIT:]
    data["redo"] = []
    stack.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def step(stack: Path, plan: dict[str, Any], direction: str) -> bool:
    """Undo or redo the last framing edit on ``plan`` (in place); False if there is none."""
    try:
        data = json.loads(stack.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    source, target = ("undo", "redo") if direction == "undo" else ("redo", "undo")
    if not data.get(source):
        return False
    data[target] = [*data.get(target, []), plan["shots"]][-UNDO_LIMIT:]
    plan["shots"] = data[source].pop()
    stack.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return True


# ---- Pictures and sound for the editor ------------------------------------------------

def _source_fps(capture) -> float:
    import cv2
    value = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    return value if 1 <= value <= 240 else 30.0


def source_frame_at(plan: dict[str, Any], n: int) -> tuple[int, int] | None:
    """(shot index, plan frame of the source) shown on output frame ``n``."""
    for index, (first, shot) in enumerate(zip(_firsts(plan), plan["shots"])):
        if n < first + _length(shot):
            return index, int(shot["start_frame"]) + max(0, n - first)
    return None


THUMB = (320, 180)


def filmstrip(plan: dict[str, Any], times: list[float], height: int, cache: Path, *, framed: bool = True) -> bytes:
    """One JPEG of pictures side by side, one for each edited moment in ``times``, framed as
    the edit frames them (``framed``) or whole. Frames read once are kept small in ``cache``."""
    import cv2
    fps = _fps(plan)
    source = Path(str(plan.get("source") or ""))
    stat = source.stat() if source.is_file() else None
    stamp = hashlib.sha1(f"{source}|{stat and stat.st_size}|{stat and stat.st_mtime_ns}".encode()).hexdigest()[:12]
    cache.mkdir(parents=True, exist_ok=True)
    width = max(16, int(round(height * 16 / 9)))
    wanted = []
    for t in times:
        n = int(round(float(t) * fps))
        hit = source_frame_at(plan, n)
        wanted.append((n, hit))
    pictures: dict[int, np.ndarray] = {}
    capture = None
    try:
        for n, hit in sorted(wanted, key=lambda item: item[1][1] if item[1] else -1):
            if hit is None or hit[1] in pictures:
                continue
            path = cache / f"{stamp}-{hit[1]}.jpg"
            picture = cv2.imread(str(path)) if path.is_file() else None
            if picture is None:
                if capture is None:
                    capture = cv2.VideoCapture(str(source))
                capture.set(cv2.CAP_PROP_POS_FRAMES, int(round(hit[1] / fps * _source_fps(capture))))
                ok, bgr = capture.read()
                if not ok:
                    continue
                picture = cv2.resize(bgr, THUMB, interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(path), picture, [cv2.IMWRITE_JPEG_QUALITY, 80])
            pictures[hit[1]] = picture
    finally:
        if capture is not None:
            capture.release()
    tiles = []
    for n, hit in wanted:
        picture = pictures.get(hit[1]) if hit else None
        if picture is None:
            tiles.append(np.full((height, width, 3), 24, np.uint8))
            continue
        if framed:
            from core.show_camera import camera_at
            x0, y0, w = window(*camera_at(plan, n))
            H, W = picture.shape[:2]
            picture = picture[int(y0 * H):max(int(y0 * H) + 2, int((y0 + w) * H)), int(x0 * W):max(int(x0 * W) + 2, int((x0 + w) * W))]
        tiles.append(cv2.resize(picture, (width, height), interpolation=cv2.INTER_AREA))
    strip = np.hstack(tiles) if tiles else np.zeros((height, width, 3), np.uint8)
    ok, data = cv2.imencode(".jpg", strip, [cv2.IMWRITE_JPEG_QUALITY, 78])
    return data.tobytes() if ok else b""


PEAK_RATE = 8000


def peaks(plan: dict[str, Any], ffmpeg: str, cache: Path, buckets: int, *, audio: Path | None = None) -> list[float]:
    """The edited voice's loudness in ``buckets`` steps (0–1), for drawing its waveform."""
    from core.show_render import kept_ranges
    source = Path(str(audio or plan.get("source") or ""))
    stat = source.stat()
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{PEAK_RATE}".encode()).hexdigest()[:16]
    path = cache / f"voice-{key}.npy"
    if path.is_file():
        samples = np.load(path)
    else:
        raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(source), "-vn", "-ac", "1", "-ar", str(PEAK_RATE),
                              "-f", "f32le", "-"], check=True, capture_output=True).stdout
        samples = np.abs(np.frombuffer(raw, np.float32)).astype(np.float16)
        np.save(path, samples)
    fps = _fps(plan)
    joined = np.concatenate([samples[int(a * PEAK_RATE / fps):int(b * PEAK_RATE / fps)] for a, b in kept_ranges(plan)] or [np.zeros(1, np.float16)])
    buckets = max(1, min(int(buckets), 8000))
    edges = np.linspace(0, len(joined), buckets + 1).astype(int)
    loud = np.array([float(joined[a:b].max()) if b > a else 0.0 for a, b in zip(edges, edges[1:])])
    top = float(np.percentile(loud, 99)) or 1.0
    return [round(min(1.0, value / top), 3) for value in loud]
