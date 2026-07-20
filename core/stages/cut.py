"""Coverage planning stage for wizard edits."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.media_validation import record_is_usable_camera_video
from core.messages import t
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.sync import load_song_boundaries, load_sync_map, sync_confidence_threshold

LOGGER = logging.getLogger(__name__)


class CutStage(Stage):
    """Plan usable synced clip coverage for the requested wizard window."""

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
        progress_callback(20, t("reading_sync"))
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

        progress_callback(70, t("building_coverage"))
        path = artifact_path(project, "coverage.json")
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "platform": platform,
                "song_choice": song_choice,
                "songs": songs,
                "window": window,
                "warnings": warnings,
                "excluded_clips": selection["excluded"],
                "clip_diagnostics": selection["diagnostics"],
                "sources": selection["clips"],
                "segments": [segment],
            },
        )
        progress_callback(100, t("coverage_ready"))
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
            return {"title": song.get("title") or t("song_default"), "start_sec": start, "duration_sec": duration}
        except (IndexError, TypeError, ValueError):
            pass
    duration = float(sync_map.get("master_duration_sec") or 0) or 60.0
    return {"title": t("full_video"), "start_sec": 0.0, "duration_sec": duration}


def _selectable_synced_clips(project: Project, sync_map: dict[str, Any]) -> dict[str, Any]:
    records_by_path: dict[str, dict[str, Any]] = {}
    for record in project.data.get("inputs", {}).get("videos", []):
        if record.get("path"):
            records_by_path[record["path"]] = record
        normalized = record.get("normalized") or {}
        if normalized.get("path"):
            records_by_path[normalized["path"]] = record
    threshold = sync_confidence_threshold(project)
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
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
            "unstable_sync": bool(clip.get("unstable_sync")) and not bool(clip.get("manual_override")),
            "manual_override": bool(clip.get("manual_override")),
            "verification": clip.get("verification"),
            "error": clip.get("error"),
            "no_audio": bool(clip.get("no_audio")),
            "path": clip.get("path"),
            "source_path": clip.get("source_path"),
        }
        diagnostics.append(diagnostic)
        reason = _exclusion_reason(diagnostic)
        if reason:
            excluded.append({"filename": diagnostic["filename"], "reason": reason, "diagnostic": diagnostic})
            continue
        selected.append(clip)
    return {"clips": selected, "warnings": [], "diagnostics": diagnostics, "excluded": excluded}


def _exclusion_reason(diagnostic: dict[str, Any]) -> str | None:
    if not diagnostic["valid_video"]:
        return t("not_usable_camera_video")
    if diagnostic.get("error"):
        return str(diagnostic["error"])
    if diagnostic.get("no_audio"):
        return t("no_sync_audio")
    if diagnostic.get("unstable_sync"):
        verification = diagnostic.get("verification") or {}
        delta = verification.get("delta_sec")
        if isinstance(delta, (int, float)):
            return t("unstable_sync_detail", ms=delta * 1000)
        return t("unstable_sync")
    if diagnostic.get("low_confidence") and not diagnostic.get("manual_override"):
        return t("low_confidence_excluded")
    return None


def _diagnostic_error_message(diagnostics: list[dict[str, Any]]) -> str:
    lines = [t("no_usable_camera_video"), t("clip_diagnostics")]
    if not diagnostics:
        lines.append("- no clips in sync_map")
        return "\n".join(lines)
    for item in diagnostics:
        valid = t("valid_yes") if item["valid_video"] else t("valid_no")
        confidence = f"{item['confidence']:.3f}"
        threshold = f"{item['threshold']:.3f}"
        reason_bits = []
        if item.get("error"):
            reason_bits.append(f"error={item['error']}")
        if item.get("no_audio"):
            reason_bits.append(t("no_audio"))
        if item.get("low_confidence"):
            reason_bits.append(t("low_confidence"))
        if item.get("unstable_sync"):
            reason_bits.append(t("unstable_sync"))
        reason = f" ({'; '.join(reason_bits)})" if reason_bits else ""
        lines.append(f"- {item['filename']}: valid video={valid}, confidence={confidence}, threshold={threshold}{reason}")
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
