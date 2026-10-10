"""Unsynced filler footage: faces, interviews, audience and ambience.

Videos marked as filler in Inputs skip sync and are never treated as cameras.
Each one is sampled once per second, tagged locally (``core.scene_tags``) and
cut into short windows. When no synced camera covers a stretch of the song,
the edit planner takes a compatible window instead of leaving a gap.

Compatibility is deliberately conservative. A window that may show rain or
fire is never used unless the project is confirmed to have it, and day/night
must not contradict the project. Shots of musicians performing are never
used: they would not match the music that is playing.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from core import scene_tags
from core.stages.base import stable_fingerprint

LOGGER = logging.getLogger(__name__)

FILLER_VERSION = 1
SAMPLE_FPS = 1.0
MIN_WINDOW_SEC = 2.0
MAX_WINDOW_SEC = 8.0
REUSE_COOLDOWN_SEC = 180.0
# A window is unsafe for a dry/fire-free project from this mean probability.
RISK_PROBABILITY = 0.35
CONFIDENT_PROBABILITY = 0.6
CONDITION_CHOICES = {
    "time": ("day", "sunset", "night"),
    "place": ("indoor", "outdoor"),
    "rain": ("yes", "no"),
    "fire": ("yes", "no"),
}
# Editorial preference among usable shot types; performance is never used.
SHOT_PREFERENCE = {"audience": 1.0, "face": 0.95, "ambient": 0.9, "interview": 0.8, "detail": 0.7}


def filler_paths(project: Any) -> set[str]:
    # Filler is a YouTube coverage role, never an exclusion switch for a Reel
    # compilation. Old projects may still carry those marks after changing mode.
    mode = ((project.data.get("settings") or {}).get("wizard") or {}).get("platform")
    if mode in {"reel", "medley", "backstage", "360"}:
        return set()
    edit = (project.data.get("settings") or {}).get("edit") or {}
    return {str(path) for path in edit.get("fillers") or []}


def is_filler_record(project: Any, record: dict[str, Any]) -> bool:
    return str(record.get("path") or "") in filler_paths(project)


def condition_overrides(project: Any) -> dict[str, str]:
    edit = (project.data.get("settings") or {}).get("edit") or {}
    raw = edit.get("scene_conditions") or {}
    return {key: str(raw[key]) for key in CONDITION_CHOICES if raw.get(key) in CONDITION_CHOICES[key]}


def _probe_duration(record: dict[str, Any]) -> float:
    probe = record.get("probe") or {}
    try:
        return float(probe.get("duration") or probe.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def sample_frames(path: str, *, fps: float = SAMPLE_FPS, start: float = 0.0, duration: float | None = None,
                  max_frames: int | None = None, size: int = 256) -> list[np.ndarray]:
    """Decode evenly spaced RGB frames as centred ``size`` squares in one FFmpeg pass.

    A fixed square keeps the byte layout known even when FFmpeg auto-rotates
    portrait phone footage; CLIP centre-crops to a square anyway.
    """
    from core.ffmpeg import tool_status
    command = [str(tool_status().get("ffmpeg_path") or "ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    # Keyframes only: 1 fps tagging does not need every 4K frame decoded.
    command += ["-skip_frame", "nokey", "-i", path]
    if duration:
        command += ["-t", f"{duration:.3f}"]
    command += ["-vf", f"fps={fps},scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
                "-an", "-pix_fmt", "rgb24", "-f", "rawvideo"]
    if max_frames:
        command += ["-frames:v", str(max_frames)]
    command.append("pipe:1")
    try:
        result = subprocess.run(command, capture_output=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOGGER.warning("Could not sample frames from %s: %s", path, exc)
        return []
    frame_bytes = size * size * 3
    data = result.stdout or b""
    return [np.frombuffer(data[offset:offset + frame_bytes], np.uint8).reshape(size, size, 3)
            for offset in range(0, len(data) - frame_bytes + 1, frame_bytes)]


def _record_key(record: dict[str, Any], purpose: str) -> str | None:
    path = Path(str(record.get("path") or ""))
    try:
        stat = path.stat()
    except OSError:
        return None
    return stable_fingerprint({
        "purpose": purpose, "path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "filler": FILLER_VERSION, "scene": scene_tags.SCENE_TAGS_VERSION,
        "prompts": scene_tags.prompts_fingerprint(), "model": scene_tags.available(),
    })[:24]


def _cached(project: Any, key: str, compute) -> Any:
    root = Path(project.cache_dir) / "fillers"
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{key}.json"
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    payload = compute()
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    temporary.replace(target)
    return payload


def _top(probabilities: dict[str, float] | None) -> tuple[str, float]:
    if not probabilities:
        return "unknown", 0.0
    name = max(probabilities, key=probabilities.get)
    return name, float(probabilities[name])


def summarize_conditions(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-frame tags into conditions plus the evidence behind them."""
    summary: dict[str, Any] = {"samples": len(samples), "model": any(sample.get("model") for sample in samples)}
    for attribute in ("time", "place"):
        totals: dict[str, float] = {}
        for sample in samples:
            for name, value in (sample.get(attribute) or {}).items():
                totals[name] = totals.get(name, 0.0) + float(value)
        mean = {name: value / max(1, len(samples)) for name, value in totals.items()}
        name, confidence = _top(mean)
        summary[attribute] = name if confidence >= CONFIDENT_PROBABILITY else "unknown"
        summary[f"{attribute}_confidence"] = round(confidence, 3)
    for attribute in ("rain", "fire"):
        values = [float(sample[attribute]) for sample in samples if attribute in sample]
        if not values:
            summary[attribute] = "unknown"
            continue
        mean, peak = float(np.mean(values)), float(np.max(values))
        summary[f"{attribute}_probability"] = round(mean, 3)
        summary[f"{attribute}_peak"] = round(peak, 3)
        summary[attribute] = "yes" if mean >= 0.5 else "no" if peak < RISK_PROBABILITY else "unknown"
    return summary


def build_windows(samples: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Group consecutive samples of one usable shot type into 2-8 s windows."""
    runs: list[list[int]] = []
    for index, sample in enumerate(samples):
        shot, _ = _top(sample.get("shot"))
        if shot not in SHOT_PREFERENCE or float(sample.get("brightness") or 0.0) < 6.0:
            continue
        if runs and runs[-1][-1] == index - 1 and _top(samples[runs[-1][0]].get("shot"))[0] == shot:
            runs[-1].append(index)
        else:
            runs.append([index])
    step = 1.0 / SAMPLE_FPS
    windows: list[dict[str, Any]] = []
    for run in runs:
        run_start, run_end = run[0] * step, min(duration or 1e9, (run[-1] + 1) * step)
        cursor = run_start
        while run_end - cursor >= MIN_WINDOW_SEC:
            end = min(run_end, cursor + MAX_WINDOW_SEC)
            if run_end - end < MIN_WINDOW_SEC:
                end = run_end if run_end - cursor <= MAX_WINDOW_SEC + MIN_WINDOW_SEC else end
            members = samples[int(cursor / step):int(round(end / step))]
            shot, shot_confidence = _top(members[0].get("shot"))
            sharpness = float(np.median([float(item.get("sharpness") or 0.0) for item in members]))
            windows.append({
                "start_sec": round(cursor, 3),
                "end_sec": round(end, 3),
                "shot": shot,
                "shot_confidence": round(shot_confidence, 3),
                "sharpness": round(sharpness, 2),
                "conditions": summarize_conditions(members),
            })
            cursor = end
    return windows


def _analysis_media(record: dict[str, Any]) -> str:
    """The lighter of the original and its same-timeline normalized proxy."""
    original = str(record.get("path") or "")
    proxy = str((record.get("normalized") or {}).get("path") or "")
    try:
        if proxy and Path(proxy).stat().st_size < Path(original).stat().st_size:
            return proxy
    except OSError:
        pass
    return original


def analyze_filler(project: Any, record: dict[str, Any]) -> dict[str, Any] | None:
    key = _record_key(record, "filler")
    path = str(record.get("path") or "")
    if not key:
        return None

    def compute() -> dict[str, Any]:
        duration = _probe_duration(record)
        samples = scene_tags.analyze_frames(sample_frames(_analysis_media(record)))
        LOGGER.info("Filler analysed path=%s samples=%d model=%s", path, len(samples), scene_tags.available())
        return {"version": FILLER_VERSION, "path": path, "duration_sec": duration,
                "conditions": summarize_conditions(samples), "windows": build_windows(samples, duration)}
    return _cached(project, key, compute)


def detect_project_conditions(project: Any, sources: list[dict[str, Any]], frames_per_source: int = 10) -> dict[str, Any]:
    """Tag a few frames from each flat synced camera (360 frames confuse CLIP)."""
    samples: list[dict[str, Any]] = []
    for source in sources:
        if str(source.get("projection") or "").lower() in {"equirect", "raw_insv"}:
            continue
        path = str(source.get("source_path") or source.get("path") or "")
        record = next((item for item in (project.data.get("inputs") or {}).get("videos") or []
                       if str(item.get("path")) == path), {"path": path, "probe": source.get("probe") or {}})
        key = _record_key(record, f"project-conditions-{frames_per_source}")
        if not key:
            continue

        def compute(path: str = path) -> list[dict[str, Any]]:
            # Seek to each instant: a few frames must not read a whole multi-GB file.
            duration = _probe_duration(record) or 60.0
            frames = [frame for index in range(frames_per_source)
                      for frame in sample_frames(path, start=duration * (index + 0.5) / frames_per_source, max_frames=1)]
            return scene_tags.analyze_frames(frames)
        samples.extend(_cached(project, key, compute))
    return summarize_conditions(samples)


def effective_project_conditions(detected: dict[str, Any], overrides: dict[str, str]) -> dict[str, Any]:
    return {**detected, **overrides, "confirmed": sorted(overrides)}


def compatibility(window: dict[str, Any], project_conditions: dict[str, Any]) -> tuple[bool, str]:
    """Hard rules. Unknown project weather counts as "no": never invent rain or fire."""
    conditions = window.get("conditions") or {}
    for attribute in ("rain", "fire"):
        risky = conditions.get(attribute) == "yes" or float(conditions.get(f"{attribute}_probability") or 0.0) >= RISK_PROBABILITY
        if risky and project_conditions.get(attribute) != "yes":
            return False, f"{attribute} in filler but not in the video"
    project_time, filler_time = project_conditions.get("time"), conditions.get("time")
    if {project_time, filler_time} == {"day", "night"}:
        return False, f"{filler_time} filler in a {project_time} video"
    if window.get("shot") not in SHOT_PREFERENCE:
        return False, "shot type not usable as filler"
    return True, ""


def _window_score(window: dict[str, Any], project_conditions: dict[str, Any]) -> float:
    conditions = window.get("conditions") or {}
    score = SHOT_PREFERENCE.get(str(window.get("shot")), 0.0) * (0.6 + 0.4 * float(window.get("shot_confidence") or 0.0))
    if project_conditions.get("place") in {"indoor", "outdoor"}:
        score *= 1.0 if conditions.get("place") == project_conditions["place"] else 0.75
    if project_conditions.get("time") and conditions.get("time") == project_conditions.get("time"):
        score *= 1.1
    score *= min(1.0, 0.5 + float(window.get("sharpness") or 0.0) / 400.0)
    return score


def build_filler_bank(project: Any, coverage_sources: list[dict[str, Any]]) -> dict[str, Any]:
    """Analysed filler windows plus the conditions they must match."""
    records = [record for record in (project.data.get("inputs") or {}).get("videos") or [] if is_filler_record(project, record)]
    overrides = condition_overrides(project)
    bank: dict[str, Any] = {"enabled": bool(records), "model": scene_tags.available(), "windows": [], "rejected": {}}
    if not records:
        bank["conditions"] = effective_project_conditions({}, overrides)
        return bank
    detected = detect_project_conditions(project, coverage_sources)
    conditions = effective_project_conditions(detected, overrides)
    bank["conditions"] = conditions
    for record in records:
        if str((record.get("probe") or {}).get("projection") or "").lower() in {"equirect", "raw_insv"}:
            bank["rejected"]["360 filler not supported"] = bank["rejected"].get("360 filler not supported", 0) + 1
            continue
        analysis = analyze_filler(project, record)
        if not analysis:
            continue
        normalized = record.get("normalized") or {}
        for window in analysis.get("windows") or []:
            usable, reason = compatibility(window, conditions)
            if not usable:
                bank["rejected"][reason] = bank["rejected"].get(reason, 0) + 1
                continue
            bank["windows"].append({
                **window,
                "source_path": str(record.get("path")),
                "clip_path": str(normalized.get("path") or record.get("path")),
                "filename": record.get("filename") or Path(str(record.get("path"))).name,
                "score": round(_window_score(window, conditions), 4),
            })
    return bank


def choose_filler(bank: dict[str, Any], master_start: float, needed_sec: float, history: list[dict[str, Any]],
                  seed: str, segment_index: int) -> dict[str, Any] | None:
    """Best compatible window not used in the last three minutes or last shot."""
    candidates = []
    for window in bank.get("windows") or []:
        length = float(window["end_sec"]) - float(window["start_sec"])
        if length < min(needed_sec, MIN_WINDOW_SEC):
            continue
        key = f"{window['source_path']}|{float(window['start_sec']):.3f}"
        if history and history[-1]["key"] == key:
            continue
        if any(item["key"] == key and abs(master_start - item["master_start_sec"]) < REUSE_COOLDOWN_SEC for item in history):
            continue
        tie_break = int(stable_fingerprint({"seed": seed, "segment": segment_index, "key": key})[:6], 16) / 0xFFFFFF
        # Keep variety among near-equal windows without letting a weak shot win.
        candidates.append((float(window.get("score") or 0.0) + 0.08 * tie_break, key, window))
    if not candidates:
        return None
    _, key, window = max(candidates, key=lambda item: item[0])
    history.append({"key": key, "master_start_sec": master_start})
    return {**window, "key": key}


def warm_filler_analysis(project: Any) -> int:
    """Analyse marked filler videos ahead of the edit (cached); returns how many."""
    records = [record for record in (project.data.get("inputs") or {}).get("videos") or []
               if is_filler_record(project, record)
               and str((record.get("probe") or {}).get("projection") or "").lower() not in {"equirect", "raw_insv"}]
    for record in records:
        analyze_filler(project, record)
    return len(records)
