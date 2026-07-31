"""Recorded 360 camera move artifacts."""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.project import Project

CAMERA_MOVE_VERSION = 3
DEFAULT_SAMPLE_RATE_HZ = 15.0
SMOOTHING_RADII = {"light": 3, "medium": 7, "strong": 12}
# Recorded Director movement expresses where the user wanted to look, not the
# exact hand velocity. Keep even the lightest smoothing comfortable enough for
# a finished edit and make this a hard render-time guarantee.
MAX_RECORDED_YAW_RATE_DEG_PER_SEC = 40.0


def camera_moves_dir(project: Project) -> Path:
    path = project.artifacts_dir / "camera_moves"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_take_name(name: str | None) -> str:
    text = re.sub(r"\s+", " ", str(name or "").strip())
    if not text:
        text = datetime.now().strftime("Take %Y-%m-%d %H.%M.%S")
    safe = re.sub(r"[^A-Za-z0-9 ._-]+", "-", text).strip(" ._-")
    return safe[:80] or "Take"


def normalize_recorded_samples(samples: list[dict[str, Any]]) -> list[dict[str, float]]:
    normalized: list[dict[str, float]] = []
    previous_t: float | None = None
    for sample in samples:
        try:
            t = float(sample.get("t"))
            yaw = float(sample.get("yaw"))
            pitch = float(sample.get("pitch"))
            fov = float(sample.get("fov"))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (t, yaw, pitch, fov)):
            continue
        if previous_t is not None and t <= previous_t:
            continue
        previous_t = t
        item = {
            "t": round(max(0.0, t), 6),
            "yaw": round(yaw % 360.0, 6),
            "pitch": round(max(-89.0, min(89.0, pitch)), 6),
            "fov": round(max(1.0, min(300.0, fov)), 6),
        }
        if sample.get("video_time") is not None:
            try:
                item["video_time"] = round(max(0.0, float(sample.get("video_time"))), 6)
            except (TypeError, ValueError):
                pass
        normalized.append(item)
    return normalized


def smooth_camera_curve(samples: list[dict[str, float]], radius: int = 2) -> list[dict[str, float]]:
    if len(samples) <= 2:
        return [dict(sample) for sample in samples]
    unwrapped_yaws = _unwrap_yaws([float(sample["yaw"]) for sample in samples])
    smoothed: list[dict[str, float]] = []
    for index, sample in enumerate(samples):
        start = max(0, index - radius)
        end = min(len(samples), index + radius + 1)
        weights = [1.0 - abs(i - index) / (radius + 1.0) for i in range(start, end)]
        total = sum(weights) or 1.0

        def average(values: list[float]) -> float:
            return sum(value * weight for value, weight in zip(values, weights)) / total

        smoothed.append(
            {
                **sample,
                "yaw": round(average(unwrapped_yaws[start:end]) % 360.0, 6),
                "pitch": round(average([float(item["pitch"]) for item in samples[start:end]]), 6),
                "fov": round(average([float(item["fov"]) for item in samples[start:end]]), 6),
            }
        )
    return smoothed


def limit_yaw_velocity(
    samples: list[dict[str, Any]],
    max_rate_deg_per_sec: float = MAX_RECORDED_YAW_RATE_DEG_PER_SEC,
) -> list[dict[str, float]]:
    """Slew-limit a recorded curve after unwrapping its yaw.

    The limiter works in a continuous yaw space, so 359° -> 1° remains a
    small positive move. It deliberately limits against the already-emitted
    yaw rather than merely clamping each raw delta: a burst cannot reappear
    after a wrap or after a preceding sample was held back.
    """
    normalized = normalize_recorded_samples(samples)
    if len(normalized) <= 1:
        return [dict(sample) for sample in normalized]
    limit = max(0.0, float(max_rate_deg_per_sec))
    raw_unwrapped = _unwrap_yaws([float(sample["yaw"]) for sample in normalized])
    limited_yaw = [raw_unwrapped[0]]
    output = [dict(normalized[0])]
    for index in range(1, len(normalized)):
        previous = normalized[index - 1]
        current = normalized[index]
        dt = max(0.0, float(current["t"]) - float(previous["t"]))
        target_delta = raw_unwrapped[index] - limited_yaw[-1]
        max_delta = limit * dt
        if abs(target_delta) > max_delta:
            target_delta = math.copysign(max_delta, target_delta)
        next_yaw = limited_yaw[-1] + target_delta
        limited_yaw.append(next_yaw)
        output.append({**current, "yaw": round(next_yaw % 360.0, 6)})
    return output


def smoothing_radius(strength: str | None) -> int:
    return SMOOTHING_RADII.get(str(strength or "medium").lower(), SMOOTHING_RADII["medium"])


def save_camera_move(
    project: Project,
    name: str | None,
    samples: list[dict[str, Any]],
    source_path: str | None = None,
    smoothing: str | None = "medium",
) -> dict[str, Any]:
    raw = normalize_recorded_samples(samples)
    if len(raw) < 2:
        raise ValueError("Record at least two camera samples")
    strength = str(smoothing or "medium").lower()
    smoothed = limit_yaw_velocity(smooth_camera_curve(raw, radius=smoothing_radius(strength)))
    take_name = _unique_take_name(project, sanitize_take_name(name))
    duration = max(0.0, smoothed[-1]["t"] - smoothed[0]["t"])
    sample_rate = (len(smoothed) - 1) / duration if duration > 0 else DEFAULT_SAMPLE_RATE_HZ
    artifact = {
        "version": CAMERA_MOVE_VERSION,
        "name": take_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": source_path or "",
        "sample_rate_hz": round(sample_rate, 3),
        "smoothing": strength if strength in SMOOTHING_RADII else "medium",
        "start_master_sec": smoothed[0]["t"],
        "end_master_sec": smoothed[-1]["t"],
        "sample_count": len(smoothed),
        "raw": raw,
        "smoothed": smoothed,
    }
    path = camera_moves_dir(project) / f"{take_name}.json"
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return _summary(artifact, path)


def list_camera_moves(project: Project) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for path in sorted(camera_moves_dir(project).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        moves.append(_summary(data, path))
    return moves


def load_camera_moves(project: Project) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for path in sorted(camera_moves_dir(project).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        raw_samples = normalize_recorded_samples(data.get("raw") or [])
        if raw_samples and int(data.get("version") or 1) < CAMERA_MOVE_VERSION:
            strength = str(data.get("smoothing") or "medium")
            samples = limit_yaw_velocity(smooth_camera_curve(raw_samples, radius=smoothing_radius(strength)))
        else:
            samples = limit_yaw_velocity(data.get("smoothed") or data.get("raw") or [])
        if len(samples) < 2:
            continue
        data["smoothed"] = samples
        data["path"] = str(path)
        data["name"] = data.get("name") or path.stem
        data["start_master_sec"] = float(samples[0]["t"])
        data["end_master_sec"] = float(samples[-1]["t"])
        moves.append(data)
    return moves


def delete_camera_move(project: Project, name: str) -> bool:
    safe = sanitize_take_name(name)
    path = camera_moves_dir(project) / f"{safe}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def recorded_move_covering(moves: list[dict[str, Any]], start_sec: float, end_sec: float) -> dict[str, Any] | None:
    for move in moves:
        samples = normalize_recorded_samples(move.get("smoothed") or move.get("raw") or [])
        if len(samples) < 2:
            continue
        move_start = float(move.get("start_master_sec") if move.get("start_master_sec") is not None else samples[0]["t"])
        move_end = float(move.get("end_master_sec") if move.get("end_master_sec") is not None else samples[-1]["t"])
        if move_start <= start_sec + 0.04 and move_end >= end_sec - 0.04:
            return move
    return None


def recorded_shot_for_segment(move: dict[str, Any], start_sec: float, end_sec: float) -> dict[str, Any]:
    curve = clip_curve_for_segment(move, start_sec, end_sec)
    first = curve[0] if curve else {"yaw": 0.0, "pitch": 0.0, "fov": 100.0}
    return {
        "type": "recorded_move",
        "label": f"Recorded take: {move.get('name') or 'Take'}",
        "recorded_take": move.get("name") or "Take",
        "yaw": first["yaw"],
        "pitch": first["pitch"],
        "fov": first["fov"],
        "curve": curve,
    }


# Defensive ceiling for malformed data after the user-facing 40°/s limiter.
# This remains a local-rate check (rather than total travel over duration), so
# it catches a future time-mapping regression without rejecting legitimate
# long takes. Normal recorded curves should never reach this ceiling.
MAX_PLAUSIBLE_YAW_RATE_DEG_PER_SEC = 720.0


def clip_curve_for_segment(move: dict[str, Any], start_sec: float, end_sec: float) -> list[dict[str, float]]:
    samples = limit_yaw_velocity(move.get("smoothed") or move.get("raw") or [])
    if len(samples) < 2:
        return []
    duration = max(0.001, end_sec - start_sec)
    selected = [_interpolate_sample(samples, start_sec)]
    for sample in samples:
        if start_sec < sample["t"] < end_sec:
            selected.append(sample)
    selected.append(_interpolate_sample(samples, end_sec))
    output: list[dict[str, float]] = []
    last_local: float | None = None
    for sample in selected:
        local_t = round(max(0.0, min(duration, sample["t"] - start_sec)), 6)
        if last_local is not None and local_t <= last_local:
            local_t = round(min(duration, last_local + 0.000001), 6)
        last_local = local_t
        output.append({"t": local_t, "yaw": sample["yaw"], "pitch": sample["pitch"], "fov": sample["fov"]})
    output = limit_yaw_velocity(output)
    _assert_plausible_yaw_rate(output, start_sec, end_sec)
    return output


def _assert_plausible_yaw_rate(curve: list[dict[str, float]], start_sec: float, end_sec: float) -> None:
    for left, right in zip(curve, curve[1:]):
        dt = right["t"] - left["t"]
        if dt <= 0:
            continue
        # Short-path (shortest-arc) delta — the same wrap-safe distance the
        # renderer's own interpolation uses, so a natural 359°->1° pan across
        # the seam reads as ~2°, not ~358°.
        delta = abs(((right["yaw"] - left["yaw"] + 180.0) % 360.0) - 180.0)
        rate = delta / dt
        if rate > MAX_PLAUSIBLE_YAW_RATE_DEG_PER_SEC:
            raise ValueError(
                f"Implausible yaw rate in clipped curve for segment [{start_sec:.2f}, {end_sec:.2f}]s: "
                f"{rate:.1f}°/s between local t={left['t']:.3f}s and t={right['t']:.3f}s "
                f"(limit {MAX_PLAUSIBLE_YAW_RATE_DEG_PER_SEC:.0f}°/s) — likely a curve time-mapping bug, "
                "not real recorded motion."
            )


def interpolate_curve(curve: list[dict[str, Any]], t: float) -> tuple[float, float, float] | None:
    samples = normalize_recorded_samples(curve)
    if not samples:
        return None
    if t <= samples[0]["t"]:
        return samples[0]["yaw"], samples[0]["pitch"], samples[0]["fov"]
    if t >= samples[-1]["t"]:
        return samples[-1]["yaw"], samples[-1]["pitch"], samples[-1]["fov"]
    return _sample_tuple(_interpolate_sample(samples, t))


def _interpolate_sample(samples: list[dict[str, float]], t: float) -> dict[str, float]:
    if t <= samples[0]["t"]:
        return dict(samples[0])
    if t >= samples[-1]["t"]:
        return dict(samples[-1])
    for index in range(len(samples) - 1):
        left = samples[index]
        right = samples[index + 1]
        if left["t"] <= t <= right["t"]:
            span = max(0.000001, right["t"] - left["t"])
            amount = (t - left["t"]) / span
            yaw = _lerp_yaw(left["yaw"], right["yaw"], amount)
            return {
                "t": round(t, 6),
                "yaw": round(yaw % 360.0, 6),
                "pitch": round(left["pitch"] + (right["pitch"] - left["pitch"]) * amount, 6),
                "fov": round(left["fov"] + (right["fov"] - left["fov"]) * amount, 6),
            }
    return dict(samples[-1])


def _sample_tuple(sample: dict[str, float]) -> tuple[float, float, float]:
    return sample["yaw"], sample["pitch"], sample["fov"]


def _lerp_yaw(start: float, end: float, amount: float) -> float:
    delta = ((end - start + 540.0) % 360.0) - 180.0
    return start + delta * max(0.0, min(1.0, amount))


def _unwrap_yaws(values: list[float]) -> list[float]:
    if not values:
        return []
    output = [values[0]]
    for value in values[1:]:
        previous = output[-1]
        candidate = value
        while candidate - previous > 180.0:
            candidate -= 360.0
        while candidate - previous < -180.0:
            candidate += 360.0
        output.append(candidate)
    return output


def _unique_take_name(project: Project, name: str) -> str:
    base = sanitize_take_name(name)
    path = camera_moves_dir(project) / f"{base}.json"
    if not path.exists():
        return base
    for index in range(2, 1000):
        candidate = f"{base} {index}"
        if not (camera_moves_dir(project) / f"{candidate}.json").exists():
            return candidate
    return f"{base} {datetime.now().strftime('%H.%M.%S')}"


def _summary(data: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "name": data.get("name") or path.stem,
        "path": str(path),
        "created_at": data.get("created_at"),
        "sample_rate_hz": data.get("sample_rate_hz"),
        "sample_count": data.get("sample_count") or len(data.get("smoothed") or data.get("raw") or []),
        "start_master_sec": data.get("start_master_sec"),
        "end_master_sec": data.get("end_master_sec"),
        "source_path": data.get("source_path") or "",
    }
