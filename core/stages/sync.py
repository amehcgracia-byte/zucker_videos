"""Audio-based clip synchronization stage."""

from __future__ import annotations

import json
import hashlib
import math
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import butter, correlate, sosfiltfilt

from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.media_validation import record_is_usable_camera_video, record_media_path
from core.messages import t
from core.normalization import global_cache_root, global_clip_audio_path, global_clip_envelope_path, global_thumbnail_path, source_cache_key
from core.project import Project, atomic_write_json
from core.stages.base import ProgressCallback, Stage, artifact_path, file_signature, stable_fingerprint, write_artifact_json

SYNC_SAMPLE_RATE = 22050
SYNC_HOP_LENGTH = 512
PREVIEW_SECONDS = 10.0


class SyncStage(Stage):
    """Match each clip's audio against the master timeline."""

    name = "sync"
    dependencies = ["ingest"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint master, videos, ingest output, and sync settings."""
        return stable_fingerprint(
            {
                "ingest": project.data["stages"]["ingest"].get("fingerprint"),
                "master": project.data["inputs"].get("master"),
                "videos": project.data["inputs"].get("videos", []),
                "settings": project.data["settings"].get(self.name, {}),
                "algorithm": {"sr": SYNC_SAMPLE_RATE, "hop": SYNC_HOP_LENGTH, "version": 3, "verify_tolerance_sec": 0.150},
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the sync map artifact path."""
        return {"sync_map": str(sync_map_path(project))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Build a real sync map by correlating onset envelopes."""
        master_record = project.data["inputs"].get("master")
        if not master_record:
            raise ValueError("Master audio must be registered before sync")

        old_map = load_sync_map(project, missing_ok=True)
        migrate_legacy_manual_overrides(project, old_map)
        master_env = load_or_compute_master_envelope(project)
        master_duration = media_duration(master_record["path"])
        clips: dict[str, Any] = {}
        all_videos = project.data["inputs"].get("videos", [])
        videos = [record for record in all_videos if record_is_usable_camera_video(record)]
        threshold = sync_confidence_threshold(project)
        total = max(1, len(videos))

        for index, record in enumerate(videos, start=1):
            clip_id = clip_id_for_record(record)
            filename = Path(record["path"]).name
            percent = int(((index - 1) / total) * 90) + 5
            progress_callback(percent, t("syncing_clip", index=index, total=len(videos), filename=filename))
            try:
                result = sync_clip(project, record, master_env, threshold)
                result = preserve_manual_override(old_map, clip_id, record, result)
                result = apply_project_manual_override(project, old_map, clip_id, record, result)
                result["clip_id"] = clip_id
            except Exception as exc:
                result = error_clip_entry(record, str(exc))
                result["clip_id"] = clip_id
            clips[clip_id] = result

        progress_callback(96, t("writing_sync_map"))
        write_artifact_json(
            sync_map_path(project),
            {
                "schema_version": 1,
                "sample_rate": SYNC_SAMPLE_RATE,
                "hop_length": SYNC_HOP_LENGTH,
                "confidence_formula": "confidence=(peak-median(correlation))/(1.4826*MAD(correlation)+1e-9)",
                "confidence_threshold": threshold,
                "master_duration_sec": master_duration,
                "songs": load_song_boundaries(project),
                # Keep overrides in the project artifact.  They are deliberately
                # keyed by the current master and source signature, rather than
                # being a global camera setting.
                "manual_overrides": (old_map or {}).get("manual_overrides", {}),
                "clips": clips,
            },
        )
        progress_callback(100, t("sync_complete"))
        return self.outputs(project)


def sync_map_path(project: Project) -> Path:
    """Return the sync map path."""
    return artifact_path(project, "sync_map.json")


def sync_confidence_threshold(project: Project) -> float:
    """Return the configured robust-z confidence threshold."""
    value = project.data["settings"].get("sync", {}).get("confidence_threshold", 6.0)
    return float(value)


def clip_id_for_record(record: dict[str, Any]) -> str:
    """Return a stable clip id from the registered absolute path."""
    return stable_fingerprint({"path": record["path"]})[:16]


def cache_key(record: dict[str, Any]) -> str:
    """Return the cache key for a clip."""
    return source_cache_key(record)


def media_duration(path: str) -> float:
    """Return media duration in seconds using ffprobe metadata."""
    metadata = ffprobe(path)
    value = metadata.get("format", {}).get("duration")
    if value is None:
        for stream in metadata.get("streams", []):
            if stream.get("duration") is not None:
                value = stream["duration"]
                break
    return float(value or 0.0)


def has_audio_stream(path: str) -> bool:
    """Return True when ffprobe finds at least one audio stream."""
    metadata = ffprobe(path)
    return any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", []))


def normalized_onset_envelope(path: str) -> np.ndarray:
    """Load audio and return a zero-mean, unit-std onset envelope."""
    import librosa

    y, _ = librosa.load(path, sr=SYNC_SAMPLE_RATE, mono=True)
    y = preprocess_sync_audio(y, SYNC_SAMPLE_RATE)
    envelope = librosa.onset.onset_strength(y=y, sr=SYNC_SAMPLE_RATE, hop_length=SYNC_HOP_LENGTH)
    return normalize_envelope(envelope)


def normalized_spectral_flux_envelope(path: str) -> np.ndarray:
    """Load audio and return a normalized spectral-flux envelope for weak camera audio."""
    import librosa

    y, _ = librosa.load(path, sr=SYNC_SAMPLE_RATE, mono=True)
    y = preprocess_sync_audio(y, SYNC_SAMPLE_RATE)
    spectrogram = np.abs(librosa.stft(y, n_fft=2048, hop_length=SYNC_HOP_LENGTH))
    if spectrogram.shape[1] < 2:
        return np.zeros(0, dtype=np.float32)
    flux = np.maximum(0.0, np.diff(spectrogram, axis=1)).sum(axis=0)
    return normalize_envelope(flux)


def preprocess_sync_audio(y: np.ndarray, sr: int) -> np.ndarray:
    """Band-limit and compress camera audio before sync envelope extraction."""
    samples = np.asarray(y, dtype=np.float32)
    if samples.size == 0:
        return samples
    high = min(8000.0, sr / 2 - 100.0)
    if high > 100.0:
        try:
            sos = butter(4, [100.0, high], btype="bandpass", fs=sr, output="sos")
            samples = sosfiltfilt(sos, samples).astype(np.float32)
        except ValueError:
            pass
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1e-6:
        samples = samples / peak
    samples = np.sign(samples) * np.sqrt(np.abs(samples))
    return samples.astype(np.float32)


def normalize_envelope(envelope: Any) -> np.ndarray:
    """Return a zero-mean, unit-std float envelope."""
    values = np.asarray(envelope, dtype=np.float32)
    if values.size == 0:
        return values
    std = float(np.std(values))
    if std < 1e-9:
        return np.zeros_like(values, dtype=np.float32)
    return ((values - float(np.mean(values))) / std).astype(np.float32)


def load_or_compute_master_envelope(project: Project) -> np.ndarray:
    """Load cached master envelope or compute it from the registered master."""
    master_path = project.data["inputs"]["master"]["path"]
    cache_path = project.cache_dir / "envelopes" / "master_onset_v3.npy"
    if cache_path.exists() and cache_path.stat().st_mtime >= Path(master_path).stat().st_mtime:
        return np.load(cache_path)
    envelope = normalized_onset_envelope(master_path)
    atomic_save_npy(cache_path, envelope)
    return envelope


def load_or_compute_master_spectral_envelope(project: Project) -> np.ndarray:
    """Load cached master spectral-flux envelope or compute it."""
    master_path = project.data["inputs"]["master"]["path"]
    cache_path = project.cache_dir / "envelopes" / "master_spectral_v3.npy"
    if cache_path.exists() and cache_path.stat().st_mtime >= Path(master_path).stat().st_mtime:
        return np.load(cache_path)
    envelope = normalized_spectral_flux_envelope(master_path)
    atomic_save_npy(cache_path, envelope)
    return envelope


def load_or_compute_clip_envelope(project: Project, record: dict[str, Any]) -> tuple[np.ndarray | None, str | None]:
    """Extract clip audio if needed, then load or compute its onset envelope."""
    video_path = record_media_path(record)
    if not has_audio_stream(video_path):
        return None, None
    audio_path = clip_audio_path(project, record)
    if not audio_path.exists() or audio_path.stat().st_mtime < Path(video_path).stat().st_mtime:
        extract_clip_audio(video_path, audio_path)
    envelope_path = clip_envelope_path(project, record)
    if envelope_path.exists() and envelope_path.stat().st_mtime >= audio_path.stat().st_mtime:
        return np.load(envelope_path), str(audio_path)
    envelope = normalized_onset_envelope(str(audio_path))
    atomic_save_npy(envelope_path, envelope)
    return envelope, str(audio_path)


def load_or_compute_clip_spectral_envelope(project: Project, record: dict[str, Any]) -> tuple[np.ndarray | None, str | None]:
    """Extract clip audio if needed, then load or compute its spectral-flux envelope."""
    video_path = record_media_path(record)
    if not has_audio_stream(video_path):
        return None, None
    audio_path = clip_audio_path(project, record)
    if not audio_path.exists() or audio_path.stat().st_mtime < Path(video_path).stat().st_mtime:
        extract_clip_audio(video_path, audio_path)
    envelope_path = global_clip_envelope_path(cache_key(record)).with_name(f"{cache_key(record)}.spectral.npy")
    envelope_path.parent.mkdir(parents=True, exist_ok=True)
    if envelope_path.exists() and envelope_path.stat().st_mtime >= audio_path.stat().st_mtime:
        return np.load(envelope_path), str(audio_path)
    envelope = normalized_spectral_flux_envelope(str(audio_path))
    atomic_save_npy(envelope_path, envelope)
    return envelope, str(audio_path)


def clip_audio_path(project: Project, record: dict[str, Any]) -> Path:
    """Return the cached extracted clip-audio path."""
    path = global_clip_audio_path(cache_key(record))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clip_envelope_path(project: Project, record: dict[str, Any]) -> Path:
    """Return the cached clip envelope path."""
    key = cache_key(record)
    path = global_clip_envelope_path(key).with_name(f"{key}.onset-v3.npy")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def extract_clip_audio(video_path: str, audio_path: Path) -> None:
    """Extract mono 22050 Hz WAV audio from a clip."""
    tmp_path = audio_path.with_suffix(".tmp.wav")
    command = [
        ffmpeg_path(),
        "-y",
        "-i",
        video_path,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SYNC_SAMPLE_RATE),
        str(tmp_path),
    ]
    run_ffmpeg(command)
    os.replace(tmp_path, audio_path)


def atomic_save_npy(path: Path, array: np.ndarray) -> None:
    """Atomically save a numpy array."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("wb") as fh:
        np.save(fh, array)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp_path, path)


def sync_clip(project: Project, record: dict[str, Any], master_env: np.ndarray, threshold: float) -> dict[str, Any]:
    """Synchronize one clip record against the master envelope."""
    media_path = record_media_path(record)
    duration = media_duration(media_path)
    clip_env, audio_path = load_or_compute_clip_envelope(project, record)
    base = {
        "path": media_path,
        "source_path": record["path"],
        "filename": Path(record["path"]).name,
        "duration_sec": duration,
        "source_signature": file_signature(record["path"]),
        "manual_override": False,
        "projection": record.get("projection") or (record.get("probe") or {}).get("projection"),
        "raw_360": bool(record.get("raw_360") or (record.get("probe") or {}).get("raw_360")),
        "info": record.get("info"),
    }
    if clip_env is None:
        return {**base, "offset_sec": 0.0, "confidence": 0.0, "low_confidence": True, "no_audio": True}
    if master_env.size == 0 or clip_env.size == 0:
        return {**base, "offset_sec": 0.0, "confidence": 0.0, "low_confidence": True, "error": "Empty onset envelope"}

    offset_sec, confidence = recover_offset(master_env, clip_env)
    sync_method = "onset"
    if confidence < threshold:
        spectral_master = load_or_compute_master_spectral_envelope(project)
        spectral_clip, _ = load_or_compute_clip_spectral_envelope(project, record)
        if spectral_clip is not None and spectral_master.size and spectral_clip.size:
            fallback_offset, fallback_confidence = recover_offset(spectral_master, spectral_clip)
            if fallback_confidence > confidence:
                offset_sec = fallback_offset
                confidence = fallback_confidence
                clip_env = spectral_clip
                master_env = spectral_master
                sync_method = "spectral_flux"
    verification = verify_sync_stability(master_env, clip_env, offset_sec)
    unstable = bool(verification.get("unstable_sync"))
    return {
        **base,
        "audio_cache": audio_path,
        "offset_sec": offset_sec,
        "duration_sec": duration,
        "confidence": confidence,
        "sync_method": sync_method,
        "low_confidence": confidence < threshold,
        "verification": verification,
        "unstable_sync": unstable,
    }


def recover_offset(master_env: np.ndarray, clip_env: np.ndarray) -> tuple[float, float]:
    """Recover clip offset in seconds by cross-correlating onset envelopes."""
    if clip_env.size <= master_env.size:
        curve = correlate(master_env, clip_env, mode="valid")
        peak_index = int(np.argmax(curve))
        offset_frames = peak_index
    else:
        curve = correlate(master_env, clip_env, mode="full")
        lags = np.arange(-clip_env.size + 1, master_env.size)
        peak_index = int(np.argmax(curve))
        offset_frames = max(0, int(lags[peak_index]))
    offset_sec = offset_frames * SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
    return float(offset_sec), confidence_from_correlation(curve)


def verify_sync_stability(master_env: np.ndarray, clip_env: np.ndarray, full_offset_sec: float) -> dict[str, Any]:
    """Verify sync by correlating first and last thirds independently."""
    thirds = split_clip_verification_segments(clip_env)
    if len(thirds) < 2:
        return {"checked": False, "reason": "clip too short for second-pass sync verification"}
    offsets: list[dict[str, float]] = []
    for name, start_frame, segment in thirds:
        offset_sec, confidence = recover_offset(master_env, segment)
        adjusted = offset_sec - start_frame * SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
        offsets.append({"segment": name, "offset_sec": adjusted, "confidence": confidence})
    delta = abs(offsets[0]["offset_sec"] - offsets[-1]["offset_sec"])
    return {
        "checked": True,
        "full_offset_sec": full_offset_sec,
        "first_offset_sec": offsets[0]["offset_sec"],
        "last_offset_sec": offsets[-1]["offset_sec"],
        "delta_sec": delta,
        "tolerance_sec": 0.150,
        "unstable_sync": delta > 0.150,
        "segments": offsets,
    }


def split_clip_verification_segments(clip_env: np.ndarray) -> list[tuple[str, int, np.ndarray]]:
    """Return first/last thirds large enough for independent correlation."""
    total_sec = clip_env.size * SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
    if total_sec < 10.0:
        return []
    third = clip_env.size // 3
    if third < 8:
        return []
    return [
        ("first_third", 0, clip_env[:third]),
        ("last_third", third * 2, clip_env[third * 2 :]),
    ]


def confidence_from_correlation(curve: np.ndarray) -> float:
    """Return robust peak confidence: (peak - median) / (1.4826 * MAD + 1e-9)."""
    values = np.asarray(curve, dtype=np.float64)
    if values.size == 0:
        return 0.0
    peak = float(np.max(values))
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return float((peak - median) / (1.4826 * mad + 1e-9))


def preserve_manual_override(
    old_map: dict[str, Any] | None, clip_id: str, record: dict[str, Any], detected: dict[str, Any]
) -> dict[str, Any]:
    """Preserve a manual offset when the clip file signature has not changed."""
    old_clip = (old_map or {}).get("clips", {}).get(clip_id)
    if not old_clip or not old_clip.get("manual_override"):
        return detected
    if old_clip.get("source_signature") != file_signature(record["path"]):
        return detected
    preserved = dict(detected)
    preserved["detected_offset_sec"] = old_clip.get("detected_offset_sec", detected.get("offset_sec", 0.0))
    preserved["offset_sec"] = float(old_clip.get("offset_sec", 0.0))
    preserved["manual_override"] = True
    preserved["low_confidence"] = False
    preserved["unstable_sync"] = False
    preserved["manual_override_rescues_sync"] = True
    return preserved


def _master_override_key(project: Project) -> str:
    """Return the project-local key for the currently selected master."""
    master = project.data["inputs"].get("master") or {}
    path = str(master.get("path") or "")
    signature = safe_file_signature(path)
    return stable_fingerprint(signature or {"path": str(Path(path).resolve())})[:24]


SYNC_OVERRIDE_CACHE_VERSION = 1
SYNC_OVERRIDE_SAMPLE_BYTES = 1024 * 1024


def _content_identity(path: str | None) -> dict[str, Any] | None:
    """Return a copy-stable identity without hashing an entire multi-GB video."""
    if not path:
        return None
    source = Path(path).expanduser().resolve()
    try:
        size = source.stat().st_size
        digest = hashlib.sha256()
        with source.open("rb") as fh:
            digest.update(fh.read(SYNC_OVERRIDE_SAMPLE_BYTES))
            if size > SYNC_OVERRIDE_SAMPLE_BYTES * 2:
                fh.seek(max(0, size - SYNC_OVERRIDE_SAMPLE_BYTES))
                digest.update(fh.read(SYNC_OVERRIDE_SAMPLE_BYTES))
        return {"size": size, "sample_sha256": digest.hexdigest()}
    except OSError:
        return None


def _source_override_key(master_path: str | None, source_path: str | None) -> str | None:
    master_identity = _content_identity(master_path)
    source_identity = _content_identity(source_path)
    if not master_identity or not source_identity:
        return None
    return stable_fingerprint({"master": master_identity, "source": source_identity})[:32]


def _sync_override_cache_path() -> Path:
    return global_cache_root() / "sync_overrides.json"


def _load_global_overrides() -> dict[str, Any]:
    try:
        with _sync_override_cache_path().open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {"overrides": {}}
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SYNC_OVERRIDE_CACHE_VERSION, "overrides": {}}


def _save_global_overrides(payload: dict[str, Any]) -> None:
    payload["schema_version"] = SYNC_OVERRIDE_CACHE_VERSION
    atomic_write_json(_sync_override_cache_path(), payload)


def _master_path(project: Project) -> str | None:
    return str((project.data["inputs"].get("master") or {}).get("path") or "") or None


def _global_override_for(project: Project, source_path: str | None) -> dict[str, Any] | None:
    key = _source_override_key(_master_path(project), source_path)
    if not key:
        return None
    return _load_global_overrides().get("overrides", {}).get(key)


def _store_global_override(project: Project, source_path: str, offset_sec: float) -> None:
    key = _source_override_key(_master_path(project), source_path)
    if not key:
        return
    payload = _load_global_overrides()
    payload.setdefault("overrides", {})[key] = {
        "master_path": _master_path(project),
        "master_identity": _content_identity(_master_path(project)),
        "source_path": source_path,
        "source_filename": Path(source_path).name,
        "source_identity": _content_identity(source_path),
        "offset_sec": float(offset_sec),
    }
    _save_global_overrides(payload)


def migrate_legacy_manual_overrides(project: Project, sync_map: dict[str, Any] | None) -> None:
    """Promote pre-global project overrides so old projects keep working."""
    for clip in (sync_map or {}).get("clips", {}).values():
        if not clip.get("manual_override"):
            continue
        source_path = clip.get("source_path") or clip.get("path")
        if source_path and clip.get("offset_sec") is not None:
            _store_global_override(project, str(source_path), float(clip["offset_sec"]))


def _apply_override_result(detected: dict[str, Any], offset_sec: float) -> dict[str, Any]:
    """Apply a user-confirmed offset without changing correlation or thresholds."""
    preserved = dict(detected)
    preserved["detected_offset_sec"] = detected.get("detected_offset_sec", detected.get("offset_sec", 0.0))
    preserved["offset_sec"] = float(offset_sec)
    preserved["manual_override"] = True
    preserved["low_confidence"] = False
    preserved["unstable_sync"] = False
    preserved["manual_override_rescues_sync"] = True
    return preserved


def apply_project_manual_override(
    project: Project,
    old_map: dict[str, Any] | None,
    clip_id: str,
    record: dict[str, Any],
    detected: dict[str, Any],
) -> dict[str, Any]:
    """Restore an override by master/source identity, across project folders."""
    global_override = _global_override_for(project, record.get("source_path") or record.get("path"))
    if isinstance(global_override, dict):
        try:
            return _apply_override_result(detected, float(global_override["offset_sec"]))
        except (KeyError, TypeError, ValueError):
            pass
    # Compatibility with the previous project-local registry.
    registry = (old_map or {}).get("manual_overrides", {})
    master_entry = registry.get(_master_override_key(project), {})
    override = (master_entry.get("clips", {}) if isinstance(master_entry, dict) else {}).get(clip_id)
    if not isinstance(override, dict):
        return detected
    if override.get("source_signature") != safe_file_signature(record["path"]):
        return detected
    try:
        return _apply_override_result(detected, float(override["offset_sec"]))
    except (KeyError, TypeError, ValueError):
        return detected


def error_clip_entry(record: dict[str, Any], message: str) -> dict[str, Any]:
    """Return a non-fatal sync error entry for one clip."""
    return {
        "path": record["path"],
        "source_path": record["path"],
        "filename": Path(record["path"]).name,
        "duration_sec": 0.0,
        "offset_sec": 0.0,
        "confidence": 0.0,
        "low_confidence": True,
        "manual_override": False,
        "source_signature": safe_file_signature(record["path"]),
        "error": message,
    }


def safe_file_signature(path: str) -> dict[str, Any] | None:
    """Return a file signature or None if the file cannot be statted."""
    try:
        return file_signature(path)
    except OSError:
        return None


def load_sync_map(project: Project, missing_ok: bool = False) -> dict[str, Any] | None:
    """Load sync_map.json."""
    path = sync_map_path(project)
    if missing_ok and not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def save_sync_map(project: Project, payload: dict[str, Any]) -> None:
    """Atomically save sync_map.json."""
    write_artifact_json(sync_map_path(project), payload)


def load_song_boundaries(project: Project) -> list[dict[str, Any]]:
    """Load simple song boundary records from songs.json when present."""
    songs_record = project.data["inputs"].get("songs")
    if not songs_record:
        return []
    try:
        with Path(songs_record["path"]).open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    source = payload.get("songs", payload) if isinstance(payload, dict) else payload
    if not isinstance(source, list):
        return []
    songs: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        if not isinstance(item, dict):
            continue
        start = _first_float(item, ("start_sec", "start_seconds", "start", "start_time"))
        end = _first_float(item, ("end_sec", "end_seconds", "end", "end_time"))
        duration = _first_float(item, ("duration_sec", "duration_seconds", "duration"))
        if start is None:
            continue
        if end is None and duration is not None:
            end = start + duration
        songs.append(
            {
                "title": str(item.get("title") or item.get("name") or f"Song {index + 1}"),
                "start_sec": start,
                "end_sec": end,
            }
        )
    return songs


def _first_float(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in payload and payload[key] is not None:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                return None
    return None


def set_manual_override(project: Project, clip_id: str, offset_sec: float) -> dict[str, Any]:
    """Set a manual sync offset and mark downstream stages stale."""
    sync_map = load_sync_map(project)
    clips = sync_map.get("clips", {})
    if clip_id not in clips:
        raise KeyError(f"Unknown clip_id: {clip_id}")
    clip = clips[clip_id]
    if not clip.get("manual_override"):
        clip["detected_offset_sec"] = clip.get("offset_sec", 0.0)
    clip["offset_sec"] = float(offset_sec)
    clip["manual_override"] = True
    _store_global_override(project, str(clip.get("source_path") or clip.get("path") or ""), float(offset_sec))
    master_key = _master_override_key(project)
    overrides = sync_map.setdefault("manual_overrides", {})
    master_entry = overrides.setdefault(
        master_key,
        {"master_signature": safe_file_signature((project.data["inputs"].get("master") or {}).get("path", "")), "clips": {}},
    )
    master_entry.setdefault("clips", {})[clip_id] = {
        "source_path": clip.get("source_path") or clip.get("path"),
        "source_signature": safe_file_signature(clip.get("source_path") or clip.get("path")),
        "offset_sec": float(offset_sec),
    }
    save_sync_map(project, sync_map)
    mark_downstream_stale(project)
    project.save()
    return clip


def clear_manual_override(project: Project, clip_id: str) -> dict[str, Any]:
    """Clear a manual sync offset and restore the detected offset."""
    sync_map = load_sync_map(project)
    clips = sync_map.get("clips", {})
    if clip_id not in clips:
        raise KeyError(f"Unknown clip_id: {clip_id}")
    clip = clips[clip_id]
    if "detected_offset_sec" in clip:
        clip["offset_sec"] = float(clip["detected_offset_sec"])
        del clip["detected_offset_sec"]
    clip["manual_override"] = False
    source_path = str(clip.get("source_path") or clip.get("path") or "")
    key = _source_override_key(_master_path(project), source_path)
    if key:
        global_overrides = _load_global_overrides()
        if global_overrides.get("overrides", {}).pop(key, None) is not None:
            _save_global_overrides(global_overrides)
    overrides = sync_map.get("manual_overrides", {})
    master_entry = overrides.get(_master_override_key(project), {})
    if isinstance(master_entry, dict):
        master_entry.get("clips", {}).pop(clip_id, None)
        if not master_entry.get("clips"):
            overrides.pop(_master_override_key(project), None)
    save_sync_map(project, sync_map)
    mark_downstream_stale(project)
    project.save()
    return clip


def mark_downstream_stale(project: Project) -> None:
    """Mark stages downstream of sync stale without changing sync status."""
    for name in ("cut", "edit", "export"):
        state = project.data["stages"][name]
        if state["status"] in {"done", "failed", "blocked"}:
            state["status"] = "stale"
            state["error"] = None


def generate_preview(project: Project, clip_id: str) -> Path:
    """Generate or return a cached 10-second verification preview."""
    sync_map = load_sync_map(project)
    clip = sync_map.get("clips", {}).get(clip_id)
    if not clip:
        raise KeyError(f"Unknown clip_id: {clip_id}")
    clip_path = clip["path"]
    signature = file_signature(clip_path)
    preview_path = project.cache_dir / "previews" / f"{clip_id}-{signature['size']}-{int(signature['mtime'])}.mp4"
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    if preview_path.exists():
        return preview_path

    clip_duration = float(clip.get("duration_sec") or media_duration(clip_path))
    clip_start = clip_duration / 2.0 if clip_duration < 40.0 else 30.0
    master_start = max(0.0, float(clip.get("offset_sec", 0.0)) + clip_start)
    tmp_path = preview_path.with_suffix(".tmp.mp4")
    command = [
        ffmpeg_path(),
        "-y",
        "-ss",
        f"{clip_start:.3f}",
        "-t",
        f"{PREVIEW_SECONDS:.3f}",
        "-i",
        clip_path,
        "-ss",
        f"{master_start:.3f}",
        "-t",
        f"{PREVIEW_SECONDS:.3f}",
        "-i",
        project.data["inputs"]["master"]["path"],
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]
    run_ffmpeg(command)
    os.replace(tmp_path, preview_path)
    return preview_path


def generate_thumbnail(project: Project, clip_id: str) -> Path:
    """Generate or return a cached midpoint thumbnail for a clip."""
    record = record_for_clip_id(project, clip_id)
    media_path = record_media_path(record)
    signature = file_signature(media_path)
    thumb_path = global_thumbnail_path(cache_key(record), signature)
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    if thumb_path.exists():
        return thumb_path
    duration = media_duration(media_path)
    midpoint = max(0.0, duration / 2.0)
    tmp_path = thumb_path.with_suffix(".tmp.jpg")
    command = [
        ffmpeg_path(),
        "-y",
        "-ss",
        f"{midpoint:.3f}",
        "-i",
        media_path,
        "-frames:v",
        "1",
        "-q:v",
        "4",
        str(tmp_path),
    ]
    run_ffmpeg(command)
    os.replace(tmp_path, thumb_path)
    return thumb_path


def record_for_clip_id(project: Project, clip_id: str) -> dict[str, Any]:
    """Return the registered video record for a clip id."""
    for record in project.data["inputs"].get("videos", []):
        if clip_id_for_record(record) == clip_id:
            return record
    raise KeyError(f"Unknown clip_id: {clip_id}")


def run_ffmpeg(command: list[str]) -> None:
    """Run ffmpeg and raise a compact error on failure."""
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")


def ffmpeg_path() -> str:
    """Return the configured ffmpeg executable path."""
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    return str(ffmpeg)


def format_seconds(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    total = max(0, int(math.floor(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
