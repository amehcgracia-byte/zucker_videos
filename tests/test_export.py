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
    _render_plan,
    _render_segment,
    _verify_moving_segment,
    _clip_fates,
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
    assert "fade=t=in" not in graph
    assert "fade=t=out" not in graph


def test_segment_filtergraph_keeps_only_explicit_intro_outro_fades():
    graph = _segment_filtergraph("youtube", 12.0, {}, {}, has_watermark=False, text_enabled=False, intro_fade=True, outro_fade=True)

    assert "fade=t=in:st=0:d=0.5" in graph
    assert "fade=t=out:st=11.500:d=0.5" in graph


def test_clip_fates_report_used_excluded_and_not_covering(tmp_path):
    project = create_project("Fates", str(tmp_path / "Fates.zuckervid"))
    plan = {
        "clip_diagnostics": [
            {"clip_id": "a", "filename": "a.mp4", "path": "/cache/a.mp4", "confidence": 9.0, "threshold": 6.0, "offset_sec": 0.0},
            {"clip_id": "b", "filename": "b.mp4", "path": "/cache/b.mp4", "confidence": 2.0, "threshold": 6.0, "offset_sec": 1.0},
            {"clip_id": "c", "filename": "c.mp4", "path": "/cache/c.mp4", "confidence": 8.0, "threshold": 6.0, "offset_sec": 99.0},
        ],
        "excluded_clips": [{"filename": "b.mp4", "reason": "low confidence 2.0 < threshold 6.0", "diagnostic": {"path": "/cache/b.mp4"}}],
    }
    segments = [{"clip_path": "/cache/a.mp4", "filename": "a.mp4", "duration_sec": 5.0}]

    fates = _clip_fates(project, plan, segments, 10.0)

    assert {item["filename"]: item["status"] for item in fates} == {"a.mp4": "used", "b.mp4": "excluded", "c.mp4": "not_covering"}
    assert next(item for item in fates if item["filename"] == "a.mp4")["used_percent"] == 50.0


def test_render_plan_fades_only_intro_and_outro_segments(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Fades", str(tmp_path / "Fades.zuckervid"))
    project.data["settings"]["export"]["verify_motion"] = False
    master = tmp_path / "master.wav"
    master.write_bytes(b"master")
    calls = []

    def fake_render_segment(*args, **kwargs):
        calls.append(kwargs)
        args[3].write_bytes(b"segment")
        return "original"

    def fake_progress(command, duration, label, progress):
        Path(command[-1]).write_bytes(b"export")

    monkeypatch.setattr("core.stages.export._render_segment", fake_render_segment)
    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", fake_progress)
    monkeypatch.setattr("core.stages.export._ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("core.stages.export._color_profiles_for_segments", lambda project, segments, warnings: {})

    _render_plan(
        project,
        [
            {"clip_path": "/a.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 2},
            {"clip_path": "/b.mp4", "clip_start_sec": 0, "master_start_sec": 2, "duration_sec": 2},
            {"clip_path": "/c.mp4", "clip_start_sec": 0, "master_start_sec": 4, "duration_sec": 2},
        ],
        str(master),
        tmp_path / "out.mp4",
        "youtube",
        4_000_000,
        [],
        lambda percent, detail: None,
    )

    assert [call["intro_fade"] for call in calls] == [True, False, False]
    assert [call["outro_fade"] for call in calls] == [False, False, True]


def test_render_plan_verifies_every_segment_not_just_every_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("VerifyAll", str(tmp_path / "VerifyAll.zuckervid"))
    master = tmp_path / "master.wav"
    master.write_bytes(b"master")
    verified: list[Path] = []

    def fake_render_segment(*args, **kwargs):
        args[3].write_bytes(b"segment")
        return "original"

    def fake_progress(command, duration, label, progress):
        Path(command[-1]).write_bytes(b"export")

    monkeypatch.setattr("core.stages.export._render_segment", fake_render_segment)
    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", fake_progress)
    monkeypatch.setattr("core.stages.export._verify_moving_segment", lambda path, duration, label, command: verified.append(path))
    monkeypatch.setattr("core.stages.export._ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("core.stages.export._color_profiles_for_segments", lambda project, segments, warnings: {})

    shared_source = str(tmp_path / "sony.mp4")
    _render_plan(
        project,
        [
            {"clip_path": shared_source, "source_path": shared_source, "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 2},
            {"clip_path": shared_source, "source_path": shared_source, "clip_start_sec": 10, "master_start_sec": 2, "duration_sec": 2},
            {"clip_path": shared_source, "source_path": shared_source, "clip_start_sec": 20, "master_start_sec": 4, "duration_sec": 2},
        ],
        str(master),
        tmp_path / "out.mp4",
        "youtube",
        4_000_000,
        [],
        lambda percent, detail: None,
    )

    assert len(verified) == 3


def test_render_segment_uses_original_source_with_proxy_metadata(tmp_path, monkeypatch):
    project = create_project("Original", str(tmp_path / "Original.zuckervid"))
    source = tmp_path / "source.mov"
    proxy = tmp_path / "proxy.mp4"
    master = tmp_path / "master.wav"
    output = tmp_path / "segment.mp4"
    source.write_bytes(b"source")
    proxy.write_bytes(b"proxy")
    master.write_bytes(b"master")
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "video_codec": "h264", "duration": 5.0, "width": 1920, "height": 1080, "fps": 24.0}
    record["cache_key"] = "clip-key"
    record["normalized"] = {"path": str(proxy), "cache_key": "clip-key", "kind": "proxy"}
    project.data["inputs"]["videos"] = [record]
    commands = []

    monkeypatch.setattr("core.stages.export._ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("core.stages.export._watermark_path", lambda: None)
    monkeypatch.setattr("core.stages.export._ffmpeg_supports_filter", lambda name: False)

    def fake_progress(command, duration, label, progress):
        commands.append(command)
        output.write_bytes(b"segment")

    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", fake_progress)

    rendered_from = _render_segment(
        project,
        {"clip_path": str(proxy), "source_path": str(source), "clip_start_sec": 1, "master_start_sec": 2, "duration_sec": 3},
        str(master),
        output,
        "youtube",
        4_000_000,
    )

    assert rendered_from == "original"
    assert str(source) in commands[0]
    assert str(proxy) not in commands[0]
    assert "fps=24.000,setpts=PTS-STARTPTS" in commands[0][commands[0].index("-filter_complex") + 1]


def test_render_segment_falls_back_to_proxy_when_original_decode_fails(tmp_path, monkeypatch):
    project = create_project("Fallback", str(tmp_path / "Fallback.zuckervid"))
    source = tmp_path / "source.mov"
    proxy = tmp_path / "proxy.mp4"
    master = tmp_path / "master.wav"
    output = tmp_path / "segment.mp4"
    source.write_bytes(b"source")
    proxy.write_bytes(b"proxy")
    master.write_bytes(b"master")
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "video_codec": "h264", "duration": 5.0, "width": 1920, "height": 1080}
    record["cache_key"] = "clip-key"
    record["normalized"] = {"path": str(proxy), "cache_key": "clip-key", "kind": "proxy"}
    project.data["inputs"]["videos"] = [record]
    calls = []

    monkeypatch.setattr("core.stages.export._ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("core.stages.export._watermark_path", lambda: None)
    monkeypatch.setattr("core.stages.export._ffmpeg_supports_filter", lambda name: False)

    def fake_progress(command, duration, label, progress):
        calls.append(command)
        if len(calls) < 3:
            from core.ffmpeg import FFmpegError

            raise FFmpegError("decode failed")
        output.write_bytes(b"proxy segment")

    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", fake_progress)
    warnings = []

    rendered_from = _render_segment(
        project,
        {"clip_path": str(proxy), "source_path": str(source), "clip_start_sec": 1, "master_start_sec": 2, "duration_sec": 3},
        str(master),
        output,
        "youtube",
        4_000_000,
        warnings=warnings,
    )

    assert rendered_from == "proxy"
    assert str(source) in calls[0]
    assert str(source) in calls[1]
    assert str(proxy) in calls[2]
    assert warnings


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
def test_compliant_skip_original_segment_renders_moving_video(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("core.stages.export._watermark_path", lambda: None)
    monkeypatch.setattr("core.stages.export._ffmpeg_supports_filter", lambda name: False)
    project = create_project("Moving", str(tmp_path / "Moving.zuckervid"))
    source = tmp_path / "source.mp4"
    master = tmp_path / "master.wav"
    output = tmp_path / "segment.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24:duration=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(master)], check=True, capture_output=True, text=True)
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "video_codec": "h264", "duration": 3.0, "width": 640, "height": 360, "fps": 24.0, "cfr": True}
    record["cache_key"] = "skip-key"
    record["normalized"] = {"path": str(source), "cache_key": "skip-key", "kind": "original", "proxy_skipped": True}
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]

    rendered_from = _render_segment(
        project,
        {"clip_path": str(source), "source_path": str(source), "clip_start_sec": 0.2, "master_start_sec": 0.2, "duration_sec": 1.5},
        str(master),
        output,
        "youtube",
        4_000_000,
    )

    assert rendered_from == "original"
    _verify_moving_segment(output, 1.5, "source.mp4", "test command")


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
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
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
