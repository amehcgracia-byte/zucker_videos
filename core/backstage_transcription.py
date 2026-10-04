"""Optional local Whisper transcription for Backstage.

This module deliberately has no network/API path.  The transcript artifact is
cacheable per source fingerprint and model version, so later story-bite/LLM
passes can consume it without decoding the clips again.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from core.storage import whisper_model_path, data_root
from typing import Any, Callable

from core.stages.base import stable_fingerprint, write_artifact_json
from core.normalization import global_cache_root
from core.ffmpeg import ensure_tools_on_path, ffprobe

LOGGER = logging.getLogger(__name__)
WHISPER_TRANSCRIPTION_VERSION = 8
DEFAULT_WHISPER_MODEL = "large-v3"
DEFAULT_WHISPER_TASK = "transcribe"
LANGUAGE_CONFIDENCE_THRESHOLD = 0.75


def _faster_whisper_assets_path() -> Path:
    """Resolve faster-whisper's runtime assets in dev and frozen apps.

    PyInstaller places collected package data below ``sys._MEIPASS``.  The
    upstream package derives this path from its imported module, so explicitly
    selecting the frozen copy also protects the VAD lookup from a stale source
    checkout or a partially collected package.
    """
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    bundled = bundle_root / "faster_whisper" / "assets"
    if bundled.is_dir():
        return bundled
    try:
        from faster_whisper.utils import get_assets_path  # type: ignore

        return Path(get_assets_path())
    except Exception:
        return bundled


def _configure_faster_whisper_assets() -> Path:
    """Point faster-whisper's VAD loader at the packaged assets directory."""
    import faster_whisper.utils as whisper_utils  # type: ignore

    assets = _faster_whisper_assets_path()
    vad_model = assets / "silero_vad_v6.onnx"
    if not vad_model.is_file():
        raise FileNotFoundError(f"faster-whisper VAD model is missing: {vad_model}")
    whisper_utils.get_assets_path = lambda: str(assets)
    return assets


def extract_story_bites(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group Whisper segments into complete, reviewable phrase-sized bites."""
    bites: list[dict[str, Any]] = []
    for source in sources:
        current: list[dict[str, Any]] = []
        def flush() -> None:
            nonlocal current
            if not current:
                return
            text = " ".join(str(item.get("text") or "").strip() for item in current).strip()
            if text:
                start = float(current[0].get("start_sec") or 0.0)
                end = float(current[-1].get("end_sec") or start)
                word_count = len(text.split())
                punctuation_complete = text.endswith((".", "!", "?", "…"))
                bite_id = stable_fingerprint({"path": source.get("path"), "start": round(start, 3), "end": round(end, 3), "text": text})[:20]
                bites.append({
                    "id": bite_id,
                    "source_path": source.get("path"),
                    "filename": source.get("filename"),
                    "start_sec": round(start, 3),
                    "end_sec": round(end, 3),
                    "text": text,
                    "text_original": text,
                    "speaker": "unknown",
                    "word_count": word_count,
                    "complete": bool(punctuation_complete or word_count >= 8),
                    "narrative_scores": {"funny": None, "story": None, "hook": None, "payoff": None, "complete": bool(punctuation_complete or word_count >= 8)},
                })
            current = []
        for segment in source.get("segments") or []:
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            if current and (float(segment.get("start_sec") or 0.0) - float(current[-1].get("end_sec") or 0.0) > 0.85 or sum(len(str(item.get("text") or "").split()) for item in current) >= 42):
                flush()
            current.append(segment)
            if text.endswith((".", "!", "?", "…")):
                flush()
        flush()
    return bites


def _source_fingerprint(
    path: str,
    model_name: str,
    task: str,
    backend: str,
    forced_language: str | None = None,
    transcription_options: dict[str, Any] | None = None,
) -> str:
    source = Path(path)
    stat = source.stat()
    return stable_fingerprint({
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "model": model_name,
        "version": WHISPER_TRANSCRIPTION_VERSION, "task": task,
        "forced_language": forced_language or "auto",
        "transcription_options": transcription_options or {},
    })


def _media_diagnostics(path: str) -> dict[str, Any]:
    """Describe the exact media handed to Whisper without extracting/truncating it."""
    metadata: dict[str, Any] = {
        "audio_input_path": str(Path(path).resolve()),
        "extraction_command": None,
        "audio_extracted": False,
        "video_duration_sec": None,
        "audio_duration_sec": None,
        "audio_codec": None,
    }
    try:
        probe = ffprobe(path)
        streams = probe.get("streams") or []
        video = next((item for item in streams if item.get("codec_type") == "video"), None)
        audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
        metadata["video_duration_sec"] = float((video or {}).get("duration") or 0.0) or None
        metadata["audio_duration_sec"] = float((audio or {}).get("duration") or ((probe.get("format") or {}).get("duration") if audio else 0.0) or 0.0) or None
        metadata["audio_codec"] = (audio or {}).get("codec_name")
    except Exception as exc:
        metadata["probe_error"] = str(exc)
        LOGGER.warning("Unable to probe Whisper input %s: %s", path, exc)
    return metadata


def _cache_path(fingerprint: str) -> Path:
    root = global_cache_root() / "transcription"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{fingerprint}.json"


def _backend_name() -> str:
    forced = os.environ.get("ZUCKER_WHISPER_BACKEND", "").strip().lower()
    if forced:
        return forced
    try:
        import mlx_whisper  # type: ignore  # noqa: F401
        return "mlx-whisper"
    except Exception:
        pass
    try:
        import faster_whisper  # type: ignore  # noqa: F401
        return "faster-whisper"
    except Exception:
        return "openai-whisper"


def transcribe_sources(
    sources: list[dict[str, Any]], artifact: Path,
    progress_callback: Callable[[int, str], None] | None = None,
    model_name: str = DEFAULT_WHISPER_MODEL,
    task: str = DEFAULT_WHISPER_TASK,
    language_overrides: dict[str, str] | None = None,
    language_confidence_threshold: float = LANGUAGE_CONFIDENCE_THRESHOLD,
    model_by_language: dict[str, str] | None = None,
    initial_prompt: str | None = None,
    vad_filter: bool = True,
    vad_parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transcribe/translate all sources, using an app-wide fingerprint cache."""
    progress_callback = progress_callback or (lambda _p, _m: None)
    # openai-whisper invokes the external executable as ``ffmpeg``. Resolve
    # the same binary used by the renderer before importing/using Whisper;
    # configure_tools also prepends its directory to this process PATH.
    if not ensure_tools_on_path().get("ffmpeg_path"):
        payload = {"stage": "backstage_transcription", "version": WHISPER_TRANSCRIPTION_VERSION, "model": model_name, "task": task, "backend": _backend_name(), "status": "unavailable", "reason": "ffmpeg is required for local Whisper transcription", "sources": []}
        write_artifact_json(artifact, payload)
        return payload
    backend = _backend_name()
    vad_parameters = dict(vad_parameters or {})
    transcription_options = {
        "vad_filter": bool(vad_filter),
        "vad_parameters": vad_parameters,
        "condition_on_previous_text": True,
    }

    old = {}
    if artifact.exists():
        try:
            candidate = json.loads(artifact.read_text(encoding="utf-8"))
            if candidate.get("version") == WHISPER_TRANSCRIPTION_VERSION and candidate.get("model") == model_name and candidate.get("task") == task:
                old = {str(item.get("path")): item for item in candidate.get("sources", [])}
        except (OSError, ValueError, TypeError):
            old = {}
    started = time.perf_counter()
    output_sources: list[dict[str, Any]] = []
    missing = []
    language_overrides = {str(key): str(value).strip().lower() for key, value in (language_overrides or {}).items() if value}
    initial_prompt = str(initial_prompt or "").strip() or None
    model_by_language = {str(key).strip().lower(): str(value) for key, value in (model_by_language or {}).items() if value}
    for source in sources:
        path = str(source["path"])
        forced_language = language_overrides.get(path) or language_overrides.get(str(source.get("filename") or Path(path).name)) or source.get("language_hint")
        forced_language = str(forced_language).strip().lower() if forced_language else None
        source_model = model_by_language.get(forced_language or "", model_name)
        fingerprint = _source_fingerprint(path, source_model, task, backend, forced_language, transcription_options)
        cache = _cache_path(fingerprint)
        media_diagnostics = _media_diagnostics(path)
        LOGGER.info(
            "Whisper input path=%s video_duration_sec=%s audio_duration_sec=%s "
            "audio_input_path=%s extraction_command=%s vad_filter=%s vad_parameters=%s",
            path,
            media_diagnostics.get("video_duration_sec"),
            media_diagnostics.get("audio_duration_sec"),
            media_diagnostics.get("audio_input_path"),
            media_diagnostics.get("extraction_command"),
            vad_filter,
            vad_parameters,
        )
        cached = None
        try:
            if cache.exists():
                cached = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            cached = None
        if cached and cached.get("fingerprint") == fingerprint:
            cached = dict(cached)
            cached["path"] = path
            cached["filename"] = source.get("filename") or Path(path).name
            cached["media_diagnostics"] = media_diagnostics
            LOGGER.info("Whisper cache hit path=%s fingerprint=%s segments=%s", path, fingerprint, len(cached.get("segments") or []))
            output_sources.append(cached)
        else:
            missing.append((source, path, fingerprint, cache, forced_language, source_model))

    models: dict[str, Any] = {}
    if missing:
        try:
            for source_model in sorted({item[5] for item in missing}):
                if backend == "faster-whisper":
                    _configure_faster_whisper_assets()
                    from faster_whisper import WhisperModel  # type: ignore
                    compute_type = "int8_float16" if platform.machine() in {"arm64", "aarch64"} else "int8"
                    models[source_model] = WhisperModel(whisper_model_path(source_model), device="auto", compute_type=compute_type)
                elif backend == "mlx-whisper":
                    import mlx_whisper  # type: ignore
                    models[source_model] = mlx_whisper
                else:
                    import whisper  # type: ignore
                    models[source_model] = whisper.load_model(source_model, download_root=str(data_root() / "Cache" / "OpenAIWhisper"))
        except Exception as exc:
            payload = {"stage": "backstage_transcription", "version": WHISPER_TRANSCRIPTION_VERSION, "model": model_name, "task": task, "backend": backend, "status": "unavailable", "reason": f"Local Whisper unavailable: {exc}", "sources": output_sources}
            write_artifact_json(artifact, payload)
            return payload
    for index, (source, path, fingerprint, cache, forced_language, source_model) in enumerate(missing):
        progress_callback(int(index / max(1, len(sources)) * 90), f"Transcribing {Path(path).name}")
        model = models[source_model]
        if backend == "faster-whisper":
            kwargs = {
                "task": task, "language": forced_language, "beam_size": 5,
                "word_timestamps": True, "vad_filter": bool(vad_filter),
                "condition_on_previous_text": True,
            }
            if vad_filter and vad_parameters:
                kwargs["vad_parameters"] = vad_parameters
            if initial_prompt:
                kwargs["initial_prompt"] = initial_prompt
            # Whisper language is selected once per source. This prevents a
            # low-confidence segment from changing language in the middle of
            # a sentence; callers may still force a source language.
            try:
                segments_iter, info = model.transcribe(path, **kwargs)
                transcription_segments = list(segments_iter)
            except (IndexError, ValueError) as exc:
                # Some short AAC/MP4 montages make faster-whisper's word
                # alignment index empty even though segment transcription is
                # valid. Auto Read's fixed-colour default does not require
                # word timings, so recover the captions instead of failing
                # the whole job; explicit karaoke remains available whenever
                # the backend returns word timestamps successfully.
                LOGGER.warning("Whisper word alignment failed for %s; retrying without word timestamps: %s", path, exc)
                fallback_kwargs = {**kwargs, "word_timestamps": False}
                segments_iter, info = model.transcribe(path, **fallback_kwargs)
                transcription_segments = list(segments_iter)
            detected_language = getattr(info, "language", None)
            language_probability = getattr(info, "language_probability", None)
        elif backend == "mlx-whisper":
            transcription = model.transcribe(path, path_or_hf_repo=model_name, task=task, language=forced_language, word_timestamps=True, initial_prompt=initial_prompt)
            transcription_segments = transcription.get("segments", [])
            detected_language = transcription.get("language")
            language_probability = transcription.get("language_probability")
        else:
            transcription = model.transcribe(path, task=task, language=forced_language, fp16=False, verbose=False, condition_on_previous_text=True, word_timestamps=True, initial_prompt=initial_prompt)
            transcription_segments = transcription.get("segments", [])
            detected_language = transcription.get("language")
            language_probability = transcription.get("language_probability")
        segments = []
        for item in transcription_segments:
                words = getattr(item, "words", None) if not isinstance(item, dict) else item.get("words")
                text = getattr(item, "text", "") if not isinstance(item, dict) else item.get("text", "")
                start = getattr(item, "start", 0.0) if not isinstance(item, dict) else item.get("start", 0.0)
                end = getattr(item, "end", 0.0) if not isinstance(item, dict) else item.get("end", 0.0)
                segments.append({
                    "start_sec": round(float(start), 3), "end_sec": round(float(end), 3), "text": str(text).strip(),
                    "words": [
                        {"start_sec": round(float(getattr(word, "start", 0.0) if not isinstance(word, dict) else word.get("start", 0.0)), 3), "end_sec": round(float(getattr(word, "end", 0.0) if not isinstance(word, dict) else word.get("end", 0.0)), 3), "word": str(getattr(word, "word", "") if not isinstance(word, dict) else word.get("word", ""))}
                        for word in (words or [])
                    ],
                })
        accepted_language = forced_language or (str(detected_language or "").lower() if language_probability is not None and float(language_probability) >= language_confidence_threshold else None)
        result = {
            "path": path, "fingerprint": fingerprint, "task": task, "backend": backend,
            "filename": source.get("filename") or Path(path).name,
            "language": accepted_language,
            "detected_language": str(detected_language or "").lower() or None,
            "language_probability": round(float(language_probability), 4) if language_probability is not None else None,
            "language_forced": bool(forced_language),
            "language_review_required": not bool(forced_language) and (language_probability is None or float(language_probability) < language_confidence_threshold),
            "segments": segments,
            "media_diagnostics": media_diagnostics,
            "transcription_options": transcription_options,
        }
        LOGGER.info(
            "Whisper result path=%s segments=%s range_sec=%s-%s",
            path,
            len(segments),
            segments[0]["start_sec"] if segments else None,
            segments[-1]["end_sec"] if segments else None,
        )
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        output_sources.append(result)
    bites = extract_story_bites(output_sources)
    payload = {
        "stage": "backstage_transcription", "version": WHISPER_TRANSCRIPTION_VERSION,
        "model": model_name, "task": task, "backend": backend, "status": "ready", "elapsed_sec": round(time.perf_counter() - started, 3),
        "sources": output_sources, "story_bites": bites, "initial_prompt": initial_prompt,
        "transcription_options": transcription_options,
    }
    write_artifact_json(artifact, payload)
    progress_callback(100, "Backstage transcription ready")
    return payload
