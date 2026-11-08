"""Show resources: close-ups, other angles and photos that go with the talk.

Each resource is analysed once (cached by size and date):
  intervals  the usable stretches: still (tripod-steady, sharp) or moving
             (hand-held but sharp), each with a thumbnail and a CLIP picture
             vector for matching
  speech     what is said in it (local Whisper): a possible voice for a
             character, or instructions to the editor - the person decides
Matching a line of the talk to a resource uses CLIP text-picture similarity,
corrected for pictures that resemble everything ("hubs"). On real footage it
ranks the right close-up first for some lines and among the top three for
others, with small margins: it proposes candidates for review, it does not
decide. Nothing is uploaded; thumbnails stay in the project's cache.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

SAMPLES_PER_SEC = 2
THUMB_W, THUMB_H = 384, 216
MIN_INTERVAL = 1.5
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
ANALYSIS_VERSION = 1


def _key(path: Path) -> str:
    stat = path.stat()
    return hashlib.sha1(f"{ANALYSIS_VERSION}|{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:16]


def intervals_from_metrics(sharp: list[float], motion: list[float], fps: float = SAMPLES_PER_SEC) -> list[dict[str, Any]]:
    """Steady sharp stretches ("still") and sharp hand-held ones ("moving")."""
    if not sharp:
        return []
    reference = sorted(sharp)[len(sharp) * 3 // 4] or 1.0
    found: list[dict[str, Any]] = []
    taken = [False] * len(sharp)
    for kind, max_motion, min_sharp in (("still", 12.0, 0.45), ("moving", 30.0, 0.6)):
        start = None
        for index in range(len(sharp) + 1):
            ok = index < len(sharp) and not taken[index] and motion[index] < max_motion and sharp[index] > min_sharp * reference
            if ok and start is None:
                start = index
            if not ok and start is not None:
                if (index - start) / fps >= MIN_INTERVAL:
                    found.append({"start": round(start / fps, 2), "end": round(index / fps, 2), "kind": kind,
                                  "sharp": round(float(np.mean(sharp[start:index])), 1)})
                    for i in range(start, index):
                        taken[i] = True
                start = None
    return sorted(found, key=lambda item: item["start"])


def _video_frames(path: Path, ffmpeg: str) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-vf", f"fps={SAMPLES_PER_SEC},scale={THUMB_W}:{THUMB_H}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, THUMB_H, THUMB_W, 3)


def _speech(path: Path, cache: Path, ffmpeg: str) -> list[dict[str, Any]]:
    wav = cache / "speech.wav"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
                   capture_output=True, check=False)
    if not wav.is_file() or wav.stat().st_size < 32000:
        return []
    from faster_whisper import WhisperModel
    from core.storage import whisper_model_path
    model = WhisperModel(whisper_model_path("small"), device="cpu", compute_type="int8")
    segments, info = model.transcribe(str(wav), vad_filter=True, beam_size=5)
    lines = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()} for s in segments if s.text.strip()]
    wav.unlink(missing_ok=True)
    return [{**line, "language": info.language} for line in lines]


def analyze(path: str | Path, cache_root: Path, ffmpeg: str = "ffmpeg", *, transcribe: bool = True,
            cancel: Callable[[], None] | None = None) -> dict[str, Any]:
    """The resource's usable intervals (with thumbnails and picture vectors) and speech."""
    import cv2
    from PIL import Image
    from core import scene_tags
    path = Path(path)
    cache = cache_root / "show_resources" / _key(path)
    stored = cache / "analysis.json"
    if stored.is_file():
        return json.loads(stored.read_text(encoding="utf-8"))
    cache.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in PHOTO_SUFFIXES:
        picture = np.asarray(Image.open(path).convert("RGB").resize((THUMB_W, THUMB_H)))
        intervals = [{"start": 0.0, "end": 0.0, "kind": "photo", "sharp": 0.0}]
        middles = [picture]
        speech: list[dict[str, Any]] = []
        duration = 0.0
    else:
        frames = _video_frames(path, ffmpeg)
        if cancel:
            cancel()
        grey = [cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY).astype(np.float32) for frame in frames]
        sharp = [float(cv2.Laplacian(g, cv2.CV_32F).var()) for g in grey]
        motion = [0.0] + [float(np.abs(a - b).mean()) for a, b in zip(grey[1:], grey[:-1])]
        intervals = intervals_from_metrics(sharp, motion)
        middles = [frames[min(len(frames) - 1, int((item["start"] + item["end"]) / 2 * SAMPLES_PER_SEC))] for item in intervals]
        duration = len(frames) / SAMPLES_PER_SEC
        speech = _speech(path, cache, ffmpeg) if transcribe else []
    vectors = scene_tags._embed(middles) if middles and scene_tags.available() else None
    for index, (item, picture) in enumerate(zip(intervals, middles)):
        thumb = cache / f"interval-{index:03d}.jpg"
        Image.fromarray(picture).save(thumb, quality=82)
        item["thumb"] = str(thumb)
        item["vector"] = [round(float(v), 5) for v in vectors[index]] if vectors is not None else None
    result = {"path": str(path), "kind": "photo" if path.suffix.lower() in PHOTO_SUFFIXES else "video", "duration": round(duration, 2),
              "intervals": intervals, "speech": speech, "version": ANALYSIS_VERSION}
    stored.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def match(lines: list[str], resources: list[dict[str, Any]], *, top: int = 3) -> list[list[dict[str, Any]]]:
    """For each line, the ``top`` resource intervals it may show, best first:
    {resource, interval, score} with the hub correction applied."""
    from core.gag_library import embed_texts
    candidates = [(resource_index, interval_index, interval["vector"])
                  for resource_index, resource in enumerate(resources)
                  for interval_index, interval in enumerate(resource.get("intervals") or []) if interval.get("vector")]
    if not lines or not candidates:
        return [[] for _ in lines]
    texts = embed_texts(lines)
    if texts is None:
        return [[] for _ in lines]
    pictures = np.asarray([vector for *_, vector in candidates], dtype=np.float32)
    similarity = texts @ pictures.T
    hub = similarity.mean(axis=0)            # how much each picture resembles every line
    scores = similarity - hub
    out = []
    for row in scores:
        order = np.argsort(-row)[:top]
        out.append([{"resource": candidates[i][0], "interval": candidates[i][1], "score": round(float(row[i]), 4)} for i in order])
    return out
