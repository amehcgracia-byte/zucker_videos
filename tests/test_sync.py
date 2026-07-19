from __future__ import annotations

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
    recover_offset,
)


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
