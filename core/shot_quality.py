"""Local deterministic shot-quality scoring for handheld director cameras."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
from PIL import Image

from core.ffmpeg import tool_status
from core.project import Project
from core.stages.base import stable_fingerprint

SHOT_QUALITY_VERSION = 1
DIRECTOR_SCORE_THRESHOLD = 0.46


def score_director_window(signals: dict[str, float]) -> tuple[float, list[str]]:
    face = max(0.0, min(1.0, float(signals.get("face_score") or 0.0)))
    stability = 1.0 - max(0.0, min(1.0, float(signals.get("motion") or 0.0)))
    sharpness = max(0.0, min(1.0, float(signals.get("sharpness") or 0.0)))
    exposure = max(0.0, min(1.0, float(signals.get("exposure") or 0.0)))
    score = 0.42 * face + 0.28 * stability + 0.18 * sharpness + 0.12 * exposure
    reasons: list[str] = []
    if face < 0.18:
        reasons.append("no face")
    if stability < 0.42:
        reasons.append("camera moving")
    if sharpness < 0.32:
        reasons.append("blur")
    if exposure < 0.42:
        reasons.append("exposure")
    return round(score, 4), reasons


def director_quality_for_segment(source: dict[str, Any], master_start: float, master_end: float) -> dict[str, Any]:
    quality = source.get("director_quality") or {}
    windows = quality.get("windows") or []
    if not windows:
        return {"score": 1.0, "eligible": True, "reasons": []}
    overlaps = []
    for window in windows:
        start = float(window.get("master_start_sec") or 0.0)
        end = float(window.get("master_end_sec") or start)
        overlap = max(0.0, min(master_end, end) - max(master_start, start))
        if overlap > 0:
            overlaps.append((overlap, window))
    if not overlaps:
        return {"score": 1.0, "eligible": True, "reasons": []}
    total = sum(overlap for overlap, _window in overlaps) or 1.0
    score = sum(overlap * float(window.get("score") or 0.0) for overlap, window in overlaps) / total
    reasons: dict[str, int] = {}
    for _overlap, window in overlaps:
        if bool(window.get("eligible", True)):
            continue
        for reason in window.get("reasons") or ["low director score"]:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "score": round(score, 4),
        "eligible": score >= DIRECTOR_SCORE_THRESHOLD and not reasons,
        "reasons": sorted(reasons, key=lambda key: (-reasons[key], key)),
    }


def analyze_handheld_director_quality(project: Project, sources: list[dict[str, Any]], progress: Any | None = None) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in sources:
        if _source_role(source) != "handheld":
            output.append(source)
            continue
        try:
            quality = _analyze_source_quality(project, source)
            output.append({**source, "director_quality": quality})
        except Exception as exc:
            output.append({**source, "director_quality": {"error": str(exc), "windows": [], "summary": {}}})
    return output


def _analyze_source_quality(project: Project, source: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(source.get("source_path") or source.get("path") or "")).expanduser()
    if not path.exists():
        return {"error": "source missing", "windows": [], "summary": {}}
    stat = path.stat()
    cache_key = stable_fingerprint(
        {
            "recipe": SHOT_QUALITY_VERSION,
            "path": str(path.resolve()),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        }
    )[:24]
    cache_path = project.cache_dir / "shot_quality" / f"{cache_key}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    ffmpeg = tool_status().get("ffmpeg_path")
    if not ffmpeg:
        return {"error": "ffmpeg missing", "windows": [], "summary": {}}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="zucker-shot-quality-") as tmpdir:
        pattern = Path(tmpdir) / "frame-%05d.jpg"
        command = [
            str(ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "fps=2,scale=320:-1",
            "-q:v",
            "5",
            str(pattern),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {"error": result.stderr.strip() or "quality frame extraction failed", "windows": [], "summary": {}}
        frames = sorted(Path(tmpdir).glob("frame-*.jpg"))
        quality = _quality_from_frames(frames, float(source.get("offset_sec") or 0.0))
    cache_path.write_text(json.dumps(quality, indent=2) + "\n", encoding="utf-8")
    return quality


def _quality_from_frames(frames: list[Path], offset_sec: float) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    previous: np.ndarray | None = None
    for index, frame in enumerate(frames):
        image = Image.open(frame).convert("RGB")
        array = np.asarray(image, dtype=np.float32)
        gray = array.mean(axis=2)
        motion = 0.0 if previous is None else float(np.mean(np.abs(gray - previous)) / 255.0)
        previous = gray
        samples.append(
            {
                "t": index / 2.0,
                "face_score": _face_like_score(array),
                "motion": min(1.0, motion * 3.0),
                "sharpness": _sharpness_score(gray),
                "exposure": _exposure_score(gray),
            }
        )
    windows = []
    for start_index in range(0, len(samples), 4):
        group = samples[start_index : start_index + 4]
        if not group:
            continue
        signals = {
            "face_score": float(np.mean([item["face_score"] for item in group])),
            "motion": float(np.mean([item["motion"] for item in group])),
            "sharpness": float(np.mean([item["sharpness"] for item in group])),
            "exposure": float(np.mean([item["exposure"] for item in group])),
        }
        score, reasons = score_director_window(signals)
        clip_start = group[0]["t"]
        clip_end = group[-1]["t"] + 0.5
        windows.append(
            {
                "clip_start_sec": round(clip_start, 3),
                "clip_end_sec": round(clip_end, 3),
                "master_start_sec": round(offset_sec + clip_start, 3),
                "master_end_sec": round(offset_sec + clip_end, 3),
                "score": score,
                "eligible": score >= DIRECTOR_SCORE_THRESHOLD and not reasons,
                "reasons": reasons,
                "signals": {key: round(value, 4) for key, value in signals.items()},
            }
        )
    summary = _quality_summary(windows)
    return {"version": SHOT_QUALITY_VERSION, "windows": windows, "summary": summary}


def _quality_summary(windows: list[dict[str, Any]]) -> dict[str, Any]:
    if not windows:
        return {}
    rejected = [window for window in windows if not bool(window.get("eligible", True))]
    reasons: dict[str, int] = {}
    for window in rejected:
        for reason in window.get("reasons") or ["low director score"]:
            reasons[str(reason)] = reasons.get(str(reason), 0) + 1
    return {
        "windows": len(windows),
        "rejected_windows": len(rejected),
        "rejected_percent": round(len(rejected) / len(windows) * 100.0, 1),
        "reasons": reasons,
    }


def _face_like_score(rgb: np.ndarray) -> float:
    # Deterministic lightweight fallback: find a face-sized skin-tone region in the upper/middle frame.
    r = rgb[:, :, 0]
    g = rgb[:, :, 1]
    b = rgb[:, :, 2]
    mask = (r > 70) & (g > 45) & (b > 30) & (r > g * 1.05) & (r > b * 1.25) & ((np.maximum.reduce([r, g, b]) - np.minimum.reduce([r, g, b])) > 15)
    height, width = mask.shape
    roi = mask[: int(height * 0.72), int(width * 0.18) : int(width * 0.82)]
    area = float(np.mean(roi))
    if area <= 0:
        return 0.0
    yx = np.argwhere(roi)
    if yx.size == 0:
        return 0.0
    y_min, x_min = yx.min(axis=0)
    y_max, x_max = yx.max(axis=0)
    bbox_area = ((y_max - y_min + 1) * (x_max - x_min + 1)) / max(1, roi.shape[0] * roi.shape[1])
    return max(0.0, min(1.0, area * 8.0 + bbox_area * 0.8))


def _sharpness_score(gray: np.ndarray) -> float:
    gy, gx = np.gradient(gray)
    value = float(np.var(gx) + np.var(gy))
    return max(0.0, min(1.0, value / 1800.0))


def _exposure_score(gray: np.ndarray) -> float:
    mean = float(np.mean(gray))
    clipped = float(np.mean((gray < 8) | (gray > 247)))
    mean_score = 1.0 - min(1.0, abs(mean - 118.0) / 118.0)
    return max(0.0, min(1.0, mean_score * (1.0 - min(1.0, clipped * 3.0))))


def _source_role(source: dict[str, Any]) -> str:
    projection = str(source.get("projection") or "").lower()
    filename = str(source.get("filename") or source.get("path") or source.get("source_path") or "").lower()
    if projection in {"equirect", "raw_insv"} or filename.endswith(".insv") or "360" in filename:
        return "360"
    if "iphone" in filename or filename.endswith(".mov"):
        return "fixed_rear"
    return "handheld"
