from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from core.project import create_project
from core.stages.ingest import IngestStage
from core.stages.sync import (
    SYNC_HOP_LENGTH,
    SYNC_SAMPLE_RATE,
    SyncStage,
    confidence_from_correlation,
    extract_clip_audio,
    apply_project_manual_override,
    file_signature,
    recover_offset,
    sync_clip,
    set_manual_override,
    verify_sync_stability,
)


def test_manual_override_is_project_master_and_source_specific(tmp_path):
    project = create_project("Manual", str(tmp_path / "Manual.zuckervid"))
    master = tmp_path / "song.mp3"
    other_master = tmp_path / "other.mp3"
    video = tmp_path / "iphone.mov"
    master.write_bytes(b"master")
    other_master.write_bytes(b"other")
    video.write_bytes(b"video")
    project.data["inputs"]["master"] = {"path": str(master)}
    clip_id = "iphone-clip"
    sync_path = project.artifacts_dir / "sync_map.json"
    sync_path.parent.mkdir(parents=True, exist_ok=True)
    sync_path.write_text(
        json.dumps(
            {
                "clips": {
                    clip_id: {
                        "path": str(video),
                        "source_path": str(video),
                        "source_signature": file_signature(str(video)),
                        "offset_sec": 199.018,
                        "confidence": 5.152,
                        "low_confidence": True,
                        "unstable_sync": True,
                        "manual_override": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    set_manual_override(project, clip_id, 386.775)
    payload = json.loads(sync_path.read_text(encoding="utf-8"))
    assert payload["manual_overrides"]

    restored = apply_project_manual_override(
        project,
        payload,
        clip_id,
        {"path": str(video)},
        {"offset_sec": 199.018, "confidence": 5.152, "low_confidence": True, "unstable_sync": True},
    )
    assert restored["offset_sec"] == pytest.approx(386.775)
    assert restored["manual_override"] is True
    assert restored["low_confidence"] is False
    assert restored["unstable_sync"] is False

    project.data["inputs"]["master"] = {"path": str(other_master)}
    assert apply_project_manual_override(
        project,
        payload,
        clip_id,
        {"path": str(video)},
        {"offset_sec": 199.018, "low_confidence": True, "unstable_sync": True},
    )["low_confidence"] is True


def test_confidence_formula_separates_planted_peak_from_noise():
    rng = np.random.default_rng(42)
    curve = rng.normal(0.0, 1.0, 500)
    noise_confidence = confidence_from_correlation(curve)
    curve[250] = 15.0
    planted_confidence = confidence_from_correlation(curve)

    assert planted_confidence > 8.0
    assert planted_confidence > noise_confidence * 2.0


def test_offset_math_recovers_known_shift_exactly():
    rng = np.random.default_rng(7)
    clip = rng.normal(0.0, 1.0, 18).astype(np.float32)
    master = np.zeros(96, dtype=np.float32)
    shift_frames = 37
    master[shift_frames : shift_frames + clip.size] = clip

    offset_sec, _ = recover_offset(master, clip)

    assert offset_sec == pytest.approx(shift_frames * SYNC_HOP_LENGTH / SYNC_SAMPLE_RATE)


def test_extract_clip_audio_uses_configured_ffmpeg_path(tmp_path, monkeypatch):
    calls = []
    video = tmp_path / "clip.mp4"
    audio = tmp_path / "clip.wav"
    video.write_bytes(b"video")

    monkeypatch.setattr("core.stages.sync.tool_status", lambda: {"ffmpeg_path": "/opt/homebrew/bin/ffmpeg"})

    def fake_run(command):
        calls.append(command)
        audio.with_suffix(".tmp.wav").write_bytes(b"wav")

    monkeypatch.setattr("core.stages.sync.run_ffmpeg", fake_run)

    extract_clip_audio(str(video), audio)

    assert calls[0][0] == "/opt/homebrew/bin/ffmpeg"
    assert audio.exists()


def test_sync_verification_marks_disagreeing_offsets_unstable(monkeypatch):
    import core.stages.sync as sync

    calls = iter([(1.0, 8.0), (1.3, 8.0)])
    monkeypatch.setattr(sync, "recover_offset", lambda master, clip: next(calls))

    result = verify_sync_stability(np.ones(2000), np.ones(900), 1.0)

    assert result["checked"] is True
    assert result["unstable_sync"] is True
    assert result["delta_sec"] > 0.150


def test_sync_clip_uses_spectral_flux_fallback_when_onset_confidence_is_low(tmp_path, monkeypatch):
    import core.stages.sync as sync

    project = create_project("Fallback", str(tmp_path / "Fallback.zuckervid"))
    master = tmp_path / "master.wav"
    clip = tmp_path / "clip.mp4"
    master.write_bytes(b"master")
    clip.write_bytes(b"clip")
    project.data["inputs"]["master"] = {"path": str(master)}
    record = {"path": str(clip), "size": clip.stat().st_size, "mtime": clip.stat().st_mtime}
    onset_master = np.ones(100, dtype=np.float32)
    onset_clip = np.ones(20, dtype=np.float32)
    spectral_master = np.arange(100, dtype=np.float32)
    spectral_clip = np.arange(20, dtype=np.float32)
    calls = []

    monkeypatch.setattr(sync, "media_duration", lambda path: 20.0)
    monkeypatch.setattr(sync, "load_or_compute_clip_envelope", lambda project, record: (onset_clip, "/audio.wav"))
    monkeypatch.setattr(sync, "load_or_compute_master_spectral_envelope", lambda project: spectral_master)
    monkeypatch.setattr(sync, "load_or_compute_clip_spectral_envelope", lambda project, record: (spectral_clip, "/audio.wav"))
    monkeypatch.setattr(sync, "verify_sync_stability", lambda master, clip, offset: {"checked": True, "unstable_sync": False})

    def fake_recover(master, clip_env):
        calls.append((master, clip_env))
        return (1.0, 2.0) if len(calls) == 1 else (3.0, 9.0)

    monkeypatch.setattr(sync, "recover_offset", fake_recover)

    result = sync_clip(project, record, onset_master, threshold=6.0)

    assert result["offset_sec"] == 3.0
    assert result["confidence"] == 9.0
    assert result["sync_method"] == "spectral_flux"


@pytest.mark.slow
def test_sync_stage_with_tiny_generated_media_recovers_known_offset(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")

    project = create_project("Slow", str(tmp_path / "Slow.zuckervid"))
    master = tmp_path / "master.wav"
    songs = tmp_path / "songs.json"
    clip_wav = tmp_path / "clip.wav"
    clip_mp4 = tmp_path / "clip.mp4"
    songs.write_text("[]", encoding="utf-8")

    sample_rate = 22050
    duration = 30.0
    offset = 12.0
    clip_duration = 8.0
    audio = np.zeros(int(duration * sample_rate), dtype=np.float32)
    click_times = [1.0, 2.3, 4.1, 7.4, 9.2, 12.3, 13.7, 15.9, 18.4, 21.8, 26.2]
    for time_sec in click_times:
        index = int(time_sec * sample_rate)
        audio[index : index + 80] += np.hanning(80).astype(np.float32)
    write_wav(master, audio, sample_rate)

    start = int(offset * sample_rate)
    end = start + int(clip_duration * sample_rate)
    write_wav(clip_wav, audio[start:end], sample_rate)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=320x240:rate=15:duration={clip_duration}",
            "-i",
            str(clip_wav),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(clip_mp4),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    project.set_master_and_songs(str(master), str(songs))
    project.set_videos([str(clip_mp4)])
    IngestStage().run(project, lambda percent, message: None)
    SyncStage().run(project, lambda percent, message: None)

    sync_map = (project.artifacts_dir / "sync_map.json").read_text(encoding="utf-8")
    import json

    payload = json.loads(sync_map)
    clip = next(iter(payload["clips"].values()))
    assert clip["offset_sec"] == pytest.approx(offset, abs=0.1)
    assert clip["low_confidence"] is False


def write_wav(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write mono float audio to int16 WAV."""
    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767).astype("<i2")
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(pcm.tobytes())
