"""Clean cuts when the talking video is already an edit.

A talking video can be an earlier export with its own cuts and zooms burned in. Cutting it
again, an old cut that falls a few frames from a new one leaves a flash: one to a few frames
of another shot (another zoom, another moment) right before or after the new cut. The old
cuts are found once (a cheap low-resolution pass, kept in the project's cache) and those few
frames are replaced by holding the neighbouring frame of the right shot; the timing and the
sound do not move, so nothing placed on the timeline shifts.

Effects that start or end one to a few frames from a cut (a close-up, a clip) are put exactly
on the cut, so the main video does not show through for a frame.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

NEAR = 6          # frames: an old cut this close to a new cut would flash
SNAP = 4          # frames: an effect this close to a cut is put on it


def detect(source: str | Path, cache: Path, ffmpeg: str = "ffmpeg", fps: float = 30) -> list[int]:
    """Frame numbers (at ``fps``) where the video itself cuts (a new shot starts), cached by file."""
    import hashlib
    source = Path(source)
    try:
        stat = source.stat()
    except OSError:
        return []
    cache.mkdir(parents=True, exist_ok=True)
    stamp = hashlib.sha1(json.dumps([str(source.resolve()), stat.st_size, stat.st_mtime_ns, fps]).encode()).hexdigest()[:16]
    key = cache / f"source-cuts-{stamp}.json"
    try:
        return [int(frame) for frame in json.loads(key.read_text())["cuts"]]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    width, height = 64, 36
    process = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(source), "-vf", f"fps={fps},scale={width}:{height}",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=900)
    if process.returncode or not process.stdout:
        return []
    frames = np.frombuffer(process.stdout, np.uint8)
    frames = frames[: len(frames) // (width * height) * width * height].reshape(-1, height, width).astype(np.float32)
    if len(frames) < 3:
        return []
    change = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    # A cut changes the whole picture at once, far more than motion does.
    cuts = [int(index) + 1 for index in np.nonzero(change > max(12.0, float(np.median(change)) * 6))[0]]
    key.write_text(json.dumps({"cuts": cuts}))
    return cuts


def holds(plan: dict[str, Any], cuts: list[int], near: int = NEAR) -> list[tuple[int, int, int]]:
    """(first, last, shown) output frames of ``plan``'s cut: frames first..last come from the
    shot the new cut leaves (or the one it goes to) and show frame ``shown`` instead."""
    if not cuts:
        return []
    marks = np.asarray(sorted(set(cuts)))
    out: list[tuple[int, int, int]] = []
    position = 0
    for shot in plan.get("shots") or []:
        start, end = int(shot["start_frame"]), int(shot["end_frame"])
        length = end - start
        if length > 2 * near + 2:
            inside = marks[(marks > start) & (marks < end)]
            head = [c for c in inside if c - start <= near]
            tail = [c for c in inside if end - c <= near]
            if head:                                   # the shot opens on a few frames of the old shot before
                first_clean = position + int(max(head)) - start
                out.append((position, first_clean - 1, first_clean))
            if tail:                                   # it closes on a few frames of the old shot after
                last_clean = position + int(min(tail)) - start - 1
                out.append((last_clean + 1, position + length - 1, last_clean))
        position += length
    return out


def plan_cuts(plan: dict[str, Any]) -> list[float]:
    """Edited-time seconds where the plan cuts from one shot to the next."""
    fps = float(plan.get("fps") or 30)
    edges, position = [], 0
    for shot in plan.get("shots") or []:
        edges.append(position / fps)
        position += int(shot["end_frame"]) - int(shot["start_frame"])
    edges.append(position / fps)
    return edges


def snap(entries: list[dict[str, Any]], edges: list[float], fps: float, frames: int = SNAP) -> list[dict[str, Any]]:
    """Close-ups and inserts that start or end a few frames from a cut, put exactly on it."""
    if not edges:
        return entries
    reach = frames / fps
    marks = np.asarray(edges)
    out = []
    for entry in entries:
        if not (entry.get("source") or entry.get("insert")) or entry.get("duration") is None:
            out.append(entry)
            continue
        at, duration = float(entry["at"]), float(entry["duration"])
        end = at + duration
        nearest = marks[np.argmin(np.abs(marks - at))]
        if 0 < abs(nearest - at) <= reach:
            at = float(nearest)
        if not entry.get("insert") and duration > 0:     # an insert keeps its own length (it plays whole)
            nearest = marks[np.argmin(np.abs(marks - end))]
            if 0 < abs(nearest - end) <= reach:
                end = float(nearest)
            duration = max(1 / fps, end - at)
        out.append({**entry, "at": round(at, 4), "duration": round(duration, 4)})
    return out
