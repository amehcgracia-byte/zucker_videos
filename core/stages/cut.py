"""Simple wizard cut planning stage."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.media_validation import record_is_usable_camera_video
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.sync import load_song_boundaries, load_sync_map, sync_confidence_threshold

LOGGER = logging.getLogger(__name__)


class CutStage(Stage):
    """Plan a minimal single-clip edit for the wizard.

    This is intentionally placeholder creative logic: pick the first clip that
    overlaps the requested song window, or the longest clip when there is no
    songs.json.
    """

    name = "cut"
    dependencies = ["sync"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint sync output and cut settings."""
        return stable_fingerprint(
            {
                "sync": project.data["stages"]["sync"].get("fingerprint"),
                "songs": project.data["inputs"].get("songs"),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the coverage artifact path."""
        return {"coverage": str(artifact_path(project, "coverage.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write a simple coverage plan consumed by export."""
        progress_callback(20, "Leyendo sincronización")
        sync_map = load_sync_map(project) or {}
        selection = _selectable_synced_clips(project, sync_map)
        if not selection["clips"]:
            diagnostics = selection["diagnostics"]
            LOGGER.error("Cut rejected all clips: %s", diagnostics)
            raise ValueError(_diagnostic_error_message(diagnostics))
        wizard = project.data["settings"].get("wizard", {})
        platform = str(wizard.get("platform") or "youtube")
        songs = load_song_boundaries(project)
        song_choice = wizard.get("song_choice")
        window = _selected_window(songs, song_choice, sync_map)
        clip = _first_covering_clip(selection["clips"], window) or _longest_clip(selection["clips"])
        segment = _segment_for_platform(clip, window, platform)
        warnings = selection["warnings"]
        if warnings:
            segment["warnings"] = warnings

        progress_callback(70, "Creando plan simple")
        path = artifact_path(project, "coverage.json")
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "placeholder_logic": "first covering clip; no multicam or highlight scoring yet",
                "platform": platform,
                "song_choice": song_choice,
                "songs": songs,
                "warnings": warnings,
                "clip_diagnostics": selection["diagnostics"],
                "segments": [segment],
            },
        )
        progress_callback(100, "Plan de corte listo")
        return self.outputs(project)


def load_coverage(project: Project) -> dict[str, Any]:
    """Load coverage.json."""
    path = artifact_path(project, "coverage.json")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _selected_window(songs: list[dict[str, Any]], song_choice: Any, sync_map: dict[str, Any]) -> dict[str, Any]:
    if songs and song_choice != "all":
        try:
            song = songs[int(song_choice or 0)]
            start = float(song.get("start_sec") or 0)
            end = song.get("end_sec")
            duration = max(1.0, float(end) - start) if end is not None else 60.0
            return {"title": song.get("title") or "Canción", "start_sec": start, "duration_sec": duration}
        except (IndexError, TypeError, ValueError):
            pass
    duration = float(sync_map.get("master_duration_sec") or 0) or 60.0
    return {"title": "Vídeo completo", "start_sec": 0.0, "duration_sec": duration}


def _selectable_synced_clips(project: Project, sync_map: dict[str, Any]) -> dict[str, Any]:
    records_by_path: dict[str, dict[str, Any]] = {}
    for record in project.data.get("inputs", {}).get("videos", []):
        if record.get("path"):
            records_by_path[record["path"]] = record
        normalized = record.get("normalized") or {}
        if normalized.get("path"):
            records_by_path[normalized["path"]] = record
    threshold = sync_confidence_threshold(project)
    confident: list[dict[str, Any]] = []
    low_confidence: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for clip in (sync_map.get("clips") or {}).values():
        path = clip.get("source_path") or clip.get("path")
        record = records_by_path.get(path)
        if record is None and clip.get("path"):
            record = records_by_path.get(clip["path"])
        confidence = _float_or_zero(clip.get("confidence"))
        valid_video = bool(record and record_is_usable_camera_video(record))
        diagnostic = {
            "filename": clip.get("filename") or Path(str(path or "")).name or "clip",
            "valid_video": valid_video,
            "confidence": confidence,
            "threshold": threshold,
            "low_confidence": bool(clip.get("low_confidence") or confidence < threshold),
            "error": clip.get("error"),
            "no_audio": bool(clip.get("no_audio")),
            "path": clip.get("path"),
            "source_path": clip.get("source_path"),
        }
        diagnostics.append(diagnostic)
        if not valid_video:
            continue
        if clip.get("error") or clip.get("no_audio"):
            continue
        if confidence < threshold or clip.get("low_confidence"):
            low_confidence.append(clip)
            continue
        confident.append(clip)
    if confident:
        return {"clips": confident, "warnings": [], "diagnostics": diagnostics}
    if low_confidence:
        best = max(low_confidence, key=lambda clip: _float_or_zero(clip.get("confidence")))
        warning = "Sincronización dudosa — revisa el resultado"
        LOGGER.warning("Cut falling back to low-confidence clip: %s diagnostics=%s", best.get("filename"), diagnostics)
        return {"clips": [best], "warnings": [warning], "diagnostics": diagnostics}
    return {"clips": [], "warnings": [], "diagnostics": diagnostics}


def _diagnostic_error_message(diagnostics: list[dict[str, Any]]) -> str:
    lines = ["Ninguno de los archivos parece un vídeo de cámara utilizable", "Diagnóstico por clip:"]
    if not diagnostics:
        lines.append("- sin clips en sync_map")
        return "\n".join(lines)
    for item in diagnostics:
        valid = "sí" if item["valid_video"] else "no"
        confidence = f"{item['confidence']:.3f}"
        threshold = f"{item['threshold']:.3f}"
        reason_bits = []
        if item.get("error"):
            reason_bits.append(f"error={item['error']}")
        if item.get("no_audio"):
            reason_bits.append("sin audio")
        if item.get("low_confidence"):
            reason_bits.append("confianza baja")
        reason = f" ({'; '.join(reason_bits)})" if reason_bits else ""
        lines.append(f"- {item['filename']}: vídeo válido={valid}, confianza={confidence}, umbral={threshold}{reason}")
    return "\n".join(lines)


def _first_covering_clip(clips: list[dict[str, Any]], window: dict[str, Any]) -> dict[str, Any] | None:
    start = float(window["start_sec"])
    end = start + float(window["duration_sec"])
    for clip in clips:
        clip_start = float(clip.get("offset_sec") or 0)
        clip_end = clip_start + float(clip.get("duration_sec") or 0)
        if clip_start <= start and clip_end >= min(end, start + 1):
            return clip
    return None


def _longest_clip(clips: list[dict[str, Any]]) -> dict[str, Any]:
    return max(clips, key=lambda clip: float(clip.get("duration_sec") or 0))


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _segment_for_platform(clip: dict[str, Any], window: dict[str, Any], platform: str) -> dict[str, Any]:
    clip_offset = float(clip.get("offset_sec") or 0)
    clip_duration = float(clip.get("duration_sec") or 1)
    window_start = float(window["start_sec"])
    window_duration = float(window["duration_sec"])
    duration = min(window_duration, clip_duration)
    if platform == "instagram":
        duration = min(45.0, duration)
    elif platform == "tiktok":
        duration = min(20.0, duration)
    source_start = max(0.0, window_start - clip_offset)
    if platform in {"instagram", "tiktok"} and clip_duration > duration:
        source_start = max(0.0, (clip_duration - duration) / 2)
    source_start = min(source_start, max(0.0, clip_duration - duration))
    master_start = max(0.0, clip_offset + source_start)
    return {
        "title": window["title"],
        "clip_path": clip["path"],
        "clip_start_sec": source_start,
        "master_start_sec": master_start,
        "duration_sec": max(1.0, duration),
        "clip_offset_sec": clip_offset,
    }
