"""Beat-aligned edit decision generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage


class EditStage(Stage):
    """Build wizard edit decisions from coverage.

    YouTube uses a first-pass real multicam plan with beat-aligned cuts.
    Instagram and TikTok still use the simple cut-stage segment until the
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
        progress_callback(10, "Analizando ritmo")
        coverage = load_coverage(project)
        platform = str(coverage.get("platform") or "youtube")
        if platform != "youtube":
            plan = _simple_plan(coverage)
            beats = {"stage": self.name, "platform": platform, "beats_sec": [], "tempo": None, "placeholder_short_form": True}
        else:
            beats = _load_or_analyze_beats(project, coverage, progress_callback)
            progress_callback(55, "Eligiendo cámaras")
            plan = _youtube_multicam_plan(coverage, beats)
        write_artifact_json(artifact_path(project, "beats.json"), beats)
        write_artifact_json(artifact_path(project, "edit_plan.json"), plan)
        progress_callback(100, "Plan de edición listo")
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
            progress_callback(45, "Ritmo en caché")
            return cached
    master = project.data.get("inputs", {}).get("master")
    if not master:
        raise ValueError("Falta el audio master")
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    duration = max(1.0, float(window.get("duration_sec") or 1.0))
    try:
        import librosa

        y, sr = librosa.load(str(Path(master["path"])), sr=22050, mono=True, offset=start, duration=duration)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        beat_times = [round(start + float(value), 3) for value in librosa.frames_to_time(beat_frames, sr=sr)]
        tempo_value = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
    except Exception:
        beat_times = _fallback_beats(start, duration)
        tempo_value = None
    if len(beat_times) < 2:
        beat_times = _fallback_beats(start, duration)
    progress_callback(45, "Ritmo listo")
    return {
        "stage": "edit",
        "platform": "youtube",
        "fingerprint": _beat_fingerprint(project, coverage),
        "window_start_sec": start,
        "window_duration_sec": duration,
        "tempo": tempo_value,
        "beats_sec": beat_times,
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
    beat_times = [value for value in (beats.get("beats_sec") or []) if start <= float(value) <= end]
    if not beat_times or beat_times[0] > start:
        beat_times.insert(0, start)
    if beat_times[-1] < end:
        beat_times.append(end)

    sources = coverage.get("sources") or []
    segments: list[dict[str, Any]] = []
    gaps: list[dict[str, float]] = []
    previous_source: str | None = None
    beat_index = 0
    segment_index = 0
    while beat_index < len(beat_times) - 1:
        beats_per_segment = 8 if segment_index % 4 else 4
        next_index = min(len(beat_times) - 1, beat_index + beats_per_segment)
        while next_index > beat_index + 1 and beat_times[next_index] - beat_times[beat_index] > 15.0:
            next_index -= 1
        while next_index < len(beat_times) - 1 and beat_times[next_index] - beat_times[beat_index] < 2.0:
            next_index += 1
        segment_start = float(beat_times[beat_index])
        segment_end = min(end, float(beat_times[next_index]))
        if segment_end <= segment_start:
            break
        available = _covering_sources(sources, segment_start, segment_end)
        if not available:
            gaps.append({"start_sec": round(segment_start, 3), "end_sec": round(segment_end, 3)})
            beat_index = next_index
            continue
        source = _choose_source(available, previous_source)
        previous_source = _source_id(source)
        segments.append(_segment_from_source(source, segment_start, segment_end, window.get("title") or "Vídeo completo"))
        beat_index = next_index
        segment_index += 1

    if not segments and coverage.get("segments"):
        segments = coverage["segments"]
    warnings = list(coverage.get("warnings") or [])
    if gaps:
        warnings.extend([f"Sin vídeo entre {_fmt_time(gap['start_sec'])}–{_fmt_time(gap['end_sec'])}" for gap in gaps])
    usage: dict[str, int] = {}
    for segment in segments:
        usage[Path(str(segment.get("clip_path"))).name] = usage.get(Path(str(segment.get("clip_path"))).name, 0) + 1
    return {
        "stage": "edit",
        "platform": "youtube",
        "title": window.get("title") or "Vídeo completo",
        "real_edit_logic": "youtube beat-aligned multicam v1",
        "warnings": warnings,
        "gaps": gaps,
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": usage,
        "segments": segments,
    }


def _simple_plan(coverage: dict[str, Any]) -> dict[str, Any]:
    segments = coverage.get("segments") or []
    return {
        "stage": "edit",
        "platform": coverage.get("platform") or "youtube",
        "placeholder_logic": "short-form middle excerpt; multicam/highlight logic pending",
        "warnings": coverage.get("warnings") or [],
        "gaps": [],
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": _camera_usage(segments),
        "segments": segments,
    }


def _covering_sources(sources: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    available = []
    for source in sources:
        offset = float(source.get("offset_sec") or 0.0)
        duration = float(source.get("duration_sec") or 0.0)
        if offset <= start and offset + duration >= end:
            available.append(source)
    return available


def _choose_source(sources: list[dict[str, Any]], previous_source: str | None) -> dict[str, Any]:
    sorted_sources = sorted(sources, key=lambda source: float(source.get("confidence") or 0.0), reverse=True)
    if previous_source and len(sorted_sources) > 1:
        for source in sorted_sources:
            if _source_id(source) != previous_source:
                return source
    return sorted_sources[0]


def _segment_from_source(source: dict[str, Any], start: float, end: float, title: str) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    return {
        "title": title,
        "clip_path": source["path"],
        "clip_start_sec": max(0.0, start - offset),
        "master_start_sec": start,
        "duration_sec": max(0.1, end - start),
        "clip_offset_sec": offset,
        "confidence": float(source.get("confidence") or 0.0),
        "filename": source.get("filename") or Path(str(source["path"])).name,
    }


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("source_path") or source.get("path") or source.get("filename"))


def _camera_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        name = Path(str(segment.get("clip_path"))).name
        usage[name] = usage.get(name, 0) + 1
    return usage


def _fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
