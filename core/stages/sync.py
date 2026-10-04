"""Audio-based clip synchronization stage."""

from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from core.storage import data_root, project_locations
from typing import Any

import numpy as np
from scipy.signal import butter, correlate, sosfiltfilt

from core.ffmpeg import FFmpegError, ffprobe, tool_status
from core.media_validation import record_is_usable_camera_video, record_media_path
from core.messages import t
from core.normalization import global_cache_root, global_clip_audio_path, global_clip_envelope_path, global_thumbnail_path, source_cache_key
from core.project import Project, atomic_write_json, load_project
from core.spherical_view import spherical_view_filter
from core.stages.base import ProgressCallback, Stage, artifact_path, file_signature, stable_fingerprint, write_artifact_json

SYNC_SAMPLE_RATE = 22050
SYNC_HOP_LENGTH = 512
PREVIEW_SECONDS = 10.0
HPSS_SYNC_VERSION = 1
SYNC_ALGORITHM_VERSION = 5


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
                "algorithm": {"sr": SYNC_SAMPLE_RATE, "hop": SYNC_HOP_LENGTH, "version": SYNC_ALGORITHM_VERSION, "hpss": HPSS_SYNC_VERSION, "verify_tolerance_sec": 0.150},
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
        progress_callback(0, "Sync: analysing the master audio waveform")
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
                "sync_algorithm_version": SYNC_ALGORITHM_VERSION,
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


def sync_map_is_current(project: Project) -> bool:
    """Return whether the persisted sync artifact was made by this algorithm."""
    payload = load_sync_map(project, missing_ok=True)
    return bool(payload and payload.get("sync_algorithm_version") == SYNC_ALGORITHM_VERSION)


def invalidate_stale_sync_artifact(project: Project) -> bool:
    """Invalidate old sync output so callers cannot silently reuse it."""
    if sync_map_is_current(project):
        return False
    state = project.data.get("stages", {}).get("sync") or {}
    if state.get("status") in {"done", "failed", "blocked"}:
        project.mark_all_stale_from("sync")
        project.save()
        return True
    return False


def sync_diagnostics_root() -> Path:
    return data_root() / "SyncDiagnostics"


def _diagnostic_slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return slug or fallback


def sync_diagnostics_dir(project: Project, source_filename: str) -> Path:
    project_name = str(project.data.get("name") or project.folder.stem)
    camera_name = Path(source_filename).stem
    return sync_diagnostics_root() / _diagnostic_slug(project_name, "project") / _diagnostic_slug(camera_name, "camera")


def clear_sync_diagnostics(project: Project, clip_id: str | None = None, source_filename: str | None = None) -> None:
    """Remove listened/discarded sync diagnostics after a confirmed override."""
    if source_filename is None and clip_id:
        sync_map = load_sync_map(project, missing_ok=True) or {}
        source_filename = ((sync_map.get("clips", {}).get(clip_id) or {}).get("filename"))
    targets: list[Path] = []
    if source_filename:
        targets.append(sync_diagnostics_dir(project, str(source_filename)))
    # Compatibility with diagnostics made before centralisation.
    legacy_root = project.cache_dir / "sync_diagnostics"
    if legacy_root.exists():
        targets.extend(path for path in legacy_root.iterdir() if path.is_dir())
    for target in targets:
        if target.exists() and target.is_dir():
            shutil.rmtree(target)


def cleanup_closed_sync_diagnostics() -> int:
    """Delete central diagnostics for projects whose export is complete."""
    removed = 0
    for project_folder in {p for root in project_locations() for p in root.glob("*.zuckervid")}:
        try:
            project = load_project(str(project_folder))
        except Exception:
            continue
        if (project.data.get("stages", {}).get("export", {}).get("status") != "done"):
            continue
        project_name = _diagnostic_slug(str(project.data.get("name") or project.folder.stem), "project")
        target = sync_diagnostics_root() / project_name
        if target.exists():
            shutil.rmtree(target)
            removed += 1
    return removed


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


def coarse_audio_envelope(path: str, *, duration_sec: float | None = 180.0) -> np.ndarray:
    """Cheap envelope for the Inbox master/video mismatch gate.

    Video clips stay capped at the first 180 seconds for speed, but the
    selected master must be searchable across its full duration.  Otherwise a
    clip that starts several minutes into a song is compared with the wrong
    part of the master and short unrelated clips can win by matching its
    opening bars.
    """
    import librosa

    y, sr = librosa.load(path, sr=4000, mono=True, duration=duration_sec)
    if y.size < 32:
        return np.zeros(0, dtype=np.float32)
    y = preprocess_sync_audio(y, sr)
    envelope = librosa.onset.onset_strength(y=y, sr=sr, hop_length=256)
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


def normalized_percussive_envelope(path: str) -> np.ndarray:
    """Return an onset envelope from the percussive HPSS component."""
    import librosa

    y, _ = librosa.load(path, sr=SYNC_SAMPLE_RATE, mono=True)
    y = preprocess_sync_audio(y, SYNC_SAMPLE_RATE)
    if y.size < 32:
        return np.zeros(0, dtype=np.float32)
    _, percussive = librosa.effects.hpss(y, margin=1.0)
    envelope = librosa.onset.onset_strength(y=percussive, sr=SYNC_SAMPLE_RATE, hop_length=SYNC_HOP_LENGTH)
    return normalize_envelope(envelope)


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


def load_or_compute_master_percussive_envelope(project: Project) -> np.ndarray:
    master_path = project.data["inputs"]["master"]["path"]
    cache_path = project.cache_dir / "envelopes" / f"master_percussive_v{HPSS_SYNC_VERSION}.npy"
    if cache_path.exists() and cache_path.stat().st_mtime >= Path(master_path).stat().st_mtime:
        return np.load(cache_path)
    envelope = normalized_percussive_envelope(master_path)
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


def load_or_compute_clip_percussive_envelope(project: Project, record: dict[str, Any]) -> tuple[np.ndarray | None, str | None]:
    video_path = record_media_path(record)
    if not has_audio_stream(video_path):
        return None, None
    audio_path = clip_audio_path(project, record)
    if not audio_path.exists() or audio_path.stat().st_mtime < Path(video_path).stat().st_mtime:
        extract_clip_audio(video_path, audio_path)
    envelope_path = global_clip_envelope_path(cache_key(record)).with_name(
        f"{cache_key(record)}.percussive-v{HPSS_SYNC_VERSION}.npy"
    )
    envelope_path.parent.mkdir(parents=True, exist_ok=True)
    if envelope_path.exists() and envelope_path.stat().st_mtime >= audio_path.stat().st_mtime:
        return np.load(envelope_path), str(audio_path)
    envelope = normalized_percussive_envelope(str(audio_path))
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
    alternatives: list[dict[str, Any]] = [{"method": sync_method, "offset_sec": offset_sec, "confidence": confidence}]
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
            alternatives.append({"method": "spectral_flux", "offset_sec": fallback_offset, "confidence": fallback_confidence})
    if confidence < threshold:
        percussive_master = load_or_compute_master_percussive_envelope(project)
        percussive_clip, _ = load_or_compute_clip_percussive_envelope(project, record)
        if percussive_clip is not None and percussive_master.size and percussive_clip.size:
            percussive_offset, percussive_confidence = recover_offset(percussive_master, percussive_clip)
            alternatives.append({"method": "hpss_percussive", "offset_sec": percussive_offset, "confidence": percussive_confidence})
            if percussive_confidence > confidence:
                offset_sec = percussive_offset
                confidence = percussive_confidence
                clip_env = percussive_clip
                master_env = percussive_master
                sync_method = "hpss_percussive"
    verification = verify_sync_stability(master_env, clip_env, offset_sec)
    unstable = bool(verification.get("unstable_sync"))
    return {
        **base,
        "audio_cache": audio_path,
        "offset_sec": offset_sec,
        "duration_sec": duration,
        "confidence": confidence,
        "sync_method": sync_method,
        # A strong global peak is not sufficient when independent thirds
        # disagree. Keep unstable sync visible as questionable sync in the
        # artifact; CutStage also hard-excludes it unless overridden.
        "low_confidence": confidence < threshold or unstable,
        "verification": verification,
        "unstable_sync": unstable,
        "sync_alternatives": alternatives,
    }


def recover_offset(master_env: np.ndarray, clip_env: np.ndarray) -> tuple[float, float]:
    """Recover the timeline offset where ``master = clip + offset``.

    The shorter signal is searched as a window in the longer signal.  This
    matters in both directions: a normal clip can be a subsection of the
    master (positive offset), while a camera recording can contain a trimmed
    master (negative offset).  The old full-correlation branch for the latter
    discarded the negative lag and therefore forced the answer to zero.
    """
    offsets, curve = normalized_overlap_correlation(master_env, clip_env)
    if curve.size == 0:
        return 0.0, 0.0
    peak_index = int(np.argmax(curve))
    frame_sec = SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
    return float(offsets[peak_index] * frame_sec), confidence_from_correlation(curve)


def normalized_overlap_correlation(master_env: np.ndarray, clip_env: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Correlate every physically possible overlap without circular aliases.

    ``offset`` means ``master_time = clip_time + offset``.  A full FFT
    correlation is used only for the dot products; local means and energies
    are computed for each actual overlap, so short edge overlaps cannot win
    merely because they have a larger raw sum. The minimum overlap is a
    validity guard, not an offset search limit.
    """
    master = np.asarray(master_env, dtype=np.float64)
    clip = np.asarray(clip_env, dtype=np.float64)
    if not master.size or not clip.size:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.float64)
    frame_sec = SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
    min_overlap = max(8, min(master.size, clip.size, int(round(10.0 / frame_sec))))
    # scipy's FFT path zero-pads internally to at least N+M-1. Explicitly
    # request the linear length so this remains visibly non-circular if the
    # implementation changes later.
    dot = correlate(clip, master, mode="full", method="fft")
    offsets = (master.size - 1 - np.arange(dot.size, dtype=np.int64))
    clip_sum = np.concatenate(([0.0], np.cumsum(clip)))
    clip_sq = np.concatenate(([0.0], np.cumsum(clip * clip)))
    master_sum = np.concatenate(([0.0], np.cumsum(master)))
    master_sq = np.concatenate(([0.0], np.cumsum(master * master)))
    scores = np.full(dot.size, -np.inf, dtype=np.float64)
    for index, offset in enumerate(offsets):
        clip_start = max(0, -int(offset))
        clip_end = min(clip.size, master.size - int(offset))
        overlap = clip_end - clip_start
        if overlap < min_overlap:
            continue
        master_start = clip_start + int(offset)
        master_end = clip_end + int(offset)
        sx = clip_sum[clip_end] - clip_sum[clip_start]
        sy = master_sum[master_end] - master_sum[master_start]
        sx2 = clip_sq[clip_end] - clip_sq[clip_start]
        sy2 = master_sq[master_end] - master_sq[master_start]
        numerator = dot[index] - (sx * sy / overlap)
        denominator = np.sqrt(max(0.0, sx2 - sx * sx / overlap) * max(0.0, sy2 - sy * sy / overlap))
        if denominator > 1e-12:
            scores[index] = numerator / denominator
    valid = np.isfinite(scores)
    return offsets[valid], scores[valid]


def coarse_master_match(master_env: np.ndarray, clip_env: np.ndarray, factor: int = 4) -> dict[str, Any]:
    """Quickly decide whether two recordings could be the same song.

    This intentionally uses only a decimated onset envelope. It is a gate for
    the Inbox UX, not a sync decision: plausible pairs still go through the
    normal full-resolution sync pipeline.
    """
    if master_env.size < 8 or clip_env.size < 8:
        return {"offset_sec": 0.0, "confidence": 0.0, "reasonable_peak": False}
    step = max(1, int(factor))
    offset_sec, confidence = recover_offset(master_env[::step], clip_env[::step])
    offset_sec *= step
    return {
        "offset_sec": float(offset_sec),
        "confidence": float(confidence),
        "reasonable_peak": bool(confidence >= 2.5),
    }


def verify_sync_stability(master_env: np.ndarray, clip_env: np.ndarray, full_offset_sec: float) -> dict[str, Any]:
    """Verify sync over the real master/clip overlap, split into three parts."""
    thirds = split_overlap_verification_segments(master_env, clip_env, full_offset_sec)
    if len(thirds) < 2:
        return {"checked": False, "reason": "overlap too short for second-pass sync verification"}
    offsets: list[dict[str, float]] = []
    for name, start_frame, segment in thirds:
        offset_sec, confidence = recover_offset(master_env, segment)
        adjusted = offset_sec - start_frame * SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
        offsets.append({"segment": name, "offset_sec": adjusted, "confidence": confidence})
    values = [item["offset_sec"] for item in offsets]
    tolerance = 0.150
    best_group: list[int] = []
    for index, value in enumerate(values):
        group = [candidate for candidate, other in enumerate(values) if abs(other - value) <= tolerance]
        if len(group) > len(best_group):
            best_group = group
    majority = len(best_group) >= 2
    consensus = float(np.median([values[index] for index in best_group])) if majority else float(full_offset_sec)
    suspect_segments = [offsets[index]["segment"] for index in range(len(offsets)) if index not in best_group] if majority else []
    delta = max(values) - min(values)
    return {
        "checked": True,
        "full_offset_sec": full_offset_sec,
        "first_offset_sec": offsets[0]["offset_sec"],
        "last_offset_sec": offsets[-1]["offset_sec"],
        "delta_sec": delta,
        "tolerance_sec": tolerance,
        "consensus_offset_sec": consensus,
        "consensus_count": len(best_group),
        "suspect_segments": suspect_segments,
        "unstable_sync": not majority,
        "segments": offsets,
    }


def split_clip_verification_segments(clip_env: np.ndarray) -> list[tuple[str, int, np.ndarray]]:
    """Backward-compatible helper for callers that only have clip audio."""
    return split_overlap_verification_segments(
        np.empty(0, dtype=np.float32), clip_env, 0.0, clip_only=True
    )


def split_overlap_verification_segments(
    master_env: np.ndarray,
    clip_env: np.ndarray,
    full_offset_sec: float,
    *,
    clip_only: bool = False,
) -> list[tuple[str, int, np.ndarray]]:
    """Return first/middle/last thirds of the actual master/clip overlap.

    ``start_frame`` is in clip time and is used to convert each independently
    recovered master position back to the common timeline offset.
    """
    if clip_only:
        overlap_start = 0
        overlap_end = clip_env.size
    else:
        frame_sec = SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
        master_duration = master_env.size * frame_sec
        clip_duration = clip_env.size * frame_sec
        overlap_start = max(0.0, -float(full_offset_sec))
        overlap_end = min(clip_duration, master_duration - float(full_offset_sec))
        if overlap_end <= overlap_start:
            return []
        overlap_start = int(round(overlap_start / frame_sec))
        overlap_end = int(round(overlap_end / frame_sec))
    total_frames = overlap_end - overlap_start
    total_sec = total_frames * SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE
    if total_sec < 10.0:
        return []
    third = total_frames // 3
    if third < 8:
        return []
    return [
        ("first_third", overlap_start, clip_env[overlap_start : overlap_start + third]),
        ("middle_third", overlap_start + third, clip_env[overlap_start + third : overlap_start + third * 2]),
        ("last_third", overlap_start + third * 2, clip_env[overlap_start + third * 2 : overlap_end]),
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


def content_identity(path: str | None) -> dict[str, Any] | None:
    """Return the copy-stable content identity used by sync overrides."""
    return _content_identity(path)


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


def _store_global_override(
    project: Project,
    source_path: str,
    offset_sec: float | None = None,
    offset_ranges: list[dict[str, float]] | None = None,
) -> None:
    key = _source_override_key(_master_path(project), source_path)
    if not key:
        return
    payload = _load_global_overrides()
    entry: dict[str, Any] = {
        "master_path": _master_path(project),
        "master_identity": _content_identity(_master_path(project)),
        "source_path": source_path,
        "source_filename": Path(source_path).name,
        "source_identity": _content_identity(source_path),
    }
    if offset_ranges:
        entry["offset_ranges"] = [dict(item) for item in offset_ranges]
    elif offset_sec is not None:
        entry["offset_sec"] = float(offset_sec)
    payload.setdefault("overrides", {})[key] = entry
    _save_global_overrides(payload)


def migrate_legacy_manual_overrides(project: Project, sync_map: dict[str, Any] | None) -> None:
    """Promote pre-global project overrides so old projects keep working."""
    for clip in (sync_map or {}).get("clips", {}).values():
        if not clip.get("manual_override"):
            continue
        source_path = clip.get("source_path") or clip.get("path")
        if source_path and clip.get("offset_ranges"):
            _store_global_override(project, str(source_path), offset_ranges=_normalise_offset_ranges(clip.get("offset_ranges"), clip.get("duration_sec")))
        elif source_path and clip.get("offset_sec") is not None:
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


def _apply_override_ranges_result(
    detected: dict[str, Any], offset_ranges: list[dict[str, float]]
) -> dict[str, Any]:
    """Apply only confirmed clip-time ranges; unconfirmed time stays unusable."""
    preserved = dict(detected)
    preserved["detected_offset_sec"] = detected.get("detected_offset_sec", detected.get("offset_sec", 0.0))
    preserved["offset_ranges"] = [dict(item) for item in offset_ranges]
    # Keep the old scalar for diagnostics and legacy consumers.  YouTube's
    # range-aware coverage path deliberately ignores it when ranges exist.
    if offset_ranges:
        preserved["offset_sec"] = float(offset_ranges[0]["offset_sec"])
    preserved["manual_override"] = True
    preserved["manual_override_ranges"] = True
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
            ranges = _normalise_offset_ranges(global_override.get("offset_ranges"), detected.get("duration_sec"))
            if ranges:
                return _apply_override_ranges_result(detected, ranges)
        except (TypeError, ValueError):
            pass
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
        ranges = _normalise_offset_ranges(override.get("offset_ranges"), detected.get("duration_sec"))
        if ranges:
            return _apply_override_ranges_result(detected, ranges)
    except (TypeError, ValueError):
        pass
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


def _normalise_offset_ranges(raw: Any, duration_sec: Any = None) -> list[dict[str, float]]:
    """Validate clip-local offset ranges without changing sync thresholds."""
    if not isinstance(raw, list):
        return []
    duration = _first_float({"value": duration_sec}, ("value",)) if duration_sec is not None else None
    result: list[dict[str, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("clip_start_sec", item.get("start_sec")))
            end = float(item.get("clip_end_sec", item.get("end_sec")))
            offset = float(item["offset_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < 0 or end <= start or not all(map(lambda value: value == value and abs(value) != float("inf"), (start, end, offset))):
            continue
        if duration is not None:
            end = min(end, duration)
            if end <= start:
                continue
        result.append({"clip_start_sec": start, "clip_end_sec": end, "offset_sec": offset})
    return sorted(result, key=lambda item: (item["clip_start_sec"], item["clip_end_sec"]))


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
    clip.pop("offset_ranges", None)
    clip.pop("manual_override_ranges", None)
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
    clear_sync_diagnostics(project, clip_id=clip_id)
    return clip


def set_manual_anchor(
    project: Project,
    clip_id: str,
    master_sec: float,
    clip_sec: float,
) -> dict[str, Any]:
    """Set a scalar override from one confirmed master/clip correspondence."""
    try:
        master_point = float(master_sec)
        clip_point = float(clip_sec)
    except (TypeError, ValueError) as exc:
        raise ValueError("master_sec and clip_sec must be numbers") from exc
    if not all(value == value and abs(value) != float("inf") for value in (master_point, clip_point)):
        raise ValueError("master_sec and clip_sec must be finite")
    clip = set_manual_override(project, clip_id, master_point - clip_point)
    clip["manual_anchor"] = {"master_sec": master_point, "clip_sec": clip_point}
    sync_map = load_sync_map(project)
    sync_map["clips"][clip_id]["manual_anchor"] = dict(clip["manual_anchor"])
    save_sync_map(project, sync_map)
    return clip


def set_manual_override_ranges(
    project: Project,
    clip_id: str,
    offset_ranges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Set confirmed offsets for selected portions of one source clip.

    This is intentionally a separate API from the legacy one-offset override:
    no unconfirmed portion inherits an offset.  YouTube coverage consumes these
    ranges; Reel and 360 retain their existing scalar behavior.
    """
    sync_map = load_sync_map(project)
    clips = sync_map.get("clips", {})
    if clip_id not in clips:
        raise KeyError(f"Unknown clip_id: {clip_id}")
    clip = clips[clip_id]
    ranges = _normalise_offset_ranges(offset_ranges, clip.get("duration_sec"))
    if not ranges:
        raise ValueError("At least one valid clip-time offset range is required")
    if not clip.get("manual_override"):
        clip["detected_offset_sec"] = clip.get("offset_sec", 0.0)
    clip["offset_ranges"] = ranges
    clip["offset_sec"] = float(ranges[0]["offset_sec"])
    clip["manual_override"] = True
    clip["manual_override_ranges"] = True
    source_path = str(clip.get("source_path") or clip.get("path") or "")
    _store_global_override(project, source_path, offset_ranges=ranges)
    master_key = _master_override_key(project)
    overrides = sync_map.setdefault("manual_overrides", {})
    master_entry = overrides.setdefault(
        master_key,
        {"master_signature": safe_file_signature((project.data["inputs"].get("master") or {}).get("path", "")), "clips": {}},
    )
    master_entry.setdefault("clips", {})[clip_id] = {
        "source_path": source_path,
        "source_signature": safe_file_signature(source_path),
        "offset_ranges": ranges,
    }
    save_sync_map(project, sync_map)
    mark_downstream_stale(project)
    project.save()
    clear_sync_diagnostics(project, clip_id=clip_id)
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
    clip.pop("offset_ranges", None)
    clip.pop("manual_override_ranges", None)
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


def _spherical_thumbnail_spec(
    project: Project,
    record: dict[str, Any],
    shot: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Return the original source and effective saved pose for a 360 thumbnail."""
    probe = record.get("probe") or {}
    projection = str(record.get("projection") or probe.get("projection") or "").lower()
    if projection not in {"equirect", "raw_insv"}:
        return None
    landmarks = ((project.data.get("settings") or {}).get("spherical_landmarks") or {})
    requested = dict(shot or {})
    shot_id = str(requested.get("shot_id") or requested.get("type") or "")
    saved = dict(landmarks.get(shot_id) or {}) if shot_id else {}
    if not requested and not saved:
        saved = dict(landmarks.get("full_stage") or {})
        shot_id = "full_stage"
    effective = {**saved, **requested}
    effective.setdefault("type", shot_id)
    effective.setdefault("shot_id", shot_id)
    effective.setdefault("yaw", 0.0)
    effective.setdefault("pitch", 0.0)
    effective.setdefault("fov", 95.0)
    effective["projection"] = projection
    effective["insv_fov"] = float(
        probe.get("insv_fov")
        or ((project.data.get("settings") or {}).get("ingest") or {}).get("insv_fov")
        or 190.0
    )
    return str(record.get("path") or ""), effective


def generate_thumbnail(
    project: Project,
    clip_id: str,
    spherical_shot: dict[str, Any] | None = None,
) -> Path:
    """Generate a cached clip/shot thumbnail without flattening 360 media first."""
    record = record_for_clip_id(project, clip_id)
    spherical = _spherical_thumbnail_spec(project, record, spherical_shot)
    if spherical:
        media_path, pose = spherical
        signature = file_signature(media_path)
        pose_key = stable_fingerprint({
            "projection": pose.get("projection"),
            "yaw": float(pose.get("yaw") or 0.0) % 360.0,
            "pitch": float(pose.get("pitch") or 0.0),
            "fov": float(pose.get("fov") or 95.0),
            "shot_id": pose.get("shot_id") or pose.get("type"),
            "insv_fov": pose.get("insv_fov"),
        })[:16]
        thumb_path = global_thumbnail_path(cache_key(record), signature)
        thumb_path = thumb_path.with_name(f"{thumb_path.stem}-{pose_key}.jpg")
        view_filter = spherical_view_filter(
            str(pose["projection"]),
            float(pose["yaw"]),
            float(pose["pitch"]),
            float(pose["fov"]),
            str(pose.get("type") or ""),
            insv_fov=float(pose.get("insv_fov") or 190.0),
            width=360,
            height=202,
        )
        filter_graph = f"{view_filter},format=yuvj420p"
    else:
        media_path = record_media_path(record)
        signature = file_signature(media_path)
        thumb_path = global_thumbnail_path(cache_key(record), signature)
        filter_graph = None
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
    ]
    if filter_graph:
        command += ["-vf", filter_graph]
    command += ["-q:v", "4", str(tmp_path)]
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
