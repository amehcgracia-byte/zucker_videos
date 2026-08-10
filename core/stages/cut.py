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

REEL_DEFAULT_DURATION_SEC = 30.0
REEL_MIN_DURATION_SEC = 20.0
REEL_MAX_DURATION_SEC = 60.0


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
        wizard = project.data["settings"].get("wizard", {})
        platform = str(wizard.get("platform") or "youtube")
        selection = _selectable_synced_clips(project, sync_map, allow_unsynced=platform in {"360", "reel"})
        if platform not in {"360", "reel"}:
            # Keep readable clips with best-scoring offsets available to the
            # multicam chooser even when their correlation is below the hard
            # threshold.  Otherwise one weak camera silently disappears from
            # a mixed edit and its configured weight can never be honoured.
            fallback = _selectable_synced_clips(project, sync_map, allow_unsynced=True)
            fallback_clips = [
                clip for clip in fallback["clips"]
                if not clip.get("error") and not clip.get("no_audio")
            ]
            if fallback_clips and len(fallback_clips) > len(selection["clips"]):
                fallback["clips"] = fallback_clips
                selection = fallback
                selection["warnings"].append(
                    "Some clips did not meet the sync confidence threshold. Proceeding anyway with their best-scoring offsets; "
                    "sync may be imprecise. Check that the selected master matches this footage and that the camera audio is strong enough."
                )
        if not selection["clips"]:
            diagnostics = selection["diagnostics"]
            LOGGER.error("Cut rejected all clips: %s", diagnostics)
            raise ValueError(_diagnostic_error_message(diagnostics))
        songs = load_song_boundaries(project)
        song_choice = wizard.get("song_choice")
        window = _selected_window(songs, song_choice, sync_map, wizard)
        window, coverage_warnings = _tighten_window_to_video_coverage(window, selection["clips"])
        if platform == "reel":
            reel_duration = max(REEL_MIN_DURATION_SEC, min(REEL_MAX_DURATION_SEC, float(wizard.get("reel_duration_sec") or REEL_DEFAULT_DURATION_SEC)))
            # The Reel music bed is the exact master Start/End selection. The
            # duration control limits that selection when it is longer, but
            # never silently relocates it to an automatically detected energy
            # peak.
            if float(window.get("duration_sec") or 0.0) > reel_duration:
                window = {**window, "duration_sec": reel_duration, "trim_end_sec": float(window["start_sec"]) + reel_duration}
        warnings_360: list[str] = []
        if platform == "360":
            clip = _select_360_clip(selection["clips"])
            if not clip:
                raise ValueError("No registered 360 clip is available for 360 export")
            # Passthrough mode must trim to the song's own Start/End range, not
            # the 360 camera's own recording extent (which previously silently
            # replaced the requested window entirely -- if the camera started
            # later or stopped earlier than the chosen song range, the "song"
            # window shrank to match, producing a truncated export).
            segment = _segment_for_360(clip, window)
            if segment["duration_sec"] <= 0:
                raise ValueError(
                    "The registered 360 clip does not cover any of the selected song range "
                    f"(song {window['start_sec']:.1f}-{window['start_sec'] + window['duration_sec']:.1f}s, "
                    f"clip covers {segment['clip_offset_sec']:.1f}-{segment['clip_offset_sec'] + float(clip.get('duration_sec') or 0):.1f}s)"
                )
            if segment["duration_sec"] < window["duration_sec"] - 0.5:
                warnings_360.append(
                    f"360 clip only covers {segment['duration_sec']:.1f}s of the {window['duration_sec']:.1f}s song range; export was trimmed to what the camera actually recorded."
                )
            if float(clip.get("confidence") or 0.0) < sync_confidence_threshold(project) or clip.get("low_confidence") or clip.get("unstable_sync"):
                warnings_360.append(
                    "360 sync confidence low — audio alignment may be approximate."
                )
            window = {**window, "start_sec": segment["master_start_sec"], "duration_sec": segment["duration_sec"]}
        else:
            clip = _first_covering_clip(selection["clips"], window) or _longest_clip(selection["clips"])
            segment = _segment_for_platform(clip, window, platform)
        warnings = selection["warnings"] + coverage_warnings + warnings_360
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


def _selected_window(songs: list[dict[str, Any]], song_choice: Any, sync_map: dict[str, Any], wizard: dict[str, Any] | None = None) -> dict[str, Any]:
    wizard = wizard or {}
    trim = wizard.get("audio_trim") or {}
    if songs and song_choice != "all":
        try:
            song = songs[int(song_choice or 0)]
            start = float(song.get("start_sec") or 0)
            end = song.get("end_sec")
            duration = max(1.0, float(end) - start) if end is not None else 60.0
            return _trimmed_window({"title": song.get("title") or t("song_default"), "start_sec": start, "duration_sec": duration}, trim)
        except (IndexError, TypeError, ValueError):
            pass
    duration = float(sync_map.get("master_duration_sec") or 0) or 60.0
    return _trimmed_window({"title": t("full_video"), "start_sec": 0.0, "duration_sec": duration}, trim)


def _trimmed_window(window: dict[str, Any], trim: dict[str, Any]) -> dict[str, Any]:
    base_start = float(window.get("start_sec") or 0.0)
    base_end = base_start + max(1.0, float(window.get("duration_sec") or 1.0))
    start = _optional_float(trim.get("start_sec"), base_start)
    end = _optional_float(trim.get("end_sec"), base_end)
    start = max(base_start, min(start, base_end - 1.0))
    end = max(start + 1.0, min(end, base_end))
    return {**window, "start_sec": start, "duration_sec": end - start, "trim_start_sec": start, "trim_end_sec": end}


def _tighten_window_to_video_coverage(window: dict[str, Any], clips: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Keep the requested audio trim inside the union of usable video coverage.

    The requested trim remains the outer bound: this function only moves the
    effective start forward or end backward when no registered synced video
    exists there.  Internal gaps remain visible to the edit planner.
    """
    requested_start = float(window.get("start_sec") or 0.0)
    requested_end = requested_start + max(0.0, float(window.get("duration_sec") or 0.0))
    ranges = []
    for clip in clips:
        start = float(clip.get("offset_sec") or 0.0)
        end = start + max(0.0, float(clip.get("duration_sec") or 0.0))
        if end > requested_start and start < requested_end:
            ranges.append((start, end))
    if not ranges:
        return window, []
    coverage_start = max(requested_start, min(start for start, _end in ranges))
    coverage_end = min(requested_end, max(end for _start, end in ranges))
    if coverage_end <= coverage_start + 0.001:
        return window, []
    warnings: list[str] = []
    if coverage_start > requested_start + 0.001:
        warnings.append(
            f"Audio trimmed to start at {_fmt_time(coverage_start)} where video coverage begins."
        )
    if coverage_end < requested_end - 0.001:
        warnings.append(
            f"Audio trimmed to end at {_fmt_time(coverage_end)} where video coverage ends; the remaining audio tail has no footage."
        )
    if not warnings:
        return window, []
    effective = {
        **window,
        "requested_start_sec": requested_start,
        "requested_end_sec": requested_end,
        "video_coverage_start_sec": coverage_start,
        "video_coverage_end_sec": coverage_end,
        "start_sec": coverage_start,
        "duration_sec": coverage_end - coverage_start,
        "trim_start_sec": coverage_start,
        "trim_end_sec": coverage_end,
    }
    return effective, warnings


def _fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def _optional_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _pick_energetic_window(master_path: str, window: dict[str, Any], target_duration: float) -> dict[str, Any]:
    """Narrow the song window to its highest-energy target_duration-second
    stretch (reel mode), reusing RMS energy over the master track as a cheap,
    dependency-light proxy for "high energy" -- a full chorus/drop detector
    would be a much larger undertaking than a short-form auto-highlight
    needs. Falls back to the middle of the range if analysis fails for any
    reason (missing librosa, unreadable file, etc.) so reel export still
    works, just without the highlight-picking.
    """
    start = float(window.get("start_sec") or 0.0)
    duration = max(0.0, float(window.get("duration_sec") or 0.0))
    if duration <= target_duration:
        # Nothing to trim -- the available range is already no longer than
        # what was asked for, so use all of it rather than claiming a
        # duration longer than what actually exists.
        return window
    if not master_path or not Path(master_path).exists():
        best_start_local = max(0.0, (duration - target_duration) / 2.0)
    else:
        try:
            import librosa
            import numpy as np

            y, sr = librosa.load(master_path, sr=22050, mono=True, offset=start, duration=duration)
            hop = 512
            rms = librosa.feature.rms(y=y, hop_length=hop)[0]
            frame_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
            frame_duration = hop / sr
            window_frames = max(1, int(round(target_duration / frame_duration)))
            if window_frames >= len(rms):
                best_start_local = 0.0
            else:
                energy = rms.astype("float64") ** 2
                cumulative = np.concatenate([[0.0], np.cumsum(energy)])
                scores = cumulative[window_frames:] - cumulative[:-window_frames]
                best_index = int(np.argmax(scores))
                best_start_local = float(frame_times[best_index]) if best_index < len(frame_times) else 0.0
        except Exception:
            LOGGER.warning("Reel energy analysis failed for %s; falling back to the middle of the range", master_path, exc_info=True)
            best_start_local = max(0.0, (duration - target_duration) / 2.0)
    best_start = start + max(0.0, min(max(0.0, duration - target_duration), best_start_local))
    return {**window, "start_sec": best_start, "duration_sec": target_duration, "trim_start_sec": best_start, "trim_end_sec": best_start + target_duration}


def _selectable_synced_clips(project: Project, sync_map: dict[str, Any], *, allow_unsynced: bool = False, allow_unsynced_360: bool | None = None) -> dict[str, Any]:
    # Reel is intentionally not a sync edit: confidence, offsets and missing
    # camera audio must not remove otherwise usable promo footage. Keep the
    # old keyword as a compatibility shim for callers/tests.
    if allow_unsynced_360 is not None:
        allow_unsynced = allow_unsynced or allow_unsynced_360
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
    for map_clip_id, clip in (sync_map.get("clips") or {}).items():
        clip_id = str(clip.get("clip_id") or map_clip_id)
        path = clip.get("source_path") or clip.get("path")
        record = records_by_path.get(path)
        if record is None and clip.get("path"):
            record = records_by_path.get(clip["path"])
        confidence = _float_or_zero(clip.get("confidence"))
        valid_video = bool(record and record_is_usable_camera_video(record))
        diagnostic = {
            "clip_id": clip_id,
            "filename": clip.get("filename") or Path(str(path or "")).name or "clip",
            "valid_video": valid_video,
            "confidence": confidence,
            "threshold": threshold,
            "offset_sec": clip.get("offset_sec"),
            "low_confidence": bool(clip.get("low_confidence") or confidence < threshold),
            "unstable_sync": bool(clip.get("unstable_sync")) and not bool(clip.get("manual_override")),
            "manual_override": bool(clip.get("manual_override")),
            "verification": clip.get("verification"),
            "error": clip.get("error"),
            "no_audio": bool(clip.get("no_audio")),
            "projection": (record.get("probe") or {}).get("projection") if record else None,
            "path": clip.get("path"),
            "source_path": clip.get("source_path"),
        }
        master_duration = _float_or_zero(sync_map.get("master_duration_sec"))
        clip_start = _float_or_zero(clip.get("offset_sec"))
        clip_end = clip_start + _float_or_zero(clip.get("duration_sec"))
        overlap_start = max(0.0, clip_start)
        overlap_end = min(master_duration, clip_end)
        overlap_sec = max(0.0, overlap_end - overlap_start)
        diagnostic.update(
            {
                "recorded_start_sec": clip_start,
                "recorded_end_sec": clip_end,
                "master_start_sec": 0.0,
                "master_end_sec": master_duration,
                "master_overlap_sec": overlap_sec,
                "master_overlap": bool(overlap_sec > 0.0),
            }
        )
        diagnostics.append(diagnostic)
        reason = _exclusion_reason(diagnostic, allow_unsynced=allow_unsynced)
        if reason:
            excluded.append({"filename": diagnostic["filename"], "reason": reason, "diagnostic": diagnostic})
            continue
        selected_clip = dict(clip)
        if diagnostic.get("projection"):
            selected_clip["projection"] = diagnostic["projection"]
        selected.append(selected_clip)
    warnings = []
    for diagnostic in diagnostics:
        if diagnostic.get("unstable_sync") and not diagnostic.get("low_confidence"):
            warnings.append(
                f"{diagnostic['filename']} had inconsistent sync checks, but its confidence met the threshold; it was kept with a sync warning."
            )
    return {"clips": selected, "warnings": warnings, "diagnostics": diagnostics, "excluded": excluded}


def _exclusion_reason(diagnostic: dict[str, Any], *, allow_unsynced: bool = False, allow_unsynced_360: bool | None = None) -> str | None:
    if allow_unsynced_360 is not None:
        allow_unsynced = allow_unsynced or allow_unsynced_360
    if not diagnostic["valid_video"]:
        return t("not_usable_camera_video")
    if diagnostic.get("error") and not allow_unsynced:
        return str(diagnostic["error"])
    if diagnostic.get("no_audio") and not allow_unsynced:
        return t("no_sync_audio")
    # Stability is a secondary warning. A clip with a confidence score at or
    # above the configured threshold remains usable; previously this hidden
    # criterion discarded clips such as confidence=6.085 at threshold=6.0.
    if diagnostic.get("unstable_sync") and not allow_unsynced and diagnostic["confidence"] < diagnostic["threshold"]:
        verification = diagnostic.get("verification") or {}
        delta = verification.get("delta_sec")
        if isinstance(delta, (int, float)):
            return t("unstable_sync_detail", ms=delta * 1000)
        return t("unstable_sync")
    if diagnostic.get("low_confidence") and not allow_unsynced and not diagnostic.get("manual_override"):
        return t("low_confidence_excluded_detail", confidence=float(diagnostic.get("confidence") or 0.0), threshold=float(diagnostic.get("threshold") or 0.0))
    return None


def _diagnostic_error_message(diagnostics: list[dict[str, Any]]) -> str:
    if any(item.get("valid_video") for item in diagnostics):
        lines = [
            "The camera videos were readable, but none could be aligned reliably with the selected master audio.",
            "This usually means the master is the wrong song, the song is outside the recorded time range, or the camera audio is too weak for correlation.",
            "Choose the matching master or strengthen the camera audio; you can also proceed anyway with the best-scoring offsets, with imperfect sync expected.",
            t("clip_diagnostics"),
        ]
    else:
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
        overlap = "overlaps master" if item.get("master_overlap") else "does not overlap master"
        range_text = (
            f"recorded {item.get('recorded_start_sec', 0.0):.1f}–{item.get('recorded_end_sec', 0.0):.1f}s; {overlap}"
        )
        reason = f" ({'; '.join(reason_bits)})" if reason_bits else ""
        lines.append(f"- {item['filename']}: valid video={valid}, confidence={confidence}, threshold={threshold}, {range_text}{reason}")
    if all(item.get("low_confidence") for item in diagnostics if item.get("valid_video")):
        if any(item.get("master_overlap") is False for item in diagnostics if item.get("valid_video")):
            lines.append("The recorded ranges do not overlap the selected master session. The master may be the wrong song or outside the footage time range.")
        lines.append("If the master is correct, the camera audio may be too weak for reliable correlation. You can proceed anyway using the best-scoring offsets, but sync may be imprecise.")
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


def _select_360_clip(clips: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [clip for clip in clips if clip.get("projection") in {"equirect", "raw_insv"} or clip.get("raw_360")]
    if not candidates:
        return None
    return sorted(candidates, key=lambda clip: 0 if clip.get("projection") == "equirect" else 1)[0]


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
        "source_path": clip.get("source_path") or clip["path"],
        "clip_start_sec": source_start,
        "master_start_sec": master_start,
        "duration_sec": max(1.0, duration),
        "clip_offset_sec": clip_offset,
    }


def _segment_for_360(clip: dict[str, Any], window: dict[str, Any]) -> dict[str, Any]:
    """Build the passthrough segment for 360 export: the intersection of the
    song's chosen Start/End range with whatever the 360 camera actually
    covers -- never the camera's own full recording extent regardless of
    what the song range asked for.
    """
    clip_offset = float(clip.get("offset_sec") or 0)
    clip_duration = float(clip.get("duration_sec") or 0)
    window_start = float(window["start_sec"])
    window_end = window_start + float(window["duration_sec"])
    start = max(window_start, clip_offset)
    end = min(window_end, clip_offset + clip_duration)
    duration = max(0.0, end - start)
    source_start = max(0.0, start - clip_offset)
    return {
        "title": clip.get("filename") or t("full_video"),
        "clip_path": clip["path"],
        "source_path": clip.get("source_path") or clip["path"],
        "clip_start_sec": source_start,
        "master_start_sec": start,
        "duration_sec": duration,
        "clip_offset_sec": clip_offset,
        "confidence": float(clip.get("confidence") or 0.0),
        "filename": clip.get("filename") or Path(str(clip.get("path"))).name,
        "projection": clip.get("projection"),
    }
