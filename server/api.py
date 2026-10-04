"""Versioned Flask API routes for Zucker Videos."""

from __future__ import annotations

import json
import hashlib
import io
import logging
import math
import os
import signal
import sys
import threading
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.datastructures import FileStorage
from urllib.parse import unquote

from core.engine import PipelineEngine, StageBlockedError, StageNotFoundError
from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.messages import t
from core.project import Project, ProjectError, create_project, load_project
from core.spherical_view import MAX_SPHERICAL_FOV, spherical_view_filter
from core.build_info import build_info
from core.spherical_view import (
    MAX_SPHERICAL_FOV,
    effective_fov,
    effective_pitch,
    effective_projection_control,
    effective_roll,
    normalize_projection_preset,
    view_parameters,
)
from core.media_validation import record_media_path
from core.normalization import cache_status, cleanup_unreferenced_cache, global_cache_root, migrate_project_normalization_cache
from core.retention import build_storage_report, cleanup_plan
from core.stages.sync import clear_manual_override, cleanup_closed_sync_diagnostics, generate_preview, generate_thumbnail, invalidate_stale_sync_artifact, set_manual_anchor, set_manual_override, set_manual_override_ranges
from core.shot_review import (
    _review_segments,
    _review_signature,
    render_review_thumbnails,
    replace_slots,
    review_items,
)
from core.shot_review import mark_review_render_failed, replace_slots, review_items, set_review_transition_types
from core.stages.export import TRANSITION_LIBRARY
from core.backstage_feedback import record_feedback
from core.stages.backstage import update_backstage_cue_text
from core.backstage_transcription import transcribe_sources
from server.inbox import (
    app_home,
    classify_paths,
    configured_source_folders,
    load_global_config,
    normalize_master_audio_extensions,
    normalize_source_folders,
    reconcile_registered_inputs,
    register_selected_inputs,
    save_global_config,
    save_uploads,
    scan_inbox,
    start_inbox_analysis,
    suggest_songs_json,
    unique_destination,
)
from server.media import send_file_with_range
from server.media_import import save_media_upload
from server.projects import delete_project_folder, find_project_by_inputs, input_signature, list_projects, project_input_signature
from server.wizard import _create_wizard_project, WizardRunner, is_single_source_reel, merge_spherical_landmarks, same_project_path, serialize_wizard_job, wizard_report, wizard_song_options
from captions.burn import _video_dimensions, burn as burn_captions
from captions.align import align_known_lyrics
from captions.model import Cue, CueTrack, Word
from captions.render import render_ass
from captions.sources import from_lrc, from_lyrics, from_srt, to_srt
from captions.styles import CAPTIONS_VERSION, get_style, list_styles

LOGGER = logging.getLogger(__name__)
_REVIEW_RENDER_LOCK = threading.Lock()
_REVIEW_RENDERING_KEYS: set[str] = set()


def _queue_review_thumbnail_render(project: Project, indices: set[int]) -> None:
    """Queue review thumbnails and return before FFmpeg work starts."""
    if not indices:
        return
    signature = _review_signature(_review_segments(project))
    key = f"{project.folder}:{signature}"
    with _REVIEW_RENDER_LOCK:
        if key in _REVIEW_RENDERING_KEYS:
            return
        _REVIEW_RENDERING_KEYS.add(key)

    def worker() -> None:
        try:
            render_review_thumbnails(project, indices, max_workers=2)
        finally:
            with _REVIEW_RENDER_LOCK:
                _REVIEW_RENDERING_KEYS.discard(key)

    threading.Thread(
        target=worker,
        name="review-thumbnail-batch",
        daemon=True,
    ).start()

FFMPEG_TIMEOUT_SECONDS = 30 * 60


def _terminate_ffmpeg_process(process: subprocess.Popen | None, timeout: float = 5.0) -> None:
    """Terminate ffmpeg and its process group without leaving an orphan."""
    if process is None or process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def _single_video_has_audio(video_paths: list[str]) -> bool:
    """Return whether the exact single Reel source carries usable audio."""
    if len(video_paths) != 1:
        return False
    try:
        return any(stream.get("codec_type") == "audio" for stream in ffprobe(video_paths[0]).get("streams") or [])
    except Exception:
        return False


@dataclass
class CompositionJob:
    id: str
    status: str = "running"
    progress: int = 0
    message: str = "Rendering final video"
    detail: str = "Preparing the final video render"
    stage: str = "compose"
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None
    result: dict[str, Any] | None = None
    project_path: str | None = None
    output_path: str | None = None
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        state = dict(self.__dict__)
        state["tasks"] = [dict(task) for task in dict(self.tasks).values()]
        return state


@dataclass
class AutoReadJob:
    id: str
    status: str = "running"
    progress: int = 0
    message: str = "Transcribing project audio"
    detail: str = "Preparing local Whisper transcription"
    error: str | None = None
    result: dict[str, Any] | None = None
    project_path: str | None = None
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _caption_audio_source(project: Project) -> dict[str, Any] | None:
    """Legacy source selector retained for projects without a mounted result."""
    wizard = project.data.get("settings", {}).get("wizard", {})
    inputs = project.data.get("inputs", {})
    candidates = [
        wizard.get("master_path"),
        (inputs.get("master") or {}).get("path") if isinstance(inputs.get("master"), dict) else inputs.get("master"),
    ]
    for item in inputs.get("videos") or []:
        if isinstance(item, dict):
            probe = item.get("probe") or {}
            if probe.get("audio_codec"):
                candidates.append(item.get("path"))
        else:
            candidates.append(item)
    for raw in candidates:
        path = Path(str(raw or "")).expanduser()
        if path.is_file():
            duration = None
            try:
                duration = float((ffprobe(str(path)).get("format") or {}).get("duration") or 0.0)
            except Exception:
                duration = None
            return {"path": str(path.resolve()), "filename": path.name, "duration_sec": duration}
    return None


def _caption_montage_source(project: Project, progress_callback=None) -> dict[str, Any]:
    """Return the clean, already-mounted MP4 used as Auto Read's timeline.

    Auto Read must see the same clock the user sees in the composition player.
    If the mode export has not happened yet, create/reuse that base export
    before starting Whisper; captions are never transcribed from source clips.
    """
    progress_callback = progress_callback or (lambda _value, _detail: None)
    base = _export_result(project)
    if not base:
        from core.stages.export import ExportStage

        progress_callback(5, "Preparing the mounted video for Auto Read")
        ExportStage().run(
            project,
            lambda value, detail: progress_callback(5 + round(min(1.0, float(value) / 100.0) * 20), detail),
        )
        base = _export_result(project)
    if not base:
        raise ValueError("No mounted video is available for Auto Read")
    path = Path(str(base.get("path") or "")).resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("The mounted video is missing or empty")
    metadata = ffprobe(str(path))
    streams = metadata.get("streams") or []
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not audio:
        raise ValueError("The mounted video has no audio track for Auto Read")
    duration = float((metadata.get("format") or {}).get("duration") or audio.get("duration") or 0.0)
    return {
        "path": str(path),
        "filename": path.name,
        "duration_sec": duration,
        "source_kind": "mounted_export",
        "source_platform": base.get("platform"),
    }


def _caption_lines(text: str, max_line_chars: int = 38) -> list[str]:
    """Keep generated captions readable without changing their timestamps."""
    words = str(text or "").split()
    if not words:
        return [""]
    if len(str(text)) <= max_line_chars:
        return [str(text)]
    midpoint = max(1, len(words) // 2)
    best = min(range(1, len(words)), key=lambda index: abs(len(" ".join(words[:index])) - len(" ".join(words[index:]))))
    return [" ".join(words[:best]), " ".join(words[best:])]


def _caption_words(cue: dict[str, Any]) -> tuple[Word, ...]:
    """Accept both UI word keys and the start_sec/end_sec Whisper keys."""
    words = []
    for word in cue.get("words", []) or []:
        start = word.get("start", word.get("start_sec", 0.0))
        end = word.get("end", word.get("end_sec", start))
        words.append(Word(str(word.get("text") or word.get("word") or ""), float(start), float(end)))
    return tuple(words)


class AutoReadRunner:
    """Asynchronous local-only Whisper transcription for the captions editor."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._job: AutoReadJob | None = None
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any] | None:
        with self._lock:
            return self._job.snapshot() if self._job else None

    def start(self, project: Project, requested_model: str | None = None) -> AutoReadJob:
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("Auto Read is already transcribing")
            job = AutoReadJob(id=f"auto-read-{uuid.uuid4().hex[:10]}", project_path=str(project.folder))
            self._job = job
            self._thread = threading.Thread(target=self._run, args=(job, project, requested_model), daemon=True, name="zucker-auto-read")
            self._thread.start()
            return job

    def _run(self, job: AutoReadJob, project: Project, requested_model: str | None) -> None:
        try:
            source = _caption_montage_source(
                project,
                lambda value, detail: self._set_progress(job, value, detail),
            )
            wizard = project.data.get("settings", {}).get("wizard", {})
            requested_model = str(requested_model or wizard.get("auto_read_whisper_model") or "auto").strip().lower()
            if requested_model not in {"auto", "tiny", "base", "small", "medium", "large-v3"}:
                raise ValueError(f"Unsupported Auto Read model: {requested_model}")
            duration = float(source.get("duration_sec") or 0.0)
            model_name = "large-v3" if requested_model == "auto" else requested_model
            language_overrides = wizard.get("backstage_whisper_language_overrides") or {}
            artifact = project.cache_dir / "captions" / CAPTIONS_VERSION / "auto_read.json"

            def progress(value: int, detail: str) -> None:
                with self._lock:
                    if job.status == "running":
                        job.progress = max(0, min(100, int(value)))
                        job.detail = detail

            payload = transcribe_sources(
                [source],
                artifact,
                progress_callback=progress,
                model_name=model_name,
                task="transcribe",
                language_overrides=language_overrides,
                vad_filter=False,
            )
            if payload.get("status") != "ready":
                raise RuntimeError(str(payload.get("reason") or "Local Whisper transcription was unavailable"))
            transcription_source = (payload.get("sources") or [{}])[0]
            segments = transcription_source.get("segments") or []
            text = "\n\n".join(str(segment.get("text") or "").strip() for segment in segments if str(segment.get("text") or "").strip())
            cues = []
            for segment in segments:
                value = str(segment.get("text") or "").strip()
                start = float(segment.get("start_sec") or 0.0)
                end = max(start + 0.05, float(segment.get("end_sec") or start + 0.05))
                if value:
                    cues.append({
                        "lines": _caption_lines(value), "start": start, "end": end,
                        "words": segment.get("words") or [], "style_override": {},
                    })
            with self._lock:
                job.progress = 100
                job.status = "done"
                job.message = "Auto Read ready"
                job.detail = "Review the local audio transcription before synchronizing"
                job.result = {
                    "text": text,
                    "cues": cues,
                    "source_path": source["path"],
                    "source_filename": source["filename"],
                    "backend": payload.get("backend"),
                    "model": payload.get("model"),
                    "duration_sec": duration,
                    "media_diagnostics": transcription_source.get("media_diagnostics") or {},
                    "transcription_options": payload.get("transcription_options") or {},
                    "style": "autoread_fixed_white",
                    "source_kind": source.get("source_kind"),
                    "provenance": "project_audio_transcription",
                }
        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.error = str(exc)
                job.detail = "Auto Read failed"

    def _set_progress(self, job: AutoReadJob, value: int, detail: str) -> None:
        with self._lock:
            if job.status == "running":
                job.progress = max(0, min(100, int(value)))
                job.detail = detail


class CompositionRunner:
    """Post-export composition job: visual overlays first, captions last."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._job: CompositionJob | None = None
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen | None = None

    def status(self, project: Project | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._job
            if job and project and job.status == "running" and job.output_path:
                candidate = Path(job.output_path)
                try:
                    ready_on_disk = (
                        candidate.is_file()
                        and candidate.stat().st_size > 0
                        and candidate.stat().st_mtime >= job.started_at - 1.0
                        and time.time() - candidate.stat().st_mtime >= 1.0
                    )
                except OSError:
                    ready_on_disk = False
                if ready_on_disk:
                    try:
                        base = _composition_base_result(project)
                        spec_path = project.folder / "overlay_spec.json"
                        spec = (
                            json.loads(spec_path.read_text(encoding="utf-8"))
                            if spec_path.is_file()
                            else {"images": [], "videos": []}
                        )
                        validation = (
                            _validate_composition_output(Path(str(base.get("path") or "")), candidate, spec)
                            if base
                            else {"ok": False, "reason": "composition_validation:no_clean_base"}
                        )
                        process_alive = bool(self._process and self._process.poll() is None)
                        worker_alive = bool(self._thread and self._thread.is_alive())
                        if validation.get("ok"):
                            job.progress = 100
                            job.status = "done"
                            job.message = "Final video ready"
                            job.detail = "Recovered validated final export from disk"
                            job.result = {
                                **(base or {}),
                                "path": str(candidate),
                                "filename": candidate.name,
                                "media_url": "/api/v1/wizard/result",
                            }
                            LOGGER.warning("Recovered composition completion from disk path=%s", candidate)
                        elif not process_alive and not worker_alive:
                            job.status = "failed"
                            job.error = str(validation.get("reason") or "composition output is invalid")
                            job.detail = "The composition stopped without a valid final export"
                            LOGGER.error("Composition recovery rejected path=%s reason=%s", candidate, job.error)
                    except Exception as exc:
                        # Polling must never turn an incomplete MP4 into HTTP 500.
                        process_alive = bool(self._process and self._process.poll() is None)
                        worker_alive = bool(self._thread and self._thread.is_alive())
                        if not process_alive and not worker_alive:
                            job.status = "failed"
                            job.error = f"composition recovery failed: {exc}"
                            job.detail = "The composition stopped before producing a valid final export"
                        else:
                            LOGGER.debug("Composition recovery check deferred: %s", exc)
            return job.snapshot() if job else None

    def reset(self) -> None:
        with self._lock:
            if self._job and self._job.status == "running":
                self.cancel()
            self._job = None

    def cancel(self) -> bool:
        with self._lock:
            job = self._job
            process = self._process
            if not job or job.status != "running":
                return False
            job.status = "cancelled"
            job.message = "Composition cancelled"
            job.detail = "The active FFmpeg process was stopped"
        _terminate_ffmpeg_process(process)
        return True

    def start(self, project: Project) -> CompositionJob:
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("A composition is already running")
            job = CompositionJob(id=f"compose-{uuid.uuid4().hex[:10]}", project_path=str(project.folder))
            self._job = job
            self._thread = threading.Thread(target=self._run, args=(job, project), daemon=True, name="zucker-compose")
            self._thread.start()
            return job

    def _run(self, job: CompositionJob, project: Project) -> None:
        try:
            base = _composition_base_result(project)
            if not base:
                raise RuntimeError("No base export is available for composition")
            if str(base.get("platform") or "") == "reel":
                # The mode export historically baked its saved Reel overlays
                # into the base file. Result must start from a clean base and
                # apply exactly the current Overlay & Captions state once;
                # otherwise removed flyers (and a removed logo-like flyer)
                # survive as ghost pixels even when overlay_spec.json is empty.
                from core.stages.export import ExportStage

                wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
                saved_overlay_state = {
                    key: wizard.get(key)
                    for key in ("reel_text_overlays", "reel_image_overlays", "reel_video_overlays")
                }
                wizard.update({
                    "reel_text_overlays": [],
                    "reel_image_overlays": [],
                    "reel_video_overlays": [],
                })
                project.save()
                try:
                    job.progress = 2
                    job.message = "Rendering final video"
                    job.detail = "Preparing a clean Reel base before the final render"
                    def base_progress(percent: int, detail: str) -> None:
                        job.detail = str(detail)
                        job.tasks["base"] = {"id": "base", "label": "Rendering clean Reel base", "percent": percent, "detail": str(detail)}
                        _set_job_progress(job, 2 + round(min(1.0, float(percent) / 100.0) * 6))
                    ExportStage().run(project, base_progress)
                finally:
                    wizard.update(saved_overlay_state)
                    project.save()
                base = _export_result(project)
                if not base:
                    raise RuntimeError("The refreshed Reel base export is unavailable")
            original_base_path = Path(base["path"]).resolve()
            composition_cache = project.cache_dir / "composition-base.mp4"
            composition_cache_meta = project.cache_dir / "composition-base.json"
            # Keep one clean private base so re-saving captions/flyers never
            # compounds the previous final export, but refresh it whenever the
            # mounted export changes.  The old existence-only check silently
            # composed new UI state over a stale edit/export.
            source_stat = original_base_path.stat()
            expected_cache_meta = {
                "source": str(original_base_path),
                "source_size": int(source_stat.st_size),
                "source_mtime_ns": int(source_stat.st_mtime_ns),
            }
            cache_is_current = False
            if composition_cache.is_file() and composition_cache.stat().st_size > 0 and composition_cache_meta.is_file():
                try:
                    cache_is_current = json.loads(composition_cache_meta.read_text(encoding="utf-8")) == expected_cache_meta
                except (OSError, json.JSONDecodeError):
                    cache_is_current = False
            if not cache_is_current:
                shutil.copy2(original_base_path, composition_cache)
                composition_cache_meta.write_text(
                    json.dumps(expected_cache_meta, indent=2) + "\n",
                    encoding="utf-8",
                )
            _composition_event(project, job, "cache_miss" if not cache_is_current else "cache_hit", input_path=original_base_path, output_path=composition_cache, duration=None, reason="composition_base_provenance")
            base_path = composition_cache
            output_stem = original_base_path.stem
            render_platform = str(
                base.get("platform")
                or project.data.get("settings", {}).get("wizard", {}).get("platform")
                or ""
            ).strip().lower()
            youtube_longform = render_platform in {"youtube", "youtube_longform", "youtube_horizontal"}
            spec_path = project.folder / "overlay_spec.json"
            track_path = project.folder / "cue_track.json"
            spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.is_file() else {"images": [], "videos": []}
            if youtube_longform:
                # Long-form YouTube is deliberately a clean horizontal export:
                # no flyer/image overlays and no caption/letterbox pass. Keep
                # the saved caption state intact so TikTok/backstage can use it
                # later, but never let it change the YouTube render.
                spec = {"images": [], "videos": []}
            track_data = json.loads(track_path.read_text(encoding="utf-8")) if track_path.is_file() else {"cues": [], "style": "clean_bottom"}
            header = track_data.get("header") if isinstance(track_data.get("header"), dict) else {}
            logo = _composition_logo(project, str(header.get("logo_source") or "none"))
            logo_overlay = header.get("logo_overlay") if isinstance(header.get("logo_overlay"), dict) else {}
            LOGGER.info("Composition source base=%s overlay_spec=%s cue_track=%s logo=%s", base_path, spec_path, track_path, logo)
            job.progress = 8
            job.message = "Rendering final video"
            job.detail = f"Overlay pass: {len(spec.get('images') or [])} flyer(s), {len(spec.get('videos') or [])} video overlay(s), logo={'yes' if logo else 'no'}"
            composed = project.cache_dir / f"{output_stem}_overlay-composed.mp4"
            _composition_event(project, job, "composition_input", input_path=base_path, output_path=composed, duration=_media_duration(base_path), reason="clean_base_selected")
            def overlay_progress(value: float) -> None:
                percent = max(0, min(100, round(float(value) * 100)))
                job.detail = f"Overlay pass (flyer/logo): {percent}%"
                job.tasks["overlays"] = {"id": "overlays", "label": "Flyers, images and logo", "percent": percent, "detail": job.detail}
                _set_job_progress(job, 10 + round(float(value) * 45))
            def register_overlay_process(process: subprocess.Popen | None) -> None:
                with self._lock:
                    self._process = process
                    cancelled = job.status == "cancelled"
                if process is not None and cancelled and process.poll() is None:
                    _terminate_ffmpeg_process(process)

            _compose_visual_overlays(
                base_path, spec, composed, overlay_progress,
                logo_path=logo, logo_overlay=logo_overlay,
                process_callback=register_overlay_process,
            )
            with self._lock:
                self._process = None
            _composition_event(project, job, "composition_filter", input_path=base_path, output_path=composed, duration=_media_duration(composed), reason="finite_overlay_eof_pass", extra={"image_count": len(spec.get("images") or []), "video_count": len(spec.get("videos") or []), "logo": bool(logo)})
            if job.status == "cancelled":
                return
            cues = tuple(
                Cue(
                    tuple(str(line) for line in cue.get("lines", [])),
                    float(cue["start"]),
                    float(cue.get("end", cue["start"])),
                    _caption_words(cue),
                    dict(cue.get("style_override") or {}),
                )
                for cue in track_data.get("cues", [])
            )
            track = CueTrack(cues, str(track_data.get("lang") or "und"))
            style = get_style(str(track_data.get("style") or "clean_bottom"))
            letterbox = track_data.get("letterbox") if isinstance(track_data.get("letterbox"), dict) else None
            final = project.exports_dir / (
                f"{output_stem}_composed.mp4"
                if youtube_longform
                else f"{output_stem}_composed-captions.mp4"
            )
            with self._lock:
                job.output_path = str(final)
            needs_caption_pass = (not youtube_longform) and (
                bool(cues)
                or bool(header.get("title_enabled") and header.get("title"))
                or bool(letterbox and letterbox.get("enabled"))
            )
            if needs_caption_pass:
                job.progress = 55
                job.message = "Rendering final video"
                job.detail = "Caption pass: burning captions onto the rendered video"
                composition_duration = _media_duration(composed)
                def burn_progress(seconds: float) -> None:
                    duration = composition_duration
                    percent = round(min(1.0, seconds / duration if duration else 0.0) * 100)
                    job.detail = f"Caption pass: {percent}%"
                    job.tasks["captions"] = {"id": "captions", "label": "Burning captions", "percent": percent, "detail": job.detail}
                    _set_job_progress(job, 55 + round(min(1.0, seconds / duration if duration else 0.0) * 40))
                output = burn_captions(
                    composed,
                    _expand_caption_animations(track),
                    style,
                    output_path=final,
                    header=header,
                    # Flyer and logo are already in the first composition pass.
                    logo_path=None,
                    letterbox=letterbox,
                    progress_callback=burn_progress,
                    process_callback=register_overlay_process,
                )
            else:
                # YouTube has no captions.  Do not run a second full video
                # transcode just to burn an empty ASS file; copy the already
                # composed horizontal result as the single final export.
                job.progress = 96
                job.message = "Rendering final video"
                job.detail = "Finalizing the horizontal YouTube export"
                _remux_shortest(composed, final, process_callback=register_overlay_process)
                output = final
            if job.status == "cancelled":
                output.unlink(missing_ok=True)
                return
            validation = _validate_composition_output(base_path, output, spec)
            _composition_event(project, job, "composition_validation", input_path=base_path, output_path=output, duration=validation.get("duration"), reason=validation.get("reason"), extra=validation)
            if not validation["ok"]:
                output.unlink(missing_ok=True)
                composed.unlink(missing_ok=True)
                job.status = "failed"
                job.error = validation["reason"]
                job.detail = "Final composition rejected; clean base export preserved"
                _composition_event(project, job, "composition_output_rejected", input_path=base_path, output_path=output, duration=validation.get("duration"), reason=validation.get("reason"), extra=validation)
                return
            _composition_event(project, job, "composition_output", input_path=base_path, output_path=output, duration=validation.get("duration"), reason="validated_before_publish")
            job.progress = 100
            job.status = "done"
            job.message = "Final video ready"
            job.detail = "Validated final export published from the saved project state"
            job.result = {**base, "path": str(output), "filename": output.name, "media_url": "/api/v1/wizard/result"}
            manifest_path = ((project.data.get("stages") or {}).get("export") or {}).get("outputs", {}).get("export_manifest")
            if manifest_path:
                try:
                    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
                    exports = manifest.get("exports") or []
                    if exports:
                        exports[0]["path"] = str(output)
                        exports[0]["filename"] = output.name
                        manifest["exports"] = exports
                        Path(manifest_path).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                except (OSError, json.JSONDecodeError):
                    LOGGER.warning("Could not update export manifest to the composed result", exc_info=True)
            for candidate in project.exports_dir.glob("*.mp4"):
                if candidate.resolve() != output.resolve():
                    candidate.unlink(missing_ok=True)
            composed.unlink(missing_ok=True)
            project.data.setdefault("settings", {}).setdefault("wizard", {})["last_composed_result"] = str(output)
            project.save()
            LOGGER.info("Composition complete base=%s overlays=%s final=%s", original_base_path, composed, output)
        except Exception as exc:
            if job.status == "cancelled":
                return
            LOGGER.exception("Composition failed for %s", project.folder)
            job.status = "failed"
            job.error = str(exc)
            job.detail = "The final composition failed"
        finally:
            with self._lock:
                process = self._process
                self._process = None
            if process and process.poll() is None:
                _terminate_ffmpeg_process(process)


def _set_job_progress(job: CompositionJob, progress: int) -> None:
    if job.status == "running":
        job.progress = max(job.progress, min(98, int(progress)))


def _composition_event(project: Project, job: CompositionJob, event: str, *, input_path: Path | None = None,
                       output_path: Path | None = None, duration: float | None = None,
                       reason: str | None = None, extra: dict[str, Any] | None = None) -> None:
    """Write unambiguous, machine-readable composition evidence."""
    payload = {
        "event": event,
        "project_path": str(project.folder),
        "job_id": job.id,
        "git_commit": build_info().get("git_commit"),
        "input_path": str(input_path) if input_path else None,
        "output_path": str(output_path) if output_path else None,
        "duration": duration,
        "camera": None,
        "timestamp": time.time(),
        "selection_reason": reason,
    }
    if extra:
        payload.update(extra)
    log_path = project.cache_dir / "logs" / "composition.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _composition_base_result(project: Project) -> dict[str, Any] | None:
    """Return the clean export, never the last composed result."""
    manifest_result = _export_result(project)
    cache = project.cache_dir / "composition-base.mp4"
    meta = project.cache_dir / "composition-base.json"
    if cache.is_file() and cache.stat().st_size > 0 and meta.is_file():
        try:
            record = json.loads(meta.read_text(encoding="utf-8"))
            if int(record.get("source_size") or 0) == cache.stat().st_size:
                return {**(manifest_result or {}), "path": str(cache), "filename": cache.name}
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    result = manifest_result
    if not result:
        return None
    path = Path(str(result.get("path") or ""))
    if "_composed" in path.stem or "_overlay-composed" in path.stem or "_captions" in path.stem:
        candidates = sorted(project.exports_dir.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
        path = next((item for item in candidates if "_composed" not in item.stem and "_overlay-composed" not in item.stem and "_captions" not in item.stem and item.stat().st_size > 0), Path())
        if not path:
            return None
    return {**result, "path": str(path), "filename": path.name}


def _spherical_event(project: Project, event: str, *, input_path: Path | None = None,
                     output_path: Path | None = None, camera: str | None = None,
                     reason: str | None = None, extra: dict[str, Any] | None = None) -> None:
    payload = {
        "event": event,
        "project_path": str(project.folder),
        "job_id": f"spherical-{uuid.uuid4().hex[:10]}",
        "git_commit": build_info().get("git_commit"),
        "input_path": str(input_path) if input_path else None,
        "output_path": str(output_path) if output_path else None,
        "duration": None,
        "camera": camera,
        "timestamp": time.time(),
        "selection_reason": reason,
    }
    if extra:
        payload.update(extra)
    log_path = project.cache_dir / "logs" / "spherical.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _validate_composition_output(base: Path, output: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Reject black, truncated, malformed, or stream-mismatched results."""
    try:
        base_probe = ffprobe(str(base))
        probe = ffprobe(str(output))
        streams = probe.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        base_video = next((item for item in (base_probe.get("streams") or []) if item.get("codec_type") == "video"), None)
        duration = float((probe.get("format") or {}).get("duration") or 0.0)
        base_duration = float((base_probe.get("format") or {}).get("duration") or 0.0)
        if not video or not base_video:
            return {"ok": False, "reason": "composition_validation:no_video_stream", "duration": duration}
        if len(streams) > 2 or not audio:
            return {"ok": False, "reason": "composition_validation:expected_one_video_and_one_audio_stream", "duration": duration}
        if duration < base_duration - 0.5 or abs(float(video.get("duration") or duration) - base_duration) > 0.75:
            return {"ok": False, "reason": "composition_validation:duration_mismatch", "duration": duration, "base_duration": base_duration}
        if int(video.get("width") or 0) != int(base_video.get("width") or 0) or int(video.get("height") or 0) != int(base_video.get("height") or 0):
            return {"ok": False, "reason": "composition_validation:resolution_mismatch", "duration": duration}
        # A single representative sample after every overlay interval catches
        # the full-canvas opaque flyer failure without trusting job status.
        overlay_end = max(
            [0.0]
            + [max(0.0, float(item.get("start_sec") or 0.0)) + max(0.1, float(item.get("duration_sec") or 3.0)) for kind in ("images", "videos") for item in (spec.get(kind) or []) if isinstance(item, dict)]
        )
        # The first/last frames may intentionally be black intro/outro cards.
        # The decisive sample is just after the last configured overlay.
        samples = [min(base_duration - 0.05, overlay_end + 0.5)]
        if overlay_end + 0.5 >= base_duration:
            samples = [min(base_duration - 0.05, base_duration / 2.0)]
        for timestamp in sorted(set(round(max(0.0, value), 3) for value in samples)):
            command = [str(tool_status().get("ffmpeg_path") or "ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(output), "-frames:v", "1", "-vf", "scale=1:1,format=gray", "-f", "rawvideo", "-"]
            result = subprocess.run(command, capture_output=True, check=False)
            if not result.stdout or max(result.stdout) <= 1:
                return {"ok": False, "reason": f"composition_validation:black_frame_at_{timestamp:.3f}", "duration": duration, "timestamp": timestamp}
        return {
            "ok": True,
            "reason": "composition_validation:passed",
            "duration": duration,
            "base_duration": base_duration,
            "streams": [{"index": item.get("index"), "type": item.get("codec_type"), "codec": item.get("codec_name"), "width": item.get("width"), "height": item.get("height"), "duration": item.get("duration")} for item in streams],
        }
    except (FFmpegError, OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "reason": f"composition_validation:probe_failed:{exc}", "duration": 0.0}


def _media_duration(path: Path) -> float:
    try:
        probe = ffprobe(str(path))
        video = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), {})
        return float(video.get("duration") or (probe.get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _remux_shortest(source: Path, destination: Path, process_callback=None) -> Path:
    """Mux the result through a temporary file and publish it atomically."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    pending = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy", "-avoid_negative_ts", "make_zero", "-shortest", str(pending),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    if process_callback:
        process_callback(process)
    try:
        output, _ = process.communicate(timeout=FFMPEG_TIMEOUT_SECONDS)
        if process.returncode:
            raise subprocess.CalledProcessError(
                process.returncode, command, stderr=(output or "")[-4000:],
            )
        pending.replace(destination)
        return destination
    except subprocess.TimeoutExpired as exc:
        _terminate_ffmpeg_process(process)
        raise TimeoutError(f"ffmpeg remux timed out after {FFMPEG_TIMEOUT_SECONDS}s") from exc
    finally:
        if process.poll() is None:
            _terminate_ffmpeg_process(process)
        if process_callback:
            process_callback(None)
        pending.unlink(missing_ok=True)


def _default_brand_logo() -> Path:
    personal = Path(str(load_global_config().get("personal_logo_path") or "")).expanduser()
    if personal.is_file():
        return personal
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "web" / "logo_editor_green.png"


def _composition_logo(project: Project, mode: str) -> Path | None:
    wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
    if mode == "custom":
        candidate = Path(str(wizard.get("reel_logo_path") or "")).expanduser().resolve()
        return candidate if candidate.is_file() and candidate.parent == project.folder.resolve() else None
    if mode == "default":
        candidate = _default_brand_logo().resolve()
        return candidate if candidate.is_file() else None
    return None


def _hex_rgb(value: Any, default: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int, int]:
    raw = str(value or "").strip()
    if len(raw) == 7 and raw.startswith("#"):
        try:
            return tuple(int(raw[offset:offset + 2], 16) for offset in (1, 3, 5))
        except ValueError:
            pass
    return default


def _image_overlay_canvas(path: Path, raw: dict[str, Any], width: int, height: int, output: Path) -> None:
    from PIL import Image, ImageFilter

    image = Image.open(path).convert("RGBA")
    target_width = max(2, int(width * max(0.02, min(1.0, float(raw.get("width") or 0.35)))))
    image.thumbnail((target_width, height), Image.Resampling.LANCZOS)
    opacity = max(0.0, min(1.0, float(raw.get("opacity") or 1.0)))
    alpha = image.getchannel("A").point(lambda value: round(value * opacity))
    image.putalpha(alpha)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = int(float(raw.get("x") if raw.get("x") is not None else 0.5) * width - image.width / 2)
    y = int(float(raw.get("y") if raw.get("y") is not None else 0.5) * height - image.height / 2)
    tint = raw.get("tint_color") or raw.get("overlay_color")
    tint_strength = max(0.0, min(1.0, float(raw.get("tint_opacity") or 0.0)))
    if tint and tint_strength:
        tint_layer = Image.new("RGBA", image.size, (*_hex_rgb(tint), 255))
        image = Image.blend(image, tint_layer, tint_strength)
        image.putalpha(alpha)
    effect_alpha = image.getchannel("A")
    shadow_distance = max(0, int(float(raw.get("shadow_distance") or raw.get("shadow_offset") or 0)))
    shadow_blur = max(0, int(float(raw.get("shadow_blur") or 0)))
    shadow_opacity = max(0.0, min(1.0, float(raw.get("shadow_opacity") if raw.get("shadow_opacity") is not None else 0.0)))
    if shadow_distance or shadow_blur or shadow_opacity:
        shadow_mask = effect_alpha.point(lambda value: round(value * shadow_opacity))
        shadow = Image.new("RGBA", image.size, (*_hex_rgb(raw.get("shadow_color"), (0, 0, 0)), 0))
        shadow.putalpha(shadow_mask)
        if shadow_blur:
            shadow = shadow.filter(ImageFilter.GaussianBlur(shadow_blur))
        shadow_canvas = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_canvas.alpha_composite(shadow, (x + shadow_distance, y + shadow_distance))
        canvas.alpha_composite(shadow_canvas)
    glow_layers = max(0, min(8, int(float(raw.get("glow_layers") or 0))))
    glow_blur = max(0, int(float(raw.get("glow_blur") or 0)))
    if glow_layers and glow_blur:
        for layer_index in range(glow_layers, 0, -1):
            glow = Image.new("RGBA", image.size, (*_hex_rgb(raw.get("glow_color") or tint, (255, 255, 255)), 0))
            glow.putalpha(effect_alpha.point(lambda value, n=layer_index: round(value * min(1.0, 0.18 * n))))
            glow = glow.filter(ImageFilter.GaussianBlur(glow_blur * layer_index / glow_layers))
            canvas.alpha_composite(glow, (x, y))
    canvas.alpha_composite(image, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, "PNG")


def _compose_visual_overlays(
    source: Path,
    spec: dict[str, Any],
    destination: Path,
    progress_callback,
    *,
    logo_path: Path | None = None,
    logo_overlay: dict[str, Any] | None = None,
    process_callback=None,
) -> Path:
    """Render saved overlays and logo onto one full-length base export."""
    from tempfile import TemporaryDirectory

    probe = ffprobe(str(source))
    video_stream = next((stream for stream in probe.get("streams", []) if stream.get("codec_type") == "video"), {})
    width = int(video_stream.get("width") or 1080)
    height = int(video_stream.get("height") or 1920)
    duration = max(0.1, float(video_stream.get("duration") or (probe.get("format") or {}).get("duration") or 0.0))
    requested_images = [item for item in spec.get("images") or [] if isinstance(item, dict)]
    requested_videos = [item for item in spec.get("videos") or [] if isinstance(item, dict)]
    missing_overlays = [
        str(item.get("path") or "")
        for item in (*requested_images, *requested_videos)
        if not Path(str(item.get("path") or "")).is_file()
    ]
    if missing_overlays:
        raise RuntimeError("composition_overlay_source_missing: " + ", ".join(missing_overlays[:5]))
    images = requested_images
    videos = requested_videos
    valid_logo = Path(str(logo_path)).resolve() if logo_path and Path(str(logo_path)).is_file() else None
    overlay_count = len(images) + len(videos) + (1 if valid_logo else 0)
    if overlay_count == 0:
        _remux_shortest(source, destination, process_callback=process_callback)
        progress_callback(1.0)
        return destination

    with TemporaryDirectory(prefix="zucker-compose-") as tmp:
        tmp_dir = Path(tmp)
        inputs: list[str] = ["-i", str(source)]
        filters = ["[0:v]setpts=PTS-STARTPTS,fps=30[base]"]
        current = "base"
        for index, raw in enumerate(images):
            canvas = tmp_dir / f"image-{index}.png"
            _image_overlay_canvas(Path(str(raw["path"])).resolve(), raw, width, height, canvas)
            inputs += ["-loop", "1", "-i", str(canvas)]
            start = max(0.0, float(raw.get("start_sec") or 0.0))
            end = min(duration, start + max(0.1, float(raw.get("duration_sec") or 3.0)))
            label = f"image_{index}"
            filters.append(f"[{index + 1}:v]format=rgba,setpts=PTS-STARTPTS,fps=30,trim=duration={max(0.1, end - start):.3f},setpts=PTS-STARTPTS+{start:.3f}/TB[{label}_src]")
            filters.append(f"[{current}][{label}_src]overlay=0:0:eof_action=pass:repeatlast=0:shortest=0[{label}_out]")
            current = f"{label}_out"

        for offset, raw in enumerate(videos, start=len(images)):
            inputs += ["-stream_loop", "-1", "-i", str(Path(str(raw["path"])).resolve())]
            start = max(0.0, float(raw.get("start_sec") or 0.0))
            end = min(duration, start + max(0.1, float(raw.get("duration_sec") or 3.0)))
            scale = max(2, int(width * max(0.02, min(1.0, float(raw.get("width") or 0.35)))))
            x = max(0, int(float(raw.get("x") or 0.5) * width - scale / 2))
            y = max(0, int(float(raw.get("y") or 0.5) * height - scale / 2))
            label = f"video_{offset}"
            filters.append(f"[{offset + 1}:v]format=rgba,scale={scale}:-2,colorchannelmixer=aa={max(0.0, min(1.0, float(raw.get('opacity') or 1.0))):.3f},trim=duration={max(0.1, end - start):.3f},setpts=PTS-STARTPTS+{start:.3f}/TB[{label}]")
            filters.append(f"[{current}][{label}]overlay={x}:{y}:eof_action=pass:repeatlast=0:shortest=0[{label}_out]")
            current = f"{label}_out"

        if valid_logo:
            logo_index = 1 + len(images) + len(videos)
            inputs += ["-loop", "1", "-i", str(valid_logo)]
            overlay = logo_overlay or {}
            logo_width = max(40, int(width * max(0.05, min(0.9, float(overlay.get("width", 0.22))))))
            logo_x = max(0.0, min(1.0, float(overlay.get("x", 0.5))))
            logo_y = max(0.0, min(1.0, float(overlay.get("y", 0.08))))
            filters.append(f"[{logo_index}:v]format=rgba,scale={logo_width}:-1,trim=duration={duration:.3f},setpts=PTS-STARTPTS[composition_logo]")
            filters.append(f"[{current}][composition_logo]overlay=(W-w)*{logo_x:.5f}:(H-h)*{logo_y:.5f}:eof_action=pass:repeatlast=0:shortest=0[with_logo]")
            current = "with_logo"

        ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", "-stats_period", "0.5", "-progress", "pipe:1",
            *inputs, "-filter_complex", ";".join(filters),
            "-map", f"[{current}]", "-map", "0:a?", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-avoid_negative_ts", "make_zero", "-shortest", str(destination),
        ]
        # Merge FFmpeg diagnostics into the progress stream. Reading stdout and
        # stderr independently can deadlock when one pipe fills during a long
        # H.264 composition, leaving the UI on the last reported percentage.
        pending_destination = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        command[-1] = str(pending_destination)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=(os.name == "posix"),
        )
        if process_callback:
            process_callback(process)
        diagnostics: list[str] = []
        timed_out = threading.Event()
        watchdog_stop = threading.Event()

        def watchdog() -> None:
            if not watchdog_stop.wait(FFMPEG_TIMEOUT_SECONDS):
                timed_out.set()
                _terminate_ffmpeg_process(process)

        watchdog_thread = threading.Thread(target=watchdog, daemon=True, name="zucker-ffmpeg-watchdog")
        watchdog_thread.start()
        try:
            assert process.stdout is not None
            progress_callback(0.0)
            last_progress = 0.0
            for raw_line in process.stdout:
                line = raw_line.strip()
                if line.startswith(("out_time_ms=", "out_time_us=")):
                    try:
                        raw_value = float(line.split("=", 1)[1])
                        value = raw_value / 1_000_000.0
                        last_progress = min(1.0, max(last_progress, value / duration))
                        progress_callback(last_progress)
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
                elif line:
                    diagnostics.append(line)
            return_code = process.wait()
            if timed_out.is_set():
                raise TimeoutError(f"ffmpeg overlay timed out after {FFMPEG_TIMEOUT_SECONDS}s")
            if return_code:
                raise subprocess.CalledProcessError(
                    return_code,
                    command,
                    stderr="\n".join(diagnostics[-20:]),
                )
            pending_destination.replace(destination)
            progress_callback(1.0)
        finally:
            watchdog_stop.set()
            if process.poll() is None:
                _terminate_ffmpeg_process(process)
            if process_callback:
                process_callback(None)
            pending_destination.unlink(missing_ok=True)
    return destination

def _expand_caption_animations(track: CueTrack) -> CueTrack:
    """Encode per-cue slide/scale motion as short ASS cue segments.

    The captions package remains deliberately pure; this adapter translates
    UI-only animation overrides into ordinary position/size overrides before
    the package's single-pass burn is invoked.
    """
    expanded: list[Cue] = []
    for cue in track.cues:
        override = dict(cue.style_override or {})
        enter = str(override.pop("animation_in", "none") or "none")
        exit_ = str(override.pop("animation_out", "none") or "none")
        if enter == "fade":
            override["_fade_in_ms"] = 250
        if exit_ == "fade":
            override["_fade_out_ms"] = 250
        base_vertical = float(override.get("vertical", 68))
        base_size = float(override.get("size", 54))
        cuts = {float(cue.start), float(cue.end)}
        if enter in {"slide", "scale"}:
            cuts.update(float(cue.start) + step for step in (0.08, 0.16, 0.24) if float(cue.start) + step < float(cue.end))
        if exit_ in {"slide", "scale"}:
            cuts.update(float(cue.end) - step for step in (0.24, 0.16, 0.08) if float(cue.end) - step > float(cue.start))
        points = sorted(cuts)
        for index, (start, end) in enumerate(zip(points, points[1:])):
            local = dict(override)
            progress_in = min(1.0, max(0.0, (start - float(cue.start)) / 0.24))
            progress_out = min(1.0, max(0.0, (float(cue.end) - end) / 0.24))
            if enter == "slide" and start < float(cue.start) + 0.24:
                # Stay inside the frame while approaching the preset's
                # position. Horizontal centering remains controlled by the
                # preset's explicit ASS alignment.
                local["vertical"] = max(5.0, min(95.0, base_vertical + 12.0 * (1.0 - progress_in)))
            elif exit_ == "slide" and end > float(cue.end) - 0.24:
                local["vertical"] = max(5.0, min(95.0, base_vertical + 12.0 * progress_out))
            if enter == "scale" and start < float(cue.start) + 0.24:
                local["size"] = base_size * (0.72 + 0.28 * progress_in)
            elif exit_ == "scale" and end > float(cue.end) - 0.24:
                local["size"] = base_size * (0.72 + 0.28 * progress_out)
            expanded.append(Cue(cue.lines, start, end, cue.words, local))
    return CueTrack(tuple(expanded), track.lang)


def _invalidate_stale_sync_on_open(project: Project) -> None:
    if invalidate_stale_sync_artifact(project):
        LOGGER.warning("Invalidated stale sync_map.json for %s; sync will be recomputed", project.folder)
# This limit only matters for a plain browser tab (--dev mode), which has no
# choice but to upload file bytes over HTTP. The packaged desktop app
# references files in place by path (see handleDrop's file.path branch in
# web/app.js) and never needs to buffer a whole video through this server, so
# it gets no cap at all -- real camera/360 footage routinely runs many GB,
# and this used to block it even in the desktop app whenever the drag-drop
# path-detection fell through to the upload fallback.
BROWSER_UPLOAD_MAX_BYTES = 512 * 1024 * 1024
DESKTOP_UPLOAD_MAX_BYTES = None


@dataclass
class AppState:
    """Mutable process state shared by API routes."""

    engine: PipelineEngine
    project: Project | None = None
    dev: bool = False
    wizard: WizardRunner | None = None
    composition: CompositionRunner = field(default_factory=CompositionRunner)
    auto_read: AutoReadRunner = field(default_factory=AutoReadRunner)


def create_app(project_path: str | None = None, dev: bool = False) -> Flask:
    """Create and configure the Flask application."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    app = Flask(__name__, static_folder=str(root / "web"), static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = BROWSER_UPLOAD_MAX_BYTES if dev else DESKTOP_UPLOAD_MAX_BYTES
    load_global_config()
    state = AppState(engine=PipelineEngine(), dev=dev, wizard=WizardRunner())
    if project_path:
        state.project = load_project(project_path)
        _invalidate_stale_sync_on_open(state.project)
        reconciled = reconcile_registered_inputs(state.project)
        migrated = migrate_project_normalization_cache(state.project)
        if reconciled or migrated:
            LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
    app.config["ZUCKER_STATE"] = state

    if dev:
        _enable_cors(app)

    @app.before_request
    def start_request_timing() -> None:
        g.request_started = time.perf_counter()

    @app.after_request
    def add_no_cache_headers(response: Response) -> Response:
        elapsed_ms = (time.perf_counter() - g.request_started) * 1000
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        if elapsed_ms >= 2000 and request.path.startswith("/api/"):
            LOGGER.warning("Slow request %s %s: %.1f ms (HTTP %s)", request.method, request.path, elapsed_ms, response.status_code)
        if request.path == "/" or request.path.endswith((".html", ".css", ".js", ".png")):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.errorhandler(RequestEntityTooLarge)
    def api_upload_too_large(_: RequestEntityTooLarge) -> tuple[Response, int]:
        limit_mb = int(app.config["MAX_CONTENT_LENGTH"] or 0) // (1024 * 1024)
        return error_response(
            "upload_too_large",
            f"Browser uploads are limited to {limit_mb} MB. Put large videos in the Inbox or use the desktop folder picker.",
            413,
        )

    @app.get("/")
    def index() -> Response:
        return send_from_directory(app.static_folder or "", "index.html")

    @app.get("/advanced")
    def advanced() -> Response:
        return send_from_directory(app.static_folder or "", "advanced.html")

    @app.post("/api/v1/project")
    def api_create_project() -> tuple[Response, int] | Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        folder = str(body.get("folder") or "").strip()
        if not name or not folder:
            return error_response("bad_request", "name and folder are required", 400)
        try:
            state.project = create_project(name, folder)
            _remember_project(state.project)
            return jsonify(state.project.snapshot()), 201
        except ProjectError as exc:
            return error_response("project_error", str(exc), 409)

    @app.get("/api/v1/project")
    def api_get_project() -> Response:
        project = _require_project(state)
        if project.refresh_input_records():
            project.save()
        return jsonify(project.snapshot())

    @app.post("/api/v1/project/open")
    def api_open_project() -> Response:
        body = _json_body()
        folder = str(body.get("folder") or "").strip()
        if not folder:
            return error_response("bad_request", "folder is required", 400)
        try:
            state.composition.reset()
            state.project = load_project(folder)
            _invalidate_stale_sync_on_open(state.project)
            reconciled = reconcile_registered_inputs(state.project)
            migrated = migrate_project_normalization_cache(state.project)
            if reconciled or migrated:
                LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
            _remember_project(state.project)
            return jsonify(state.project.snapshot())
        except ProjectError as exc:
            return error_response(
                "project_unrecoverable",
                "Este proyecto no se puede recuperar. Los vídeos del Drop Here siguen disponibles; crea un proyecto nuevo.",
                409,
            )

    @app.post("/api/v1/inputs/videos")
    def api_register_videos() -> Response:
        project = _require_project(state)
        body = _json_body()
        paths = body.get("paths")
        append = bool(body.get("append", False))
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return error_response("bad_request", "paths must be a list of strings", 400)
        try:
            return jsonify(register_selected_inputs(project, video_paths=paths, append_videos=append))
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.post("/api/v1/inputs/master")
    def api_register_master() -> Response:
        project = _require_project(state)
        body = _json_body()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip()
        if not master and not songs:
            return error_response("bad_request", "master or songs is required", 400)
        try:
            return jsonify(register_selected_inputs(project, master_path=master or None, songs_path=songs or None))
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/inbox")
    def api_inbox() -> Response:
        try:
            return jsonify(scan_inbox())
        except OSError as exc:
            return error_response("inbox_error", str(exc), 500)

    @app.post("/api/v1/inbox/analysis/start")
    def api_inbox_analysis_start() -> Response:
        try:
            body = request.get_json(silent=True) or {}
            folder = body.get("folder")
            return jsonify(start_inbox_analysis(folder if isinstance(folder, str) and folder.strip() else None))
        except OSError as exc:
            return error_response("inbox_analysis_error", str(exc), 500)

    @app.get("/api/v1/settings/source-folders")
    def api_source_folders() -> Response:
        return jsonify({"source_folders": configured_source_folders()})

    @app.post("/api/v1/settings/source-folders")
    def api_save_source_folders() -> Response:
        body = _json_body()
        folders = body.get("source_folders", body.get("folders"))
        if not isinstance(folders, list) or not all(isinstance(folder, str) for folder in folders):
            return error_response("bad_request", "source_folders must be a list of absolute paths", 400)
        normalized = normalize_source_folders(folders)
        if not normalized:
            return error_response("bad_request", "Add at least one absolute source folder", 400)
        config = load_global_config()
        config["source_folders"] = normalized
        save_global_config(config)
        return jsonify({"source_folders": configured_source_folders()})

    @app.post("/api/v1/settings/master-audio-filter")
    def api_master_audio_filter() -> Response:
        body = _json_body()
        extensions = body.get("extensions")
        if not isinstance(extensions, list) or not all(isinstance(value, str) for value in extensions):
            return error_response("bad_request", "extensions must be a list", 400)
        normalized = normalize_master_audio_extensions(extensions)
        if not normalized:
            return error_response("bad_request", "Choose at least one audio master format", 400)
        config = load_global_config()
        config["master_audio_extensions"] = normalized
        save_global_config(config)
        return jsonify({"master_audio_extensions": normalized})

    @app.post("/api/v1/settings/personal-logo")
    def api_personal_logo() -> Response:
        config = load_global_config()
        branding_dir = app_home() / "Branding"
        branding_dir.mkdir(parents=True, exist_ok=True)
        source_path = ""
        uploaded = request.files.get("file")
        if uploaded and uploaded.filename:
            suffix = Path(uploaded.filename).suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                return error_response("bad_request", "Logo must be PNG, JPEG, or WebP", 400)
            destination = branding_dir / f"personal_logo{suffix}"
            uploaded.save(destination)
            source_path = str(destination)
        else:
            body = _json_body()
            raw_path = str(body.get("path") or "").strip()
            candidate = Path(raw_path).expanduser().resolve()
            if not raw_path or not candidate.is_file() or candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                return error_response("bad_request", "Choose a valid PNG, JPEG, or WebP logo", 400)
            destination = branding_dir / f"personal_logo{candidate.suffix.lower()}"
            shutil.copy2(candidate, destination)
            source_path = str(destination)
        config["personal_logo_path"] = source_path
        save_global_config(config)
        return jsonify({"personal_logo_path": source_path})

    @app.get("/api/v1/wizard/logo")
    def api_wizard_logo() -> Response:
        project = _require_project(state)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        custom = Path(str(wizard.get("reel_logo_path") or "")).expanduser()
        config = load_global_config()
        default = _default_brand_logo()
        mode = str(wizard.get("reel_logo_mode") or ("custom" if custom.is_file() else "none"))
        if mode not in {"custom", "default", "none"}:
            mode = "none"
        def versioned(url: str, path: Path) -> str:
            try:
                stat = path.stat()
                return f"{url}?v={stat.st_mtime_ns}-{stat.st_size}"
            except OSError:
                return url
        return jsonify({
            "mode": mode,
            "custom": {"path": str(custom), "url": versioned("/api/v1/wizard/logo/project", custom)} if custom.is_file() else None,
            "default": {"path": str(default), "url": versioned("/api/v1/wizard/logo/default", default)} if default.is_file() else None,
            "overlay": wizard.get("reel_logo_overlay") or {"x": 0.5, "y": 0.08, "width": 0.22},
        })

    @app.post("/api/v1/wizard/logo")
    def api_wizard_logo_upload() -> Response:
        project = _require_project(state)
        uploaded = request.files.get("file")
        raw_path = str(request.form.get("path") or "").strip()
        source = Path(raw_path).expanduser().resolve() if raw_path else None
        suffix = Path(uploaded.filename).suffix.lower() if uploaded and uploaded.filename else (source.suffix.lower() if source else "")
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            return error_response("bad_request", "Logo must be PNG, JPEG, or WebP", 400)
        destination = project.folder / f"overlay_logo{suffix}"
        for old in project.folder.glob("overlay_logo.*"):
            old.unlink(missing_ok=True)
        if uploaded and uploaded.filename:
            uploaded.save(destination)
        elif source and source.is_file():
            shutil.copy2(source, destination)
        else:
            return error_response("bad_request", "Choose a valid logo file", 400)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        wizard["reel_logo_path"] = str(destination.resolve())
        wizard["reel_logo_mode"] = "custom"
        project.save()
        return jsonify({"path": str(destination.resolve()), "url": "/api/v1/wizard/logo/project", "name": destination.name})

    @app.delete("/api/v1/wizard/logo")
    def api_wizard_logo_remove() -> Response:
        project = _require_project(state)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        for old in project.folder.glob("overlay_logo.*"):
            old.unlink(missing_ok=True)
        wizard["reel_logo_path"] = ""
        wizard["reel_logo_mode"] = "none"
        project.save()
        return jsonify({"ok": True})

    @app.get("/api/v1/wizard/logo/project")
    def api_wizard_logo_project_file() -> Response:
        project = _require_project(state)
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        path = Path(str(wizard.get("reel_logo_path") or ""))
        if not path.is_file() or path.parent.resolve() != project.folder.resolve():
            return error_response("not_found", "No project logo", 404)
        return send_file(str(path))

    @app.get("/api/v1/wizard/logo/default")
    def api_wizard_logo_default_file() -> Response:
        path = _default_brand_logo()
        if not path.is_file():
            return error_response("not_found", "No default brand logo", 404)
        return send_file(str(path))

    @app.post("/api/v1/inbox/register")
    def api_inbox_register() -> Response:
        project = _require_project(state)
        body = _json_body()
        master = body.get("master")
        songs = body.get("songs")
        videos = body.get("videos") or []
        if master is not None and not isinstance(master, str):
            return error_response("bad_request", "master must be a string path", 400)
        if songs is not None and not isinstance(songs, str):
            return error_response("bad_request", "songs must be a string path", 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos):
            return error_response("bad_request", "videos must be a list of string paths", 400)
        if master is None and songs is None and not videos:
            return error_response("bad_request", "select at least one input", 400)
        try:
            return jsonify(register_selected_inputs(project, master, songs, videos, append_videos=True))
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.post("/api/v1/inputs/upload")
    def api_inputs_upload() -> Response:
        project = _require_project(state)
        max_bytes = app.config["MAX_CONTENT_LENGTH"]
        if max_bytes is not None and request.content_length and request.content_length > max_bytes:
            limit_mb = int(max_bytes) // (1024 * 1024)
            return error_response(
                "upload_too_large",
                f"Browser uploads are limited to {limit_mb} MB. Put large videos in the Inbox or use the desktop folder picker.",
                413,
            )
        files = list(request.files.getlist("files"))
        if not files:
            return error_response("bad_request", "multipart field files is required", 400)
        try:
            return jsonify(save_uploads(project, files))
        except OSError as exc:
            return error_response("upload_error", str(exc), 400)

    @app.get("/api/v1/inputs/suggestions/songs")
    def api_songs_suggestions() -> Response:
        project = _require_project(state)
        master = project.data.get("inputs", {}).get("master")
        master_path = master.get("path") if master else None
        return jsonify({"songs": suggest_songs_json(master_path)})

    @app.post("/api/v1/wizard/upload")
    def api_wizard_upload() -> Response:
        max_bytes = app.config["MAX_CONTENT_LENGTH"]
        if max_bytes is not None and request.content_length and request.content_length > max_bytes:
            limit_mb = int(max_bytes) // (1024 * 1024)
            return error_response(
                "upload_too_large",
                f"Browser uploads are limited to {limit_mb} MB. Put large videos in the Inbox or use the desktop folder picker.",
                413,
            )
        # Raw browser uploads stream straight to the selected disk, avoiding
        # Werkzeug multipart spooling followed by a second full media copy.
        if request.mimetype == "application/octet-stream":
            name = unquote(request.headers.get("X-Zucker-Filename", ""))
            if not name:
                return error_response("bad_request", "filename is required", 400)
            files = [FileStorage(stream=request.stream, filename=name)]
        else:
            files = list(request.files.getlist("files"))
        if not files:
            return error_response("bad_request", "multipart field files is required", 400)
        upload_dir = app_home() / "WizardUploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        for storage in files:
            destination = save_media_upload(storage, upload_dir)
            saved.append(str(destination.resolve()))
        return jsonify(classify_paths(saved))

    @app.post("/api/v1/wizard/reel-overlay")
    def api_wizard_reel_overlay() -> Response:
        storage = request.files.get("file")
        if not storage or not storage.filename:
            return error_response("bad_request", "file is required", 400)
        suffix = Path(storage.filename).suffix.lower()
        if suffix not in {".png", ".webp", ".jpg", ".jpeg"}:
            return error_response("bad_request", "Reel overlays must be PNG, WEBP, or JPEG images", 400)
        destination_dir = app_home() / "ReelOverlays"
        destination_dir.mkdir(parents=True, exist_ok=True)
        payload = storage.read()
        digest = hashlib.sha256(payload).hexdigest()
        for existing in sorted(destination_dir.iterdir()) if destination_dir.exists() else []:
            if existing.is_file() and existing.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg"}:
                try:
                    if hashlib.sha256(existing.read_bytes()).hexdigest() == digest:
                        return jsonify({"path": str(existing.resolve()), "url": f"/api/v1/wizard/reel-overlay/{existing.name}", "reused": True})
                except OSError:
                    continue
        destination = unique_destination(destination_dir / Path(storage.filename).name)
        destination.write_bytes(payload)
        return jsonify({"path": str(destination.resolve()), "url": f"/api/v1/wizard/reel-overlay/{destination.name}"})

    @app.post("/api/v1/wizard/reel-video-overlay")
    def api_wizard_reel_video_overlay() -> Response:
        storage = request.files.get("file")
        if not storage or not storage.filename:
            return error_response("bad_request", "file is required", 400)
        suffix = Path(storage.filename).suffix.lower()
        if suffix not in {".mp4", ".mov", ".m4v", ".webm"}:
            return error_response("bad_request", "Video overlays must be MP4, MOV, M4V, or WEBM", 400)
        destination_dir = app_home() / "ReelOverlays"
        destination_dir.mkdir(parents=True, exist_ok=True)
        payload = storage.read()
        digest = hashlib.sha256(payload).hexdigest()
        for existing in sorted(destination_dir.iterdir()) if destination_dir.exists() else []:
            if existing.is_file() and existing.suffix.lower() in {".mp4", ".mov", ".m4v", ".webm"}:
                try:
                    if hashlib.sha256(existing.read_bytes()).hexdigest() == digest:
                        return jsonify({"path": str(existing.resolve()), "url": f"/api/v1/wizard/reel-video-overlay/{existing.name}", "reused": True})
                except OSError:
                    continue
        destination = unique_destination(destination_dir / Path(storage.filename).name)
        destination.write_bytes(payload)
        return jsonify({"path": str(destination.resolve()), "url": f"/api/v1/wizard/reel-video-overlay/{destination.name}"})

    @app.get("/api/v1/wizard/flyers")
    def api_wizard_flyers() -> Response:
        root = app_home() / "ReelOverlays"
        root.mkdir(parents=True, exist_ok=True)
        allowed = {".png", ".webp", ".jpg", ".jpeg"}
        items = []
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if not path.is_file() or path.suffix.lower() not in allowed:
                continue
            items.append({"name": path.name, "path": str(path.resolve()), "url": f"/api/v1/wizard/reel-overlay/{path.name}"})
        return jsonify({"items": items})

    @app.delete("/api/v1/wizard/flyers/<path:filename>")
    def api_wizard_flyer_delete(filename: str) -> Response:
        """Remove a flyer from the reusable library without breaking projects."""
        root = (app_home() / "ReelOverlays").resolve()
        candidate = (root / Path(filename).name).resolve()
        if candidate.parent != root or not candidate.is_file():
            return error_response("not_found", "Flyer not found", 404)
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        used_by: list[str] = []
        for summary in list_projects():
            try:
                other = load_project(summary["path"])
            except ProjectError:
                continue
            wizard = other.data.get("settings", {}).get("wizard", {})
            references = []
            for item in (wizard.get("reel_image_overlays") or []):
                if isinstance(item, dict): references.append(item.get("path"))
            for item in (json.loads((other.folder / "overlay_spec.json").read_text(encoding="utf-8")).get("images") or []) if (other.folder / "overlay_spec.json").is_file() else []:
                if isinstance(item, dict): references.append(item.get("path"))
            for raw in references:
                try:
                    if Path(str(raw or "")).expanduser().resolve() == candidate:
                        used_by.append(str(other.folder))
                        break
                except (OSError, RuntimeError):
                    continue
        if used_by:
            return jsonify({"ok": True, "removed": False, "retained": True, "sha256": digest, "used_by": used_by})
        candidate.unlink()
        return jsonify({"ok": True, "removed": True, "retained": False, "sha256": digest, "used_by": []})

    @app.get("/api/v1/wizard/reel-overlay/<path:filename>")
    def api_wizard_reel_overlay_file(filename: str) -> Response:
        # Browsers cannot load an absolute local path; serve only uploaded
        # overlays from the dedicated directory.
        return send_from_directory(app_home() / "ReelOverlays", Path(filename).name)

    @app.get("/api/v1/wizard/reel-video-overlay/<path:filename>")
    def api_wizard_reel_video_overlay_file(filename: str) -> Response:
        return send_from_directory(app_home() / "ReelOverlays", Path(filename).name)

    @app.post("/api/v1/wizard/songs")
    def api_wizard_songs() -> Response:
        body = _json_body()
        songs_path = body.get("songs")
        if songs_path is not None and not isinstance(songs_path, str):
            return error_response("bad_request", "songs must be a string path", 400)
        return jsonify({"songs": wizard_song_options(songs_path)})

    @app.post("/api/v1/wizard/start")
    def api_wizard_start() -> Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        platform = str(body.get("platform") or "").strip().lower()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip() or None
        videos = body.get("videos") or []
        platform = str(body.get("platform") or "youtube")
        audio_trim = _audio_trim_from_body(body)
        spherical_landmarks = _spherical_landmarks_from_body(body)
        spherical_landmark_profiles = body.get("spherical_landmarks_by_source") or {}
        if not isinstance(spherical_landmark_profiles, dict):
            spherical_landmark_profiles = {}
        if not spherical_landmark_profiles and state.project is not None:
            spherical_landmark_profiles = (
                state.project.data.get("settings", {}).get("spherical_landmarks_by_source") or {}
            )
        spherical_source_path = str(body.get("spherical_source_path") or "").strip()
        camera_role_weights = _camera_role_weights_from_body(body)
        fixed_rear_motion = _fixed_rear_motion_from_body(body)
        spherical_motion = _spherical_motion_from_body(body)
        spherical_mode = _spherical_mode_from_body(body)
        spherical_sweep = _spherical_sweep_from_body(body)
        sweep_speed_deg_per_sec = _sweep_speed_from_body(body)
        reel_duration_sec = _backstage_duration_from_body(body) if platform == "backstage" else _reel_duration_from_body(body)
        reel_aspect = _reel_aspect_from_body(body)
        reel_mix_vertical_ratio = _reel_mix_vertical_ratio_from_body(body)
        reel_cuts_per_source = _reel_cuts_per_source_from_body(body)
        reel_text_overlays = _reel_text_overlays_from_body(body)
        reel_image_overlays = _reel_image_overlays_from_body(body)
        transition_type = str(body.get("transition_type") or "auto").strip().lower()
        allowed_transition_types = {"auto", "none"} | set(TRANSITION_LIBRARY)
        if transition_type not in allowed_transition_types:
            return error_response("bad_request", "transition_type is not a supported native transition", 400)
        backstage_messages = body.get("backstage_messages") or []
        if not isinstance(backstage_messages, list) or not all(isinstance(value, str) for value in backstage_messages):
            return error_response("bad_request", "backstage_messages must be a list of strings", 400)
        backstage_messages = [value.strip() for value in backstage_messages if value.strip()][:4]
        _save_last_reel_overlays(reel_text_overlays, reel_image_overlays)
        if platform not in {"youtube", "instagram", "tiktok", "reel", "360", "backstage"}:
            return error_response("bad_request", "platform must be youtube, reel, instagram, tiktok, 360, or backstage", 400)
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", t("missing_video"), 400)
        embedded_source_audio = platform in {"reel", "360"} and _single_video_has_audio(videos)
        if not master and platform != "backstage" and not embedded_source_audio:
            return error_response("missing_master", t("missing_master"), 400)
        try:
            options = {
                "name": name or "Jam",
                "platform": platform,
                "song_choice": body.get("song_index", body.get("song_choice")),
                "audio_trim": audio_trim,
                "spherical_landmarks": spherical_landmarks,
                "spherical_landmark_profiles": spherical_landmark_profiles,
                "spherical_source_path": spherical_source_path,
                "camera_role_weights": camera_role_weights,
                "fixed_rear_motion": fixed_rear_motion,
                "spherical_motion": spherical_motion,
                "spherical_mode": spherical_mode,
                "spherical_sweep": spherical_sweep,
                "sweep_speed_deg_per_sec": sweep_speed_deg_per_sec,
                "reel_duration_sec": reel_duration_sec,
                "backstage_target_duration_sec": reel_duration_sec if platform == "backstage" else None,
                "reel_aspect": reel_aspect,
                "reel_mix_vertical_ratio": reel_mix_vertical_ratio,
                "reel_cuts_per_source": reel_cuts_per_source,
                "reel_text_overlays": reel_text_overlays,
                "reel_image_overlays": reel_image_overlays,
                "backstage_messages": backstage_messages,
                "transition_type": transition_type,
                "master_path": master,
                "songs_path": songs,
                "video_paths": videos,
            }
            if state.project is not None and state.wizard._prepared_project is None:
                if _project_matches_inputs(state.project, master, songs, videos) and _project_can_skip_prepare(state.project, platform):
                    state.project.data.setdefault("settings", {}).setdefault("wizard", {})["variation_seed"] = str(body.get("variation_seed") or time.time_ns())
                    state.project.save()
                    state.wizard.adopt_prepared_project(state.project)
                    job = state.wizard.start(**options)
                    return jsonify(serialize_wizard_job(job)), 202
                else:
                    state.project.data.setdefault("settings", {}).setdefault("wizard", {})["variation_seed"] = str(body.get("variation_seed") or time.time_ns())
                    state.project.save()
                    job = state.wizard.start_existing(state.project, **options)
                    return jsonify(serialize_wizard_job(job)), 202
            matching_project = state.project if _can_reuse_prepared_project(state.project, master, songs, videos) else find_project_by_inputs(master, songs, videos)
            if matching_project and _project_can_skip_prepare(matching_project, platform):
                state.project = matching_project
                state.wizard.adopt_prepared_project(state.project)
            elif matching_project:
                state.project = matching_project
                state.wizard.prepare_existing(matching_project, platform=platform)
            seed_project = state.wizard._prepared_project or state.project
            if seed_project is not None:
                seed_project.data.setdefault("settings", {}).setdefault("wizard", {})["variation_seed"] = str(body.get("variation_seed") or time.time_ns())
                seed_project.save()
            job = state.wizard.start(**options)
            return jsonify(serialize_wizard_job(job)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/wizard/review")
    def api_wizard_review() -> Response:
        project = state.project or _active_wizard_project(state)
        if not project:
            return error_response("not_ready", "The edit plan is not ready yet", 409)
        try:
            # Never hold the HTTP request open while decoding every original
            # 360 source. Return placeholders immediately and let the browser
            # poll the render-status file as each thumbnail becomes ready.
            render_missing = request.args.get("render", "1") != "0"
            items = review_items(project, render_missing=False)
            if render_missing:
                pending = {
                    int(item["index"])
                    for item in items
                    if item.get("thumbnail_status") == "missing"
                }
                _queue_review_thumbnail_render(project, pending)
            return jsonify({
                "items": items,
                "platform": project.data.get("settings", {}).get("wizard", {}).get("platform"),
            })
        except Exception as exc:
            LOGGER.exception("Review items failed for %s", project.folder)
            return error_response("review_render_failed", str(exc) or "Review thumbnail render failed", 500)

    @app.get("/api/v1/wizard/paper-edit")
    def api_wizard_paper_edit() -> Response:
        project = state.project or _active_wizard_project(state)
        if not project:
            return error_response("not_ready", "The Backstage paper edit is not ready yet", 409)
        path = project.artifacts_dir / "backstage_paper_edit.json"
        try:
            return jsonify(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_unavailable", str(exc), 409)

    @app.get("/api/v1/wizard/paper-edit/thumbnail/<cut_id>")
    def api_wizard_paper_edit_thumbnail(cut_id: str) -> Response:
        project = state.project or _active_wizard_project(state)
        if not project or not cut_id.startswith("backstage-"):
            return error_response("not_found", "paper-edit thumbnail not found", 404)
        try:
            paper = json.loads((project.artifacts_dir / "backstage_paper_edit.json").read_text(encoding="utf-8"))
            cut = next(row for row in paper.get("cuts") or [] if str(row.get("id")) == cut_id)
            output = project.cache_dir / "paper_edit" / f"{cut_id}.jpg"
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.exists():
                ffmpeg = tool_status().get("ffmpeg_path")
                if not ffmpeg:
                    return error_response("not_ready", "ffmpeg is unavailable", 503)
                source = str(cut.get("source_path") or "")
                command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-ss", str(cut.get("in_sec") or 0), "-i", source, "-frames:v", "1", "-vf", "scale=320:-2", str(output)]
                result = subprocess.run(command, capture_output=True, text=True, check=False)
                if result.returncode != 0:
                    return error_response("thumbnail_failed", result.stderr.strip() or "thumbnail render failed", 500)
            return send_from_directory(output.parent, output.name)
        except (OSError, StopIteration, ValueError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_thumbnail_failed", str(exc), 404)

    @app.post("/api/v1/wizard/paper-edit/approve")
    def api_wizard_paper_edit_approve() -> Response:
        state.project = state.project or _active_wizard_project(state)
        body = _json_body()
        rejected = body.get("rejected") or []
        if not isinstance(rejected, list) or not all(isinstance(value, (str, int)) for value in rejected):
            return error_response("bad_request", "rejected must be a list of paper-edit ids", 400)
        try:
            status = state.wizard.status()
            if status.get("status") != "waiting_paper_edit":
                state.wizard.adopt_paper_edit(state.project)
            job = state.wizard.approve_paper_edit(state.project, [str(value) for value in rejected])
            return jsonify(serialize_wizard_job(job)), 202
        except (RuntimeError, ValueError) as exc:
            return error_response("paper_edit_approve_failed", str(exc), 409)

    @app.post("/api/v1/wizard/paper-edit/text")
    def api_wizard_paper_edit_text() -> Response:
        project = state.project or _active_wizard_project(state)
        body = _json_body()
        cut_id = str(body.get("id") or "").strip()
        text = str(body.get("text") or "").strip()
        if not project or not cut_id or len(text) > 1000:
            return error_response("bad_request", "id and a subtitle text up to 1000 characters are required", 400)
        try:
            paper_path = project.artifacts_dir / "backstage_paper_edit.json"
            edit_path = project.artifacts_dir / "backstage_edit.json"
            paper = json.loads(paper_path.read_text(encoding="utf-8"))
            edit = json.loads(edit_path.read_text(encoding="utf-8"))
            index = next((index for index, cut in enumerate(paper.get("cuts") or []) if str(cut.get("id")) == cut_id), None)
            segment_index = next((index for index, segment in enumerate(edit.get("segments") or []) if str(segment.get("id") or f"backstage-{index:04d}") == cut_id), None)
            if index is None or segment_index is None:
                return error_response("not_found", "paper-edit cut not found", 404)
            # This endpoint is a text edit, not an edit-plan rebuild. Keep
            # every in/out/duration/timestamp byte untouched and address the
            # cue by its stable id.
            update_backstage_cue_text({"cues": paper["cuts"]}, cut_id, text)
            paper["cuts"][index]["subtitle_text"] = text
            paper["cuts"][index]["text"] = text
            edit["segments"][segment_index]["text"] = text
            edit["segments"][segment_index]["subtitle_text"] = text
            paper_path.write_text(json.dumps(paper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            project.mark_all_stale_from("export")
            project.save()
            return jsonify(paper)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_text_failed", str(exc), 409)

    @app.post("/api/v1/wizard/paper-edit/mark")
    def api_wizard_paper_edit_mark() -> Response:
        project = state.project or _active_wizard_project(state)
        body = _json_body()
        cut_id = str(body.get("id") or "").strip()
        mark = str(body.get("mark") or "").strip().lower()
        if not project or not cut_id or mark not in {"keep", "drop", "closing"}:
            return error_response("bad_request", "id and mark (keep, drop, closing) are required", 400)
        try:
            paper_path = project.artifacts_dir / "backstage_paper_edit.json"
            edit_path = project.artifacts_dir / "backstage_edit.json"
            paper = json.loads(paper_path.read_text(encoding="utf-8"))
            edit = json.loads(edit_path.read_text(encoding="utf-8"))
            index = next((i for i, cut in enumerate(paper.get("cuts") or []) if str(cut.get("id")) == cut_id), None)
            if index is None:
                return error_response("not_found", "paper-edit cut not found", 404)
            cut = paper["cuts"][index]
            record_feedback(str(cut.get("source_path") or ""), float(cut.get("in_sec") or 0), float(cut.get("out_sec") or 0), mark, cut.get("text_original", ""), cut.get("english_text", ""), cut.get("selection_reason", ""))
            cut["mark"] = mark
            cut["status"] = "dropped" if mark == "drop" else "pending"
            segments = list(edit.get("segments") or [])
            if index < len(segments):
                segments[index]["paper_mark"] = mark
            edit["segments"] = segments
            edit["cut_count"] = max(0, len(segments) - 1)
            edit["selected_duration_sec"] = round(sum(float(item.get("duration_sec") or 0) for item in segments), 3)
            for order, row in enumerate(paper.get("cuts") or [], 1):
                row["order"] = order
            write = lambda path, payload: path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            write(paper_path, paper)
            write(edit_path, edit)
            project.mark_all_stale_from("export")
            project.save()
            return jsonify(paper)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return error_response("paper_edit_mark_failed", str(exc), 409)

    @app.post("/api/v1/wizard/review/replace")
    def api_wizard_review_replace() -> Response:
        project = state.project or _active_wizard_project(state)
        body = _json_body()
        rejected = body.get("rejected") or []
        if not project or not isinstance(rejected, list) or not all(isinstance(value, int) for value in rejected):
            return error_response("bad_request", "rejected must be a list of shot indexes", 400)
        if state.wizard.status().get("status") in {"running", "cancelling"}:
            return error_response("wizard_busy", "Espera o cancela la exportación antes de cambiar frames", 409)
        try:
            result = replace_slots(project, rejected)
            replaced = {int(value) for value in result.get("replaced", [])}
            if replaced:
                def render_replaced_thumbnails() -> None:
                    render_review_thumbnails(project, replaced, max_workers=2)

                threading.Thread(
                    target=render_replaced_thumbnails,
                    name="review-thumbnail-render",
                    daemon=True,
                ).start()
            return jsonify(result)
        except Exception:
            LOGGER.exception("Review replacement failed for %s", project.folder)
            return error_response("review_replace_failed", "Could not replace the rejected shots", 500)

    @app.post("/api/v1/wizard/review/render")
    def api_wizard_review_render() -> Response:
        try:
            state.project = state.project or _active_wizard_project(state)
            body = _json_body()
            if body.get("transitions") is not None:
                set_review_transition_types(state.project, body.get("transitions"))
            job = state.wizard.render_review(state.project)
            return jsonify(serialize_wizard_job(job)), 202
        except (RuntimeError, ValueError) as exc:
            return error_response("review_render_failed", str(exc), 409)

    @app.get("/api/v1/wizard/review/thumbnail/<signature>/<path:filename>")
    def api_wizard_review_thumbnail(signature: str, filename: str) -> Response:
        project = state.project or _active_wizard_project(state)
        if not project:
            return error_response("not_ready", "No project is active", 404)
        return send_from_directory(project.cache_dir / "shot_review" / signature, Path(filename).name)

    @app.get("/api/v1/wizard/overlays")
    def api_wizard_overlays() -> Response:
        wizard = (state.project.data.get("settings", {}).get("wizard", {}) if state.project else {})
        if wizard.get("reel_text_overlays") or wizard.get("reel_image_overlays") or wizard.get("reel_video_overlays"):
            return jsonify({"texts": wizard.get("reel_text_overlays") or [], "images": wizard.get("reel_image_overlays") or [], "videos": wizard.get("reel_video_overlays") or [], "source": "project"})
        path = app_home() / "reel_overlays.json"
        try:
            return jsonify({**json.loads(path.read_text(encoding="utf-8")), "source": "last"})
        except (OSError, ValueError, TypeError):
            return jsonify({"texts": [], "images": [], "videos": [], "source": "none"})

    @app.get("/api/v1/wizard/master-preview")
    def api_wizard_master_preview() -> Response:
        status = state.wizard.status()
        project_path = status.get("project_path") or ((status.get("result") or {}).get("project_path"))
        project = state.project
        if not project and project_path:
            project = load_project(project_path)
        requested = Path(str(request.args.get("path") or "")).expanduser().resolve()
        if requested.exists() and requested.is_file():
            if requested.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}:
                return send_file_with_range(str(requested))
            try:
                if any(stream.get("codec_type") == "audio" for stream in ffprobe(str(requested)).get("streams") or []):
                    return send_file_with_range(str(requested))
            except Exception:
                pass
        if not project or not project.data.get("inputs", {}).get("master"):
            return error_response("not_found", "Master media is not registered", 404)
        master_path = project.data["inputs"]["master"]["path"]
        return send_file_with_range(master_path)

    @app.get("/api/v1/wizard/spherical-preview")
    def api_wizard_spherical_preview() -> Response:
        """Render the exact 360 preview projection used by review/export."""
        project = _require_project(state)
        requested = Path(str(request.args.get("source") or "")).expanduser().resolve()
        allowed = set()
        for record in project.data.get("inputs", {}).get("videos", []):
            allowed.update({Path(str(record.get("path") or "")).resolve(), Path(record_media_path(record)).resolve()})
        if requested not in allowed or not requested.is_file():
            return error_response("not_found", "360 source is not registered in this project", 404)
        shot_type = str(request.args.get("shot") or "").strip()
        try:
            yaw = float(request.args.get("yaw", 0.0))
            # Use the same canonical pitch as export/review. The former ±89°
            # preview-only clamp allowed a saved pose to look correct in the
            # editor and shift to a different performer in the final MP4.
            pitch = effective_pitch(float(request.args.get("pitch", 0.0)), shot_type)
            projection_preset = normalize_projection_preset(request.args.get("projection_preset"), shot_type)
            roll = effective_roll(float(request.args.get("roll", 0.0)), shot_type)
            projection_control = effective_projection_control(request.args.get("projection_control"))
            h_fov = max(30.0, min(MAX_SPHERICAL_FOV, float(request.args.get("fov", 95.0))))
            time_sec = max(0.0, float(request.args.get("time_sec", 30.0)))
        except (TypeError, ValueError):
            return error_response("bad_request", "Invalid spherical preview parameters", 400)
        try:
            duration = float(ffprobe(str(requested)).get("format", {}).get("duration") or 0.0)
            if duration > 0.0:
                time_sec = min(time_sec, max(0.0, duration - 0.05))
        except Exception:
            pass
        view = view_parameters(
            yaw, pitch, h_fov, 16.0 / 9.0, shot_type,
            projection_preset=projection_preset,
            roll=roll,
            projection_control=projection_control,
        )
        projection = "equirect"
        insv_fov = 190.0
        for record in project.data.get("inputs", {}).get("videos", []):
            record_paths = {
                str(Path(str(record.get("path") or "")).expanduser().resolve()),
                str(Path(record_media_path(record)).expanduser().resolve()),
            }
            if str(requested) not in record_paths:
                continue
            probe = record.get("probe") or {}
            normalized = record.get("normalized") or {}
            projection = str(record.get("projection") or probe.get("projection") or normalized.get("projection") or "equirect").lower()
            try:
                insv_fov = float(
                    record.get("insv_fov")
                    or probe.get("insv_fov")
                    or normalized.get("insv_fov")
                    or 190.0
                )
            except (TypeError, ValueError):
                insv_fov = 190.0
            break
        projection_prefix = ""
        if projection == "raw_insv":
            projection_prefix = f"v360=input=dfisheye:output=e:ih_fov={insv_fov:.3f}:iv_fov={insv_fov:.3f}:interp=lanczos,"
        ffmpeg_path = str(load_global_config().get("ffmpeg_path") or "ffmpeg")
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-ss", f"{time_sec:.3f}", "-i", str(requested),
             "-vf", f"{projection_prefix}v360=input=equirect:output={view['projection']}:yaw={float(view['yaw']):.3f}:pitch={float(view['pitch']):.3f}:roll={float(view['roll']):.3f}:h_fov={float(view['h_fov']):.3f}:v_fov={float(view['v_fov']):.3f},scale=640:360",
             "-frames:v", "1", "-f", "mjpeg", "pipe:1"],
            capture_output=True, check=False, timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return error_response("preview_failed", "Could not render the 360 shot preview", 500)
        return send_file(io.BytesIO(result.stdout), mimetype="image/jpeg", download_name="spherical-preview.jpg")

    @app.get("/api/v1/wizard/spherical-source-frame")
    def api_wizard_spherical_source_frame() -> Response:
        """Serve one equirectangular frame for the interactive client viewer."""
        project = _require_project(state)
        requested = Path(str(request.args.get("source") or "")).expanduser().resolve()
        source_record: dict[str, Any] | None = None
        for record in project.data.get("inputs", {}).get("videos", []):
            record_paths = {
                Path(str(record.get("path") or "")).expanduser().resolve(),
                Path(record_media_path(record)).expanduser().resolve(),
            }
            if requested in record_paths:
                source_record = record
                break
        if source_record is None or not requested.is_file():
            return error_response("not_found", "360 source is not registered in this project", 404)
        projection = str(
            source_record.get("projection")
            or (source_record.get("probe") or {}).get("projection")
            or (source_record.get("normalized") or {}).get("projection")
            or "equirect"
        ).lower()
        try:
            insv_fov = float(
                source_record.get("insv_fov")
                or (source_record.get("probe") or {}).get("insv_fov")
                or (source_record.get("normalized") or {}).get("insv_fov")
                or 190.0
            )
        except (TypeError, ValueError):
            insv_fov = 190.0
        projection_prefix = ""
        if projection == "raw_insv":
            projection_prefix = f"v360=input=dfisheye:output=e:ih_fov={insv_fov:.3f}:iv_fov={insv_fov:.3f}:interp=lanczos,"
        ffmpeg_path = str(load_global_config().get("ffmpeg_path") or "ffmpeg")
        result = subprocess.run(
            [
                ffmpeg_path, "-hide_banner", "-loglevel", "error", "-ss", "30", "-i", str(requested),
                "-frames:v", "1",
                "-vf", f"{projection_prefix}scale=2048:1024:force_original_aspect_ratio=decrease,pad=2048:1024:(ow-iw)/2:(oh-ih)/2",
                "-q:v", "3", "-f", "mjpeg", "pipe:1",
            ],
            capture_output=True, check=False, timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return error_response("preview_failed", "Could not read the equirectangular 360 frame", 500)
        return send_file(io.BytesIO(result.stdout), mimetype="image/jpeg", download_name="spherical-source-frame.jpg")

    @app.post("/api/v1/wizard/draft")
    def api_wizard_draft() -> Response:
        body = _json_body()
        videos = body.get("videos") or []
        if not isinstance(videos, list) or not videos or not all(isinstance(path, str) for path in videos):
            return error_response("missing_video", t("missing_video"), 400)
        if state.wizard.status().get("status") in {"running", "cancelling"} or (state.composition.status(state.project) or {}).get("status") in {"running", "cancelling"}:
            return error_response("wizard_busy", "Wait until the current job finishes before creating a project.", 409)
        try:
            requested_id = str(body.get("project_id") or "")
            if requested_id:
                if state.project is None or not same_project_path(requested_id, str(state.project.folder)):
                    return error_response("project_mismatch", "The selected project is no longer open", 409)
                project = state.project
            else:
                project = _create_wizard_project(str(body.get("name") or "Jam"))
                state.project = project
                # Register immediately, even if a media probe later fails: the
                # draft remains recoverable and appears in the project list.
                _remember_project(project)
            master = str(body.get("master") or "")
            songs = str(body.get("songs") or "")
            if not _project_matches_inputs(project, master, songs or None, videos):
                result = register_selected_inputs(project, master or None, songs or None, videos, append_videos=False)
                if not master:
                    project.data["inputs"]["master"] = None
                if not songs:
                    project.data["inputs"]["songs"] = None
                project.save()
            result = project.snapshot()
            if not requested_id:
                project.data.setdefault("settings", {}).setdefault("wizard", {})["reel_logo_mode"] = "default"
                project.save()
            return jsonify({"project_id": str(project.folder), "project": result})
        except (OSError, ProjectError) as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.post("/api/v1/wizard/prepare")
    def api_wizard_prepare() -> Response:
        body = _json_body()
        name = str(body.get("name") or "").strip()
        platform = str(body.get("platform") or "youtube").strip().lower()
        master = str(body.get("master") or "").strip()
        songs = str(body.get("songs") or "").strip() or None
        videos = body.get("videos") or []
        if not isinstance(videos, list) or not all(isinstance(path, str) for path in videos) or not videos:
            return error_response("missing_video", t("missing_video"), 400)
        if not master and platform != "backstage" and not (platform in {"reel", "360"} and _single_video_has_audio(videos)):
            return error_response("missing_master", t("missing_master"), 400)
        try:
            # Every new Step 1 submission is a new run. Matching inputs against
            # an older .zuckervid project was convenient for development, but
            # it made the app silently reuse stale frames, camera choices and
            # stage outputs. Reopening an old project is now explicit through
            # /wizard/projects/open only.
            composition_status = state.composition.status(state.project) or {}
            if composition_status.get("status") in {"running", "cancelling"}:
                return error_response("wizard_busy", "A previous final render is still stopping; wait until it finishes before starting a new project.", 409)
            if not state.wizard.reset():
                return error_response("wizard_busy", "The previous wizard job is still cancelling; wait until it stops before starting a new project.", 409)
            state.composition.reset()
            requested_id = str(body.get("project_id") or "")
            if requested_id:
                if state.project is None or not same_project_path(requested_id, str(state.project.folder)):
                    return error_response("project_mismatch", "The selected project is no longer open", 409)
                job = state.wizard.prepare_existing(state.project, platform=platform)
            else:
                state.project = None
                job = state.wizard.prepare(name=name or "Jam", master_path=master, songs_path=songs, video_paths=videos, platform=platform)
            return jsonify(serialize_wizard_job(job)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except OSError as exc:
            return error_response("input_file_error", str(exc), 400)

    @app.get("/api/v1/wizard/status")
    def api_wizard_status() -> Response:
        requested_project_id = str(request.args.get("project_id") or "").strip()
        if requested_project_id:
            try:
                requested_project = load_project(requested_project_id)
            except ProjectError:
                return error_response("project_not_found", "The requested project is not available", 404)
            active_path = state.project.folder.resolve() if state.project else None
            if active_path != requested_project.folder.resolve():
                # A late poll from another project must never read the
                # process-global wizard job. Return the requested project's
                # persisted state instead; the browser also validates the id.
                return jsonify(_project_wizard_status(requested_project))
        composition_status = state.composition.status(state.project)
        if composition_status and composition_status.get("status") in {"running", "cancelling", "cancelled", "failed"}:
            return jsonify(composition_status)
        if composition_status and composition_status.get("status") == "done":
            return jsonify(composition_status)
        status = state.wizard.status()
        if status.get("status") == "idle" and state.project:
            return jsonify(_project_wizard_status(state.project))
        return jsonify(status)

    @app.get("/api/v1/wizard/projects")
    def api_wizard_projects() -> Response:
        return jsonify({"projects": list_projects()})

    @app.post("/api/v1/wizard/projects/open")
    def api_wizard_project_open() -> Response:
        body = _json_body()
        folder = str(body.get("path") or "").strip()
        if not folder:
            return error_response("bad_request", "path is required", 400)
        try:
            state.composition.reset()
            state.project = load_project(folder)
            _invalidate_stale_sync_on_open(state.project)
            reconciled = reconcile_registered_inputs(state.project)
            migrated = migrate_project_normalization_cache(state.project)
            if reconciled or migrated:
                LOGGER.info("Reconciled registered inputs for %s", state.project.folder)
            _remember_project(state.project)
            # Opening is data loading only. Make it always starts a fresh
            # pipeline over the restored Drop Here inputs.
            return jsonify(_project_wizard_status(state.project))
        except ProjectError as exc:
            return error_response(
                "project_unrecoverable",
                "Este proyecto no se puede recuperar. Los vídeos del Drop Here siguen disponibles; crea un proyecto nuevo.",
                409,
            )

    @app.post("/api/v1/wizard/projects/delete")
    def api_wizard_project_delete() -> Response:
        body = _json_body()
        folder = str(body.get("path") or "").strip()
        keep_exports = bool(body.get("keep_exports", False))
        if not folder:
            return error_response("bad_request", "path is required", 400)
        try:
            result = delete_project_folder(folder, keep_exports=keep_exports)
            if state.project and Path(result["deleted"]).resolve() == state.project.folder.resolve():
                state.project = None
                config = load_global_config()
                if config.get("last_project_path") == result["deleted"]:
                    config.pop("last_project_path", None)
                    save_global_config(config)
            return jsonify(result)
        except (ProjectError, ValueError, OSError) as exc:
            return error_response("project_error", str(exc), 400)

    @app.post("/api/v1/wizard/projects/new")
    def api_wizard_project_new() -> Response:
        if not state.wizard.reset():
            return error_response(
                "wizard_busy",
                "The previous wizard job is still cancelling; wait until it stops before starting a new project.",
                409,
            )
        state.project = None
        state.composition.reset()
        config = load_global_config()
        if config.pop("last_project_path", None) is not None:
            save_global_config(config)
        return jsonify({"ok": True})

    @app.post("/api/v1/wizard/reset")
    def api_wizard_reset() -> Response:
        if not state.wizard.reset():
            return error_response(
                "wizard_busy",
                "The previous wizard job is still cancelling; wait until it stops before resetting.",
                409,
            )
        return jsonify({"ok": True})

    @app.post("/api/v1/wizard/cancel")
    def api_wizard_cancel() -> Response:
        if state.composition.cancel():
            return jsonify({"ok": True, "status": "cancelling"})
        cancelled = state.wizard.cancel()
        if not cancelled:
            return error_response("not_running", "No job is currently running", 409)
        return jsonify({"ok": True, "status": state.wizard.status().get("status")})

    @app.get("/api/v1/wizard/report")
    def api_wizard_report() -> Response:
        wizard_status = state.wizard.status()
        composition_status = state.composition.status()
        active_composition = composition_status and composition_status.get("status") in {
            "running", "cancelling", "cancelled", "failed", "done"
        }
        report_status = composition_status if active_composition else wizard_status
        report = wizard_report(report_status)
        if active_composition:
            report += "\n--- wizard export status ---\n"
            report += f"status: {wizard_status.get('status')}\n"
            report += f"message: {wizard_status.get('message')}\n"
            report += f"error: {wizard_status.get('error')}\n"
        return Response(report, mimetype="text/plain")

    @app.post("/api/v1/wizard/rescue")
    def api_wizard_rescue() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or ("offset_sec" not in body and not isinstance(body.get("offset_ranges"), list)):
            return error_response("bad_request", "clip_id and offset_sec or offset_ranges are required", 400)
        try:
            if isinstance(body.get("offset_ranges"), list):
                job = state.wizard.rescue_ranges(project, clip_id=clip_id, offset_ranges=body["offset_ranges"])
            else:
                job = state.wizard.rescue(project, clip_id=clip_id, offset_sec=float(body["offset_sec"]))
            return jsonify(serialize_wizard_job(job)), 202
        except RuntimeError as exc:
            return error_response("wizard_busy", str(exc), 409)
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/wizard/frontend-log")
    def api_wizard_frontend_log() -> Response:
        body = _json_body()
        log_dir = app_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        message = str(body.get("message") or "frontend event")
        stack = str(body.get("stack") or "")
        url = str(body.get("url") or "")
        with (log_dir / "frontend.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{message}\n")
            if url:
                fh.write(f"url: {url}\n")
            if stack:
                fh.write(f"{stack}\n")
        return jsonify({"ok": True})

    @app.post("/api/v1/sync-diagnostics/cleanup")
    def api_sync_diagnostics_cleanup() -> Response:
        return jsonify({"ok": True, "removed_projects": cleanup_closed_sync_diagnostics()})

    @app.get("/api/v1/wizard/result")
    def api_wizard_result() -> Response:
        status = state.composition.status() or state.wizard.status()
        if status.get("status") == "idle" and state.project:
            status = _project_wizard_status(state.project)
        result = status.get("result") or {}
        path = result.get("path")
        if not path:
            return error_response("not_found", "No exported video yet", 404)
        candidate = Path(path)
        if candidate.suffix.lower() != ".mp4" or not candidate.exists():
            return error_response("not_found", f"The result is not an exported MP4: {candidate}", 404)
        return send_file_with_range(str(candidate))

    @app.get("/api/v1/captions/styles")
    def api_captions_styles() -> Response:
        return jsonify({"version": CAPTIONS_VERSION, "styles": [style.__dict__ for style in list_styles()]})

    @app.post("/api/v1/captions/auto-read")
    def api_captions_auto_read() -> Response:
        project = _require_project(state)
        try:
            body = request.get_json(silent=True) or {}
            requested_model = body.get("model")
            job = state.auto_read.start(project, str(requested_model) if requested_model is not None else None)
            return jsonify(job.snapshot()), 202
        except RuntimeError as exc:
            return error_response("auto_read_busy", str(exc), 409)
        except ValueError as exc:
            return error_response("auto_read_unavailable", str(exc), 409)

    @app.get("/api/v1/captions/auto-read/status")
    def api_captions_auto_read_status() -> Response:
        job = state.auto_read.status()
        if not job:
            return jsonify({"status": "idle"})
        return jsonify(job)

    @app.get("/api/v1/captions/frame")
    def api_captions_frame() -> Response:
        project = _require_project(state)
        result = _latest_media_result(state, project)
        if not result:
            return error_response("not_found", "No exported video yet", 404)
        cache = project.cache_dir / "captions" / CAPTIONS_VERSION
        cache.mkdir(parents=True, exist_ok=True)
        frame = cache / "export-frame.jpg"
        if not frame.exists():
            ffmpeg = tool_status().get("ffmpeg_path") or "ffmpeg"
            subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "1", "-i", result["path"], "-frames:v", "1", "-vf", "scale=720:-2", str(frame)], check=True)
        return send_from_directory(frame.parent, frame.name)

    @app.post("/api/v1/captions/parse")
    def api_captions_parse() -> Response:
        body = request.get_json(silent=True) or {}
        source = str(body.get("source") or "lyrics")
        text = str(body.get("text") or "")
        if source == "lyrics":
            track = from_lyrics(text)
        elif source in {"srt", "lrc"}:
            project = _require_project(state)
            cache_dir = project.cache_dir / "captions" / CAPTIONS_VERSION
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / ("import.srt" if source == "srt" else "import.lrc")
            cache_path.write_text(text, encoding="utf-8")
            track = from_srt(cache_path) if source == "srt" else from_lrc(cache_path)
        else:
            return error_response("bad_request", "source must be lyrics, srt, or lrc", 400)
        return jsonify({"lang": track.lang, "cues": [{"lines": list(cue.lines), "start": cue.start, "end": cue.end} for cue in track.cues]})

    @app.post("/api/v1/captions/align")
    def api_captions_align() -> Response:
        body = request.get_json(silent=True) or {}
        lyrics = from_lyrics(str(body.get("lyrics") or ""))
        whisper = from_whisper(body.get("segments") or [], lang=str(body.get("lang") or "und"))
        track = align_known_lyrics(lyrics, whisper)
        return jsonify({"lang": track.lang, "cues": [{"lines": list(cue.lines), "start": cue.start, "end": cue.end, "words": [{"text": word.text, "start": word.start, "end": word.end} for word in cue.words]} for cue in track.cues]})

    @app.post("/api/v1/captions/burn")
    def api_captions_burn() -> Response:
        project = _require_project(state)
        result = _latest_media_result(state, project)
        if not result:
            return error_response("not_found", "No exported video yet", 404)
        body = request.get_json(silent=True) or {}
        try:
            style = get_style(str(body.get("style") or "clean_bottom"))
            cues = tuple(Cue(tuple(str(line) for line in cue.get("lines", [])), float(cue["start"]), float(cue["end"]), _caption_words(cue), dict(cue.get("style_override") or {})) for cue in body.get("cues", []))
            track = CueTrack(cues, str(body.get("lang") or "und"))
            cache = project.cache_dir / "captions" / CAPTIONS_VERSION
            cache.mkdir(parents=True, exist_ok=True)
            destination = project.cache_dir / "captions" / f"{Path(result['path']).stem}_captions.mp4"
            destination.parent.mkdir(parents=True, exist_ok=True)
            header = body.get("header") if isinstance(body.get("header"), dict) else None
            letterbox = body.get("letterbox") if isinstance(body.get("letterbox"), dict) else None
            ass_width, ass_height = _video_dimensions(Path(result["path"]), str(tool_status().get("ffmpeg_path") or "ffmpeg"))
            if letterbox and letterbox.get("enabled"):
                ass_width = int(letterbox.get("width", ass_width))
                ass_height = int(letterbox.get("height", ass_height))
            (cache / "captions.ass").write_text(render_ass(track, style, width=ass_width, height=ass_height, header=header), encoding="utf-8")
            (cache / "captions.srt").write_text(to_srt(track), encoding="utf-8")
            logo = None
            if header and header.get("logo_source") == "custom":
                candidate = Path(str(project.data.get("settings", {}).get("wizard", {}).get("reel_logo_path") or ""))
                if candidate.is_file() and candidate.parent.resolve() == project.folder.resolve(): logo = candidate
            elif header and header.get("logo_source") == "default":
                candidate = Path(str(load_global_config().get("personal_logo_path") or ""))
                if candidate.is_file(): logo = candidate
            track = _expand_caption_animations(track)
            output = burn_captions(result["path"], track, style, output_path=destination, header=header, logo_path=logo, letterbox=letterbox)
            return jsonify({"path": str(output), "filename": output.name, "media_url": "/api/v1/captions/result", "version": CAPTIONS_VERSION})
        except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
            return error_response("caption_burn_failed", str(exc), 400)

    @app.get("/api/v1/captions/result")
    def api_captions_result() -> Response:
        project = _require_project(state)
        exports = sorted((project.cache_dir / "captions").glob("*_captions.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not exports:
            return error_response("not_found", "No captioned export yet", 404)
        return send_file_with_range(str(exports[0]))

    @app.post("/api/v1/wizard/compose")
    def api_wizard_compose() -> Response:
        project = _require_project(state)
        body = request.get_json(silent=True) or {}
        texts = body.get("texts") if isinstance(body.get("texts"), list) else []
        images = body.get("images") if isinstance(body.get("images"), list) else []
        videos = body.get("videos") if isinstance(body.get("videos"), list) else []
        cues = body.get("cues") if isinstance(body.get("cues"), list) else []
        header = body.get("header") if isinstance(body.get("header"), dict) else {}
        logo_mode = str(header.get("logo_source") or "none")
        if logo_mode not in {"custom", "default", "none"}:
            logo_mode = "none"
        wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
        # The mounted export is authoritative. Caption UI state must never
        # turn a YouTube result into a vertical Reel composition.
        base_result = _export_result(project)
        compose_platform = str((base_result or {}).get("platform") or wizard.get("platform") or "youtube").strip().lower()
        if compose_platform not in {"youtube", "reel", "reel_horizontal", "360", "backstage"}:
            compose_platform = "youtube"
        if compose_platform == "youtube":
            # YouTube is one horizontal flyer/base composition. Captions and
            # letterbox are intentionally outside this mode's contract.
            cues = []
            letterbox = {"enabled": False}
        else:
            letterbox = body.get("letterbox") or {}
        overlay_spec = {"version": 3, "platform": compose_platform, "texts": texts, "images": images, "videos": videos}
        header = {**header, "logo_source": logo_mode, "logo_enabled": logo_mode != "none"}
        cue_track = {"version": CAPTIONS_VERSION, "platform": compose_platform, "lang": str(body.get("lang") or "und"), "cues": cues, "style": str(body.get("style") or "karaoke_word"), "header": header, "letterbox": letterbox}
        (project.folder / "overlay_spec.json").write_text(json.dumps(overlay_spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (project.folder / "cue_track.json").write_text(json.dumps(cue_track, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        wizard["composition_platform"] = compose_platform
        wizard["composition_text_overlays"] = texts
        wizard["composition_image_overlays"] = images
        wizard["composition_video_overlays"] = videos
        if compose_platform in {"reel", "reel_horizontal"}:
            wizard["reel_text_overlays"], wizard["reel_image_overlays"], wizard["reel_video_overlays"] = texts, images, videos
            wizard["reel_logo_mode"] = logo_mode
        logo_overlay = header.get("logo_overlay") if isinstance(header.get("logo_overlay"), dict) else None
        if logo_overlay is not None:
            wizard["reel_logo_overlay"] = {
                "x": max(0.05, min(0.95, _coerce_float(logo_overlay.get("x")) if _coerce_float(logo_overlay.get("x")) is not None else 0.5)),
                "y": max(0.05, min(0.95, _coerce_float(logo_overlay.get("y")) if _coerce_float(logo_overlay.get("y")) is not None else 0.08)),
                "width": max(0.05, min(0.9, _coerce_float(logo_overlay.get("width")) if _coerce_float(logo_overlay.get("width")) is not None else 0.22)),
            }
        project.save()
        try:
            job = state.composition.start(project)
        except RuntimeError as exc:
            return error_response("composition_busy", str(exc), 409)
        return jsonify({"ok": True, "overlay_spec": str(project.folder / "overlay_spec.json"), "cue_track": str(project.folder / "cue_track.json"), "job": job.snapshot()}), 202

    @app.get("/api/v1/wizard/compose")
    def api_wizard_compose_state() -> Response:
        project = _require_project(state)
        try:
            overlay_spec = json.loads((project.folder / "overlay_spec.json").read_text(encoding="utf-8")) if (project.folder / "overlay_spec.json").is_file() else {"texts": [], "images": [], "videos": []}
            cue_track = json.loads((project.folder / "cue_track.json").read_text(encoding="utf-8")) if (project.folder / "cue_track.json").is_file() else {"cues": [], "style": "karaoke_word"}
        except (OSError, json.JSONDecodeError) as exc:
            return error_response("compose_state_invalid", str(exc), 409)
        return jsonify({"overlay_spec": overlay_spec, "cue_track": cue_track})

    @app.post("/api/v1/inputs/classify-paths")
    def api_classify_paths() -> Response:
        body = _json_body()
        paths = body.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            return error_response("bad_request", "paths must be a list of strings", 400)
        return jsonify(classify_paths(paths))

    @app.post("/api/v1/settings/inputs")
    def api_input_settings() -> Response:
        project = _require_project(state)
        body = _json_body()
        settings = project.data["settings"].setdefault("inputs", {})
        if "copy_into_project" in body:
            settings["copy_into_project"] = bool(body["copy_into_project"])
        project.save()
        return jsonify(project.snapshot())

    @app.post("/api/v1/settings/camera-subjects")
    def api_camera_subjects() -> Response:
        project = _require_project(state)
        assignments = _json_body().get("camera_subjects")
        if not isinstance(assignments, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in assignments.items()):
            return error_response("bad_request", "camera_subjects must map source paths to subject names", 400)
        allowed = {"unknown", "general", "drummer", "singer", "pianist", "guitarist", "bassist", "audience"}
        sources = {str(record.get("path") or "") for record in project.data.get("inputs", {}).get("videos", [])}
        if any(path not in sources or subject not in allowed for path, subject in assignments.items()):
            return error_response("bad_request", "Choose a registered camera and supported subject", 400)
        if state.wizard.status().get("status") in {"running", "cancelling"}:
            return error_response("wizard_busy", "Wait for the current edit before changing camera subjects", 409)
        edit = project.data.setdefault("settings", {}).setdefault("edit", {})
        edit["camera_subjects"] = dict(assignments)
        project.save()
        return jsonify({"camera_subjects": edit["camera_subjects"]})

    @app.post("/api/v1/settings/spherical-landmarks")
    def api_spherical_landmarks() -> Response:
        project = _require_project(state)
        body = _json_body()
        raw_landmarks = body.get("spherical_landmarks", body)
        incoming = _sanitize_spherical_landmarks(raw_landmarks)
        # The sanitizer supplies UI defaults for a complete form. For this
        # endpoint, however, callers may send a partial edit; retain only the
        # fields actually present so omitted values are not reset.
        if isinstance(raw_landmarks, dict):
            for key in list(incoming):
                raw_value = raw_landmarks.get(key)
                if isinstance(raw_value, dict):
                    incoming[key] = {field: incoming[key][field] for field in ("yaw", "pitch", "fov", "roll", "projection_preset", "projection_control", "weight", "subject", "enabled") if field in raw_value}
        source_path = str(body.get("spherical_source_path") or "").strip()
        source_key = str(Path(source_path).expanduser().resolve()) if source_path else ""
        config = load_global_config()
        global_landmarks = config.get("spherical_landmarks") or {}
        project_settings = project.data.setdefault("settings", {})
        project_landmarks = project_settings.get("spherical_landmarks") or {}
        landmarks = merge_spherical_landmarks(global_landmarks, project_landmarks)
        landmarks = merge_spherical_landmarks(landmarks, incoming)
        # Re-sanitize the merged profile too: old project/global values may
        # contain pre-2.1.19 pitch/FOV values even when the incoming edit is
        # only a partial landmark update.
        landmarks = _sanitize_spherical_landmarks(landmarks)
        project_settings["spherical_landmarks"] = landmarks
        if source_key:
            profiles = project_settings.setdefault("spherical_landmarks_by_source", {})
            profiles[source_key] = dict(landmarks)
            # Keep the original UI path as an alias as well. The desktop
            # picker can return a symlink/bookmark spelling while edit/export
            # resolve it to the canonical filesystem path.
            if source_path != source_key:
                profiles[source_path] = dict(landmarks)
            project_settings["spherical_source_path"] = source_key
        project.mark_all_stale_from("edit")
        project.save()
        config["spherical_landmarks"] = landmarks
        save_global_config(config)
        _spherical_event(
            project,
            "spherical_preview_saved",
            input_path=Path(source_path).resolve() if source_path else None,
            camera=Path(source_path).stem if source_path else None,
            reason="explicit_save_endpoint",
            extra={"landmarks": landmarks, "source_path": source_key},
        )
        return jsonify({
            "spherical_landmarks": landmarks,
            "spherical_source_path": source_key,
            "spherical_landmarks_by_source": project_settings.get("spherical_landmarks_by_source") or {},
        })

    @app.get("/api/v1/app/config")
    def api_app_config() -> Response:
        config = load_global_config()
        info = build_info()
        config["storage_path"] = str(app_home())
        config["project_root"] = str(config.get("project_root") or app_home() / "Projects")
        config["app_version"] = info["version"]
        config["source_revision"] = info["git_commit"]
        config["dev"] = state.dev
        config["desktop"] = not state.dev
        config.setdefault("camera_role_weights", {"360": 50.0, "handheld": 30.0, "fixed_rear": 20.0})
        config.setdefault("fixed_rear_motion", True)
        config.setdefault("spherical_motion", True)
        config.setdefault("spherical_hold_motion", "subtle")
        config.setdefault("spherical_mode", "automatic")
        config.setdefault("spherical_sweep", False)
        config.setdefault("sweep_speed_deg_per_sec", 5.0)
        config.setdefault("audio_trim_by_master", {})
        config.setdefault("master_audio_extensions", [".mp3"])
        config.setdefault("personal_logo_path", "")
        return jsonify(config)

    @app.get("/api/v1/cache/status")
    def api_cache_status() -> Response:
        return jsonify(cache_status())

    @app.get("/api/v1/maintenance/storage")
    def api_storage_maintenance_report() -> Response:
        """Return a read-only storage inventory and protected cleanup plan."""
        report = build_storage_report()
        protected = [str(state.project.folder)] if state.project else []
        return jsonify({
            "report": report,
            "cleanup_plan": cleanup_plan(report, protected_paths=protected),
            "protected_paths": protected,
        })

    @app.post("/api/v1/cache/free")
    def api_cache_free() -> Response:
        from core.normalization import cleanup_expired_segment_cache
        expired = cleanup_expired_segment_cache()
        result = cleanup_unreferenced_cache()
        result["before_bytes"] += expired["deleted_bytes"]
        result["deleted_bytes"] += expired["deleted_bytes"]
        result["deleted_files"] += expired["deleted_files"]
        result["expired_segments"] = expired
        return jsonify(result)

    @app.post("/api/v1/stages/<name>/run")
    def api_run_stage(name: str) -> Response:
        project = _require_project(state)
        try:
            state.engine.submit(project, name)
            return jsonify({"ok": True, "stage": name}), 202
        except StageNotFoundError as exc:
            return error_response("not_found", str(exc), 404)
        except StageBlockedError as exc:
            return error_response("stage_not_ready", str(exc), 409)
        except RuntimeError as exc:
            return error_response("busy", str(exc), 409)

    @app.get("/api/v1/stages/status")
    def api_stage_status() -> Response:
        if state.project and state.project.refresh_input_records():
            state.project.save()
        return jsonify(state.engine.status(state.project))

    @app.get("/api/v1/artifacts/<stage>")
    def api_artifact(stage: str) -> Response:
        project = _require_project(state)
        if stage not in state.engine.stages:
            return error_response("not_found", f"Unknown stage: {stage}", 404)
        outputs = project.data["stages"][stage].get("outputs") or {}
        if not outputs:
            return error_response("not_found", f"No artifacts for stage: {stage}", 404)
        first_path = next(iter(outputs.values()))
        try:
            with Path(first_path).open("r", encoding="utf-8") as fh:
                return jsonify(json.load(fh))
        except FileNotFoundError:
            return error_response("not_found", f"Artifact missing for stage: {stage}", 404)

    @app.post("/api/v1/stages/sync/override")
    def api_sync_override() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or "offset_sec" not in body:
            return error_response("bad_request", "clip_id and offset_sec are required", 400)
        try:
            clip = set_manual_override(project, clip_id, float(body["offset_sec"]))
            return jsonify({"ok": True, "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/stages/sync/override-ranges")
    def api_sync_override_ranges() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        ranges = body.get("offset_ranges")
        if not clip_id or not isinstance(ranges, list):
            return error_response("bad_request", "clip_id and offset_ranges are required", 400)
        try:
            clip = set_manual_override_ranges(project, clip_id, ranges)
            return jsonify({"ok": True, "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/stages/sync/anchor")
    def api_sync_anchor() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id or "master_sec" not in body or "clip_sec" not in body:
            return error_response("bad_request", "clip_id, master_sec, and clip_sec are required", 400)
        try:
            clip = set_manual_anchor(project, clip_id, body["master_sec"], body["clip_sec"])
            return jsonify({"ok": True, "offset_sec": clip["offset_sec"], "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)
        except (TypeError, ValueError) as exc:
            return error_response("bad_request", str(exc), 400)

    @app.post("/api/v1/stages/sync/override/clear")
    def api_sync_clear_override() -> Response:
        project = _require_project(state)
        body = _json_body()
        clip_id = str(body.get("clip_id") or "").strip()
        if not clip_id:
            return error_response("bad_request", "clip_id is required", 400)
        try:
            clip = clear_manual_override(project, clip_id)
            return jsonify({"ok": True, "clip": clip})
        except (KeyError, FileNotFoundError) as exc:
            return error_response("not_found", str(exc), 404)

    @app.get("/api/v1/stages/sync/preview/<clip_id>")
    def api_sync_preview(clip_id: str) -> Response:
        project = _require_project(state)
        try:
            path = generate_preview(project, clip_id)
            relative = path.relative_to(project.cache_dir)
            return jsonify({"media_url": f"/api/v1/media/cache/{relative.as_posix()}"})
        except KeyError as exc:
            return error_response("not_found", str(exc), 404)
        except RuntimeError as exc:
            return error_response("ffmpeg_error", str(exc), 500)

    @app.get("/api/v1/stages/sync/thumbnail/<clip_id>")
    def api_sync_thumbnail(clip_id: str) -> Response:
        project = _require_project(state)
        try:
            shot: dict[str, Any] = {}
            for key in ("shot_id", "type", "yaw", "pitch", "fov"):
                if request.args.get(key) is not None:
                    shot[key] = request.args.get(key)
            for key in ("yaw", "pitch", "fov"):
                if key in shot:
                    shot[key] = float(shot[key])
            return send_file_with_range(str(generate_thumbnail(project, clip_id, shot or None)))
        except (KeyError, ValueError) as exc:
            is_missing = isinstance(exc, KeyError)
            return error_response("not_found" if is_missing else "bad_request", str(exc), 404 if is_missing else 400)
        except RuntimeError as exc:
            return error_response("ffmpeg_error", str(exc), 500)

    @app.get("/api/v1/media/<path:kind>/<int:index>")
    def api_media(kind: str, index: int) -> Response:
        project = _require_project(state)
        try:
            path = _media_path(project, kind, index)
        except (KeyError, IndexError, ValueError) as exc:
            return error_response("not_found", str(exc), 404)
        return send_file_with_range(path)

    @app.get("/api/v1/media/cache/<path:relative_path>")
    def api_cached_media(relative_path: str) -> Response:
        project = _require_project(state)
        cache_path = (project.cache_dir / relative_path).resolve()
        try:
            cache_path.relative_to(project.cache_dir.resolve())
        except ValueError:
            return error_response("not_found", "Cached media path is outside project cache", 404)
        return send_file_with_range(str(cache_path))

    @app.errorhandler(404)
    def not_found(_: Exception) -> tuple[Response, int]:
        return error_response("not_found", "Not found", 404)

    @app.errorhandler(ProjectError)
    def project_error(exc: ProjectError) -> tuple[Response, int]:
        return error_response("project_error", str(exc), 400)

    @app.errorhandler(500)
    def server_error(exc: Exception) -> tuple[Response, int]:
        LOGGER.exception("Unhandled API error")
        return error_response("internal_error", str(exc), 500)

    return app


def error_response(code: str, message: str, status: int) -> tuple[Response, int]:
    """Return a consistent JSON error envelope."""
    return jsonify({"error": {"code": code, "message": message}}), status


def _json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def _require_project(state: AppState) -> Project:
    if state.project is None:
        raise ProjectError("No project is open")
    return state.project


def _remember_project(project: Project) -> None:
    config = load_global_config()
    config["last_project_path"] = str(project.folder)
    config["project_roots"] = list(dict.fromkeys([*config.get("project_roots", []), str(project.folder.parent)]))
    save_global_config(config)


def _project_wizard_status(project: Project) -> dict[str, Any]:
    stages = project.data.get("stages") or {}
    logs_path = str(project.cache_dir / "logs")
    failed = next(((name, stage) for name, stage in stages.items() if stage.get("status") == "failed"), None)
    if failed:
        name, stage = failed
        return {
            "id": "project",
            "status": "failed",
            "progress": _stage_progress(name),
            "message": t("cannot_finish"),
            "detail": name,
            "error": stage.get("error") or "Error",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    running = next(((name, stage) for name, stage in stages.items() if stage.get("status") == "running"), None)
    if running:
        name, _ = running
        return {
            "id": "project",
            "status": "failed",
            "progress": _stage_progress(name),
            "message": "This project cannot be recovered",
            "detail": "The saved process was interrupted. The videos are loaded below; press Start Again to run a new montage.",
            "error": "This project cannot be recovered from its saved process state.",
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    export_result = _export_result(project) if stages.get("export", {}).get("status") == "done" else None
    if export_result:
        return {
            "id": "project",
            "status": "done",
            "progress": 100,
            "message": t("done"),
            "detail": export_result["filename"],
            "result": export_result,
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    # A review-ready project must survive an app refresh/reopen. The in-memory
    # wizard job reports this state while Edit finishes, but once the process
    # is restarted only the persisted stage statuses remain. Do not downgrade
    # edit-complete projects to the old sync chooser.
    if stages.get("edit", {}).get("status") == "done" and stages.get("export", {}).get("status") != "done":
        platform = str((project.data.get("settings", {}).get("wizard") or {}).get("platform") or "youtube")
        if platform == "backstage" and (project.artifacts_dir / "backstage_paper_edit.json").exists():
            return {
                "id": "project",
                "status": "waiting_paper_edit",
                "progress": _stage_progress("edit"),
                "message": "Paper edit ready",
                "detail": "Review the written Backstage sequence before rendering.",
                "stage": "paper_edit",
                "paper_edit_available": True,
                "project_path": str(project.folder),
                "logs_path": logs_path,
            }
        if platform == "youtube" or (platform == "reel" and not is_single_source_reel(project)):
            return {
                "id": "project",
                "status": "waiting_review",
                "progress": _stage_progress("edit"),
                "message": "Review shots",
                "detail": "I'm artificial, but not that intelligent — help me check whether these shots are any good.",
                "stage": "review",
                "project_path": str(project.folder),
                "logs_path": logs_path,
            }
    platform = str((project.data.get("settings", {}).get("wizard") or {}).get("platform") or "youtube")
    prepared_without_sync = platform in {"reel", "backstage"} and stages.get("ingest", {}).get("status") == "done"
    if (stages.get("sync", {}).get("status") == "done" or prepared_without_sync) and stages.get("export", {}).get("status") != "done":
        return {
            "id": "project",
            "status": "waiting_choice",
            "progress": 95,
            "message": t("ready_to_edit"),
            "detail": t("choose_edit_type"),
            "project_path": str(project.folder),
            "logs_path": logs_path,
        }
    return {
        "id": "project",
        "status": "idle",
        "progress": 0,
        "message": "Idle",
        "project_path": str(project.folder),
        "logs_path": logs_path,
    }


def _can_reuse_prepared_project(project: Project | None, master: str, songs: str | None, videos: list[str]) -> bool:
    """Return True when a loaded project already has these inputs prepared through sync."""
    if not project:
        return False
    if project.refresh_input_records():
        project.save()
    stages = project.data.get("stages") or {}
    if stages.get("sync", {}).get("status") != "done":
        return False
    if not _project_has_sync_candidates(project):
        return False
    inputs = project.data.get("inputs") or {}
    if _resolved(inputs.get("master", {}).get("path")) != _resolved(master):
        return False
    project_songs = inputs.get("songs")
    project_songs_path = project_songs.get("path") if project_songs else None
    if songs and _resolved(project_songs_path) != _resolved(songs):
        return False
    return project_input_signature(project) == input_signature(master, songs, videos)


def _project_can_skip_prepare(project: Project, platform: str | None = None) -> bool:
    """Return True when an existing project can resume at edit-choice time."""
    if project.refresh_input_records():
        project.save()
    stages = project.data.get("stages") or {}
    selected_platform = platform or str((project.data.get("settings", {}).get("wizard") or {}).get("platform") or "youtube")
    if selected_platform in {"reel", "backstage"}:
        return stages.get("ingest", {}).get("status") == "done"
    return stages.get("sync", {}).get("status") == "done" and _project_has_sync_candidates(project)


def _project_matches_inputs(project: Project, master: str, songs: str | None, videos: list[str]) -> bool:
    """Compare paths for a reopened video-only project without requiring a master."""
    inputs = project.data.get("inputs") or {}
    registered_master = (inputs.get("master") or {}).get("path") if isinstance(inputs.get("master"), dict) else inputs.get("master")
    registered_songs = (inputs.get("songs") or {}).get("path") if isinstance(inputs.get("songs"), dict) else inputs.get("songs")
    registered_videos = [str(item.get("path") or "") if isinstance(item, dict) else str(item) for item in inputs.get("videos") or []]
    resolve = lambda value: str(Path(value).expanduser().resolve()) if value else None
    return (
        resolve(registered_master) == resolve(master)
        and resolve(registered_songs) == resolve(songs)
        and sorted(resolve(value) for value in registered_videos) == sorted(resolve(value) for value in videos)
    )


def _resolved(path: str | None) -> str | None:
    if not path:
        return None
    return str(Path(path).expanduser().resolve())


def _project_has_sync_candidates(project: Project) -> bool:
    outputs = project.data.get("stages", {}).get("sync", {}).get("outputs") or {}
    sync_map_path = outputs.get("sync_map") or str(project.artifacts_dir / "sync_map.json")
    try:
        with Path(sync_map_path).open("r", encoding="utf-8") as fh:
            sync_map = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return False
    for clip in (sync_map.get("clips") or {}).values():
        if clip.get("error") or clip.get("no_audio"):
            continue
        return True
    return False


def _active_wizard_project(state: AppState) -> Project | None:
    status = state.wizard.status()
    project_path = status.get("project_path")
    if not project_path:
        return None
    try:
        return load_project(project_path)
    except (OSError, ProjectError):
        return None


def _export_result(project: Project) -> dict[str, Any] | None:
    outputs = project.data.get("stages", {}).get("export", {}).get("outputs") or {}
    manifest_path = outputs.get("export_manifest")
    if not manifest_path:
        return None
    try:
        with Path(manifest_path).open("r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    exports = manifest.get("exports") or []
    if not exports:
        return None
    export = exports[0]
    path = Path(export.get("path") or "")
    if not path.is_file() or path.stat().st_size == 0:
        # A cancelled/failed export can leave the manifest pointing at a
        # zero-byte MP4. Result must never serve that stale pointer; recover
        # the newest valid base export instead.
        candidates = sorted(project.exports_dir.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
        path = next((candidate for candidate in candidates if candidate.stat().st_size > 0 and "_composed" not in candidate.name and "_captions" not in candidate.name and "_overlay-composed" not in candidate.name), None)
        if path is None:
            return None
    return {
        "project_path": str(project.folder),
        "filename": path.name,
        "path": str(path),
        "media_url": "/api/v1/wizard/result",
        "platform": export.get("platform"),
        "reel_base_logo_policy_version": manifest.get("reel_base_logo_policy_version"),
        "logs_path": str(project.cache_dir / "logs"),
        "cut_count": export.get("cut_count"),
        "camera_usage": export.get("camera_usage"),
        "spherical_shot_usage": export.get("spherical_shot_usage") or manifest.get("spherical_shot_usage") or {},
        "spherical_recording_usage": export.get("spherical_recording_usage") or manifest.get("spherical_recording_usage") or {},
        "warnings": export.get("warnings") or manifest.get("warnings") or [],
        "excluded_clips": export.get("excluded_clips") or [],
        "clip_fates": export.get("clip_fates") or [],
    }


def _latest_media_result(state: AppState, project: Project) -> dict[str, Any] | None:
    composition = state.composition.status()
    if composition and composition.get("status") == "done" and same_project_path(composition.get("project_path"), str(project.folder)):
        result = composition.get("result")
        if isinstance(result, dict) and Path(str(result.get("path") or "")).is_file():
            return result
    return _export_result(project)


def _stage_progress(stage_name: str) -> int:
    return {"ingest": 8, "sync": 35, "cut": 56, "edit": 64, "export": 76}.get(stage_name, 0)


def _friendly_stage_message(stage_name: str) -> str:
    return {
        "ingest": t("listening"),
        "sync": t("syncing_audio"),
        "cut": t("cutting_song"),
        "edit": t("building_edit"),
        "export": t("exporting_video"),
    }.get(stage_name, t("working"))


def _media_path(project: Project, kind: str, index: int) -> str:
    inputs = project.data["inputs"]
    if kind == "videos":
        return record_media_path(inputs["videos"][index])
    if kind == "master":
        if index != 0 or not inputs.get("master"):
            raise ValueError("Master media is not registered")
        return inputs["master"]["path"]
    raise ValueError(f"Unknown media kind: {kind}")


def _audio_trim_from_body(body: dict[str, Any]) -> dict[str, float] | None:
    start = body.get("trim_start_sec")
    end = body.get("trim_end_sec")
    start_value = _coerce_float(start)
    end_value = _coerce_float(end)
    trim: dict[str, float] = {}
    if start_value is not None:
        trim["start_sec"] = max(0.0, start_value)
    if end_value is not None:
        trim["end_sec"] = max(0.0, end_value)
    if trim and trim.get("end_sec", 1.0) <= trim.get("start_sec", 0.0):
        return None
    return trim or None


def _reel_duration_from_body(body: dict[str, Any]) -> float:
    value = _coerce_float(body.get("reel_duration_sec"))
    return max(20.0, min(60.0, value if value is not None else 30.0))


def _backstage_duration_from_body(body: dict[str, Any]) -> float:
    value = _coerce_float(body.get("backstage_duration_sec"))
    return max(30.0, min(240.0, value if value is not None else 180.0))


def _save_last_reel_overlays(texts: list[dict[str, Any]], images: list[dict[str, Any]]) -> None:
    path = app_home() / "reel_overlays.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"texts": texts, "images": images}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        LOGGER.warning("Could not persist last Reel overlays", exc_info=True)


def _reel_aspect_from_body(body: dict[str, Any]) -> str:
    value = str(body.get("reel_aspect") or "9:16")
    return value if value in {"9:16", "16:9", "mix", "mix_vertical_horizontal"} else "9:16"


def _reel_mix_vertical_ratio_from_body(body: dict[str, Any]) -> str | float:
    value = body.get("reel_mix_vertical_ratio", "auto")
    if str(value).lower() == "auto":
        return "auto"
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return "auto"


def _reel_cuts_per_source_from_body(body: dict[str, Any]) -> float:
    value = _coerce_float(body.get("reel_cuts_per_source"))
    return max(1.0, min(5.0, value if value is not None else 1.0))


def _reel_text_overlays_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("reel_text_overlays") or []
    if not isinstance(raw, list):
        return []
    positions = {"top-left", "top-center", "top-right", "middle-left", "middle-center", "middle-right", "bottom-left", "bottom-center", "bottom-right"}
    import re
    result = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:200]
        if not text:
            continue
        color = str(item.get("color") or "#ffffff").lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", color):
            color = "#ffffff"
        size = _coerce_float(item.get("size")) or 54.0
        start = max(0.0, _coerce_float(item.get("start_sec")) or 0.0)
        duration = max(0.1, _coerce_float(item.get("duration_sec")) or 3.0)
        x = _coerce_float(item.get("x")); y = _coerce_float(item.get("y"))
        def color_value(key: str, fallback: str) -> str:
            value = str(item.get(key) or fallback).lower()
            return value if re.fullmatch(r"#[0-9a-f]{6}", value) else fallback
        animation = str(item.get("animation") or "fade")
        result.append({
            "text": text, "color": color, "size": max(18.0, min(160.0, size)),
            "position": str(item.get("position") or "middle-center") if str(item.get("position") or "middle-center") in positions else "middle-center",
            "x": max(0.0, min(1.0, x)) if x is not None else None,
            "y": max(0.0, min(1.0, y)) if y is not None else None,
            "opacity": max(0.05, min(1.0, _coerce_float(item.get("opacity")) or 1.0)),
            "font": str(item.get("font") or "bundled")[:40],
            "font_weight": str(item.get("font_weight") or "bold") if str(item.get("font_weight") or "bold") in {"normal", "bold"} else "bold",
            "outline_color": color_value("outline_color", "#000000"),
            "outline_width": max(0.0, min(12.0, _coerce_float(item.get("outline_width")) or 2.0)),
            "shadow_color": color_value("shadow_color", "#000000"),
            "shadow_offset_x": max(-40.0, min(40.0, _coerce_float(item.get("shadow_offset_x")) or 3.0)),
            "shadow_offset_y": max(-40.0, min(40.0, _coerce_float(item.get("shadow_offset_y")) or 3.0)),
            "shadow_blur": max(0.0, min(30.0, _coerce_float(item.get("shadow_blur")) or 4.0)),
            "background_color": color_value("background_color", "#000000"),
            "background_opacity": max(0.0, min(1.0, _coerce_float(item.get("background_opacity")) or 0.0)),
            "background_radius": max(0.0, min(80.0, _coerce_float(item.get("background_radius")) or 0.0)),
            "animation": animation if animation in {"none", "fade", "slide", "scale"} else "fade",
            "start_sec": start, "duration_sec": min(60.0, duration)
        })
    return result


def _reel_image_overlays_from_body(body: dict[str, Any]) -> list[dict[str, Any]]:
    raw = body.get("reel_image_overlays") or []
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw[:8]:
        if not isinstance(item, dict):
            continue
        path = Path(str(item.get("path") or "")).expanduser().resolve()
        if not path.exists() or path.suffix.lower() not in {".png", ".webp", ".jpg", ".jpeg"}:
            continue
        start = max(0.0, _coerce_float(item.get("start_sec")) or 0.0)
        duration = min(60.0, max(0.1, _coerce_float(item.get("duration_sec")) or 3.0))
        x_value = _coerce_float(item.get("x")); y_value = _coerce_float(item.get("y"))
        def color_value(key: str, fallback: str) -> str:
            value = str(item.get(key) or fallback).strip()
            return value if len(value) == 7 and value.startswith("#") else fallback

        result.append({
            "path": str(path),
            "x": max(0.0, min(1.0, x_value if x_value is not None else 0.5)),
            "y": max(0.0, min(1.0, y_value if y_value is not None else 0.5)),
            "width": max(0.05, min(1.0, _coerce_float(item.get("width")) or 0.35)),
            "opacity": max(0.05, min(1.0, _coerce_float(item.get("opacity")) or 1.0)),
            "animation": str(item.get("animation") or "fade") if str(item.get("animation") or "fade") in {"none", "fade", "slide", "scale"} else "fade",
            "start_sec": start,
            "duration_sec": duration,
            "tint_color": color_value("tint_color", "#ffffff"),
            "tint_opacity": max(0.0, min(1.0, _coerce_float(item.get("tint_opacity")) or 0.0)),
            "shadow_color": color_value("shadow_color", "#000000"),
            "shadow_distance": max(0.0, min(40.0, _coerce_float(item.get("shadow_distance")) or 0.0)),
            "shadow_blur": max(0.0, min(40.0, _coerce_float(item.get("shadow_blur")) or 0.0)),
            "shadow_opacity": max(0.0, min(1.0, _coerce_float(item.get("shadow_opacity")) or 0.0)),
            "glow_color": color_value("glow_color", "#ffffff"),
            "glow_blur": max(0.0, min(40.0, _coerce_float(item.get("glow_blur")) or 0.0)),
            "glow_layers": max(0, min(8, int(_coerce_float(item.get("glow_layers")) or 0))),
        })
    return result


def _spherical_landmarks_from_body(body: dict[str, Any]) -> dict[str, dict[str, float]] | None:
    if "spherical_landmarks" not in body:
        return None
    return _sanitize_spherical_landmarks(body.get("spherical_landmarks") or {})


def _camera_role_weights_from_body(body: dict[str, Any]) -> dict[str, float] | None:
    if "camera_role_weights" not in body:
        return None
    return _sanitize_camera_role_weights(body.get("camera_role_weights") or {})


def _spherical_motion_from_body(body: dict[str, Any]) -> bool | None:
    if "spherical_motion" not in body:
        return None
    return bool(body.get("spherical_motion"))


def _fixed_rear_motion_from_body(body: dict[str, Any]) -> bool | None:
    if "fixed_rear_motion" not in body:
        return None
    return bool(body.get("fixed_rear_motion"))


def _spherical_mode_from_body(body: dict[str, Any]) -> str | None:
    if "spherical_mode" not in body:
        return None
    value = str(body.get("spherical_mode") or "automatic").strip().lower()
    return value if value in {"automatic", "directed"} else "automatic"


def _spherical_sweep_from_body(body: dict[str, Any]) -> bool | None:
    if "spherical_sweep" not in body:
        return None
    return bool(body.get("spherical_sweep"))


def _sweep_speed_from_body(body: dict[str, Any]) -> float | None:
    if "sweep_speed_deg_per_sec" not in body:
        return None
    return max(3.0, min(8.0, _optional_float_setting(body.get("sweep_speed_deg_per_sec"), 5.0)))


def _sanitize_camera_role_weights(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    weights: dict[str, float] = {}
    for key in ("360", "handheld", "fixed_rear"):
        if key not in raw:
            continue
        weights[key] = max(0.0, _optional_float_setting(raw.get(key), 0.0))
    return weights


def _sanitize_spherical_landmarks(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    defaults = {
        "full_stage": {"legacy": "full_stage_yaw", "fov": 120.0},
        "singer": {"legacy": "singer_yaw", "fov": 95.0},
        "drummer": {"legacy": "drummer_yaw", "fov": 95.0},
        "pianist": {"legacy": "pianist_yaw", "fov": 95.0},
        "left": {"legacy": "left_yaw", "fov": 95.0},
        "right": {"legacy": "right_yaw", "fov": 95.0},
        "audience": {"legacy": "audience_yaw", "fov": 95.0},
        "audience_stage_wide": {"legacy": "audience_stage_wide_yaw", "fov": 125.0},
        "planet": {"legacy": "planet_yaw", "fov": 150.0},
    }
    landmarks: dict[str, dict[str, Any]] = {}
    for key, meta in defaults.items():
        source = raw.get(key)
        if source is None and meta["legacy"] in raw:
            source = {"yaw": raw.get(meta["legacy"])}
        if not isinstance(source, dict):
            continue
        yaw = _optional_degrees(source.get("yaw"))
        if yaw is None:
            continue
        # Persist the same canonical pose used by preview/review/export.
        # Saving raw pitch/FOV here was the remaining preview-to-MP4 drift.
        projection_preset = normalize_projection_preset(source.get("projection_preset"), key)
        pitch = effective_pitch(_optional_float_setting(source.get("pitch"), 0.0), key)
        fov = effective_fov(_optional_float_setting(source.get("fov"), float(meta["fov"])), key, projection_preset)
        roll = effective_roll(_optional_float_setting(source.get("roll"), 0.0), key)
        projection_control = effective_projection_control(source.get("projection_control"))
        weight = max(0.0, _optional_float_setting(source.get("weight"), 1.0))
        landmarks[key] = {
            "yaw": yaw,
            "pitch": pitch,
            "fov": fov,
            "roll": roll,
            "projection_preset": projection_preset,
            "projection_control": projection_control,
            "weight": weight,
            "subject": str(source.get("subject") or key),
            "enabled": source.get("enabled", True) is not False,
        }
    return landmarks


def _optional_degrees(value: Any) -> float | None:
    number = _coerce_float(value)
    return None if number is None else round(number % 360.0, 3)


def _optional_float_setting(value: Any, fallback: float) -> float:
    number = _coerce_float(value)
    return fallback if number is None else round(number, 3)


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    text = str(value).strip().replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# Wide-FOV preview: above this the preview switches to stereographic to
# match the export's wide/tiny-planet rendering (see export._use_stereographic).
def _preview_source_duration(path: Path) -> float:
    try:
        metadata = ffprobe(str(path))
        value = (metadata.get("format") or {}).get("duration")
        return max(0.1, float(value))
    except Exception:
        return 1.0


def _project_audio_trim(project: Project) -> tuple[float, float]:
    master = project.data.get("inputs", {}).get("master") or {}
    duration = 0.0
    try:
        duration = float(master.get("duration") or (master.get("probe") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0 and master.get("path"):
        try:
            duration = _preview_source_duration(Path(str(master["path"])))
        except Exception:
            duration = 0.0
    trim = project.data.get("settings", {}).get("wizard", {}).get("audio_trim") or {}
    try:
        start = max(0.0, float(trim.get("start_sec") or 0.0))
    except (TypeError, ValueError):
        start = 0.0
    try:
        end = float(trim.get("end_sec") or duration or start + 1.0)
    except (TypeError, ValueError):
        end = duration or start + 1.0
    if duration > 0:
        end = min(duration, end)
    if end <= start:
        end = start + 1.0
    return round(start, 6), round(end, 6)


def _enable_cors(app: Flask) -> None:
    @app.after_request
    def add_cors(response: Response) -> Response:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Range"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return response
