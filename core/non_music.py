"""Conservative detection and reuse of non-musical Sony cutaways.

This is intentionally a candidate bank, not a claim of semantic certainty:
MobileNet person detections plus frame energy/edge signals identify audience,
ambient and transition material that is safe to review as a cutaway.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.operator_avoidance import _detect_frame, _detector
from core.stages.base import stable_fingerprint

NON_MUSIC_VERSION = 2
SAMPLE_SEC = 1.0


def _candidate_score(frame: Any, blobs: list[dict[str, float]]) -> tuple[float, str]:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    edges = float(cv2.Canny(gray, 60, 140).mean())
    areas = sorted((float(blob.get("area_fraction") or 0.0) for blob in blobs), reverse=True)
    largest = areas[0] if areas else 0.0
    # No large performer/foreground person: likely audience, ambience or a
    # transition. Small distributed detections are also typical of audience.
    if not blobs and brightness >= 16.0 and edges >= 1.5:
        return min(1.0, 0.72 + brightness / 255.0 * 0.18), "ambient/audience (no dominant person)"
    if len(blobs) >= 2 and largest < 0.10 and brightness >= 12.0:
        return 0.76, "audience (distributed small people)"
    if blobs and largest < 0.045 and brightness >= 12.0:
        return 0.68, "ambient (no dominant performer)"
    return 0.0, ""


def detect_non_music_windows(path: str) -> list[dict[str, Any]]:
    import cv2

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return []
    detector = _detector()
    if detector is None:
        capture.release()
        return []
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if frame_count else 0.0
    candidates: list[tuple[float, float, str, bool]] = []
    try:
        for index in range(0, int(duration), int(SAMPLE_SEC)):
            capture.set(cv2.CAP_PROP_POS_MSEC, index * 1000.0)
            ok, frame = capture.read()
            if not ok:
                continue
            blobs = _detect_frame(detector, frame)
            score, reason = _candidate_score(frame, blobs)
            if score >= 0.68:
                largest = max((float(blob.get("area_fraction") or 0.0) for blob in blobs), default=0.0)
                candidates.append((float(index), score, reason, largest >= 0.045))
    finally:
        capture.release()
    windows: list[dict[str, Any]] = []
    for timestamp, score, reason, dominant_person in candidates:
        if windows and timestamp <= float(windows[-1]["end_sec"]) + 1.5:
            windows[-1]["end_sec"] = timestamp + SAMPLE_SEC
            windows[-1]["score"] = round(max(float(windows[-1]["score"]), score), 3)
            windows[-1]["dominant_person"] = bool(windows[-1].get("dominant_person") or dominant_person)
        else:
            windows.append({"start_sec": timestamp, "end_sec": timestamp + SAMPLE_SEC, "score": round(score, 3), "reason": reason, "dominant_person": dominant_person, "instrument_in_use": False})
    return [window for window in windows if float(window["end_sec"]) - float(window["start_sec"]) >= 3.0]


def analyze_non_music_sources(project: Any, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    root = project.cache_dir / "non_music"
    root.mkdir(parents=True, exist_ok=True)
    for source in sources:
        role = str(source.get("role") or "")
        filename = str(source.get("filename") or source.get("path") or "").lower()
        if role not in {"handheld", ""} or "sony" not in filename:
            output.append(source)
            continue
        path = str(source.get("path") or source.get("source_path") or "")
        try:
            stat = Path(path).stat()
            key = stable_fingerprint({"version": NON_MUSIC_VERSION, "path": path, "size": stat.st_size, "mtime": stat.st_mtime})[:24]
        except OSError:
            output.append(source)
            continue
        cache = root / f"{key}.json"
        if cache.exists():
            payload = json.loads(cache.read_text(encoding="utf-8"))
        else:
            payload = {"version": NON_MUSIC_VERSION, "source": path, "windows": detect_non_music_windows(path)}
            cache.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        master = (project.data.get("inputs") or {}).get("master") or {}
        output.append({**source, "non_music_windows": payload.get("windows") or [], "non_music_master_path": master.get("path")})
    return output


@lru_cache(maxsize=8)
def _master_audio_profile(path: str) -> tuple[Any, int]:
    import librosa
    y, sr = librosa.load(path, sr=2000, mono=True)
    return y, int(sr)


def master_audio_is_active(path: str, start_sec: float, duration_sec: float) -> bool:
    """Return whether real master audio is present during a target cutaway.

    This is deliberately conservative: any clearly audible music means a
    single-person candidate without proven instrument use is rejected.  It
    does not pretend to separate stems; it enforces the safe side of the
    image/audio mismatch rule while still allowing audience and ambient shots.
    """
    if not path or duration_sec <= 0:
        return False
    try:
        import numpy as np
        audio, sr = _master_audio_profile(path)
        begin = max(0, int(max(0.0, start_sec) * sr))
        end = min(len(audio), begin + max(1, int(duration_sec * sr)))
        y = audio[begin:end]
        if len(y) == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(y))))
        return rms >= 0.012
    except Exception:
        # If the audio cannot be measured, do not silently reject every
        # candidate; the visual detector's conservative bank remains usable.
        return False
