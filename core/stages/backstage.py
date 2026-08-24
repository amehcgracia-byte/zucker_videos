"""Independent documentary pipeline for the Backstage mode.

Backstage is video-led: it never requires or consults a master track, sync
map, or coverage plan.  Its edit artifact is intentionally separate in shape
and semantics from the music-led modes.
"""

from __future__ import annotations

import json
import hashlib
import os
import math
import re
import shutil
import signal
import subprocess
import tempfile
import time
import textwrap
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.export import (
    _ffmpeg_path,
    _logo_clip_cache_path,
    _logo_path,
    _personal_logo_path,
    _target_size,
    _ffmpeg_supports_filter,
)
from core.backstage_transcription import transcribe_sources
from core.audio_content import detect_music_window
from core.narrative_scoring import generate_storyboard, score_story_sequences
from core.narrative_sequences import group_story_bites
from core.backstage_feedback import content_fingerprint, load_feedback


BACKSTAGE_ANALYSIS_VERSION = 7
BACKSTAGE_EDIT_VERSION = 6
BACKSTAGE_EXPORT_VERSION = 6
BACKSTAGE_LOGO_DURATION = 10.0
BACKSTAGE_DEFAULT_TARGET_DURATION = 180.0
BACKSTAGE_NORMAL_CUT = 6.0
BACKSTAGE_REACTION_CUT = 3.5
BACKSTAGE_SCENE_CUT = 15.0
BACKSTAGE_AUDIO_CROSSFADE = 1.0
BACKSTAGE_VIDEO_CROSSFADE = 0.18
BACKSTAGE_MUSIC_VOLUME = 0.45
BACKSTAGE_MUSIC_ATTACK_MS = 80
BACKSTAGE_MUSIC_RELEASE_MS = 900
BACKSTAGE_MUSIC_DUCK_THRESHOLD = 0.08
BACKSTAGE_MUSIC_DUCK_RATIO = 8
BACKSTAGE_MUSIC_MIN_DURATION = 18.0
BACKSTAGE_MUSIC_FADE_IN = 1.0
BACKSTAGE_MUSIC_FADE_OUT = 1.0
BACKSTAGE_MUSIC_TARGET_LUFS = -16.0
BACKSTAGE_AUDIO_STATES = ("music_only", "music_plus_clip_audio", "clip_audio_only")
BACKSTAGE_MUSIC_EDGE_GUARD = 4.0


def _video_records(project: Project) -> list[dict[str, Any]]:
    records = []
    for record in project.data.get("inputs", {}).get("videos") or []:
        path = str((record.get("normalized") or {}).get("path") or record.get("path") or "")
        if not path or not Path(path).exists():
            continue
        probe = record.get("probe") or {}
        if not probe:
            try:
                probe = ffprobe(path)
            except Exception:
                probe = {}
        duration = float(probe.get("duration") or record.get("duration") or 0.0)
        if duration >= 2.5:
            tags = probe.get("format", {}).get("tags", {}) if isinstance(probe.get("format"), dict) else {}
            creation_time = tags.get("creation_time") or probe.get("creation_time") or record.get("creation_time")
            if not creation_time:
                try:
                    container_probe = ffprobe(path)
                    container_tags = (container_probe.get("format") or {}).get("tags") or {}
                    creation_time = container_tags.get("creation_time") or container_probe.get("creation_time")
                except Exception:
                    creation_time = None
            records.append({
                **record,
                "_path": path,
                "_duration": duration,
                "_creation_time": creation_time,
                "_chronological_origin": _parse_creation_time(creation_time, record.get("mtime", 0)),
            })
    return records


def _parse_creation_time(value: Any, fallback: Any) -> float:
    """Return a sortable epoch, preferring container creation_time."""
    if value:
        try:
            text = str(value).replace("Z", "+00:00")
            return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        return float(fallback or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _mean_volume(path: str, start: float, duration: float) -> float:
    """Measure a short window without making a persistent audio proxy."""
    ffmpeg = tool_status().get("ffmpeg_path")
    if not ffmpeg:
        return -60.0
    command = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}",
        "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr or "")
    return float(match.group(1)) if match else -60.0


def _has_audio(path: str) -> bool:
    try:
        return any(stream.get("codec_type") == "audio" for stream in ffprobe(path).get("streams") or [])
    except Exception:
        return False


def _candidate_windows(record: dict[str, Any]) -> list[dict[str, Any]]:
    path = record["_path"]
    total = float(record["_duration"])
    if not _has_audio(path):
        return []
    windows: list[dict[str, Any]] = []
    cursor = 0.0
    while cursor < total - 2.5:
        remaining = total - cursor
        duration = min(BACKSTAGE_NORMAL_CUT, remaining)
        if duration < 2.5:
            break
        volume = _mean_volume(path, cursor, duration)
        # Keep non-silent moments. Loud/reactive windows are shorter so a
        # laugh or exclamation lands as a punchy cut; quieter but active
        # dialogue receives a longer scene window.
        if volume <= -45.0:
            cursor += duration
            continue
        if volume >= -18.0:
            duration = min(BACKSTAGE_REACTION_CUT, remaining)
            kind = "reaction"
        elif volume >= -30.0 and remaining >= BACKSTAGE_SCENE_CUT:
            duration = BACKSTAGE_SCENE_CUT
            kind = "spoken_scene"
        else:
            kind = "moment"
        windows.append({
            "clip_start_sec": round(cursor, 3),
            "duration_sec": round(min(duration, remaining), 3),
            "mean_volume_db": round(volume, 2),
            "kind": kind,
            "audio_interest": round(max(0.0, min(1.0, (volume + 60.0) / 42.0)), 3),
            "visual_interest": 0.5,
            "face_interest": 0.0,
            "interest_score": round(max(0.0, min(1.0, (volume + 60.0) / 42.0)), 3),
            "audio_event": "reaction" if volume >= -18.0 else ("speech_or_activity" if volume > -35.0 else "ambient"),
        })
        windows[-1]["music"] = detect_music_window(path, cursor, duration)
        cursor += duration
    return windows


class BackstageAnalysisStage(Stage):
    """Find non-dead documentary moments from each video's own audio."""

    name = "cut"
    dependencies = ["ingest"]

    def inputs_fingerprint(self, project: Project) -> str:
        return stable_fingerprint({"videos": project.data.get("inputs", {}).get("videos", []), "version": BACKSTAGE_ANALYSIS_VERSION, "mode": "backstage"})

    def outputs(self, project: Project) -> dict[str, str]:
        return {"backstage_analysis": str(artifact_path(project, "backstage_analysis.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, str]:
        records = _video_records(project)
        sources = []
        for index, record in enumerate(records):
            progress_callback(int(index / max(1, len(records)) * 90), f"Listening for Backstage moments: {Path(record['_path']).name}")
            moments = _candidate_windows(record)
            sources.append({
                "path": record["_path"],
                "filename": record.get("filename") or Path(record["_path"]).name,
                "duration_sec": record["_duration"],
                "mtime": record.get("mtime", 0),
                "creation_time": record.get("_creation_time"),
                "chronological_origin": record.get("_chronological_origin", 0.0),
                "moments": moments,
            })
        transcription = transcribe_sources(
            sources,
            artifact_path(project, "backstage_transcription.json"),
            lambda percent, detail: progress_callback(90 + int(percent * 0.1), detail),
            model_name=str((project.data.get("settings", {}).get("wizard") or {}).get("backstage_whisper_model") or "small"),
            task=str((project.data.get("settings", {}).get("wizard") or {}).get("backstage_whisper_task") or "transcribe"),
            language_overrides=(project.data.get("settings", {}).get("wizard") or {}).get("backstage_whisper_language_overrides") or {},
            model_by_language=(project.data.get("settings", {}).get("wizard") or {}).get("backstage_whisper_model_by_language") or {"de": "medium", "en": "medium"},
        )
        story_bites = transcription.get("story_bites") or []
        sequences = group_story_bites(story_bites)
        scoring = score_story_sequences(sequences)
        scored_sequences = scoring.get("sequences") or sequences
        if scoring.get("status") != "ready":
            # A previous successful DeepSeek run is still valid cached editorial
            # input. Reuse its English translations when the current session
            # has no API key, instead of silently exporting untranslated speech.
            cached_path = artifact_path(project, "backstage_narrative_sequence_scores.json")
            try:
                cached = json.loads(cached_path.read_text(encoding="utf-8"))
                cached_by_id = {str(item.get("id")): item for item in cached.get("sequences") or [] if item.get("english_text")}
                if cached_by_id:
                    scored_sequences = []
                    for sequence in sequences:
                        previous = cached_by_id.get(str(sequence.get("id")))
                        scored_sequences.append({**sequence, **({"english_text": previous.get("english_text"), "narrative_scores": previous.get("narrative_scores") or sequence.get("narrative_scores")} if previous else {})})
                    scoring = {**scoring, "status": "ready_cached", "model": cached.get("model"), "reason": "Reused cached DeepSeek sequence translations"}
            except (OSError, ValueError, TypeError):
                pass
        if scoring.get("status") == "ready":
            write_artifact_json(artifact_path(project, "backstage_narrative_sequence_scores.json"), scoring)
        wizard = project.data.get("settings", {}).get("wizard") or {}
        music_path = str(wizard.get("backstage_music_path") or wizard.get("master_path") or "")
        storyboard = generate_storyboard(scored_sequences, music_path=music_path)
        storyboard_path = artifact_path(project, "backstage_storyboard.json")
        if storyboard.get("status") == "ready":
            write_artifact_json(storyboard_path, storyboard)
        else:
            try:
                cached_storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
                if cached_storyboard.get("status") == "ready":
                    storyboard = {**cached_storyboard, "status": "ready_cached"}
            except (OSError, ValueError, TypeError):
                pass
        payload = {"stage": "backstage_analysis", "platform": "backstage", "version": BACKSTAGE_ANALYSIS_VERSION, "sources": sources, "transcription_sources": transcription.get("sources") or [], "story_bites": story_bites, "story_sequences": scored_sequences, "storyboard": storyboard, "narrative_scoring": {"unit": "conversation_sequence", "status": scoring.get("status"), "model": scoring.get("model"), "batches": scoring.get("batches", 0), "reason": scoring.get("reason", "")}, "transcription": {"status": transcription.get("status"), "model": transcription.get("model"), "task": transcription.get("task"), "backend": transcription.get("backend"), "elapsed_sec": transcription.get("elapsed_sec"), "artifact": "backstage_transcription.json"}}
        write_artifact_json(artifact_path(project, "backstage_analysis.json"), payload)
        progress_callback(100, "Backstage moments ready")
        return self.outputs(project)


class BackstageEditStage(Stage):
    """Arrange selected moments chronologically without a master timeline."""

    name = "edit"
    dependencies = ["cut"]

    def inputs_fingerprint(self, project: Project) -> str:
        analysis = artifact_path(project, "backstage_analysis.json")
        payload = json.loads(analysis.read_text(encoding="utf-8")) if analysis.exists() else {}
        wizard = project.data.get("settings", {}).get("wizard") or {}
        return stable_fingerprint({
            "analysis": payload,
            "version": BACKSTAGE_EDIT_VERSION,
            "mode": "backstage",
            "run_id": wizard.get("backstage_run_id", "legacy"),
        })

    def outputs(self, project: Project) -> dict[str, str]:
        return {
            "backstage_edit": str(artifact_path(project, "backstage_edit.json")),
            "backstage_paper_edit": str(artifact_path(project, "backstage_paper_edit.json")),
        }

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, str]:
        analysis = json.loads(artifact_path(project, "backstage_analysis.json").read_text(encoding="utf-8"))
        moments = _flatten_moments(analysis)
        storyboard = analysis.get("storyboard") or {}
        storyboard_order: dict[str, int] = {}
        storyboard_data = storyboard.get("storyboard") if isinstance(storyboard, dict) else {}
        if isinstance(storyboard_data, dict):
            rank = 0
            for phase in ("opening", "development", "closing"):
                for item in storyboard_data.get(phase) or []:
                    sequence_id = str(item.get("sequence_id") or "")
                    if sequence_id and sequence_id not in storyboard_order:
                        storyboard_order[sequence_id] = rank
                        rank += 1
        target = float((project.data.get("settings", {}).get("wizard") or {}).get(
            "backstage_target_duration_sec", BACKSTAGE_DEFAULT_TARGET_DURATION
        ) or BACKSTAGE_DEFAULT_TARGET_DURATION)
        target = max(30.0, min(240.0, target))
        run_id = str((project.data.get("settings", {}).get("wizard") or {}).get("backstage_run_id") or "legacy")
        chosen = _select_documentary_moments(moments, target, run_id, storyboard_order)
        trim_cache: dict[tuple[str, float, float], tuple[float, float]] = {}
        segments = []
        feedback_examples = load_feedback().get("examples") or {}
        music_present_index = 0
        for index, moment in enumerate(chosen):
            key = (str(moment["source_path"]), float(moment["clip_start_sec"]), float(moment["duration_sec"]))
            is_narrative = bool(moment.get("story_sequences"))
            if key not in trim_cache and not is_narrative:
                trim_cache[key] = _trim_dead_air(*key)
            clip_start, duration = (key[1], key[2]) if is_narrative else trim_cache[key]
            music = moment.get("music") or {"music_present": False, "music_score": 0.0}
            music_present = bool(music.get("music_present"))
            if music_present and not is_narrative:
                clip_start, duration = _snap_music_interval_to_energy(
                    str(moment["source_path"]), clip_start, duration,
                )
            narrative_sequence = bool(moment.get("story_sequences"))
            if narrative_sequence:
                # A scored conversation keeps clip audio and lets the
                # sidechain compressor duck the bed beneath the voice.
                policy = "music_plus_clip_audio"
            elif music_present:
                # Never layer the chosen bed over music already recorded in
                # the source. If this fallback is selected, keep only the
                # source music; conversations are handled above and win.
                policy = "clip_music_only"
                music_present_index += 1
            else:
                # Quiet/ambient coverage is the preferred place for the
                # selected music bed. Keep the camera track muted there so
                # source noise cannot compete with it.
                policy = "background_music_only"
            segments.append({
                "source_path": moment["source_path"],
                "clip_path": moment["source_path"],
                "filename": moment["filename"],
                "clip_start_sec": round(clip_start, 3),
                "duration_sec": round(duration, 3),
                "kind": moment["kind"],
                "interest_score": moment.get("selection_score", moment["interest_score"]),
                "section": moment["section"],
                "source_creation_time": moment.get("creation_time"),
                "audio_boundary": "trimmed_to_activity_boundary",
                "j_cut_sec": 0.25 if index else 0.0,
                "l_cut_sec": 0.25 if index < len(chosen) - 1 else 0.0,
                "audio_crossfade_sec": BACKSTAGE_AUDIO_CROSSFADE if index else 0.0,
                "audio_original": True,
                "audio_music": music,
                "background_music_policy": policy,
                "story_bites": moment.get("story_bites") or [],
                "story_sequences": moment.get("story_sequences") or [],
                "transcription_segments": moment.get("transcription_segments") or [],
                "subtitle_text": "",
            })
            feedback_key = f"{content_fingerprint(segments[-1]['source_path'])}:{float(clip_start):.3f}:{float(clip_start) + float(duration):.3f}"
            feedback = feedback_examples.get(feedback_key) or {}
            segments[-1]["paper_mark"] = feedback.get("mark") or "keep"
            segments[-1]["paper_feedback_reason"] = feedback.get("reason") or ""
        # Existing user feedback is editorial input: drops disappear from the
        # render immediately, while closings are moved to the final montage
        # tranche without changing any other mode's plan.
        segments = [segment for segment in segments if segment.get("paper_mark") != "drop"]
        closings = [segment for segment in segments if segment.get("paper_mark") == "closing"]
        segments = [segment for segment in segments if segment.get("paper_mark") != "closing"] + closings
        used_duration = sum(float(segment["duration_sec"]) for segment in segments)
        source_durations: dict[str, float] = {}
        for segment in segments:
            source_durations[segment["source_path"]] = source_durations.get(segment["source_path"], 0.0) + float(segment["duration_sec"])
        payload = {
            "stage": "backstage_edit", "platform": "backstage", "version": BACKSTAGE_EDIT_VERSION,
            "audio_mode": "original_per_clip", "chronological": True,
            "target_duration_sec": target, "selected_duration_sec": round(used_duration, 3),
            "available_moment_count": len(moments), "dropped_moment_count": max(0, len(moments) - len(segments)),
            "selection": "chronological_three_act_round_robin_with_source_cap",
            "source_durations_sec": {key: round(value, 3) for key, value in source_durations.items()},
            "source_count": len(source_durations),
            "segments": segments, "cut_count": max(0, len(segments) - 1),
            "music_path": str(
                (project.data.get("settings", {}).get("wizard") or {}).get("backstage_music_path")
                or (project.data.get("settings", {}).get("wizard") or {}).get("master_path")
                or ""
            ),
        }
        write_artifact_json(artifact_path(project, "backstage_edit.json"), payload)
        paper_cuts = []
        for index, segment in enumerate(segments):
            original_text = _paper_text(segment, "original")
            english_text = _paper_text(segment, "english")
            scores = next((dict(item.get("narrative_scores") or {}) for item in segment.get("story_sequences") or [] if item.get("narrative_scores")), {})
            source_fingerprint = content_fingerprint(segment["source_path"])
            in_sec = float(segment["clip_start_sec"])
            out_sec = in_sec + float(segment["duration_sec"])
            paper_cuts.append({
                "id": f"backstage-{index:04d}",
                "thumbnail": f"/api/v1/wizard/paper-edit/thumbnail/backstage-{index:04d}",
                "order": index + 1,
                "source": segment["filename"],
                "source_path": segment["source_path"],
                "in_sec": segment["clip_start_sec"],
                "out_sec": round(out_sec, 3),
                "duration_sec": segment["duration_sec"],
                "section": segment["section"],
                "description": _paper_description(segment),
                "selection_reason": _paper_selection_reason({**segment, "text_original": original_text}),
                "text_original": original_text,
                "english_text": english_text,
                "language": segment.get("language"),
                "narrative_score": round(sum(float(scores.get(key) or 0) for key in ("funny", "story", "hook", "payoff")) / 40.0, 3) if scores else None,
                "narrative_scores": scores,
                "score": round(sum(float(scores.get(key) or 0) for key in ("funny", "story", "hook", "payoff")) / 40.0, 3) if scores else None,
                "content_fingerprint": source_fingerprint,
                "range_key": f"{source_fingerprint}:{in_sec:.3f}:{out_sec:.3f}",
                "subtitle_text": segment.get("subtitle_text") or "",
                "kind": segment["kind"],
                "interest_score": segment["interest_score"],
                "status": "pending",
                "mark": segment.get("paper_mark") or "keep",
            })
        paper = {
            "stage": "backstage_paper_edit",
            "platform": "backstage",
            "version": BACKSTAGE_EDIT_VERSION,
            "source_plan": str(artifact_path(project, "backstage_edit.json")),
            "review_required": True,
            "semantic_status": "transcript-backed: spoken text is shown for editorial review",
            "storyboard": storyboard,
            "storyboard_drives_selection": bool(storyboard_order),
            "cuts": paper_cuts,
        }
        write_artifact_json(artifact_path(project, "backstage_paper_edit.json"), paper)
        progress_callback(100, "Backstage edit ready")
        return self.outputs(project)


def _paper_description(segment: dict[str, Any]) -> str:
    kind = str(segment.get("kind") or "moment")
    if kind == "spoken_scene":
        return "Tramo hablado potencialmente completo (pendiente de transcripción semántica)."
    if kind == "reaction":
        return "Reacción/exclamación o energía alta detectada en el audio."
    return "Momento de actividad o ambiente con audio utilizable; contenido semántico pendiente."


def _paper_selection_reason(segment: dict[str, Any]) -> str:
    section = str(segment.get("section") or "body")
    score = float(segment.get("interest_score") or 0.0)
    spoken = _paper_text(segment, "original")
    if not spoken:
        kind = str(segment.get("kind") or "actividad")
        return f"{section.capitalize()}: sin diálogo; seleccionado por {kind}, energía/movimiento y variedad de fuentes."
    if section == "opening":
        return f"Apertura: puntuación {score:.2f}, elegida para enganchar al inicio."
    if section == "closing":
        return f"Cierre: puntuación {score:.2f}, elegida como remate cronológico."
    return f"Cuerpo: puntuación {score:.2f}, mantiene variedad y cobertura de fuentes."


def _paper_text(segment: dict[str, Any], language: str) -> str:
    """Show the spoken material itself in the paper edit."""
    sequences = segment.get("story_sequences") or []
    if sequences:
        key = "english_text" if language == "english" else "text"
        return " ".join(
            str(sequence.get(key) or "").strip()
            for sequence in sequences
            if str(sequence.get(key) or "").strip()
        ).strip()
    if language == "english":
        return ""
    return " ".join(
        str(item.get("text") or "").strip()
        for item in segment.get("transcription_segments") or []
        if str(item.get("text") or "").strip()
    ).strip()


def _flatten_moments(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten candidates using the file's creation_time as the timeline."""
    moments: list[dict[str, Any]] = []
    for source in analysis.get("sources") or []:
        origin = float(source.get("chronological_origin") or source.get("mtime") or 0.0)
        bites = [bite for bite in analysis.get("story_bites") or [] if bite.get("source_path") == source.get("path")]
        sequences = [sequence for sequence in analysis.get("story_sequences") or [] if sequence.get("source_path") == source.get("path")]
        for moment in source.get("moments") or []:
            duration = min(15.0, max(2.5, float(moment.get("duration_sec") or 0.0)))
            start = float(moment.get("clip_start_sec") or 0.0)
            end = start + duration
            overlapping = [bite for bite in bites if float(bite.get("start_sec") or 0.0) < end and float(bite.get("end_sec") or 0.0) > start]
            overlapping_sequences = [sequence for sequence in sequences if float(sequence.get("start_sec") or 0.0) < end and float(sequence.get("end_sec") or 0.0) > start]
            source_transcription = [item for item in analysis.get("transcription_sources") or [] if item.get("path") == source.get("path")]
            transcription_segments = [segment for item in source_transcription for segment in item.get("segments") or [] if float(segment.get("start_sec") or 0.0) < end and float(segment.get("end_sec") or 0.0) > start]
            detected_language = next((item.get("language") or item.get("detected_language") for item in source_transcription if item.get("language") or item.get("detected_language")), None)
            bite_fallback = max((min(1.0, 0.35 + len(str(bite.get("text") or "").split()) / 40.0) for bite in overlapping), default=0.0)
            story_score = max((min(1.0, sum(float(sequence.get("narrative_scores", {}).get(key) or 0.0) for key in ("funny", "story", "hook", "payoff")) / 40.0) for sequence in overlapping_sequences), default=bite_fallback)
            source_music = bool((moment.get("music") or {}).get("music_present"))
            music_penalty = 0.18 if source_music and not overlapping_sequences else 0.0
            moments.append({
                **moment,
                "source_path": source["path"],
                "filename": source["filename"],
                "creation_time": source.get("creation_time"),
                "chronological_start": round(origin + start, 3),
                "duration_sec": duration,
                "story_bites": overlapping,
                "story_sequences": overlapping_sequences,
                "transcription_segments": transcription_segments,
                "language": detected_language,
                "story_score": round(story_score, 3),
                # Narrative content dominates. Source music is a weak fallback
                # only; quiet/ambient coverage is preferred when content is
                # otherwise equivalent so the selected music bed can lead.
                "selection_score": round(max(0.0, min(1.0, float(moment.get("interest_score") or 0.0) * 0.72 + story_score * 0.28 - music_penalty)), 3),
            })
    return sorted(moments, key=lambda item: (item["chronological_start"], item["source_path"]))


def _select_documentary_moments(moments: list[dict[str, Any]], target: float, run_id: str = "legacy", storyboard_order: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Select a varied three-act narrative under a per-source 20% cap."""
    if not moments:
        return []
    # A scored conversation sequence is the atomic editorial unit. Collapse
    # several audio-analysis windows that overlap the same sequence before the
    # duration/source-cap selector runs, so a good conversation is never split
    # merely because its energy detector produced multiple windows.
    sequence_candidates: dict[str, dict[str, Any]] = {}
    storyboard_order = storyboard_order or {}
    for moment in moments:
        for sequence in moment.get("story_sequences") or []:
            sequence_id = str(sequence.get("id") or "")
            if not sequence_id:
                continue
            if storyboard_order and sequence_id not in storyboard_order:
                continue
            score = min(1.0, sum(float(sequence.get("narrative_scores", {}).get(key) or 0.0) for key in ("funny", "story", "hook", "payoff")) / 40.0)
            candidate = sequence_candidates.get(sequence_id)
            if candidate is None or score > float(candidate.get("selection_score") or 0.0):
                sequence_candidates[sequence_id] = {
                    **moment,
                    "clip_start_sec": float(sequence.get("start_sec") or moment.get("clip_start_sec") or 0.0),
                    "duration_sec": float(sequence.get("duration_sec") or moment.get("duration_sec") or 0.0),
                    "kind": "spoken_scene",
                    "interest_score": score,
                    "selection_score": score,
                    "story_sequences": [sequence],
                    "story_bites": sequence.get("bites") or moment.get("story_bites") or [],
                    "transcription_segments": moment.get("transcription_segments") or [],
                    "storyboard_rank": storyboard_order.get(sequence_id, 10_000),
                }
    if sequence_candidates:
        sequence_ids = set(sequence_candidates)
        non_sequence = [moment for moment in moments if not any(str(item.get("id") or "") in sequence_ids for item in moment.get("story_sequences") or [])]
        moments = list(sequence_candidates.values()) + non_sequence
    sources = sorted({item["source_path"] for item in moments})
    source_cap = target if len(sources) <= 1 else max(target * 0.20, 45.0)
    by_source: dict[str, list[dict[str, Any]]] = {source: [] for source in sources}
    for item in moments:
        by_source[item["source_path"]].append(item)
    def nonce_rank(item: dict[str, Any]) -> str:
        key = f"{run_id}:{item['source_path']}:{item['chronological_start']}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    for items in by_source.values():
        items.sort(key=lambda item: (0 if item.get("story_sequences") else 1, -float(item.get("selection_score") or item.get("interest_score") or 0.0), nonce_rank(item)))

    timeline_start = moments[0]["chronological_start"]
    timeline_end = max(item["chronological_start"] + float(item["duration_sec"]) for item in moments)
    span = max(1.0, timeline_end - timeline_start)
    opening_limit = timeline_start + span * 0.20
    closing_floor = timeline_start + span * 0.80
    chosen: list[dict[str, Any]] = []
    used: dict[str, float] = {source: 0.0 for source in sources}
    used_total = 0.0

    def add(item: dict[str, Any], section: str) -> bool:
        nonlocal used_total
        source = item["source_path"]
        is_sequence = bool(item.get("story_sequences"))
        duration = min(45.0 if is_sequence else 15.0, float(item["duration_sec"]))
        start, end = _snap_backstage_interval_to_words(
            float(item.get("clip_start_sec") or 0.0),
            float(item.get("clip_start_sec") or 0.0) + duration,
            item.get("transcription_segments") or [],
        )
        duration = min(45.0 if is_sequence else 15.0, max(2.5, end - start))
        if used.get(source, 0.0) + duration > source_cap + 0.01:
            return False
        if used_total + duration > target + 0.01:
            return False
        start = item["chronological_start"] - float(item.get("clip_start_sec") or 0.0) + start
        end = start + duration
        if any(other["source_path"] == source and start < other["_end"] and end > other["_start"] for other in chosen):
            return False
        chosen.append({**item, "clip_start_sec": round(start - item["chronological_start"] + float(item.get("clip_start_sec") or 0.0), 3), "duration_sec": round(duration, 3), "section": section, "_start": start, "_end": end})
        used[source] += duration
        used_total += duration
        return True

    # Reserve real opening and closing acts, rather than labelling one isolated
    # shot. The acts are deliberately short enough to hook and close without
    # consuming the documentary's varied middle.
    opening_candidates = [item for item in moments if item["chronological_start"] <= opening_limit]
    closing_candidates = [item for item in moments if item["chronological_start"] >= closing_floor]
    # Bookends are deliberately visual/ambient rather than another scored
    # conversation. This guarantees the selected bed can enter and leave
    # cleanly; narrative sequences remain the body priority.
    quiet_opening = [item for item in opening_candidates if not item.get("story_sequences") and not (item.get("music") or {}).get("music_present")]
    quiet_closing = [item for item in closing_candidates if not item.get("story_sequences") and not (item.get("music") or {}).get("music_present")]
    if quiet_opening:
        opening_candidates = quiet_opening
    if quiet_closing:
        closing_candidates = quiet_closing
    opening_budget = target * 0.12
    closing_budget = target * 0.12
    opening_used = 0.0
    for item in sorted(opening_candidates, key=lambda item: (bool(item.get("story_sequences")), bool((item.get("music") or {}).get("music_present")), -float(item.get("selection_score") or item.get("interest_score") or 0.0), nonce_rank(item))):
        if opening_used >= opening_budget:
            break
        before = used_total
        if add(item, "opening"):
            opening_used += used_total - before
    closing_used = 0.0
    for item in sorted(closing_candidates, key=lambda item: (bool(item.get("story_sequences")), bool((item.get("music") or {}).get("music_present")), -item["chronological_start"], -float(item.get("selection_score") or item.get("interest_score") or 0.0), nonce_rank(item))):
        if closing_used >= closing_budget:
            break
        before = used_total
        if add(item, "closing"):
            closing_used += used_total - before

    # Reserve the strongest scored conversations before filling the body with
    # audio-energy fallbacks. A long conversation is the editorial unit and
    # must not lose its place merely because its source appears late in the
    # chronological round-robin (notably C0135's police-call story).
    narrative_reserve = target * 0.60
    for item in sorted(sequence_candidates.values(), key=lambda item: (int(item.get("storyboard_rank", 10_000)), -float(item.get("selection_score") or 0.0), nonce_rank(item))):
        if used_total >= narrative_reserve:
            break
        add(item, "body")

    # One candidate per available source first; this prevents a single camera
    # from becoming the whole documentary.
    for source in sources:
        for item in by_source[source]:
            if add(item, "body"):
                break

    # Round-robin by source, selecting the strongest remaining candidate while
    # rejecting adjacent shots from the same source or repeated framing/person.
    cursor = 0
    while used_total < target - 0.01:
        added = False
        for offset in range(len(sources)):
            source = sources[(cursor + offset) % len(sources)]
            candidates = [item for item in by_source[source] if item not in chosen]
            candidates.sort(key=lambda item: (-float(item.get("selection_score") or item.get("interest_score") or 0.0), nonce_rank(item)))
            for item in candidates:
                if chosen and chosen[-1]["source_path"] == source:
                    continue
                if add(item, "body"):
                    added = True
                    cursor = (cursor + offset + 1) % len(sources)
                    break
            if added:
                break
        if not added:
            break

    # Bookends intentionally frame the chronological body. Within the body,
    # source chronology remains authoritative.
    section_order = {"opening": 0, "body": 1, "closing": 2}
    chosen.sort(key=lambda item: (section_order.get(str(item.get("section") or "body"), 1), int(item.get("storyboard_rank", 10_000)) if item.get("story_sequences") else 10_000, item["chronological_start"], item["source_path"]))
    return chosen


def _trim_dead_air(path: str, start: float, duration: float) -> tuple[float, float]:
    """Trim up to 1.5 s of quiet head/tail without splitting active audio."""
    head = 0.0
    tail = 0.0
    step = 0.5
    for _ in range(3):
        if _mean_volume(path, start + head, min(step, duration - head - tail)) > -42.0:
            break
        head += step
    for _ in range(3):
        remaining = duration - head - tail
        if remaining <= 2.5 or _mean_volume(path, start + head + remaining - step, min(step, remaining)) > -42.0:
            break
        tail += step
    return start + head, max(2.5, duration - head - tail)


def _snap_backstage_interval_to_words(start: float, end: float, transcription_segments: list[dict[str, Any]]) -> tuple[float, float]:
    """Move boundaries out of words using Whisper's word timestamps.

    If word timestamps are unavailable, retain the editorial boundary. This is
    deliberately mechanical: no LLM is involved in timing decisions.
    """
    words = []
    for segment in transcription_segments:
        for word in segment.get("words") or []:
            word_start = word.get("start_sec")
            word_end = word.get("end_sec")
            if word_start is not None and word_end is not None and float(word_end) > float(word_start):
                words.append((float(word_start), float(word_end)))
    if not words:
        return start, end
    words.sort()
    snapped_start = start
    snapped_end = end
    for word_start, word_end in words:
        if word_start < snapped_start < word_end:
            snapped_start = word_start
        if word_start < snapped_end < word_end:
            snapped_end = word_end
    return snapped_start, max(snapped_start, snapped_end)


def _snap_music_interval_to_energy(path: str, start: float, duration: float) -> tuple[float, float]:
    """Prefer a nearby energy drop when a music-led source is cut.

    This is a cheap mechanical proxy for a musical bar ending. It only moves
    each edge by at most 0.75 s and is never used for narrative dialogue,
    whose Whisper word boundaries are authoritative.
    """
    end = start + duration
    def edge(candidates: list[float]) -> float:
        scored = []
        for point in candidates:
            before = _mean_volume(path, max(0.0, point - 0.18), 0.18)
            after = _mean_volume(path, point, 0.18)
            scored.append((after - before, after, point))
        # A negative delta is a real drop; among similar drops prefer the
        # quieter point, which avoids chopping a sustained note.
        return min(scored, key=lambda item: (item[0], item[1]))[2]
    start_candidates = [max(0.0, start + offset) for offset in (-0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75)]
    end_candidates = [max(start + 2.5, end + offset) for offset in (-0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75)]
    snapped_start = edge(start_candidates)
    valid_end_candidates = [point for point in end_candidates if point >= snapped_start + 2.5]
    snapped_end = edge(valid_end_candidates or [end])
    return snapped_start, max(2.5, snapped_end - snapped_start)


def _render_backstage_logo_clip(output_path: Path, duration: float, video_bitrate: int, progress_callback: ProgressCallback | None) -> None:
    """Render/cache a logo-only Backstage intro; the designed card is never used."""
    ffmpeg = _ffmpeg_path()
    logo = _personal_logo_path() or _logo_path()
    width, height = _target_size("youtube")
    cache_path = _logo_clip_cache_path(
        "backstage-intro", duration, video_bitrate, logo,
        {"width": width, "height": height, "fps": 30, "audio": "silent-stereo"},
        mode="backstage-logo-only",
    )
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cache_path, output_path)
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    render_path = cache_path.with_name(f".{cache_path.stem}.{time.time_ns()}.tmp.mp4")
    inputs = ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=30:d={duration:.3f}", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    if logo:
        inputs.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", str(logo)])
        logo_input = "[2:v]format=rgba,scale=-1:{logo_h},fade=t=in:st=1.5:d=2.5:alpha=1,fade=t=out:st={fade_out:.3f}:d=3:alpha=1[logo]".format(logo_h=int(height * 0.72), fade_out=max(0.0, duration - 3.0))
        overlay = "[0:v]format=yuv420p[bg];" + logo_input + ";[bg][logo]overlay=(W-w)/2:(H-h)/2:format=auto[v]"
    else:
        overlay = "[0:v]format=yuv420p[v]"
    command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", *inputs, "-filter_complex", overlay, "-map", "[v]", "-map", "1:a", "-t", f"{duration:.3f}", "-r", "30", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(render_path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        render_path.unlink(missing_ok=True)
        raise FFmpegError(result.stderr.strip() or "Backstage logo render failed")
    os.replace(render_path, cache_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cache_path, output_path)


def _backstage_music_filter(content_duration: float, muted_ranges: list[tuple[float, float]] | None = None) -> str:
    """Build an audible music bed that ducks under meaningful camera audio.

    Each muted interval is represented by three small, independently enabled
    volume filters (fade down, silence, fade up).  This avoids the enormous
    recursively nested ``if()`` expression that used to be passed on the
    command line for long edits.
    """
    runs = _background_music_runs(content_duration, muted_ranges or [])
    # A single frame-evaluated envelope is deterministic at high volume and
    # makes the end fades measurable in the final AAC, unlike the old chain of
    # enabled volume filters. The expression is stored in an external filter
    # script, so its length is independent of the argv limit.
    parts: list[str] = []
    for start, end in runs:
        start = max(0.0, float(start)); end = min(float(content_duration), float(end))
        if end <= start:
            continue
        fade_in = min(BACKSTAGE_MUSIC_FADE_IN, max(0.01, (end - start) / 2.0))
        fade_out = min(BACKSTAGE_MUSIC_FADE_OUT, max(0.01, (end - start) / 2.0))
        gain = (
            f"min(clip((t-{start:.3f})/{fade_in:.3f},0,1),"
            f"clip(({end:.3f}-t)/{fade_out:.3f},0,1))"
        )
        parts.append(f"if(between(t,{start:.3f},{end:.3f}),{gain},0)")
    envelope = "+".join(parts) or "0"
    filters = [
        f"[1:a]aresample=48000,atrim=duration={float(content_duration):.3f},asetpts=PTS-STARTPTS,volume=eval=frame:volume='{BACKSTAGE_MUSIC_VOLUME:.3f}*({envelope})'[music]",
    ]
    return (
        ";".join(filters) + ";"
        f"[0:a]apad=whole_dur={float(content_duration):.3f}[base_audio];"
        f"[music][base_audio]sidechaincompress=threshold={BACKSTAGE_MUSIC_DUCK_THRESHOLD:.3f}:"
        f"ratio={BACKSTAGE_MUSIC_DUCK_RATIO}:attack={BACKSTAGE_MUSIC_ATTACK_MS}:"
        f"release={BACKSTAGE_MUSIC_RELEASE_MS}:makeup=1[ducked_music];"
        f"[base_audio][ducked_music]amix=inputs=2:duration=longest:dropout_transition=2:normalize=0,"
        f"loudnorm=I={BACKSTAGE_MUSIC_TARGET_LUFS:.0f}:TP=-1.5:LRA=11[a]"
    )


def _merge_time_ranges(ranges: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    bounded = sorted((max(0.0, float(start)), min(duration, float(end))) for start, end in ranges if float(end) > float(start))
    merged: list[tuple[float, float]] = []
    for start, end in bounded:
        if not merged or start > merged[-1][1] + 0.05:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _background_music_runs(content_duration: float, muted_ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return preferred music beds, with a fallback that never removes music.

    Eighteen seconds remains the preferred documentary bed length. If a cut
    pattern has no such run, keep shorter usable runs instead of producing a
    silent export; editorial content and clean bookends are more important
    than a hard minimum.
    """
    duration = max(0.0, float(content_duration))
    merged = _merge_time_ranges(muted_ranges, duration)
    cursor = 0.0
    all_runs: list[tuple[float, float]] = []
    for start, end in merged:
        if start - cursor > 0.05:
            all_runs.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor > 0.05:
        all_runs.append((cursor, duration))
    # Do not select the opening of a source song: those seconds commonly
    # contain a spoken slate (for example, “take one”). Prefer interior beds
    # in the edit, while retaining a fallback for unusually dense edits.
    interior = [
        (max(start, BACKSTAGE_MUSIC_EDGE_GUARD), min(end, duration - BACKSTAGE_MUSIC_EDGE_GUARD))
        for start, end in all_runs
        if end > BACKSTAGE_MUSIC_EDGE_GUARD and start < duration - BACKSTAGE_MUSIC_EDGE_GUARD
    ]
    interior = [run for run in interior if run[1] - run[0] > 0.05]
    usable = [run for run in interior if run[1] - run[0] >= 4.0]
    return usable or interior or all_runs


def _music_seek_offset(path: str, content_duration: float) -> float:
    """Start the source song in its middle, never at the spoken intro."""
    try:
        duration = float((ffprobe(path).get("format") or {}).get("duration") or 0.0)
    except Exception:
        duration = 0.0
    if duration <= 0.0:
        return 0.0
    if duration <= 20.0:
        return max(0.0, duration / 2.0 - max(1.0, content_duration / 2.0))
    # A point in the central third leaves enough material for a long edit and
    # is safely past any spoken intro.
    return min(max(10.0, duration * 0.42), max(0.0, duration - 10.0))


def _music_silence_ranges(content_duration: float, muted_ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Convert dialogue/clip-audio ranges into all ranges where music is off."""
    runs = _background_music_runs(content_duration, muted_ranges)
    silence: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in runs:
        if start > cursor:
            silence.append((cursor, start))
        cursor = end
    if cursor < float(content_duration):
        silence.append((cursor, float(content_duration)))
    return silence
def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate an ffmpeg process and its descendants when a stage unwinds."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.terminate()
        except OSError:
            return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
        process.wait()


def _run_backstage_ffmpeg(
    command: list[str],
    *,
    progress_callback: ProgressCallback | None = None,
    cut_boundaries: list[float] | None = None,
) -> None:
    """Run Backstage ffmpeg with cancellation-safe, cut-level progress."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise FFmpegError(f"{type(exc).__name__}: {exc}") from exc

    total_cuts = len(cut_boundaries or [])
    completed = 0
    output_lines: list[str] = []
    try:
        if progress_callback and total_cuts:
            progress_callback(0, f"Rendering cut 0 of {total_cuts}")
        assert process.stdout is not None
        for line in process.stdout:
            output_lines.append(line.rstrip())
            match = re.match(r"out_time_ms=(\d+)", line.strip())
            if not match or not total_cuts:
                continue
            elapsed = int(match.group(1)) / 1_000_000
            next_completed = sum(boundary <= elapsed + 0.05 for boundary in cut_boundaries or [])
            if next_completed > completed:
                completed = next_completed
                if progress_callback:
                    progress_callback(
                        int(completed / total_cuts * 90),
                        f"Rendering cut {completed} of {total_cuts}",
                    )
        return_code = process.wait()
    except BaseException:
        _terminate_process(process)
        raise
    if return_code != 0:
        detail = "\n".join(output_lines).strip() or f"ffmpeg exited with code {return_code}"
        raise FFmpegError(f"ffmpeg exited with code {return_code}: {detail[-4000:]}")
    if progress_callback and total_cuts and completed < total_cuts:
        progress_callback(90, f"Rendering cut {total_cuts} of {total_cuts}")


def _run_backstage_ffmpeg_checked(command: list[str]) -> None:
    """Run a non-progress Backstage ffmpeg pass without hiding OS errors."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise FFmpegError(f"{type(exc).__name__}: {exc}") from exc
    try:
        stdout, stderr = process.communicate()
    except BaseException:
        _terminate_process(process)
        raise
    if process.returncode != 0:
        detail = (stderr or stdout or "").strip() or f"ffmpeg exited with code {process.returncode}"
        raise FFmpegError(f"ffmpeg exited with code {process.returncode}: {detail[-4000:]}")


def _srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _backstage_segment_offsets(segments: list[dict[str, Any]]) -> list[float]:
    """Return video-timeline starts using the exact xfade duration formula."""
    offsets: list[float] = []
    cursor = 0.0
    for index, segment in enumerate(segments):
        offsets.append(cursor)
        duration = float(segment.get("duration_sec") or 0.0)
        if index < len(segments) - 1:
            next_duration = float(segments[index + 1].get("duration_sec") or 0.0)
            fade = min(BACKSTAGE_VIDEO_CROSSFADE, duration / 3.0, next_duration / 3.0)
            cursor += duration - max(0.01, fade)
        else:
            cursor += duration
    return offsets


def _backstage_subtitle_entries(segments: list[dict[str, Any]], offsets: list[float]) -> list[tuple[float, float, str]]:
    entries: list[tuple[float, float, str]] = []
    for index, (segment, offset) in enumerate(zip(segments, offsets)):
        clip_start = float(segment.get("clip_start_sec") or 0.0)
        segment_duration = float(segment.get("duration_sec") or 0.0)
        # Crossfades make neighbouring video intervals overlap internally, but
        # a subtitle belongs to exactly one clip. The next segment offset is
        # therefore the hard ownership boundary for the current cue.
        ownership_end = offsets[index + 1] if index + 1 < len(offsets) else offset + segment_duration
        sequence_items = segment.get("story_sequences") or []
        if sequence_items:
            items = []
            for sequence in sequence_items:
                translated = str(sequence.get("english_text") or "").strip()
                if translated:
                    items.extend(_split_translated_sequence(sequence.get("start_sec"), sequence.get("end_sec"), translated))
                else:
                    # A scored sequence is the translation unit. Its bites do
                    # not have independent LLM translations, so keep one
                    # accurately timed cue rather than falling back to Spanish.
                    translated = str(sequence.get("text") or "").strip()
                    if translated and _looks_english(translated):
                        items.append({"start_sec": sequence.get("start_sec"), "end_sec": sequence.get("end_sec"), "text": translated})
        else:
            items = segment.get("transcription_segments") or []
        seen: set[tuple[float, float, str]] = set()
        for item in items:
            # Subtitle times are source-clip times. First clip them to the
            # actual selected interval, then add the segment's export offset.
            # This prevents a cue from a sequence crossing a cut boundary from
            # leaking into the preceding/following shot.
            relative_start = max(0.0, float(item.get("start_sec") or 0.0) - clip_start)
            relative_end = min(segment_duration, ownership_end - offset, float(item.get("end_sec") or 0.0) - clip_start)
            if relative_end <= relative_start:
                continue
            start = offset + relative_start
            end = offset + relative_end
            text = str(item.get("text") or "").strip()
            key = (round(start, 3), round(end, 3), text)
            if text and end > start and key not in seen:
                entries.append((start, end, text))
                seen.add(key)
    return entries


def _backstage_final_subtitle_entries(transcription: dict[str, Any], segments: list[dict[str, Any]], duration: float) -> list[tuple[float, float, str]]:
    """Use timestamps from the already-mounted video's audio.

    Manual text corrections replace the automatically translated cues inside
    their selected cut interval. This is deliberately a final-video timeline;
    no source timestamp remapping is involved.
    """
    rows = [row for source in transcription.get("sources") or [] for row in source.get("segments") or []]
    entries = []
    for row in rows:
        start = max(0.0, float(row.get("start_sec") or 0.0))
        end = min(duration, float(row.get("end_sec") or 0.0))
        text = str(row.get("text") or "").strip()
        if text and end > start:
            entries.append((start, end, text))
    offsets = _backstage_segment_offsets(segments)
    corrections = []
    for segment, offset in zip(segments, offsets):
        text = str(segment.get("subtitle_text") or "").strip()
        if not text:
            continue
        start = max(0.0, offset)
        end = min(duration, offset + float(segment.get("duration_sec") or 0.0))
        if end > start:
            corrections.append((start, end, text))
    for start, end, text in corrections:
        entries = [row for row in entries if row[1] <= start or row[0] >= end]
        entries.append((start, end, text))
    return sorted(entries, key=lambda row: (row[0], row[1]))


def _parchment_intervals(duration: float, messages: list[str]) -> list[tuple[float, float]]:
    clean = [str(message).strip() for message in messages if str(message).strip()][:4]
    if not clean:
        return []
    durations = [min(6.0, max(4.0, 4.0 + 0.35 * (len(message.split()) / 10.0))) for message in clean]
    return [
        (max(0.0, duration * (index + 1) / (len(clean) + 1) - card_duration / 2.0),
         min(duration, duration * (index + 1) / (len(clean) + 1) + card_duration / 2.0))
        for index, card_duration in enumerate(durations)
    ]


def _render_parchment_cards(directory: Path, messages: list[str], width: int, height: int, duration: float) -> list[tuple[Path, float, float]]:
    from PIL import Image, ImageDraw, ImageFont
    clean = [str(message).strip() for message in messages if str(message).strip()][:4]
    intervals = _parchment_intervals(duration, clean)
    if not intervals:
        return []
    directory.mkdir(parents=True, exist_ok=True)
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    parchment_path = bundle_root / "assets" / "parchment_full.png"
    if not parchment_path.is_file():
        raise FileNotFoundError(f"Clean parchment asset is missing: {parchment_path}")
    try:
        font_path = str(bundle_root / "assets" / "fonts" / "BigCaslon.ttf")
        if not Path(font_path).exists():
            font_path = "/Library/Fonts/ACaslonPro-Regular.otf"
        font = ImageFont.truetype(font_path, 46)
    except OSError:
        font = ImageFont.load_default()
    parchment = Image.open(parchment_path).convert("RGBA")
    parchment_height = int(height * 0.88)
    parchment = parchment.resize((int(parchment.width * parchment_height / parchment.height), parchment_height), Image.Resampling.LANCZOS)
    parchment_x = (width - parchment.width) // 2
    box_width = int(parchment.width * 0.62)
    box_height = int(parchment.height * 0.55)
    box_left = (width - box_width) // 2
    box_top = int((height - parchment.height) / 2 + parchment.height * 0.225 - parchment.height * 0.04)
    cards = []
    for index, (message, interval) in enumerate(zip(clean, intervals)):
        image = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        image.alpha_composite(parchment, (parchment_x, (height - parchment.height) // 2))
        draw = ImageDraw.Draw(image)
        size = 46
        while size > 22:
            font = ImageFont.truetype(font_path, size) if Path(font_path).exists() else ImageFont.load_default()
            lines = textwrap.wrap(message, width=max(18, int(box_width / max(1, size * 0.52)))) or [message]
            line_height = int(size * 1.35)
            if len(lines) * line_height <= box_height:
                break
            size -= 2
        top = box_top + (box_height - len(lines) * line_height) / 2
        for line_index, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (width - (bbox[2] - bbox[0])) / 2
            draw.text((x, top + line_index * line_height), line, font=font, fill=(55, 35, 18, 255))
        seal_font = ImageFont.truetype(font_path, max(24, int(parchment.width * 0.12))) if Path(font_path).exists() else font
        seal = "Z"
        seal_box = draw.textbbox((0, 0), seal, font=seal_font)
        draw.text((box_left + box_width - (seal_box[2] - seal_box[0]) - 18, box_top + box_height - (seal_box[3] - seal_box[1]) - 12), seal, font=seal_font, fill=(58, 42, 24, 217))
        card = directory / f"parchment-{index:02d}.png"
        image.save(card)
        cards.append((card, interval[0], interval[1]))
    return cards


def _looks_english(text: str) -> bool:
    """Conservative fallback for already-English source speech."""
    return not any(marker in text.lower() for marker in ("¿", "¡", " que ", " el ", " la ", " de ", " una ", " esto "))


def _split_translated_sequence(start: Any, end: Any, text: str) -> list[dict[str, Any]]:
    """Turn a translated conversation into phrase-sized, accurately timed cues."""
    sequence_start = float(start or 0.0)
    sequence_end = max(sequence_start, float(end or sequence_start))
    phrases = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(phrases) <= 1:
        return [{"start_sec": sequence_start, "end_sec": sequence_end, "text": text}]
    weights = [max(1, len(part.split())) for part in phrases]
    total = float(sum(weights))
    cursor = sequence_start
    rows = []
    for index, (phrase, weight) in enumerate(zip(phrases, weights)):
        phrase_end = sequence_end if index == len(phrases) - 1 else cursor + (sequence_end - sequence_start) * weight / total
        rows.append({"start_sec": cursor, "end_sec": phrase_end, "text": phrase})
        cursor = phrase_end
    return rows


def _ass_timestamp(seconds: float) -> str:
    total = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(total, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _ass_escape(text: str) -> str:
    lines = textwrap.wrap(text, width=44, break_long_words=False, break_on_hyphens=False) or [text]
    return "\\N".join(line.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}") for line in lines)


def _write_backstage_ass(path: Path, entries: list[tuple[float, float, str]]) -> None:
    rows = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "WrapStyle: 2", "",
        "[V4+ Styles]", "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Backstage,Arial,48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,3,2,2,80,80,70,1", "",
        "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    rows.extend(f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Backstage,,0,0,0,,{_ass_escape(text)}" for start, end, text in entries)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_backstage_srt(path: Path, segments: list[dict[str, Any]], offsets: list[float]) -> None:
    rows = []
    for number, (start, end, text) in enumerate(_backstage_subtitle_entries(segments, offsets), 1):
        rows.append(f"{number}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{text}\n")
    path.write_text("\n".join(rows), encoding="utf-8")


def _drawtext_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,").replace("'", "\\'").replace("%", "\\%")


def _backstage_drawtext_filter(segments: list[dict[str, Any]], offsets: list[float]) -> str:
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    filters = []
    for start, end, text in _backstage_subtitle_entries(segments, offsets):
        filters.append(f"drawtext=fontfile='{font}':text='{_drawtext_escape(text)}':fontcolor=white:fontsize=42:borderw=3:bordercolor=black:shadowx=2:shadowy=2:box=1:boxcolor=black@0.45:boxborderw=18:x=(w-text_w)/2:y=h-text_h-72:enable='between(t,{start:.3f},{end:.3f})'")
    return ",".join(filters)


def _render_subtitle_cards(directory: Path, entries: list[tuple[float, float, str]], width: int, height: int) -> list[tuple[Path, float, float]]:
    """Create transparent PNG cards when this FFmpeg lacks libass/drawtext."""
    from PIL import Image, ImageDraw, ImageFont
    directory.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 42)
    except OSError:
        font = ImageFont.load_default()
    cards = []
    for index, (start, end, text) in enumerate(entries):
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] > width - 220 and current:
                lines.append(current); current = word
            else:
                current = candidate
        if current: lines.append(current)
        line_height = 54
        box_height = len(lines) * line_height + 36
        y = height - box_height - 62
        box = (90, y, width - 90, y + box_height)
        draw.rounded_rectangle(box, radius=18, fill=(0, 0, 0, 155))
        for line_index, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            x = (width - (bbox[2] - bbox[0])) / 2
            draw.text((x + 2, y + 18 + line_index * line_height + 2), line, font=font, fill=(0, 0, 0, 220))
            draw.text((x, y + 18 + line_index * line_height), line, font=font, fill="white")
        card = directory / f"subtitle-{index:04d}.png"; image.save(card)
        cards.append((card, start, end))
    return cards


def _render_subtitle_track(directory: Path, entries: list[tuple[float, float, str]], width: int, height: int, duration: float) -> Path:
    """Render one transparent 1-fps subtitle track for FFmpeg's single overlay."""
    from PIL import Image, ImageDraw, ImageFont
    directory.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 42)
    except OSError:
        font = ImageFont.load_default()
    by_second = {second: text for start, end, text in entries for second in range(max(0, int(start)), min(int(duration) + 1, int(end) + 1))}
    for second in range(max(1, int(duration) + 1)):
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        text = by_second.get(second - 1)
        if text:
            draw = ImageDraw.Draw(image)
            words, lines, current = text.split(), [], ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if draw.textbbox((0, 0), candidate, font=font)[2] > width - 220 and current:
                    lines.append(current); current = word
                else:
                    current = candidate
            if current: lines.append(current)
            line_height = 54
            box_height = len(lines) * line_height + 36
            y = height - box_height - 62
            draw.rounded_rectangle((90, y, width - 90, y + box_height), radius=18, fill=(0, 0, 0, 155))
            for line_index, line in enumerate(lines):
                bbox = draw.textbbox((0, 0), line, font=font)
                x = (width - (bbox[2] - bbox[0])) / 2
                draw.text((x + 2, y + 18 + line_index * line_height + 2), line, font=font, fill=(0, 0, 0, 220))
                draw.text((x, y + 18 + line_index * line_height), line, font=font, fill="white")
        image.save(directory / f"subtitle-{second - 1:04d}.png")
    return directory / "subtitle-%04d.png"


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


class BackstageExportStage(Stage):
    """Render source video and source audio together, with gentle audio joins."""

    name = "export"
    dependencies = ["edit"]

    def inputs_fingerprint(self, project: Project) -> str:
        path = artifact_path(project, "backstage_edit.json")
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        wizard = project.data.get("settings", {}).get("wizard", {}) or {}
        return stable_fingerprint({"edit": payload, "export": project.data.get("settings", {}).get("export", {}), "messages": wizard.get("backstage_messages", []), "version": BACKSTAGE_EXPORT_VERSION})

    def outputs(self, project: Project) -> dict[str, str]:
        return {"export_manifest": str(artifact_path(project, "export_manifest.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, str]:
        plan = json.loads(artifact_path(project, "backstage_edit.json").read_text(encoding="utf-8"))
        segments = [segment for segment in (plan.get("segments") or []) if segment.get("paper_mark") != "drop"]
        closings = [segment for segment in segments if segment.get("paper_mark") == "closing"]
        segments = [segment for segment in segments if segment.get("paper_mark") != "closing"] + closings
        if not segments:
            raise ValueError("Backstage found no moments with usable original audio")
        ffmpeg = tool_status().get("ffmpeg_path")
        if not ffmpeg:
            raise FFmpegError("ffmpeg is required for Backstage export")
        output_dir = project.exports_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        # ASS is an audit/intermediate artifact, never an exported deliverable.
        # Remove legacy loose copies left by earlier builds.
        for legacy_ass in output_dir.glob("*.ass"):
            try:
                legacy_ass.unlink()
            except OSError:
                pass
        run_id = str((project.data.get("settings", {}).get("wizard") or {}).get("backstage_run_id") or time.time_ns())
        output_path = output_dir / f"{project.data.get('name', 'Backstage')}-backstage-{time.strftime('%Y%m%d-%H%M%S')}-{run_id[-10:]}.mp4"
        with tempfile.TemporaryDirectory(prefix="zucker-backstage-", dir=str(project.cache_dir)) as tmp:
            joined = Path(tmp) / "joined.mp4"
            inputs: list[str] = []
            filters: list[str] = []
            video_labels: list[str] = []
            audio_labels: list[str] = []
            music_muted_ranges: list[tuple[float, float]] = []
            for index, segment in enumerate(segments):
                path = str(segment["source_path"])
                inputs.extend(["-ss", str(segment["clip_start_sec"]), "-t", str(segment["duration_sec"]), "-i", path])
                video_labels.append(f"v{index}")
                audio_labels.append(f"a{index}")
                filters.append(f"[{index}:v]setpts=PTS-STARTPTS,fps=30,format=yuv420p[v{index}]")
                filters.append(f"[{index}:a]aresample=48000,loudnorm=I=-20:TP=-1.5:LRA=11[a{index}]")
                if segment.get("background_music_policy") == "background_music_only":
                    muted_audio = f"am{index}"
                    filters.append(f"[a{index}]volume=0[{muted_audio}]")
                    audio_labels[-1] = muted_audio
            # Backstage is the one mode where the source audio belongs to the
            # image. Video and audio therefore use the same overlap duration;
            # this keeps a speaker exactly under the corresponding picture.
            video_current = video_labels[0]
            audio_current = audio_labels[0]
            current_duration = float(segments[0]["duration_sec"])
            cut_boundaries = [current_duration]
            if segments[0].get("background_music_policy") == "clip_music_only":
                music_muted_ranges.append((0.0, current_duration))
            for index in range(1, len(segments)):
                video_out = f"vx{index}"
                audio_out = f"ax{index}"
                duration = float(segments[index]["duration_sec"])
                if segments[index].get("background_music_policy") == "clip_music_only":
                    music_muted_ranges.append((current_duration, current_duration + duration))
                fade = min(BACKSTAGE_VIDEO_CROSSFADE, current_duration / 3.0, duration / 3.0)
                fade = max(0.01, fade)
                filters.append(
                    f"[{video_current}][{video_labels[index]}]xfade=transition=fade:duration={fade:.3f}:"
                    f"offset={max(0.0, current_duration - fade):.3f}[{video_out}]"
                )
                audio_fade = min(max(fade, float(segments[index].get("j_cut_sec") or 0.45), float(segments[index - 1].get("l_cut_sec") or 0.45)), current_duration / 3.0, duration / 3.0)
                filters.append(f"[{audio_current}][{audio_labels[index]}]acrossfade=d={max(0.05, audio_fade):.3f}:c1=tri:c2=tri[{audio_out}]")
                video_current = video_out
                audio_current = audio_out
                current_duration += duration - fade
                cut_boundaries.append(current_duration)
            filters.append(f"[{video_current}]format=yuv420p[vout]")
            filters.append(f"[{audio_current}]afade=t=in:st=0:d=0.08[aout]")
            command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1", *inputs, "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(joined)]
            _run_backstage_ffmpeg(command, progress_callback=progress_callback, cut_boundaries=cut_boundaries)
            progress_callback(92, "Joining cuts complete; mixing background music")
            music_path = str(plan.get("music_path") or "")
            if music_path and Path(music_path).exists():
                mixed = Path(tmp) / "music-mix.mp4"
                music_filter = _backstage_music_filter(current_duration, music_muted_ranges)
                music_script = Path(tmp) / "backstage-music-filter.txt"
                music_script.write_text(music_filter, encoding="utf-8")
                # FFmpeg 9 removed the deprecated -filter_complex_script
                # spelling.  The replacement -/filter_complex reads the graph
                # from a file and keeps the command independent of graph size.
                music_seek = _music_seek_offset(music_path, current_duration)
                mix_cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(joined), "-stream_loop", "-1", "-ss", f"{music_seek:.3f}", "-i", music_path, "-/filter_complex", str(music_script), "-map", "0:v", "-map", "[a]", "-t", f"{current_duration:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", str(mixed)]
                _run_backstage_ffmpeg_checked(mix_cmd)
                shutil.copy2(mixed, output_path)
            else:
                shutil.copy2(joined, output_path)
            messages = [str(value).strip() for value in ((project.data.get("settings", {}).get("wizard") or {}).get("backstage_messages") or []) if str(value).strip()][:4]
            parchment_cards = _render_parchment_cards(Path(tmp) / "parchment", messages, int(_target_size("youtube")[0]), int(_target_size("youtube")[1]), current_duration)
            if parchment_cards:
                parchment_video = Path(tmp) / "with-parchment.mp4"
                card_inputs = []
                card_filters = ["[0:v]format=yuv420p[cardbase]"]
                current_label = "cardbase"
                for index, (card, start, end) in enumerate(parchment_cards, start=1):
                    card_inputs.extend(["-loop", "1", "-i", str(card)])
                    next_label = f"card{index}"
                    fade_in = 0.8
                    fade_out = 0.6
                    card_filters.append(f"[{index}:v]format=rgba,fade=t=in:st=0:d={fade_in:.3f}:alpha=1,fade=t=out:st={max(0.0, end-start-fade_out):.3f}:d={fade_out:.3f}:alpha=1[paper{index}]")
                    card_filters.append(f"[{current_label}][paper{index}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'[{next_label}]")
                    current_label = next_label
                parchment_cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(output_path), *card_inputs, "-filter_complex", ";".join(card_filters), "-map", f"[{current_label}]", "-map", "0:a?", "-t", f"{current_duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", str(parchment_video)]
                _run_backstage_ffmpeg_checked(parchment_cmd)
                shutil.copy2(parchment_video, output_path)
            with_logo = Path(tmp) / "with-logo.mp4"
            logo = _personal_logo_path() or _logo_path()
            logo_cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(output_path)]
            outro_start = max(0.0, current_duration - BACKSTAGE_LOGO_DURATION)
            picture_fade_start = max(outro_start, current_duration - 2.0)
            audio_fade_start = max(0.0, current_duration - 2.0)
            if logo:
                logo_cmd += ["-loop", "1", "-i", str(logo), "-loop", "1", "-i", str(logo), "-f", "lavfi", "-i", f"color=c=black:s={int(_target_size('youtube')[0])}x{int(_target_size('youtube')[1])}:r=30", "-filter_complex", (f"[0:v]setpts=PTS-STARTPTS[base];[1:v]format=rgba,scale=-1:{int(_target_size('youtube')[1] * 0.72)},fade=t=in:st=0:d=1:alpha=1,fade=t=out:st=7:d=3:alpha=1[li];[2:v]format=rgba,scale=-1:{int(_target_size('youtube')[1] * 0.72)},fade=t=in:st={outro_start:.3f}:d=1:alpha=1[lo];[3:v]format=rgba,fade=t=in:st={picture_fade_start:.3f}:d=2:alpha=1[black];[base][li]overlay=(W-w)/2:(H-h)/2:enable='between(t,0,{BACKSTAGE_LOGO_DURATION:.3f})'[v1];[v1][black]overlay=0:0:enable='between(t,{picture_fade_start:.3f},{current_duration:.3f})'[v2];[v2][lo]overlay=(W-w)/2:(H-h)/2:enable='between(t,{outro_start:.3f},{current_duration:.3f})'[v];[0:a]asetpts=PTS-STARTPTS,afade=t=out:st={audio_fade_start:.3f}:d=2[aout]"), "-map", "[v]", "-map", "[aout]"]
            else:
                logo_cmd += ["-filter_complex", f"[0:v]setpts=PTS-STARTPTS,fade=t=out:st={picture_fade_start:.3f}:d=2[v];[0:a]asetpts=PTS-STARTPTS,afade=t=out:st={audio_fade_start:.3f}:d=2[a]", "-map", "[v]", "-map", "[a]"]
            logo_cmd += ["-t", f"{current_duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(with_logo)]
            progress_callback(96, "Adding logo")
            _run_backstage_ffmpeg_checked(logo_cmd)
            shutil.copy2(with_logo, output_path)
            subtitle_segments = list(segments)
            final_transcription = transcribe_sources(
                [{"path": str(output_path), "filename": output_path.name}],
                artifact_path(project, "backstage_final_transcription.json"),
                progress_callback=lambda value, message: progress_callback(97, f"Final subtitle audio: {message}"),
                model_name=str((project.data.get("settings", {}).get("wizard") or {}).get("backstage_whisper_model") or "small"),
                task="translate",
            )
            if final_transcription.get("status") not in {"ready", "ready_cached"}:
                raise RuntimeError(f"Final edited-audio transcription unavailable: {final_transcription.get('reason') or final_transcription.get('status')}")
            entries = _backstage_final_subtitle_entries(final_transcription, subtitle_segments, current_duration)
            ass_export_path = None
            if entries:
                subtitled = Path(tmp) / "subtitled.mp4"
                if not _ffmpeg_supports_filter("subtitles"):
                    raise FFmpegError(
                        f"Backstage English subtitles require an FFmpeg build with the subtitles/libass filter; "
                        f"selected binary is {ffmpeg} and `ffmpeg -filters` does not list subtitles"
                    )
                ass = Path(tmp) / "backstage-en.ass"
                _write_backstage_ass(ass, entries)
                # Keep the exact subtitle source beside the MP4 for auditing;
                # the temporary render directory is intentionally disposable.
                ass_export_path = project.cache_dir / "subtitles" / f"{output_path.stem}.ass"
                ass_export_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ass, ass_export_path)
                subtitle_path = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
                vf = f"subtitles=filename='{subtitle_path}'"
                subtitle_cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(output_path), "-vf", vf, "-map", "0:v", "-map", "0:a?", "-t", f"{current_duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", str(subtitled)]
                progress_callback(98, f"Burning English subtitles ({len(entries)} cues)")
                _run_backstage_ffmpeg_checked(subtitle_cmd)
                shutil.copy2(subtitled, output_path)
        duration = float((ffprobe(str(output_path)).get("format") or {}).get("duration") or 0.0)
        policies = {}
        for segment in segments:
            policy = str(segment.get("background_music_policy") or "background_music_allowed")
            policies[policy] = policies.get(policy, 0) + 1
        music_runs = _background_music_runs(current_duration, music_muted_ranges)
        subtitle_cue_count = len(entries) if "entries" in locals() else 0
        manifest = {"stage": "backstage_export", "platform": "backstage", "render_logic": "video-led original clip audio with duration-preserving per-clip fades, dynamically ducked audible music, mutually exclusive clip/background music policies, documentary music beds with measured fades, ASS subtitles burned in one libass pass, and cached intro/outro logo", "warnings": [], "music": {"path": str(plan.get("music_path") or ""), "base_volume": BACKSTAGE_MUSIC_VOLUME, "duck_threshold": BACKSTAGE_MUSIC_DUCK_THRESHOLD, "duck_ratio": BACKSTAGE_MUSIC_DUCK_RATIO, "minimum_duration_sec": BACKSTAGE_MUSIC_MIN_DURATION, "fade_in_sec": BACKSTAGE_MUSIC_FADE_IN, "fade_out_sec": BACKSTAGE_MUSIC_FADE_OUT, "mixed": bool(plan.get("music_path") and Path(str(plan.get("music_path"))).exists()), "segment_policies": policies, "runs": [{"start_sec": round(start, 3), "end_sec": round(end, 3), "duration_sec": round(end - start, 3)} for start, end in music_runs]}, "subtitles": {"format": "ass", "path": str(ass_export_path) if ass_export_path else None, "burned_in": subtitle_cue_count > 0, "cue_count": subtitle_cue_count, "filter": "subtitles/libass"}, "exports": [{"platform": "backstage", "path": str(output_path), "filename": output_path.name, "duration_sec": duration, "cut_count": plan.get("cut_count", 0), "camera_usage": {}, "logo_duration_sec": BACKSTAGE_LOGO_DURATION, "logo_outro_duration_sec": BACKSTAGE_LOGO_DURATION}]}
        write_artifact_json(artifact_path(project, "export_manifest.json"), manifest)
        progress_callback(100, "Backstage export ready")
        return self.outputs(project)
