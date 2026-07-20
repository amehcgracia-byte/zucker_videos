from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.project import create_project, file_record
from core.stages.base import write_artifact_json
from core.stages.export import (
    MAX_EXPORT_BYTES,
    MIN_ACCEPTABLE_VIDEO_BITRATE,
    ExportStage,
    _bitrate_for_duration,
    _run_ffmpeg_progress,
    _segment_filtergraph,
    color_sample_commands,
    color_correction_for_profile,
    measure_clip_color,
)


class FakeProcess:
    def __init__(self) -> None:
        self.stdout = iter(["out_time_ms=1000000\n", "out_time_ms=2000000\n", "progress=end\n"])
        self.stderr = ""
        self.returncode = 0

    def communicate(self) -> tuple[str, str]:
        return "", ""


def test_export_ffmpeg_progress_reports_percent(monkeypatch):
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr("core.stages.export.subprocess.Popen", lambda *args, **kwargs: FakeProcess())

    _run_ffmpeg_progress(["ffmpeg"], 4.0, "clip.mp4", lambda percent, detail: calls.append((percent, detail)))

    assert calls[0][0] == 25
    assert calls[1][0] == 50
    assert calls[-1] == (100, "clip.mp4 — 100%")


def test_export_bitrate_caps_long_outputs_under_telegram_budget():
    duration = (MAX_EXPORT_BYTES * 8) / (MIN_ACCEPTABLE_VIDEO_BITRATE / 2)

    result = _bitrate_for_duration(duration)

    assert result["video_bitrate"] < MIN_ACCEPTABLE_VIDEO_BITRATE
    assert "1.9 GB" in result["warning"]


def test_color_correction_is_subtle_and_capped():
    correction = color_correction_for_profile({"luma": 20.0, "saturation": 200.0}, target_luma=180.0, target_sat=40.0)

    assert correction["brightness_adjust"] == 0.08
    assert correction["saturation_adjust"] == 0.90


def test_segment_filtergraph_adds_watermark_and_texts():
    graph = _segment_filtergraph(
        "youtube",
        12.0,
        {"title": "My song", "band_name": "The Band", "handle": "@banda"},
        {"brightness_adjust": 0.02, "saturation_adjust": 1.05},
        has_watermark=True,
    )

    assert "drawtext=" in graph
    assert "My song" in graph
    assert "overlay=W-w-40:H-h-40" in graph
    assert "eq=brightness=0.0200:saturation=1.0500" in graph


def test_color_sampling_uses_short_seeked_windows(monkeypatch):
    monkeypatch.setattr("core.stages.export._ffmpeg_path", lambda: "ffmpeg")

    commands = color_sample_commands("/video.mp4", 600.0)

    assert len(commands) == 5
    starts = [float(command[command.index("-ss") + 1]) for command in commands]
    assert starts == [60.0, 180.0, 300.0, 420.0, 540.0]
    for command in commands:
        assert command.index("-ss") < command.index("-i")
        assert command[command.index("-t") + 1] == "2.000"


def test_color_measure_timeout_returns_warning(monkeypatch):
    monkeypatch.setattr("core.stages.export._media_duration", lambda path: 600.0)
    monkeypatch.setattr("core.stages.export._ffmpeg_path", lambda: "ffmpeg")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr("core.stages.export.subprocess.run", timeout)

    profile, warning = measure_clip_color(str(Path("/tmp/clip.mp4")))

    assert profile == {}
    assert warning is not None
    assert "Skipped color matching" in warning


@pytest.mark.slow
def test_two_segment_export_contains_bottom_right_watermark(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setattr("core.stages.export.measure_clip_color", lambda path: ({}, None))
    monkeypatch.setattr("core.stages.export._ffmpeg_supports_filter", lambda name: False)
    project = create_project("Watermark", str(tmp_path / "Watermark.zuckervid"))
    master = tmp_path / "master.wav"
    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(master)], check=True, capture_output=True, text=True)
    for clip in (clip_a, clip_b):
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=black:size=640x360:rate=24:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
            check=True,
            capture_output=True,
            text=True,
        )
    project.data["inputs"]["master"] = file_record(str(master))
    write_artifact_json(
        project.artifacts_dir / "edit_plan.json",
        {
            "platform": "youtube",
            "segments": [
                {"title": "A", "clip_path": str(clip_a), "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 1},
                {"title": "B", "clip_path": str(clip_b), "clip_start_sec": 0, "master_start_sec": 1, "duration_sec": 1},
            ],
        },
    )

    ExportStage().run(project, lambda percent, message: None)
    manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
    output = manifest["exports"][0]["path"]
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-ss",
            "0.5",
            "-i",
            output,
            "-vf",
            "crop=180:140:1700:880,signalstats,metadata=print",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "lavfi.signalstats.YAVG=0" not in f"{result.stdout}\n{result.stderr}"
