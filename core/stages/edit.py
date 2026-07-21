"""Beat-aligned edit decision generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.messages import t
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage

MIN_SEGMENT_SEC = 2.0
MAX_SEGMENT_SEC = 6.0
MAX_BARS_PER_SEGMENT = 2
EDIT_FPS = 30.0
DEFAULT_CAMERA_ROLE_WEIGHTS = {"360": 0.5, "handheld": 0.3, "fixed_rear": 0.2}
SPHERICAL_PAN_SEC = 0.45
SPHERICAL_DEFAULT_FOV = 74.8
SPHERICAL_SHOT_ORDER = ("full_stage", "singer", "drummer", "left", "right", "audience", "audience_stage_wide", "planet")
SPHERICAL_LANDMARKS = {
    "singer": ("singer_yaw", "Cantante", SPHERICAL_DEFAULT_FOV),
    "drummer": ("drummer_yaw", "Bateria", SPHERICAL_DEFAULT_FOV),
    "left": ("left_yaw", "Lado izquierdo", SPHERICAL_DEFAULT_FOV),
    "right": ("right_yaw", "Lado derecho", SPHERICAL_DEFAULT_FOV),
    "audience": ("audience_yaw", "Publico", SPHERICAL_DEFAULT_FOV),
    "full_stage": ("full_stage_yaw", "Escenario completo", 110.0),
    "audience_stage_wide": ("audience_stage_wide_yaw", "Publico y escenario", 113.6),
    "planet": ("planet_yaw", "Planeta", 150.0),
}


class EditStage(Stage):
    """Build wizard edit decisions from coverage.

    YouTube uses a first-pass real multicam plan with beat-aligned cuts.
    Instagram, TikTok, and 360 still use the simple cut-stage segment until the
    short-form creative logic is implemented.
    """

    name = "edit"
    dependencies = ["cut"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint cut output and edit settings."""
        return stable_fingerprint(
            {
                "cut": project.data["stages"]["cut"].get("fingerprint"),
                "settings": project.data["settings"].get(self.name, {}),
                "spherical_landmarks": project.data["settings"].get("spherical_landmarks", {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return edit decision artifacts."""
        return {
            "edit_plan": str(artifact_path(project, "edit_plan.json")),
            "beats": str(artifact_path(project, "beats.json")),
        }

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write the edit plan consumed by export."""
        progress_callback(10, t("analyzing_rhythm"))
        coverage = load_coverage(project)
        platform = str(coverage.get("platform") or "youtube")
        if platform != "youtube":
            plan = _simple_plan(coverage, project.data.get("settings", {}))
            beats = {"stage": self.name, "platform": platform, "beats_sec": [], "bars_sec": [], "sections_sec": [], "tempo": None, "placeholder_short_form": platform != "360"}
        else:
            beats = _load_or_analyze_beats(project, coverage, progress_callback)
            progress_callback(55, t("choosing_cameras"))
            plan = _youtube_multicam_plan(coverage, beats, project.data.get("settings", {}))
        write_artifact_json(artifact_path(project, "beats.json"), beats)
        write_artifact_json(artifact_path(project, "edit_plan.json"), plan)
        progress_callback(100, t("edit_plan_ready"))
        return self.outputs(project)


def load_edit_plan(project: Project) -> dict[str, Any]:
    """Load edit_plan.json."""
    with artifact_path(project, "edit_plan.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_or_analyze_beats(project: Project, coverage: dict[str, Any], progress_callback: ProgressCallback) -> dict[str, Any]:
    path = artifact_path(project, "beats.json")
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("fingerprint") == _beat_fingerprint(project, coverage):
            progress_callback(45, t("rhythm_cached"))
            return cached
    master = project.data.get("inputs", {}).get("master")
    if not master:
        raise ValueError(t("missing_master_for_edit"))
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    duration = max(1.0, float(window.get("duration_sec") or 1.0))
    try:
        import librosa

        y, sr = librosa.load(str(Path(master["path"])), sr=22050, mono=True, offset=start, duration=duration)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        local_beats = [float(value) for value in librosa.frames_to_time(beat_frames, sr=sr)]
        beat_times = [round(start + value, 3) for value in local_beats]
        section_times = [round(start + value, 3) for value in estimate_section_changes(y, sr, local_beats)]
        tempo_value = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
    except Exception:
        beat_times = _fallback_beats(start, duration)
        section_times = []
        tempo_value = None
    if len(beat_times) < 2:
        beat_times = _fallback_beats(start, duration)
    bars = estimate_bar_starts(beat_times, start, duration)
    progress_callback(45, t("rhythm_ready"))
    return {
        "stage": "edit",
        "platform": "youtube",
        "fingerprint": _beat_fingerprint(project, coverage),
        "window_start_sec": start,
        "window_duration_sec": duration,
        "tempo": tempo_value,
        "beats_sec": beat_times,
        "bars_sec": bars,
        "sections_sec": section_times,
    }


def _beat_fingerprint(project: Project, coverage: dict[str, Any]) -> str:
    return stable_fingerprint({"master": project.data.get("inputs", {}).get("master"), "window": coverage.get("window")})


def _fallback_beats(start: float, duration: float) -> list[float]:
    step = 0.5
    count = int(duration / step) + 1
    return [round(start + index * step, 3) for index in range(count + 1)]


def _youtube_multicam_plan(coverage: dict[str, Any], beats: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    end = start + max(1.0, float(window.get("duration_sec") or 1.0))
    bar_times = [float(value) for value in (beats.get("bars_sec") or []) if start <= float(value) <= end]
    if not bar_times or bar_times[0] > start:
        bar_times.insert(0, start)
    if bar_times[-1] < end:
        bar_times.append(end)
    section_times = [float(value) for value in (beats.get("sections_sec") or []) if start < float(value) < end]

    sources = coverage.get("sources") or []
    if not sources and coverage.get("segments"):
        sources = coverage["segments"]
    segments: list[dict[str, Any]] = []
    gaps: list[dict[str, float]] = []
    previous_source: str | None = None
    usage_counts: dict[str, int] = {}
    selection_stats = _selection_stats_template(sources, start, end)
    project_settings = settings or {}
    edit_settings = project_settings.get("edit") if "edit" in project_settings else project_settings
    spherical_landmarks = migrate_spherical_landmarks(project_settings.get("spherical_landmarks") or {})
    role_weights = _camera_role_weights(edit_settings)
    bar_index = 0
    segment_index = 0
    while bar_index < len(bar_times) - 1:
        bars_per_segment = _bars_for_segment(segment_index, bar_times, section_times, bar_index)
        max_bar_index = min(len(bar_times) - 1, bar_index + MAX_BARS_PER_SEGMENT)
        next_index = min(max_bar_index, bar_index + bars_per_segment)
        section_index = _reachable_section_index(bar_times, section_times, bar_index, next_index)
        if section_index is not None:
            next_index = min(section_index, max_bar_index)
        while next_index > bar_index + 1 and bar_times[next_index] - bar_times[bar_index] > MAX_SEGMENT_SEC:
            next_index -= 1
        while next_index < max_bar_index and bar_times[next_index] - bar_times[bar_index] < MIN_SEGMENT_SEC:
            next_index += 1
        segment_start = float(bar_times[bar_index])
        segment_end = min(end, float(bar_times[next_index]))
        if segment_end <= segment_start:
            break
        available = _covering_sources(sources, segment_start, segment_end)
        if not available:
            gaps.append({"start_sec": round(segment_start, 3), "end_sec": round(segment_end, 3)})
            bar_index = next_index
            continue
        for source in available:
            stats = selection_stats.setdefault(_source_id(source), _selection_stats_for_source(source, start, end))
            stats["eligible_segments"] += 1
            stats["eligible_seconds"] += segment_end - segment_start
        source = _choose_source(available, previous_source, usage_counts, selection_stats, role_weights)
        previous_source = _source_id(source)
        usage_counts[previous_source] = usage_counts.get(previous_source, 0) + 1
        chosen_stats = selection_stats.setdefault(previous_source, _selection_stats_for_source(source, start, end))
        chosen_stats["chosen_segments"] += 1
        chosen_stats["chosen_seconds"] += segment_end - segment_start
        segment = _segment_from_source(source, segment_start, segment_end, window.get("title") or t("full_video"))
        if _source_role(source) == "360":
            current_usage = _spherical_shot_usage(segments)
            available_shots = _available_spherical_shots(spherical_landmarks)
            include_planet = current_usage.get("Planeta", 0) == 0 and sum(current_usage.values()) >= 5
            shot = _next_weighted_spherical_shot(available_shots, _spherical_type_usage(segments), include_planet=include_planet)
            if shot:
                segment["spherical_shot"] = shot
        segments.append(segment)
        bar_index = next_index
        segment_index += 1

    warnings = list(coverage.get("warnings") or [])
    if gaps:
        warnings.extend([t("no_video_between", start=_fmt_time(gap["start_sec"]), end=_fmt_time(gap["end_sec"])) for gap in gaps])
    usage: dict[str, int] = {}
    for segment in segments:
        usage[Path(str(segment.get("clip_path"))).name] = usage.get(Path(str(segment.get("clip_path"))).name, 0) + 1
    return {
        "stage": "edit",
        "platform": "youtube",
        "title": window.get("title") or t("full_video"),
        "real_edit_logic": "youtube beat-aligned multicam v1",
        "warnings": warnings,
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "selection_diagnostics": _finalize_selection_stats(selection_stats),
        "gaps": gaps,
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": usage,
        "spherical_shot_usage": _spherical_shot_usage(segments),
        "segments": segments,
    }


def _simple_plan(coverage: dict[str, Any], project_settings: dict[str, Any] | None = None) -> dict[str, Any]:
    segments = _short_form_segments_from_best_coverage(coverage)
    platform = coverage.get("platform") or "youtube"
    if platform == "360":
        segments = build_spherical_shot_segments(coverage.get("segments") or segments, (project_settings or {}).get("spherical_landmarks") or {})
        usage = _spherical_shot_usage(segments)
    else:
        usage = {}
    return {
        "stage": "edit",
        "platform": platform,
        "placeholder_logic": "short-form middle excerpt; multicam/highlight logic pending" if platform != "360" else None,
        "real_edit_logic": "360 virtual camera shot rotation from manual landmark map" if platform == "360" else None,
        "warnings": coverage.get("warnings") or [],
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "gaps": [],
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": _camera_usage(segments),
        "spherical_shot_usage": usage,
        "segments": segments,
    }


def build_spherical_shot_segments(base_segments: list[dict[str, Any]], landmarks: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Split 360 source coverage into named virtual-camera holds."""
    if not base_segments:
        return []
    shots = _available_spherical_shots(migrate_spherical_landmarks(landmarks or {}))
    if not shots:
        return list(base_segments)
    total_duration = sum(max(0.0, float(segment.get("duration_sec") or 0.0)) for segment in base_segments)
    planet_budget = 1 if total_duration >= 18.0 else 0
    planet_after = total_duration * 0.58
    elapsed = 0.0
    usage: dict[str, int] = {}
    output: list[dict[str, Any]] = []
    for base in base_segments:
        remaining = max(0.0, float(base.get("duration_sec") or 0.0))
        local = 0.0
        while remaining > 0.001:
            hold = min(MAX_SEGMENT_SEC, remaining)
            if remaining - hold > 0.001 and remaining - hold < MIN_SEGMENT_SEC:
                hold = max(MIN_SEGMENT_SEC, remaining / 2.0)
            shot = _next_weighted_spherical_shot(shots, usage, include_planet=False)
            if planet_budget > 0 and elapsed <= planet_after < elapsed + hold and any(item.get("type") == "planet" for item in shots):
                shot = dict(next(item for item in shots if item.get("type") == "planet"))
                shot["yaw_end"] = _landmark_yaw(float(shot.get("yaw") or 0.0) + _landmark_weight(shot, "spin_deg_per_sec", 22.0) * max(0.0, hold), 0.0)
                planet_budget -= 1
            if not shot:
                break
            usage[str(shot.get("type"))] = usage.get(str(shot.get("type")), 0) + 1
            segment = {
                **base,
                "clip_start_sec": round(float(base.get("clip_start_sec") or 0.0) + local, 6),
                "master_start_sec": round(float(base.get("master_start_sec") or 0.0) + local, 6),
                "duration_sec": round(hold, 6),
                "spherical_shot": shot,
            }
            output.append(segment)
            elapsed += hold
            local += hold
            remaining -= hold
    return output


def migrate_spherical_landmarks(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Normalize legacy yaw-only landmark settings to the current per-shot schema."""
    if not isinstance(raw, dict):
        return {}
    migrated: dict[str, dict[str, float]] = {}
    for shot_type, (legacy_key, _label, default_fov) in SPHERICAL_LANDMARKS.items():
        source = raw.get(shot_type)
        if source is None and legacy_key in raw:
            source = {"yaw": raw.get(legacy_key)}
        if not isinstance(source, dict):
            continue
        yaw = _landmark_yaw(source.get("yaw"), None)
        if yaw is None:
            continue
        migrated[shot_type] = {
            "yaw": yaw,
            "pitch": _landmark_weight(source, "pitch", 0.0),
            "fov": _landmark_weight(source, "fov", default_fov),
            "weight": max(0.0, _landmark_weight(source, "weight", 1.0)),
        }
    return migrated


def _available_spherical_shots(landmarks: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for shot_type in SPHERICAL_SHOT_ORDER:
        key, label, default_fov = SPHERICAL_LANDMARKS[shot_type]
        data = landmarks.get(shot_type)
        if data is None and shot_type == "full_stage":
            data = {"yaw": 0.0, "pitch": 0.0, "fov": default_fov, "weight": 1.0}
        if not data:
            continue
        yaw = _landmark_yaw(data.get("yaw"), None)
        if yaw is None:
            continue
        weight = max(0.0, _landmark_weight(data, "weight", 1.0))
        if weight <= 0.0:
            continue
        shot = {
            "type": shot_type,
            "label": label,
            "yaw": yaw,
            "pitch": _landmark_weight(data, "pitch", 0.0),
            "fov": _landmark_weight(data, "fov", default_fov),
            "weight": weight,
            "transition_sec": SPHERICAL_PAN_SEC,
        }
        if shot_type == "planet":
            shot["spin_deg_per_sec"] = 18.0
        shots.append(shot)
    return shots


def _next_weighted_spherical_shot(shots: list[dict[str, Any]], usage: dict[str, int], include_planet: bool = False) -> dict[str, Any] | None:
    candidates = [
        shot
        for shot in shots
        if float(shot.get("weight") or 0.0) > 0.0 and (include_planet or shot.get("type") != "planet")
    ]
    if not candidates:
        return None
    return dict(
        sorted(
            candidates,
            key=lambda shot: (
                usage.get(str(shot.get("type")), 0) / max(0.001, float(shot.get("weight") or 1.0)),
                usage.get(str(shot.get("type")), 0),
                str(shot.get("type")),
            ),
        )[0]
    )

def _landmark_yaw(value: Any, fallback: float | None) -> float | None:
    try:
        return float(value) % 360.0
    except (TypeError, ValueError):
        return fallback


def _landmark_weight(data: dict[str, Any], key: str, fallback: float) -> float:
    try:
        return float(data.get(key, fallback))
    except (TypeError, ValueError):
        return fallback


def _spherical_shot_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        shot = segment.get("spherical_shot") or {}
        label = str(shot.get("label") or shot.get("type") or "").strip()
        if label:
            usage[label] = usage.get(label, 0) + 1
    return usage


def _spherical_type_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        shot = segment.get("spherical_shot") or {}
        shot_type = str(shot.get("type") or "").strip()
        if shot_type:
            usage[shot_type] = usage.get(shot_type, 0) + 1
    return usage


def estimate_bar_starts(beat_times: list[float], start: float, duration: float) -> list[float]:
    """Estimate downbeats by grouping beats in fours."""
    end = start + duration
    beats = [float(value) for value in beat_times if start <= float(value) <= end]
    if not beats:
        return [start, end]
    bars = [start]
    best_phase = 0
    if len(beats) >= 8:
        gaps = [beats[index + 4] - beats[index] for index in range(len(beats) - 4)]
        best_phase = min(range(4), key=lambda phase: abs((gaps[phase] if phase < len(gaps) else 2.0) - np_median(gaps)))
    for index, beat in enumerate(beats):
        if index % 4 == best_phase and beat > start + 0.05:
            bars.append(round(beat, 3))
    if bars[-1] < end:
        bars.append(end)
    return sorted(set(bars))


def estimate_section_changes(y: Any, sr: int, beat_times: list[float]) -> list[float]:
    """Estimate section boundaries from spectral novelty peaks."""
    if not beat_times:
        return []
    try:
        import librosa
        import numpy as np

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        novelty = np.linalg.norm(np.diff(chroma, axis=1), axis=0)
        if novelty.size == 0:
            return []
        threshold = float(np.percentile(novelty, 90))
        frame_times = librosa.frames_to_time(np.arange(novelty.size), sr=sr)
        candidates = [float(frame_times[index]) for index, value in enumerate(novelty) if float(value) >= threshold]
        sections = []
        for candidate in candidates:
            nearest = min(beat_times, key=lambda beat: abs(beat - candidate))
            if not sections or abs(nearest - sections[-1]) > 12:
                sections.append(round(nearest, 3))
        return sections
    except Exception:
        return []


def np_median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _reachable_section_index(bar_times: list[float], section_times: list[float], current_index: int, proposed_index: int) -> int | None:
    current = bar_times[current_index]
    proposed = bar_times[proposed_index]
    for section in section_times:
        if current + MIN_SEGMENT_SEC <= section <= min(proposed + 2.0, current + MAX_SEGMENT_SEC):
            nearest = min(range(current_index + 1, len(bar_times)), key=lambda index: abs(bar_times[index] - section))
            return nearest
    return None


def _bars_for_segment(segment_index: int, bar_times: list[float], section_times: list[float], bar_index: int) -> int:
    current = bar_times[bar_index]
    near_section = any(current < section <= current + MAX_SEGMENT_SEC for section in section_times)
    if near_section:
        return 1
    if segment_index % 5 in {1, 4}:
        return 1
    return MAX_BARS_PER_SEGMENT


def _short_form_segments_from_best_coverage(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    segments = coverage.get("segments") or []
    sources = coverage.get("sources") or []
    if not segments:
        return []
    platform = str(coverage.get("platform") or "")
    target = 45.0 if platform == "instagram" else 20.0 if platform == "tiktok" else float(segments[0].get("duration_sec") or 1.0)
    best = max(sources or segments, key=lambda source: float(source.get("confidence") or 0.0) * max(1.0, float(source.get("duration_sec") or 0.0)))
    duration = min(target, float(best.get("duration_sec") or target))
    clip_duration = float(best.get("duration_sec") or duration)
    clip_start = max(0.0, (clip_duration - duration) / 2)
    offset = float(best.get("offset_sec") or best.get("clip_offset_sec") or 0.0)
    return [
        {
            "title": (coverage.get("window") or {}).get("title") or best.get("title") or t("video_title"),
            "clip_path": best["path"] if "path" in best else best["clip_path"],
            "source_path": best.get("source_path") or best.get("path") or best.get("clip_path"),
            "clip_start_sec": clip_start,
            "master_start_sec": offset + clip_start,
            "duration_sec": duration,
            "clip_offset_sec": offset,
            "confidence": float(best.get("confidence") or 0.0),
            "filename": best.get("filename") or Path(str(best.get("path") or best.get("clip_path"))).name,
            "projection": best.get("projection"),
        }
    ]


def _covering_sources(sources: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    available = []
    for source in sources:
        offset = float(source.get("offset_sec") or 0.0)
        duration = float(source.get("duration_sec") or 0.0)
        if offset <= start and offset + duration >= end:
            available.append(source)
    return available


def _choose_source(
    sources: list[dict[str, Any]],
    previous_source: str | None,
    usage_counts: dict[str, int] | None = None,
    selection_stats: dict[str, dict[str, Any]] | None = None,
    role_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    usage_counts = usage_counts or {}
    candidates = [source for source in sources if _source_id(source) != previous_source] if len(sources) > 1 else sources
    if not candidates:
        candidates = sources
    selection_stats = selection_stats or {}
    role_weights = role_weights or DEFAULT_CAMERA_ROLE_WEIGHTS

    def score(source: dict[str, Any]) -> tuple[float, int, float, str]:
        role = _source_role(source)
        target_share = max(0.05, float(role_weights.get(role, role_weights.get("handheld", 0.3))))
        chosen_seconds = float((selection_stats.get(_source_id(source)) or {}).get("chosen_seconds") or 0.0)
        return (chosen_seconds / target_share, usage_counts.get(_source_id(source), 0), -float(source.get("confidence") or 0.0), _source_id(source))

    return sorted(candidates, key=score)[0]


def _segment_from_source(source: dict[str, Any], start: float, end: float, title: str) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    start = _round_to_frame(start)
    end = max(start + 1.0 / EDIT_FPS, _round_to_frame(end))
    return {
        "title": title,
        "clip_path": source["path"],
        "source_path": source.get("source_path") or source["path"],
        "clip_start_sec": max(0.0, start - offset),
        "master_start_sec": start,
        "duration_sec": max(1.0 / EDIT_FPS, end - start),
        "clip_offset_sec": offset,
        "confidence": float(source.get("confidence") or 0.0),
        "filename": source.get("filename") or Path(str(source["path"])).name,
        "projection": source.get("projection"),
    }


def _camera_role_weights(settings: dict[str, Any] | None) -> dict[str, float]:
    raw = (settings or {}).get("camera_role_weights") or {}
    weights = {**DEFAULT_CAMERA_ROLE_WEIGHTS}
    for key in weights:
        try:
            weights[key] = max(0.01, float(raw.get(key, weights[key])))
        except (TypeError, ValueError):
            pass
    total = sum(weights.values()) or 1.0
    return {key: value / total for key, value in weights.items()}


def _source_role(source: dict[str, Any]) -> str:
    projection = str(source.get("projection") or "").lower()
    filename = str(source.get("filename") or source.get("path") or source.get("source_path") or "").lower()
    if projection in {"equirect", "raw_insv"} or filename.endswith(".insv") or "360" in filename:
        return "360"
    if "iphone" in filename or filename.endswith(".mov"):
        return "fixed_rear"
    return "handheld"


def _round_to_frame(seconds: float, fps: float = EDIT_FPS) -> float:
    return round(round(float(seconds) * fps) / fps, 6)


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("source_path") or source.get("path") or source.get("filename"))


def _selection_stats_template(sources: list[dict[str, Any]], window_start: float, window_end: float) -> dict[str, dict[str, Any]]:
    return {_source_id(source): _selection_stats_for_source(source, window_start, window_end) for source in sources}


def _selection_stats_for_source(source: dict[str, Any], window_start: float, window_end: float) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    duration = float(source.get("duration_sec") or 0.0)
    covered_seconds = max(0.0, min(window_end, offset + duration) - max(window_start, offset))
    return {
        "filename": source.get("filename") or Path(str(source.get("path") or source.get("source_path") or "")).name,
        "path": source.get("path"),
        "source_path": source.get("source_path"),
        "confidence": float(source.get("confidence") or 0.0),
        "offset_sec": offset,
        "duration_sec": duration,
        "covered_seconds": covered_seconds,
        "eligible_segments": 0,
        "eligible_seconds": 0.0,
        "chosen_segments": 0,
        "chosen_seconds": 0.0,
    }


def _finalize_selection_stats(stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for item in stats.values():
        entry = dict(item)
        entry["covered_seconds"] = round(float(entry.get("covered_seconds") or 0.0), 3)
        entry["eligible_seconds"] = round(float(entry.get("eligible_seconds") or 0.0), 3)
        entry["chosen_seconds"] = round(float(entry.get("chosen_seconds") or 0.0), 3)
        if entry["eligible_segments"] and not entry["chosen_segments"]:
            entry["selection_reason"] = "eligible but not selected by camera rotation"
        elif not entry["eligible_segments"]:
            entry["selection_reason"] = "not covering selected edit intervals"
        else:
            entry["selection_reason"] = f"chosen {entry['chosen_segments']} of {entry['eligible_segments']} eligible segments"
        finalized.append(entry)
    return sorted(finalized, key=lambda item: str(item.get("filename") or ""))


def _camera_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        name = Path(str(segment.get("clip_path"))).name
        usage[name] = usage.get(name, 0) + 1
    return usage


def _fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
