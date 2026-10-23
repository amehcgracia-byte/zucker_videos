"""Zooms aimed at the face, shot by shot.

The first framing pass read keyframes only and kept faces near the usual place, so in a talk
that is already an edit (its own zooms burned in) most shots had no face and took a far one,
or the middle of the picture: zooms landed on the chest. Here three moments of every shot are
looked at with the Mac's face detector (faces turned aside too; OpenCV's frontal detector
elsewhere) and the shot's zoom is aimed at the face found there. When the person leans or turns
the zoom eases off just enough to keep the face in; a shot with no face, or whose face jumps
further than any zoom can follow (an old cut inside it), is not zoomed rather than zoomed at random.
A shot the person aimed by hand (``aimed``) is left as it is.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np

from core.show_framing import frame_centre

SIZE = (640, 360)
JUMP = 0.09          # the face moving more than this (share of the frame) inside a shot: an old cut


def _vision_faces():
    """A function (RGB frame) -> [(cx, cy, w)] using the Mac's face detector, or None."""
    if sys.platform != "darwin":
        return None
    try:
        import objc
        import Quartz
        bundle: dict[str, Any] = {}
        objc.loadBundle("Vision", bundle, bundle_path="/System/Library/Frameworks/Vision.framework")
        if "VNDetectFaceRectanglesRequest" not in bundle:
            return None
    except Exception:
        return None
    space = Quartz.CGColorSpaceCreateDeviceRGB()

    def detect(rgb: np.ndarray) -> list[tuple[float, float, float]]:
        height, width = rgb.shape[:2]
        data = np.dstack([rgb[..., ::-1], np.full((height, width, 1), 255, np.uint8)]).tobytes()
        provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
        image = Quartz.CGImageCreate(width, height, 8, 32, width * 4, space,
                                     Quartz.kCGBitmapByteOrder32Little | Quartz.kCGImageAlphaPremultipliedFirst,
                                     provider, None, False, Quartz.kCGRenderingIntentDefault)
        handler = bundle["VNImageRequestHandler"].alloc().initWithCGImage_options_(image, {})
        request = bundle["VNDetectFaceRectanglesRequest"].alloc().init()
        done = handler.performRequests_error_([request], None)
        if not (done[0] if isinstance(done, tuple) else done):
            return []
        faces = []
        for face in request.results() or []:
            (x, y), (w, h) = face.boundingBox()
            faces.append((x + w / 2, 1 - (y + h / 2), w))       # Vision counts from the bottom
        return faces
    return detect


def _haar_faces():
    import cv2
    from core.show_framing import face_detectors
    detectors = face_detectors()
    clahe = cv2.createCLAHE(2.0, (8, 8))

    def detect(rgb: np.ndarray) -> list[tuple[float, float, float]]:
        height, width = rgb.shape[:2]
        gray = clahe.apply(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))
        return [((x + w / 2) / width, (y + h / 2) / height, w / width)
                for detector, neighbours in detectors for x, y, w, h in detector.detectMultiScale(gray, 1.1, neighbours, minSize=(20, 20))]
    return detect


def detector():
    return _vision_faces() or _haar_faces()


def shot_faces(frames: list[np.ndarray], detect) -> list[tuple[float, float]] | None:
    """The host's face in each of a shot's frames (the largest one), or None when most of
    them have none."""
    found = []
    for rgb in frames:
        faces = [face for face in detect(rgb) if face[2] >= 0.04]
        if faces:
            x, y, _ = max(faces, key=lambda face: face[2])
            found.append((x, y))
    if not found or len(found) < max(1, len(frames) - 1):
        return None
    return found


def shot_face(frames: list[np.ndarray], detect) -> tuple[float, float] | None:
    """The host's face in a shot from a few of its frames: the largest face in each, their
    middle; None when there is none, or when it jumps (two shots of an old edit inside one)."""
    found = shot_faces(frames, detect)
    if not found:
        return None
    xs, ys = [x for x, _ in found], [y for _, y in found]
    if max(xs) - min(xs) > JUMP or max(ys) - min(ys) > JUMP:
        return None
    return float(np.median(xs)), float(np.median(ys))


ROOM = 0.7         # the face's travel inside a shot may take this share of the zoomed window


def fitted(found: list[tuple[float, float]], zoom: float) -> tuple[tuple[float, float], float] | None:
    """(face to aim at, zoom) keeping every place the face is in the shot inside the picture:
    the person leaning or turning moves it, and the zoom eases off to keep it in; None when
    even a gentle zoom cannot (an old cut inside the shot, to another framing)."""
    xs, ys = [x for x, _ in found], [y for _, y in found]
    spread = max(max(xs) - min(xs), max(ys) - min(ys))
    middle = ((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2)
    if spread <= JUMP:
        return (float(np.median(xs)), float(np.median(ys))), zoom
    fits = min(zoom, ROOM / spread)
    if fits < 1.15:
        return None
    return middle, round(fits * 20) / 20 if fits < zoom else zoom


def reaim(plan: dict[str, Any], source: str | Path, *, progress: Callable[[float], None] | None = None,
          cancel: Callable[[], None] | None = None) -> dict[str, int]:
    """Aim every zoomed shot of ``plan`` at the face in it (in place); returns counts."""
    import cv2
    detect = detector()
    fps = float(plan.get("fps") or 30)
    capture = cv2.VideoCapture(str(source))
    rate = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    rate = rate if 1 <= rate <= 240 else fps            # the plan's frames are on its own grid
    counts = {"aimed": 0, "unzoomed": 0, "kept": 0, "wide": 0, "eased": 0}
    try:
        shots = plan.get("shots") or []
        for index, shot in enumerate(shots):
            if cancel and index % 10 == 0:
                cancel()
            if progress:
                progress(index / max(1, len(shots)))
            zoom = max(float(shot.get("zoom") or 1.0), float(shot.get("zoom_end") or 0.0))
            if shot.get("aimed"):
                counts["kept"] += 1
                continue
            if zoom <= 1.0:
                counts["wide"] += 1
                continue
            start, end = int(shot["start_frame"]), int(shot["end_frame"])
            frames = []
            for share in (0.2, 0.5, 0.8):
                capture.set(cv2.CAP_PROP_POS_FRAMES, round((start + int((end - start) * share)) / fps * rate))
                ok, bgr = capture.read()
                if ok:
                    frames.append(cv2.cvtColor(cv2.resize(bgr, SIZE), cv2.COLOR_BGR2RGB))
            found = shot_faces(frames, detect)
            aim = fitted(found, zoom) if found else None
            if aim is None:
                shot["zoom"] = 1.0                        # no face: the whole picture, not a random close-up
                shot.pop("zoom_end", None)
                shot.pop("face", None)
                shot["focus"] = [0.5, 0.42]
                counts["unzoomed"] += 1
            else:
                face, fits = aim
                if fits < zoom:                           # eased off so the moving face stays in
                    shot["zoom"] = min(float(shot.get("zoom") or 1.0), fits)
                    if shot.get("zoom_end"):
                        shot["zoom_end"] = min(float(shot["zoom_end"]), fits)
                    counts["eased"] += 1
                shot["face"] = [round(v, 4) for v in face]
                shot["focus"] = [round(v, 4) for v in frame_centre(face, min(zoom, fits))]
                counts["aimed"] += 1
    finally:
        capture.release()
    plan.setdefault("framing", {})["reaimed"] = {**counts, "method": "vision" if _vision_faces() else "opencv"}
    return counts
