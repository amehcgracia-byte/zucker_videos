"""Where each Show shot looks: the host's face, found in the source video.

The whole picture is reframed (crop and scale of the full frame), never a
floating piece of it: a wide shot shows everything, a medium or close shot
is centred on the face with some headroom. Faces are found twice a second
with OpenCV's frontal face detector; a shot uses the middle face position of
its own seconds, or the nearest one when the host looks away.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

SAMPLES_PER_SEC = 2
WIDTH = 480
EYE_LINE = 0.38       # where the face sits in a reframed shot, from the top
NEAREST_SEC = 4.0     # how far to look for a face when a shot has none
DEFAULT_FOCUS = (0.5, 0.42)


def face_track(source: str | Path, ffmpeg: str = "ffmpeg", *, cancel: Callable[[], None] | None = None) -> list[tuple[float, float, float, float]]:
    """(seconds, centre x, centre y, width) of the largest face, as shares of the frame."""
    import tempfile

    import cv2
    folder = Path(cv2.data.haarcascades)
    detectors = [(cv2.CascadeClassifier(str(folder / "haarcascade_frontalface_default.xml")), 4),
                 (cv2.CascadeClassifier(str(folder / "haarcascade_frontalface_alt2.xml")), 3)]
    # A dim room, a cap and glasses: local contrast first (measured on a real
    # talk: 6 % of samples with a face without it, 73 % with it).
    clahe = cv2.createCLAHE(2.0, (8, 8))
    height = _probe_height(source, ffmpeg)
    frame_bytes = WIDTH * height
    # Keyframes only (no full decode), at most SAMPLES_PER_SEC of them.
    gap = 0.95 / SAMPLES_PER_SEC
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([ffmpeg, "-v", "info", "-nostdin", "-skip_frame", "nokey", "-i", str(source),
                                    "-vf", f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{gap:.3f})',scale={WIDTH}:{height},showinfo",
                                    "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                   stdout=subprocess.PIPE, stderr=log)
        found: list[tuple[int, float, float, float]] = []
        try:
            assert process.stdout is not None
            index = 0
            while True:
                raw = process.stdout.read(frame_bytes)
                if len(raw) < frame_bytes:
                    break
                if cancel and index % 20 == 0:
                    cancel()
                frame = clahe.apply(np.frombuffer(raw, dtype=np.uint8).reshape(height, WIDTH))
                faces = [face for detector, neighbours in detectors for face in detector.detectMultiScale(frame, 1.1, neighbours, minSize=(20, 20))]
                if faces:
                    x, y, w, h = max(faces, key=lambda face: face[2] * face[3])
                    found.append((index, (x + w / 2) / WIDTH, (y + h / 2) / height, w / WIDTH))
                index += 1
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        log.seek(0)
        times = [float(value) for value in re.findall(rb"pts_time:([0-9.]+)", log.read())]
    return steady([(times[i] if i < len(times) else i / SAMPLES_PER_SEC, x, y, w) for i, x, y, w in found])


def steady(track: list[tuple[float, float, float, float]], *, window: float = 8.0) -> list[tuple[float, float, float, float]]:
    """Only the host: faces much smaller than the typical one (faces in photos
    on the wall) or far from where the host is in the nearby seconds go."""
    if len(track) < 3:
        return track
    widths = sorted(w for *_, w in track)
    typical = widths[len(widths) // 2]
    big = [item for item in track if item[3] >= 0.6 * typical]
    kept = []
    for t, x, y, w in big:
        near = sorted(nx for nt, nx, _, _ in big if abs(nt - t) <= window)
        if abs(x - near[len(near) // 2]) <= 0.15:
            kept.append((t, x, y, w))
    return kept


def _probe_height(source: str | Path, ffmpeg: str) -> int:
    from core.ffmpeg import ffprobe
    stream = next(s for s in ffprobe(str(source)).get("streams", []) if s.get("codec_type") == "video")
    width, height = int(stream["width"]), int(stream["height"])
    return int(round(height * WIDTH / width / 2)) * 2


def focus_for(start: float, end: float, track: list[tuple[float, float, float, float]]) -> tuple[float, float] | None:
    """Face centre during [start, end): the median inside, else the nearest sample."""
    inside = [(x, y) for t, x, y, _ in track if start <= t < end]
    if not inside:
        near = [(abs(t - (start + end) / 2), x, y) for t, x, y, _ in track]
        near = [item for item in near if item[0] <= NEAREST_SEC]
        if not near:
            return None
        _, x, y = min(near)
        return x, y
    xs, ys = sorted(x for x, _ in inside), sorted(y for _, y in inside)
    return xs[len(xs) // 2], ys[len(ys) // 2]


def frame_centre(face: tuple[float, float] | None, zoom: float) -> tuple[float, float]:
    """Centre of the crop window (shares) that frames ``face`` at ``zoom``."""
    if face is None or zoom <= 1.0:
        return DEFAULT_FOCUS
    fx, fy = face
    window = 1.0 / zoom
    # The face at EYE_LINE of the window: headroom above, body below.
    cy = fy + (0.5 - EYE_LINE) * window
    half = window / 2
    return min(max(fx, half), 1 - half), min(max(cy, half), 1 - half)


def apply_focus(plan: dict[str, Any], track: list[tuple[float, float, float, float]]) -> dict[str, Any]:
    """Give every shot of a Show plan its framing centre (and the track's provenance)."""
    fps = int(plan.get("fps") or 30)
    found = 0
    for shot in plan["shots"]:
        face = focus_for(shot["start_frame"] / fps, shot["end_frame"] / fps, track)
        found += face is not None
        zoom = max(float(shot.get("zoom") or 1.0), float(shot.get("zoom_end") or 0.0))
        shot["focus"] = [round(v, 4) for v in frame_centre(face, zoom)]
        if face is not None:
            shot["face"] = [round(v, 4) for v in face]
    plan["framing"] = {"method": "opencv-haar-frontal", "samples_per_sec": SAMPLES_PER_SEC, "faces": len(track),
                       "shots_with_face": found, "shots": len(plan["shots"])}
    return plan


def shot_windows(plan: dict[str, Any], start: float, end: float) -> list[tuple[float, float, float, float, float]]:
    """(from, to, x0, y0, window) for each shot between ``start`` and ``end`` (edited
    seconds): the part of the source picture each shot shows, at its middle."""
    fps = int(plan.get("fps") or 30)
    out, offset = [], 0.0
    for shot in plan["shots"]:
        length = (shot["end_frame"] - shot["start_frame"]) / fps
        a, b = max(start, offset), min(end, offset + length)
        if b > a:
            zoom = float(shot.get("zoom") or 1.0)
            zoom = (zoom + float(shot.get("zoom_end") or zoom)) / 2
            cx, cy = shot.get("focus") or DEFAULT_FOCUS
            window = 1.0 / zoom
            out.append((round(a, 3), round(b, 3), min(max(cx - window / 2, 0.0), 1.0 - window),
                        min(max(cy - window / 2, 0.0), 1.0 - window), window))
        offset += length
    return out


MUG_SHOWN = 0.6    # share of the mug a shot must show for its steam to appear


def _shown(rect: list[float], x0: float, y0: float, window: float) -> float:
    """Share of ``rect`` (x, y, w, h on the source picture) inside a shot's window."""
    x, y, w, h = rect
    seen_w = max(0.0, min(x + w, x0 + window) - max(x, x0))
    seen_h = max(0.0, min(y + h, y0 + window) - max(y, y0))
    return seen_w * seen_h / max(1e-9, w * h)


def steam_on_screen(plan: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    """A steam item placed on the source picture, as one item per shot in screen
    coordinates (each shot frames the picture differently)."""
    pieces = []
    for a, b, x0, y0, window in shot_windows(plan, float(item["at"]), float(item["at"]) + float(item["duration"])):
        x, y = item["steam"]
        sx, sy = (x - x0) / window, (y - y0) / window
        if not (0.0 <= sx <= 1.0 and 0.0 <= sy <= 1.0):
            continue                                  # this shot does not show the mug
        if item.get("anchor") and _shown(item["anchor"], x0, y0, window) < MUG_SHOWN:
            continue                                  # only its rim at the edge: steam over nothing
        piece = {**item, "at": a, "duration": round(b - a, 3), "steam": [round(sx, 4), round(sy, 4)],
                 "scale": round(float(item.get("scale") or 1.0) / window, 3)}
        if item.get("anchor"):
            ax, ay, aw, ah = item["anchor"]
            piece["anchor"] = [round((ax - x0) / window, 4), round((ay - y0) / window, 4), round(aw / window, 4), round(ah / window, 4)]
        pieces.append(piece)
    return pieces


def face_on_screen(plan: dict[str, Any], at: float) -> tuple[float, float] | None:
    """Where the host's face is in the edited picture at ``at`` (shares), or None."""
    fps = int(plan.get("fps") or 30)
    offset = 0.0
    for shot in plan["shots"]:
        length = (shot["end_frame"] - shot["start_frame"]) / fps
        if at < offset + length:
            if not shot.get("face"):
                return None
            share = (at - offset) / max(1e-6, length)
            zoom = float(shot.get("zoom") or 1.0)
            zoom += (float(shot.get("zoom_end") or zoom) - zoom) * share
            cx, cy = shot.get("focus") or DEFAULT_FOCUS
            window = 1.0 / zoom
            x0 = min(max(cx - window / 2, 0.0), 1.0 - window)
            y0 = min(max(cy - window / 2, 0.0), 1.0 - window)
            fx, fy = shot["face"]
            return (fx - x0) / window, (fy - y0) / window
        offset += length
    return None
