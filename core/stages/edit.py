"""Beat-aligned edit decision generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.messages import t
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage

MIN_SEGMENT_SEC = 1.5
MAX_SEGMENT_SEC = 6.0


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
            plan = _simple_plan(coverage)
            beats = {"stage": self.name, "platform": platform, "beats_sec": [], "bars_sec": [], "sections_sec": [], "tempo": None, "placeholder_short_form": platform != "360"}
        else:
            beats = _load_or_analyze_beats(project, coverage, progress_callback)
            progress_callback(55, t("choosing_cameras"))
            plan = _youtube_multicam_plan(coverage, beats)
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


def _youtube_multicam_plan(coverage: dict[str, Any], beats: dict[str, Any]) -> dict[str, Any]:
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
    bar_index = 0
    segment_index = 0
    while bar_index < len(bar_times) - 1:
        bars_per_segment = _bars_for_segment(segment_index, bar_times, section_times, bar_index)
        next_index = min(len(bar_times) - 1, bar_index + bars_per_segment)
        section_index = _reachable_section_index(bar_times, section_times, bar_index, next_index)
        if section_index is not None:
            next_index = section_index
        while next_index > bar_index + 1 and bar_times[next_index] - bar_times[bar_index] > MAX_SEGMENT_SEC:
            next_index -= 1
        while next_index < len(bar_times) - 1 and bar_times[next_index] - bar_times[bar_index] < MIN_SEGMENT_SEC:
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
        source = _choose_source(available, previous_source, usage_counts)
        previous_source = _source_id(source)
        usage_counts[previous_source] = usage_counts.get(previous_source, 0) + 1
        chosen_stats = selection_stats.setdefault(previous_source, _selection_stats_for_source(source, start, end))
        chosen_stats["chosen_segments"] += 1
        chosen_stats["chosen_seconds"] += segment_end - segment_start
        segments.append(_segment_from_source(source, segment_start, segment_end, window.get("title") or t("full_video")))
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
        "segments": segments,
    }


def _simple_plan(coverage: dict[str, Any]) -> dict[str, Any]:
    segments = _short_form_segments_from_best_coverage(coverage)
    platform = coverage.get("platform") or "youtube"
    if platform == "360":
        segments = coverage.get("segments") or segments
    return {
        "stage": "edit",
        "platform": platform,
        "placeholder_logic": "short-form middle excerpt; multicam/highlight logic pending" if platform != "360" else None,
        "real_edit_logic": "360 passthrough full clip with synced master audio" if platform == "360" else None,
        "warnings": coverage.get("warnings") or [],
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "gaps": [],
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": _camera_usage(segments),
        "segments": segments,
    }


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
    return 2


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


def _choose_source(sources: list[dict[str, Any]], previous_source: str | None, usage_counts: dict[str, int] | None = None) -> dict[str, Any]:
    usage_counts = usage_counts or {}
    candidates = [source for source in sources if _source_id(source) != previous_source] if len(sources) > 1 else sources
    if not candidates:
        candidates = sources
    return sorted(candidates, key=lambda source: (usage_counts.get(_source_id(source), 0), -float(source.get("confidence") or 0.0), _source_id(source)))[0]


def _segment_from_source(source: dict[str, Any], start: float, end: float, title: str) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    return {
        "title": title,
        "clip_path": source["path"],
        "source_path": source.get("source_path") or source["path"],
        "clip_start_sec": max(0.0, start - offset),
        "master_start_sec": start,
        "duration_sec": max(0.1, end - start),
        "clip_offset_sec": offset,
        "confidence": float(source.get("confidence") or 0.0),
        "filename": source.get("filename") or Path(str(source["path"])).name,
        "projection": source.get("projection"),
    }


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
