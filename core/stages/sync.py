"""Audio-based clip synchronization stage."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import correlate

from core.ffmpeg import ffprobe
from core.project import Project
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
                "algorithm": {"sr": SYNC_SAMPLE_RATE, "hop": SYNC_HOP_LENGTH, "version": 1},
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
        master_env = load_or_compute_master_envelope(project)
        master_duration = media_duration(master_record["path"])
        clips: dict[str, Any] = {}
        videos = project.data["inputs"].get("videos", [])
        threshold = sync_confidence_threshold(project)
        total = max(1, len(videos))

        for index, record in enumerate(videos, start=1):
            clip_id = clip_id_for_record(record)
            filename = Path(record["path"]).name
            percent = int(((index - 1) / total) * 90) + 5
            progress_callback(percent, f"Syncing clip {index}/{len(videos)}: {filename}")
            try:
                result = sync_clip(project, record, master_env, threshold)
                result = preserve_manual_override(old_map, clip_id, record, result)
            except Exception as exc:
                result = error_clip_entry(record, str(exc))
            clips[clip_id] = result

        progress_callback(96, "Writing sync map")
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
                "clips": clips,
            },
        )
        progress_callback(100, "Sync complete")
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


def safe_stem(path: str) -> str:
    """Return a filesystem-safe stem for cache files."""
    stem = Path(path).stem.lower()
    return re.sub(r"[^a-z0-9_.-]+", "_", stem).strip("_") or "clip"


def cache_key(record: dict[str, Any]) -> str:
    """Return the cache key for a clip."""
    return f"{safe_stem(record['path'])}-{clip_id_for_record(record)}"


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
    envelope = librosa.onset.onset_strength(y=y, sr=SYNC_SAMPLE_RATE, hop_length=SYNC_HOP_LENGTH)
    envelope = np.asarray(envelope, dtype=np.float32)
    if envelope.size == 0:
        return envelope
    std = float(np.std(envelope))
    if std < 1e-9:
        return np.zeros_like(envelope, dtype=np.float32)
    return ((envelope - float(np.mean(envelope))) / std).astype(np.float32)


def load_or_compute_master_envelope(project: Project) -> np.ndarray:
    """Load cached master envelope or compute it from the registered master."""
    master_path = project.data["inputs"]["master"]["path"]
    cache_path = project.cache_dir / "envelopes" / "master.npy"
    if cache_path.exists() and cache_path.stat().st_mtime >= Path(master_path).stat().st_mtime:
        return np.load(cache_path)
    envelope = normalized_onset_envelope(master_path)
    atomic_save_npy(cache_path, envelope)
    return envelope


def load_or_compute_clip_envelope(project: Project, record: dict[str, Any]) -> tuple[np.ndarray | None, str | None]:
    """Extract clip audio if needed, then load or compute its onset envelope."""
    video_path = record["path"]
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


def clip_audio_path(project: Project, record: dict[str, Any]) -> Path:
    """Return the cached extracted clip-audio path."""
    path = project.cache_dir / "audio" / f"{cache_key(record)}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def clip_envelope_path(project: Project, record: dict[str, Any]) -> Path:
    """Return the cached clip envelope path."""
    path = project.cache_dir / "envelopes" / f"{cache_key(record)}.npy"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def extract_clip_audio(video_path: str, audio_path: Path) -> None:
    """Extract mono 22050 Hz WAV audio from a clip."""
    tmp_path = audio_path.with_suffix(".tmp.wav")
    command = [
        "ffmpeg",
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
    duration = media_duration(record["path"])
    clip_env, audio_path = load_or_compute_clip_envelope(project, record)
    base = {
        "path": record["path"],
        "filename": Path(record["path"]).name,
        "duration_sec": duration,
        "source_signature": file_signature(record["path"]),
        "manual_override": False,
    }
    if clip_env is None:
        return {**base, "offset_sec": 0.0, "confidence": 0.0, "low_confidence": True, "no_audio": True}
    if master_env.size == 0 or clip_env.size == 0:
        return {**base, "offset_sec": 0.0, "confidence": 0.0, "low_confidence": True, "error": "Empty onset envelope"}

    offset_sec, confidence = recover_offset(master_env, clip_env)
    return {
        **base,
        "audio_cache": audio_path,
        "offset_sec": offset_sec,
        "duration_sec": duration,
        "confidence": confidence,
        "low_confidence": confidence < threshold,
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
    preserved["detected_offset_sec"] = detected.get("offset_sec", 0.0)
    preserved["offset_sec"] = float(old_clip.get("offset_sec", 0.0))
    preserved["manual_override"] = True
    return preserved


def error_clip_entry(record: dict[str, Any], message: str) -> dict[str, Any]:
    """Return a non-fatal sync error entry for one clip."""
    return {
        "path": record["path"],
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
        "ffmpeg",
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
    signature = file_signature(record["path"])
    thumb_path = project.cache_dir / "thumbnails" / f"{clip_id}-{signature['size']}-{int(signature['mtime'])}.jpg"
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    if thumb_path.exists():
        return thumb_path
    duration = media_duration(record["path"])
    midpoint = max(0.0, duration / 2.0)
    tmp_path = thumb_path.with_suffix(".tmp.jpg")
    command = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{midpoint:.3f}",
        "-i",
        record["path"],
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


def format_seconds(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    total = max(0, int(math.floor(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
