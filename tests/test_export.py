from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from core.project import create_project, file_record
from core.stages.base import write_artifact_json
from core.stages.export import (
    MAX_EXPORT_BYTES,
    MAX_SPHERICAL_FOV,
    MIN_ACCEPTABLE_VIDEO_BITRATE,
    TARGET_EXPORT_FPS,
    ExportStage,
    _audio_gain_curve_samples,
    _bitrate_for_duration,
    _cadence_checked_joined_video,
    cached_segment_path,
    _continuous_spherical_render_segments,
    _effective_flat_fov,
    _expand_spherical_render_segments,
    _export_source_filter,
    _frame_pts_times,
    _motion_filter,
    _paired_flat_fov,
    _paired_motion_fov,
    _shot_peak_fov,
    _use_stereographic,
    _warn_if_spherical_framing_was_dropped,
    _v360_motion_at,
    _v360_motion_commands,
    _v360_sendcmd_filter,
    degrees_per_second_to_step,
    _run_ffmpeg_progress,
    SPHERE_V360_LABEL,
    _render_plan,
    _render_segment,
    _reel_overlay_items,
    _segment_cache_stamp_matches,
    _segment_cache_stamp_path,
    _write_segment_cache_stamp,
    _export_run_id,
    _output_path,
    _audio_rms,
    _frame_normalized_segments,
    _frame_md5,
    _verify_final_audio,
    _verify_moving_segment,
    _verify_video_cadence,
    _clip_fates,
    _segment_filtergraph,
    _probe_video_profile,
    color_sample_commands,
    color_correction_for_profile,
    measure_clip_color,
)


def test_each_export_run_gets_a_distinct_result_path(tmp_path):
    project = create_project("Run", str(tmp_path / "project"))
    first = _output_path(project, "youtube", _export_run_id())
    second = _output_path(project, "youtube", _export_run_id())
    assert first != second
    assert first.name.endswith(".mp4")


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
    assert "split=2" not in graph
    assert "overlay=(W-w)/2:(H-h)/2" not in graph
    assert "enable='lt(t,2)'" not in graph
    assert "eq=brightness=0.0200:saturation=1.0500" in graph
    assert "fade=t=in:st=0:d=1.500" not in graph
    assert "fade=t=out:st=10.500:d=1.500" not in graph


def test_segment_filtergraph_keeps_only_explicit_intro_outro_fades():
    graph = _segment_filtergraph("youtube", 12.0, {}, {}, has_watermark=False, text_enabled=False, intro_fade=True, outro_fade=True)

    assert "fade=t=in:st=0:d=1.500" in graph
    assert "fade=t=out:st=10.500:d=1.500" in graph


def test_clip_fates_report_used_excluded_and_not_covering(tmp_path):
    project = create_project("Fates", str(tmp_path / "Fates.zuckervid"))
    plan = {
        "clip_diagnostics": [
            {"clip_id": "a", "filename": "a.mp4", "path": "/cache/a.mp4", "confidence": 9.0, "threshold": 6.0, "offset_sec": 0.0},
            {"clip_id": "b", "filename": "b.mp4", "path": "/cache/b.mp4", "confidence": 2.0, "threshold": 6.0, "offset_sec": 1.0},
            {"clip_id": "c", "filename": "c.mp4", "path": "/cache/c.mp4", "confidence": 8.0, "threshold": 6.0, "offset_sec": 99.0},
        ],
        "selection_diagnostics": [
            {"filename": "a.mp4", "path": "/cache/a.mp4", "covered_seconds": 10.0, "eligible_segments": 2, "chosen_segments": 1, "chosen_seconds": 5.0}
        ],
        "excluded_clips": [{"filename": "b.mp4", "reason": "low confidence 2.0 < threshold 6.0", "diagnostic": {"path": "/cache/b.mp4"}}],
    }
    segments = [{"clip_path": "/cache/a.mp4", "filename": "a.mp4", "duration_sec": 5.0}]

    fates = _clip_fates(project, plan, segments, 10.0)

    assert {item["filename"]: item["status"] for item in fates} == {"a.mp4": "used", "b.mp4": "excluded", "c.mp4": "not_covering"}
    assert next(item for item in fates if item["filename"] == "a.mp4")["used_percent"] == 50.0
    assert next(item for item in fates if item["filename"] == "a.mp4")["chosen_segments"] == 1
    assert "chosen 1/2 eligible" in next(item for item in fates if item["filename"] == "a.mp4")["reason"]


def test_render_plan_uses_standalone_intro_and_outro_clips(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Fades", str(tmp_path / "Fades.zuckervid"))
    project.data["settings"]["export"]["verify_motion"] = False
    master = tmp_path / "master.wav"
    master.write_bytes(b"master")
    calls = []
    logo_clips = []

    def fake_render_segment(*args, **kwargs):
        calls.append(kwargs)
        args[3].write_bytes(b"segment")
        return "original"

    def fake_progress(command, duration, label, progress):
        if "-f" in command and "concat" in command:
            concat_file = Path(command[command.index("-i") + 1])
            lines = concat_file.read_text(encoding="utf-8").splitlines()
            assert "intro.mp4" in lines[0]
            assert "outro.mp4" in lines[-1]
        Path(command[-1]).write_bytes(b"export")

    monkeypatch.setattr("core.stages.export._render_segment", fake_render_segment)
    monkeypatch.setattr("core.stages.export._render_logo_clip", lambda *args, **kwargs: (logo_clips.append((args, kwargs)), args[0].write_bytes(b"logo")))
    monkeypatch.setattr("core.stages.export._can_blend_intro_outro", lambda: False)
    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", fake_progress)
    monkeypatch.setattr("core.stages.export._mux_continuous_master_audio", lambda video, master, output, *args, **kwargs: output.write_bytes(b"muxed"))
    monkeypatch.setattr("core.stages.export._verify_segment_frame_duration", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.stages.export._media_duration", lambda path: 6.0)
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

    assert sorted(call["intro_fade"] for call in calls) == [False, False, True]
    assert sorted(call["outro_fade"] for call in calls) == [False, False, True]
    assert [call[0][2] for call in logo_clips] == ["intro", "outro"]


def test_segment_filtergraph_can_blend_large_intro_logo_over_footage():
    graph = _segment_filtergraph("youtube", 8.0, {}, {}, has_watermark=True, text_enabled=False, intro_logo=True, outro_logo=True)

    assert "split=3" in graph
    assert "scale=-1:756" in graph
    assert "enable='lt(t,9.6)'" in graph
    assert "enable='gte(t,0.000)'" in graph
    assert "overlay=W-w-40:H-h-40" in graph


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
    monkeypatch.setattr("core.stages.export._verify_segment_frame_duration", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.stages.export._verify_joined_output", lambda path, segments, timeline_offset=0.0: None)
    monkeypatch.setattr("core.stages.export._verify_final_audio", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.stages.export._mux_continuous_master_audio", lambda video, master, output, *args, **kwargs: output.write_bytes(b"muxed"))
    monkeypatch.setattr("core.stages.export._media_duration", lambda path: 6.0)
    monkeypatch.setattr("core.stages.export._render_logo_clip", lambda *args, **kwargs: args[0].write_bytes(b"logo"))
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
    assert "fps=fps=30.000:round=near:start_time=0,trim=start_frame=0:end_frame=90,setpts=N/(30.000*TB)" in commands[0][commands[0].index("-filter_complex") + 1]
    assert str(master) not in commands[0]
    assert "-an" in commands[0]
    assert commands[0][commands[0].index("-r") + 1] == "30.000"
    assert commands[0][commands[0].index("-fps_mode") + 1] == "cfr"
    assert commands[0][commands[0].index("-video_track_timescale") + 1] == "30000"


def test_spherical_proxy_fallback_keeps_v360_sendcmd(tmp_path, monkeypatch):
    project = create_project("Spherical proxy", str(tmp_path / "Spherical proxy.zuckervid"))
    source = tmp_path / "source.mp4"
    proxy = tmp_path / "proxy.mp4"
    master = tmp_path / "master.wav"
    output = tmp_path / "segment.mp4"
    for path in (source, proxy, master):
        path.write_bytes(b"input")
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "projection": "equirect", "width": 1920, "height": 1080, "fps": 30.0}
    record["cache_key"] = "spherical-clip"
    record["normalized"] = {"path": str(proxy), "cache_key": "spherical-clip", "kind": "proxy"}
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
        {
            "clip_path": str(proxy),
            "source_path": str(source),
            "clip_start_sec": 1,
            "master_start_sec": 2,
            "duration_sec": 3,
            "spherical_shot": {"type": "audience", "yaw": 175, "pitch": -12.9, "fov": 100, "hold_motion": "none"},
        },
        str(master), output, "youtube", 4_000_000, force_proxy=True,
    )

    assert rendered_from == "proxy"
    assert str(proxy) in commands[0]
    filtergraph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "sendcmd=f=" not in filtergraph
    assert "v360@sphere" in filtergraph
    assert not output.with_suffix(".sendcmd.txt").exists()


def test_cached_segment_path_includes_spherical_shot_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Cache", str(tmp_path / "Cache.zuckervid"))
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "projection": "equirect"}
    record["cache_key"] = "source-key"
    project.data["inputs"]["videos"] = [record]
    base = {"clip_path": str(source), "source_path": str(source), "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 3}

    first = cached_segment_path(project, {**base, "spherical_shot": {"type": "singer", "yaw": 10}}, "youtube", 4_000_000, {}, {}, False, False)
    second = cached_segment_path(project, {**base, "spherical_shot": {"type": "drummer", "yaw": 120}}, "youtube", 4_000_000, {}, {}, False, False)

    assert first != second


def test_cached_segment_path_includes_spherical_motion_recipe_version(tmp_path, monkeypatch):
    project = create_project("Motion cache", str(tmp_path / "Motion cache.zuckervid"))
    source = tmp_path / "360.mp4"
    source.write_bytes(b"360")
    project.data["inputs"]["videos"] = [{"path": str(source), "normalized": {}, "cache_key": "source"}]
    segment = {"clip_path": str(source), "source_path": str(source), "clip_start_sec": 0, "duration_sec": 3}

    first = cached_segment_path(project, segment, "youtube", 4_000_000, {}, {}, False, False)
    monkeypatch.setattr("core.stages.export.SPHERICAL_MOTION_RECIPE_VERSION", 99)
    second = cached_segment_path(project, segment, "youtube", 4_000_000, {}, {}, False, False)

    assert first != second


def test_segment_cache_attestation_rejects_missing_or_changed_recipe(tmp_path, monkeypatch):
    segment_path = tmp_path / "segment.mp4"
    segment_path.write_bytes(b"rendered")
    segment = {
        "clip_path": str(tmp_path / "source.mp4"),
        "source_path": str(tmp_path / "source.mp4"),
        "clip_start_sec": 0,
        "duration_sec": 3,
        "spherical_shot": {"type": "singer", "yaw": 10, "pitch": 0, "fov": 90},
    }

    assert not _segment_cache_stamp_matches(segment_path, segment)
    _write_segment_cache_stamp(segment_path, segment)
    assert _segment_cache_stamp_matches(segment_path, segment)

    monkeypatch.setattr("core.stages.export.SPHERICAL_MOTION_RECIPE_VERSION", 13)
    assert not _segment_cache_stamp_matches(segment_path, segment)
    assert _segment_cache_stamp_path(segment_path).exists()


def test_segment_cache_attestation_rejects_rewritten_bytes(tmp_path):
    segment_path = tmp_path / "segment.mp4"
    segment_path.write_bytes(b"rendered")
    segment = {
        "clip_path": str(tmp_path / "source.mp4"),
        "source_path": str(tmp_path / "source.mp4"),
        "clip_start_sec": 0,
        "duration_sec": 3,
        "spherical_shot": {"type": "audience", "yaw": 175, "pitch": -12.9, "fov": 100},
    }

    _write_segment_cache_stamp(segment_path, segment)
    assert _segment_cache_stamp_matches(segment_path, segment)
    segment_path.write_bytes(b"different-render")
    assert not _segment_cache_stamp_matches(segment_path, segment)


def test_segment_cache_attestation_includes_full_spherical_shot(tmp_path):
    segment_path = tmp_path / "segment.mp4"
    segment_path.write_bytes(b"rendered")
    segment = {
        "clip_path": str(tmp_path / "source.mp4"),
        "source_path": str(tmp_path / "source.mp4"),
        "clip_start_sec": 0,
        "duration_sec": 3,
        "spherical_shot": {"type": "audience", "yaw": 175, "pitch": -12.9, "fov": 100, "hold_motion": "none"},
    }

    _write_segment_cache_stamp(segment_path, segment)
    segment["spherical_shot"]["hold_motion"] = "subtle"
    assert not _segment_cache_stamp_matches(segment_path, segment)


def test_join_fast_path_skips_cfr_rewrite_when_cadence_passes(tmp_path, monkeypatch):
    source = tmp_path / "joined.mp4"
    fallback = tmp_path / "fallback.mp4"
    source.write_bytes(b"video")
    calls = []

    monkeypatch.setattr("core.stages.export._media_duration", lambda path: 12.0)
    monkeypatch.setattr("core.stages.export._verify_video_cadence", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.stages.export._normalize_joined_video_cadence", lambda *args, **kwargs: calls.append(args))

    selected = _cadence_checked_joined_video(source, fallback, 4_000_000, lambda percent, detail: None)

    assert selected == source
    assert calls == []


def test_spherical_render_parts_apply_gentle_planet_motion():
    segments = [
        {"clip_path": "/tmp/360.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 3, "spherical_shot": {"type": "singer", "label": "Cantante", "yaw": 20, "pitch": 0, "fov": 80}},
        {"clip_path": "/tmp/360.mp4", "clip_start_sec": 3, "master_start_sec": 3, "duration_sec": 3, "spherical_shot": {"type": "planet", "label": "Planeta", "yaw": 40, "pitch": -65, "fov": 180, "spin_deg_per_sec": 22, "transition_sec": 0.45}},
    ]

    parts = _expand_spherical_render_segments(segments)

    assert sum(float(part["duration_sec"]) for part in parts) == pytest.approx(6.0)
    yaws = [round(float(part["spherical_shot"]["yaw"]), 1) for part in parts if part["spherical_shot"].get("type") == "planet"]
    assert len(yaws) == 6
    assert max(b - a for a, b in zip(yaws, yaws[1:])) <= 15.0


def test_planet_part_motion_uses_seconds_not_frames():
    segments = [
        {
            "clip_path": "/tmp/360.mp4",
            "clip_start_sec": 0,
            "master_start_sec": 0,
            "duration_sec": 3,
            "spherical_shot": {"type": "planet", "yaw": 40, "pitch": -65, "fov": 180, "spin_deg_per_sec": 0.4},
        }
    ]

    parts = _expand_spherical_render_segments(segments)
    yaws = [float(part["spherical_shot"]["yaw"]) for part in parts]
    assert max(yaws) - min(yaws) == pytest.approx(0.4 * 2.5, abs=0.05)


def test_spherical_render_parts_do_not_add_static_drift():
    segments = [
        {
            "clip_path": "/tmp/360.mp4",
            "clip_start_sec": 0,
            "master_start_sec": 0,
            "duration_sec": 3,
            "spherical_shot": {"type": "singer", "label": "Cantante", "yaw": 336.8, "pitch": -28.8, "fov": 74.8, "drift_yaw_deg": 4, "drift_pitch_deg": 2},
        }
    ]

    parts = _expand_spherical_render_segments(segments)

    yaws = [part["spherical_shot"]["yaw"] for part in parts]
    pitches = [part["spherical_shot"]["pitch"] for part in parts]
    assert len(parts) == 3
    assert yaws[1] == pytest.approx(336.8)
    assert yaws[0] < yaws[1] < yaws[2]
    assert pitches == [-28.8, -28.8, -28.8]


def test_spherical_pan_steps_keep_angle_increments_small():
    segments = [
        {"clip_path": "/tmp/360.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 3, "spherical_shot": {"type": "singer", "label": "Cantante", "yaw": 20, "pitch": 0, "fov": 80}},
        {"clip_path": "/tmp/360.mp4", "clip_start_sec": 3, "master_start_sec": 3, "duration_sec": 3, "spherical_shot": {"type": "left", "label": "Lado izquierdo", "yaw": 100, "pitch": 0, "fov": 80, "transition_sec": 0.45}},
    ]

    parts = _expand_spherical_render_segments(segments)
    pan_yaws = [float(part["spherical_shot"]["yaw"]) for part in parts if part["spherical_shot"].get("type") == "pan"]
    deltas = [abs(((b - a + 540) % 360) - 180) for a, b in zip([20.0, *pan_yaws], pan_yaws)]

    assert pan_yaws == []
    assert deltas == []


def test_shortest_yaw_delta_crossing_zero_takes_short_positive_path():
    from core.stages.export import _shortest_yaw_delta

    assert _shortest_yaw_delta(349.0, 17.0) == 28.0


@pytest.mark.parametrize("duration", [0.1, 1.0, 1.999])
def test_short_360_segments_hold_all_view_axes(duration):
    shot = {
        "type": "singer",
        "yaw": 17.0,
        "pitch": -12.0,
        "fov": 95.0,
        "previous_shot": {"type": "left", "yaw": 349.0, "pitch": -30.0, "fov": 140.0},
        "sweep_enabled": True,
        "sweep_speed_deg_per_sec": 33.0,
        "drift_yaw_fraction": 0.06,
    }
    assert _v360_motion_at(shot, duration, 0.0) == (17.0, -12.0, 95.0)
    assert _v360_motion_at(shot, duration, duration) == (17.0, -12.0, 95.0)


def test_planet_spin_is_capped_to_a_gentle_rate():
    planet = {"type": "planet", "yaw": 0.0, "pitch": -90.0, "fov": 240.0, "spin_deg_per_sec": 18.0}
    start = _v360_motion_at(planet, 4.0, 0.0)[0]
    end = _v360_motion_at(planet, 4.0, 4.0)[0]
    assert end - start == pytest.approx(20.0)


def test_sendcmd_emits_gentle_yaw_only_pose_motion():
    shot = {
        "type": "singer",
        "yaw": 17.0,
        "pitch": -12.0,
        "fov": 95.0,
        "drift_yaw_fraction": 0.04,
        "drift_pitch_fraction": 0.0,
        "fov_delta_fraction": 0.0,
    }

    samples = [_v360_motion_at(shot, 3.0, t) for t in (0.0, 1.5, 3.0)]
    commands = _v360_motion_commands(shot, 3.0)

    assert len(commands) == 8
    assert len({line.split()[0] for line in commands}) == 2
    assert samples[0][0] < samples[1][0] < samples[2][0]
    assert samples[0][1:] == samples[1][1:] == samples[2][1:]


def test_continuous_spherical_segments_keep_cut_count_flat():
    segments = [
        {"clip_path": "/tmp/360.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 3, "spherical_shot": {"type": "singer", "label": "Cantante", "yaw": 20, "pitch": 0, "fov": 80}},
        {"clip_path": "/tmp/360.mp4", "clip_start_sec": 3, "master_start_sec": 3, "duration_sec": 3, "spherical_shot": {"type": "left", "label": "Lado izquierdo", "yaw": 100, "pitch": 0, "fov": 80, "transition_sec": 0.45}},
    ]

    render_segments = _continuous_spherical_render_segments(segments)

    assert len(render_segments) == len(segments)
    assert render_segments[1]["spherical_shot"]["previous_shot"]["type"] == "singer"


def test_v360_sendcmd_is_constant_even_for_a_legacy_pan_plan():
    shot = {"type": "left", "yaw": 100, "pitch": 0, "fov": 80, "transition_sec": 0.45, "previous_shot": {"type": "singer", "yaw": 20, "pitch": 0, "fov": 80}}
    samples = [_v360_motion_at(shot, 3.0, index * (1.0 / TARGET_EXPORT_FPS))[0] for index in range(10)]
    deltas = [abs(((b - a + 540) % 360) - 180) for a, b in zip(samples, samples[1:])]
    commands = _v360_motion_commands(shot, 3.0)

    assert all(delta == pytest.approx(0.0) for delta in deltas)
    assert samples[0] == samples[-1]
    assert commands == []


def test_static_hold_has_no_sendcmd_filter_or_commands():
    shot = {
        "type": "audience",
        "yaw": 175.0,
        "pitch": -12.9,
        "fov": 100.0,
        "hold_motion": "none",
        "hold_motion_rate_deg_per_sec": 0.0,
        "sweep_enabled": False,
    }
    assert _v360_motion_commands(shot, 8.9) == []
    assert _v360_sendcmd_filter(shot, 8.9, Path("/tmp/static-hold.sendcmd")) == ""


def test_landmark_sweep_uses_distance_over_speed_not_transition_sec():
    shot = {
        "type": "audience",
        "yaw": 175.0,
        "pitch": -12.0,
        "fov": 111.0,
        "transition_sec": 0.45,
        "sweep_speed_deg_per_sec": 30.0,
        "previous_shot": {"type": "left", "yaw": 16.0, "pitch": -16.0, "fov": 100.0},
    }

    # A landmark with sweep disabled never interpolates from the previous shot,
    # regardless of legacy transition fields.
    before_target = _v360_motion_at(shot, 6.0, 5.0)[0]
    at_target = _v360_motion_at(shot, 6.0, 5.3)[0]
    commands = _v360_motion_commands(shot, 6.0)
    yaws = [float(line.split(" yaw ", 1)[1].strip().rstrip(";")) for line in commands if " yaw " in line]

    assert before_target == pytest.approx(175.0)
    assert at_target == pytest.approx(175.0)
    assert yaws == []


def test_returning_to_360_keeps_previous_spherical_view_across_other_camera_cut():
    segments = [
        {"duration_sec": 3, "spherical_shot": {"type": "left", "yaw": 16, "pitch": -16, "fov": 100}},
        {"duration_sec": 3},
        {"duration_sec": 3, "spherical_shot": {"type": "audience", "yaw": 175, "pitch": -12, "fov": 111, "sweep_speed_deg_per_sec": 30}},
    ]

    rendered = _continuous_spherical_render_segments(segments)
    assert rendered[2]["spherical_shot"]["previous_shot"]["yaw"] == 16


def test_v360_sendcmd_follows_recorded_curve_samples():
    shot = {
        "type": "recorded_move",
        "label": "Recorded take: Main",
        "recorded_take": "Main",
        "curve": [
            {"t": 0.0, "yaw": 350, "pitch": -10, "fov": 90},
            {"t": 1.0, "yaw": 355, "pitch": -8, "fov": 95},
            {"t": 2.0, "yaw": 5, "pitch": -6, "fov": 100},
        ],
    }

    yaws = [_v360_motion_at(shot, 2.0, value)[0] for value in (0.0, 0.5, 1.0, 1.5, 2.0)]
    commands = _v360_motion_commands(shot, 2.0)

    assert yaws == pytest.approx([-10.0, -7.5, -5.0, 0.0, 5.0])
    assert any("sphere h_fov" in command for command in commands)
    assert any("sphere v_fov" in command for command in commands)


def test_recorded_take_render_path_caps_yaw_rate_and_unwraps_seam():
    shot = {
        "type": "recorded_move",
        "curve": [
            {"t": 0.0, "yaw": 350.0, "pitch": 0.0, "fov": 90.0},
            {"t": 0.1, "yaw": 20.0, "pitch": 0.0, "fov": 90.0},
        ],
    }

    start = _v360_motion_at(shot, 0.1, 0.0)[0]
    end = _v360_motion_at(shot, 0.1, 0.1)[0]
    delta = ((end - start + 180.0) % 360.0) - 180.0

    assert delta == pytest.approx(4.0)


def test_landmark_hold_motion_is_bounded_and_never_pans_from_previous_shot():
    base = {
        "type": "singer",
        "yaw": 17.0,
        "pitch": -12.0,
        "fov": 95.0,
        "sweep_enabled": False,
        "previous_shot": {"type": "left", "yaw": 160.0, "pitch": -12.0, "fov": 95.0},
    }
    for mode, rate in (("subtle", 0.75), ("none", 0.0)):
        shot = {**base, "hold_motion": mode, "hold_motion_rate_deg_per_sec": rate}
        duration = 3.0
        yaws = [_v360_motion_at(shot, duration, frame / 30.0)[0] for frame in range(91)]
        total = abs(((yaws[-1] - yaws[0] + 180.0) % 360.0) - 180.0)
        measured_rate = total / duration
        assert measured_rate <= 1.0
        assert total <= 3.0
        if mode == "none":
            assert total == pytest.approx(0.0)
        assert yaws[0] == pytest.approx(17.0)
        assert yaws[-1] == pytest.approx(19.25 if mode == "subtle" else 17.0)


def test_hold_rate_is_converted_from_degrees_per_second_to_total_travel():
    shot = {
        "type": "audience",
        "yaw": 175.0,
        "pitch": -12.9,
        "fov": 100.0,
        "hold_motion": "subtle",
        "hold_motion_rate_deg_per_sec": 0.4,
        "sweep_enabled": False,
    }
    duration = 8.7
    commands = _v360_motion_commands(shot, duration)
    yaws = [float(line.split(" yaw ", 1)[1].rstrip(";\n")) for line in commands if " yaw " in line]
    assert len({line.split()[0] for line in commands}) == 2
    assert yaws[-1] - yaws[0] == pytest.approx(degrees_per_second_to_step(0.4, duration / 2.0), rel=0.05)
    assert yaws[-1] - yaws[0] < 10.0


@pytest.mark.parametrize(
    ("shot", "expected_travel"),
    [
        (
            {
                "type": "planet",
                "yaw": 10.0,
                "pitch": -90.0,
                "fov": 240.0,
                "spin_deg_per_sec": 5.0,
            },
            5.0 * 8.9,
        ),
        (
            {
                "type": "audience",
                "yaw": 120.0,
                "pitch": -12.0,
                "fov": 100.0,
                "sweep_enabled": True,
                "sweep_speed_deg_per_sec": 20.0,
                "previous_shot": {"type": "left", "yaw": 30.0},
            },
            90.0,
        ),
    ],
)
def test_sendcmd_rate_fields_are_seconds_not_frames(shot, expected_travel):
    commands = _v360_motion_commands(shot, 8.9)
    yaws = [float(line.split(" yaw ", 1)[1].rstrip(";\n")) for line in commands if " yaw " in line]
    travel = abs(((yaws[-1] - yaws[0] + 180.0) % 360.0) - 180.0)
    assert travel == pytest.approx(expected_travel, rel=0.10, abs=0.1)
    # A 30-fps implementation accidentally multiplying a deg/s value by the
    # number of command steps would exceed this by roughly 30x.
    assert travel < expected_travel * 1.2 + 0.2


def test_planet_uses_stereographic_tiny_planet_projection():
    graph = _export_source_filter({"projection": "equirect"}, {"type": "planet", "yaw": 6, "pitch": -90, "fov": 260}, duration=3.0, command_path=None)

    assert "output=sg" in graph
    assert "pitch=-90.000" in graph
    assert "h_fov=260.000" in graph


def test_wide_shots_render_stereographic_and_narrow_ones_stay_rectilinear():
    # A rectilinear ("flat") view tears as it approaches 180 deg, so a shot
    # pulled back to the full-sphere look has to switch to stereographic. An
    # ordinary shot must NOT: sg would visibly bend a normal 95 deg framing.
    wide = _export_source_filter({"projection": "equirect"}, {"type": "planet", "yaw": 0, "pitch": -90, "fov": 240})
    narrow = _export_source_filter({"projection": "equirect"}, {"type": "singer", "yaw": 0, "pitch": -20, "fov": 95})

    assert "output=sg" in wide
    assert "h_fov=240.000" in wide
    assert "output=flat" in narrow
    assert "output=sg" not in narrow


def test_shot_wider_than_the_old_rectilinear_cap_is_no_longer_clamped_to_190():
    # Regression guard for the authored range: the UI now offers up to 300 deg,
    # so the export must actually render it rather than silently clamping.
    assert _effective_flat_fov({"type": "audience", "fov": 260}) == 100.0
    assert _effective_flat_fov({"type": "audience", "fov": 400}) == 100.0
    assert _effective_flat_fov({"type": "planet", "fov": 300}) == 300.0


def test_normal_landmark_fov_stays_natural_and_reaches_v360_unchanged_when_in_range():
    assert _effective_flat_fov({"type": "drummer", "fov": 73.3}) == 73.3
    assert _effective_flat_fov({"type": "singer", "fov": 95.0}) == 95.0
    assert _effective_flat_fov({"type": "audience", "fov": 111.4}) == 100.0
    assert "output=flat" in _export_source_filter({"projection": "equirect"}, {"type": "audience", "fov": 260})


def test_automatic_hold_yaw_travel_is_capped_to_a_few_degrees():
    shot = {
        "type": "singer",
        "yaw": 90.0,
        "pitch": -20.0,
        "fov": 95.0,
        "drift_yaw_fraction": 1.0,
        "drift_pitch_fraction": 0.0,
        "fov_delta_fraction": 0.0,
    }
    start = _v360_motion_at(shot, 4.0, 0.0)[0]
    end = _v360_motion_at(shot, 4.0, 4.0)[0]
    assert abs(((end - start + 180.0) % 360.0) - 180.0) <= 10.1


def test_projection_choice_is_fixed_per_segment_so_fov_drift_cannot_pop_the_framing():
    """The flat/sg decision must not depend on the instantaneous FOV.

    The output projection is baked into the filtergraph once, while h_fov/v_fov
    are driven per frame by sendcmd. When the two disagreed, a shot sitting near
    the threshold with a little fov drift paired its vertical field by flat
    rules for part of the segment and stereographic rules for the rest --
    v_fov jumped from ~162 deg to 120 deg partway through an otherwise still
    hold, a visible pop.
    """
    shot = {"type": "recorded_move", "yaw": 72.0, "pitch": -14.3, "fov": 170.0, "fov_delta_fraction": 0.04}
    assert not _use_stereographic(shot)  # 170 is not past the threshold...

    # ...so every frame of the segment, including ones whose drifting FOV
    # crosses 170, must stay on the flat pairing and vary smoothly.
    verticals = [_paired_motion_fov(shot, fov, 16.0 / 9.0)[1] for fov in (166.0, 168.0, 170.0, 172.0, 174.0)]
    assert verticals == sorted(verticals)
    steps = [b - a for a, b in zip(verticals, verticals[1:])]
    assert max(steps) < 2.5 * min(steps), verticals


def test_a_recorded_take_that_zooms_wide_is_stereographic_for_its_whole_duration():
    # The authored `fov` is only where a recorded move STARTS; the curve says
    # how wide it gets. Picking the projection from the start value would flip
    # projection mid-take, so the widest moment decides for the whole segment.
    zooming_out = {
        "type": "recorded_move",
        "fov": 90.0,
        "curve": [{"t": 0.0, "yaw": 0, "pitch": 0, "fov": 90.0}, {"t": 4.0, "yaw": 0, "pitch": 0, "fov": 280.0}],
    }

    assert _shot_peak_fov(zooming_out) == 280.0
    assert _use_stereographic(zooming_out)
    # ...and while it is still zoomed in, the vertical field must stay NARROWER
    # than the horizontal one. A floor on the stereographic vertical field made
    # v_fov exceed h_fov here, which renders as a vertically stretched frame.
    h_fov, v_fov = _paired_motion_fov(zooming_out, 90.0, 16.0 / 9.0)
    assert v_fov < h_fov


def test_planet_framing_is_unchanged_by_the_wide_shot_support():
    # Planet's pairing is a deliberately un-aspect-paired signature look, not a
    # geometric view. Generalising the stereographic path must not restyle every
    # existing planet shot.
    for fov in (220.0, 250.0, 300.0):
        h_fov, v_fov = _paired_motion_fov({"type": "planet", "fov": fov}, fov, 16.0 / 9.0)
        assert h_fov == fov
        assert v_fov == max(160.0, min(260.0, fov / (16.0 / 9.0)))


def test_warns_when_a_360_segment_loses_its_framing_and_renders_flat():
    # A segment that wants a spherical shot but whose probe reports no
    # projection gets NO v360 reframing: it renders as a flat letterboxed
    # passthrough, identical for every landmark and with no motion. That used to
    # happen silently whenever a segment's source failed to match its input
    # record.
    warnings: list[str] = []
    segment = {"source_path": "/tmp/insta360.mp4", "spherical_shot": {"type": "singer", "yaw": 10, "fov": 95}}
    _warn_if_spherical_framing_was_dropped(segment, {}, warnings)

    assert len(warnings) == 1
    assert "insta360.mp4" in warnings[0]
    assert "360 framing was dropped" in warnings[0]


def test_no_dropped_framing_warning_for_healthy_360_or_ordinary_flat_segments():
    healthy: list[str] = []
    _warn_if_spherical_framing_was_dropped(
        {"source_path": "/tmp/insta360.mp4", "spherical_shot": {"type": "singer", "fov": 95}},
        {"projection": "equirect"},
        healthy,
    )
    assert healthy == []

    # An iPhone/Sony segment never asked for spherical framing in the first place.
    flat: list[str] = []
    _warn_if_spherical_framing_was_dropped({"source_path": "/tmp/iphone.mov"}, {}, flat)
    assert flat == []


def test_motion_filter_builds_bounded_ken_burns_zoom():
    graph = _motion_filter({"motion": {"type": "ken_burns", "zoom_start": 1.0, "zoom_end": 1.08, "pan_x": 0.5, "pan_y": 0.5}}, "youtube", 4.0)

    assert graph is not None
    assert "zoompan=" not in graph
    assert "eval=frame" in graph
    assert "crop=1920:1080" in graph


def test_fixed_rear_export_cadence_passes_with_zoom_on_and_off(tmp_path):
    project = create_project("IphoneCadence", str(tmp_path / "IphoneCadence.zuckervid"))
    source = tmp_path / "iphone.mov"
    master = tmp_path / "master.wav"
    _make_test_video(source, duration=3.0, fps=30.0, size="640x360")
    _make_silent_wav(master, duration=3.0)
    project.data["inputs"]["master"] = file_record(str(master))
    record = file_record(str(source))
    record["probe"] = {"valid_video": True, "projection": None, "duration": 3.0, "fps": 30.0, "width": 640, "height": 360}
    project.data["inputs"]["videos"] = [record]
    base = {"clip_path": str(source), "source_path": str(source), "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 3, "filename": "iphone.mov"}

    for name, segment in {"off": base, "on": {**base, "motion": {"type": "ken_burns", "zoom_start": 1.0, "zoom_end": 1.08, "pan_x": 0.5, "pan_y": 0.5}}}.items():
        output = tmp_path / f"{name}.mp4"
        _render_segment(project, segment, str(master), output, "youtube", 4_000_000, {}, {}, None, warnings=[], command_recorder=[])
        _verify_video_cadence(output, f"fixed rear zoom {name}", duration=3.0)


def test_spherical_flat_filter_uses_signed_yaw_for_saved_singer_value():
    graph = _export_source_filter({"projection": "equirect"}, {"type": "singer", "yaw": 336.8, "pitch": -28.8, "fov": 74.8})

    assert "yaw=-23.200" in graph
    assert "pitch=-28.800" in graph
    assert "h_fov=74.800" in graph
    assert "v_fov=46.542" in graph
    assert "interp=lanczos" in graph


def test_v360_sendcmd_always_pairs_horizontal_and_vertical_fov():
    commands = _v360_motion_commands({"type": "audience", "yaw": 10, "pitch": 0, "fov": 95, "fov_delta_deg": 6}, 1.0)

    assert any(f" {SPHERE_V360_LABEL} h_fov " in command for command in commands)
    assert any(f" {SPHERE_V360_LABEL} v_fov " in command for command in commands)
    assert sum(f" {SPHERE_V360_LABEL} h_fov " in command for command in commands) == sum(f" {SPHERE_V360_LABEL} v_fov " in command for command in commands)


def test_paired_flat_fov_uses_projection_math_for_16x9():
    h_fov, v_fov = _paired_flat_fov(150.0, 16.0 / 9.0)

    assert h_fov == 150.0
    assert round(v_fov, 3) == 129.058


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
def test_export_join_normalizes_mixed_fps_segments(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("core.stages.export.measure_clip_color", lambda path: ({}, None))
    monkeypatch.setattr("core.stages.export._watermark_path", lambda: None)
    monkeypatch.setattr("core.stages.export._ffmpeg_supports_filter", lambda name: False)
    project = create_project("Mixed FPS", str(tmp_path / "Mixed FPS.zuckervid"))
    master = tmp_path / "master.wav"
    clip_25 = tmp_path / "sony25.mp4"
    clip_30 = tmp_path / "phone30.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=6", str(master)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_25)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip_30)],
        check=True,
        capture_output=True,
        text=True,
    )
    record_25 = file_record(str(clip_25))
    record_25.update(
        {
            "probe": {"valid_video": True, "video_codec": "h264", "duration": 6.0, "width": 640, "height": 360, "fps": 25.0, "cfr": True},
            "cache_key": "sony25-key",
            "normalized": {"path": str(clip_25), "cache_key": "sony25-key", "kind": "original", "proxy_skipped": True},
        }
    )
    record_30 = file_record(str(clip_30))
    record_30.update(
        {
            "probe": {"valid_video": True, "video_codec": "h264", "duration": 6.0, "width": 640, "height": 360, "fps": 30.0, "cfr": True},
            "cache_key": "phone30-key",
            "normalized": {"path": str(clip_30), "cache_key": "phone30-key", "kind": "original", "proxy_skipped": True},
        }
    )
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record_25, record_30]
    write_artifact_json(
        project.artifacts_dir / "edit_plan.json",
        {
            "platform": "youtube",
            "segments": [
                {"filename": "sony25.mp4", "clip_path": str(clip_25), "source_path": str(clip_25), "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 1.5},
                {"filename": "phone30.mp4", "clip_path": str(clip_30), "source_path": str(clip_30), "clip_start_sec": 0, "master_start_sec": 1.5, "duration_sec": 1.5},
                {"filename": "sony25.mp4", "clip_path": str(clip_25), "source_path": str(clip_25), "clip_start_sec": 1.5, "master_start_sec": 3.0, "duration_sec": 1.5},
                {"filename": "phone30.mp4", "clip_path": str(clip_30), "source_path": str(clip_30), "clip_start_sec": 1.5, "master_start_sec": 4.5, "duration_sec": 1.5},
            ],
        },
    )

    ExportStage().run(project, lambda percent, message: None)

    manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
    output = Path(manifest["exports"][0]["path"])
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,r_frame_rate,time_base,pix_fmt",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["avg_frame_rate"] == "30/1"
    assert stream["r_frame_rate"] == "30/1"
    assert stream["pix_fmt"] == "yuv420p"
    pts = _frame_pts_times(output, 1.45, 0.3)
    deltas = [later - earlier for earlier, later in zip(pts, pts[1:])]
    assert deltas
    assert all(delta > 0 for delta in deltas)
    assert min(deltas) >= (1 / TARGET_EXPORT_FPS) * 0.5
    _verify_video_cadence(output, "mixed-fps final", duration=6.0)
    _verify_moving_segment(output, 5.5, "mixed-fps final", "joined output")


def test_copy_trim_video_uses_stream_copy_not_a_reencode(tmp_path, monkeypatch):
    """White-box check on the trim step itself: it must ask ffmpeg to copy
    the video stream (no bitrate/encoder flags), which is what actually makes
    this mode a passthrough rather than a fast re-encode.
    """
    from core.stages.export import _copy_trim_video

    commands = []
    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", lambda command, duration, label, progress: commands.append(command))

    _copy_trim_video("/tmp/source.mp4", tmp_path / "body.mp4", 5.0, 8.0, None)

    command = commands[0]
    assert command[command.index("-c") + 1] == "copy"
    assert "-b:v" not in command
    assert "-crf" not in command
    assert command[command.index("-map") + 1] == "0:v:0"
    assert "-an" in command
    # -ss before -i (input-side seek), not after -- see _copy_trim_video's
    # docstring for why the post-input form silently miscounts duration.
    assert command.index("-ss") < command.index("-i")


@pytest.mark.slow
def test_360_export_is_a_true_passthrough_not_a_reencode(tmp_path, monkeypatch):
    """360 mode must only trim + swap audio + add intro/outro -- never
    reproject, cut, or apply motion. Verifies: correct total duration (trim
    range + intro/outro, not the camera's own full recording length), the
    final audio is the MASTER track (not the camera's own on-board audio),
    and spherical metadata survives. (The trim step's use of a real stream
    copy, not a re-encode, is verified separately at the command level in
    test_copy_trim_video_uses_stream_copy_not_a_reencode.)
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Passthrough360", str(tmp_path / "Passthrough360.zuckervid"))
    master = tmp_path / "master.wav"
    source = tmp_path / "360source.mp4"
    # Distinct tones so we can tell master audio apart from the source's own audio.
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=60", str(master)], check=True, capture_output=True, text=True)
    # A short GOP mirrors real camera footage (long GOPs are unusual for
    # handheld/action cams) and keeps the input-seek keyframe snap small.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x320:rate=25:duration=20",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=20",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    record = file_record(str(source))
    record.update({"projection": "equirect", "probe": {"valid_video": True, "projection": "equirect", "duration": 20.0, "width": 640, "height": 320, "fps": 25.0, "video_codec": "h264"}})
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "360"}
    clip_start = 5.0
    body_duration = 8.0
    write_artifact_json(
        project.artifacts_dir / "edit_plan.json",
        {
            "platform": "360",
            "segments": [
                {
                    "filename": "360source.mp4",
                    "clip_path": str(source),
                    "source_path": str(source),
                    "clip_start_sec": clip_start,
                    "master_start_sec": 15.0,
                    "duration_sec": body_duration,
                    "projection": "equirect",
                }
            ],
        },
    )

    ExportStage().run(project, lambda percent, message: None)

    manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
    export_entry = manifest["exports"][0]
    output = Path(export_entry["path"])
    assert output.exists()
    assert any("passthrough" in warning for warning in export_entry["warnings"])
    assert any("watermark is skipped" in warning for warning in export_entry["warnings"])

    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(output)], check=True, capture_output=True, text=True).stdout.strip())
    assert duration == pytest.approx(body_duration + 10.2 + 10.2, abs=1.5)

    # Final audio must be the MASTER track (880 Hz), not the source clip's
    # own on-board audio (220 Hz).
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream_tags=projection,spherical_video:format_tags=projection,spherical_video", "-of", "json", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "equirectangular" in probe.stdout
    assert _audio_rms(output, 15.0, 1.0) > 0.01

    # The cosmetic -metadata tags above are NOT enough for real players to
    # recognize the file as 360 (ffmpeg drops the real box structures on
    # stream copy) -- so the export pipeline injects real Google Spherical
    # Video V2 boxes as a final stage. Verify those boxes actually exist,
    # both at the raw byte level and via ffprobe's structured side_data,
    # which is what an ffmpeg-based player actually consults.
    output_bytes = output.read_bytes()
    assert b"sv3d" in output_bytes
    assert b"st3d" in output_bytes
    side_data_probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(output)],
        check=True,
        capture_output=True,
        text=True,
    )
    streams = json.loads(side_data_probe.stdout)["streams"]
    side_data_types = {
        entry.get("side_data_type")
        for stream in streams
        for entry in (stream.get("side_data_list") or [])
    }
    assert "Spherical Mapping" in side_data_types


def test_copy_trim_video_measures_duration_from_the_snapped_keyframe(tmp_path, monkeypatch):
    """The 360 End trim must not be eaten by the input-side keyframe snap.

    ffmpeg's pre-input -ss lands on the keyframe at or before the requested
    start. Asking for exactly `duration` seconds from there ends the output up
    to one GOP EARLY -- the reported "360 export cuts the song early". The
    duration must therefore be measured from the snapped keyframe.
    """
    from core.stages.export import _copy_trim_video

    monkeypatch.setattr("core.stages.export._preceding_keyframe", lambda path, start: 6.0)
    commands = []
    monkeypatch.setattr("core.stages.export._run_ffmpeg_progress", lambda command, duration, label, progress: commands.append(command))

    _copy_trim_video("/tmp/source.mp4", tmp_path / "body.mp4", 8.0, 10.0, None)

    command = commands[0]
    # Requested 8.0 -> 18.0, but ffmpeg starts at the 6.0 keyframe, so it must
    # copy 12.0s (not 10.0s) to still contain the requested end instant.
    assert float(command[command.index("-t") + 1]) == pytest.approx(12.0, abs=0.01)


def test_outro_extends_so_the_trimmed_song_can_finish_but_never_past_the_end_trim():
    """L-cut at the end: remaining music plays under the outro logo.

    "Remaining music" is bounded by the user's End trim, never by the master
    file's full length -- otherwise the outro would play back audio the user
    deliberately trimmed away.
    """
    from core.stages.export import MAX_OUTRO_DURATION, OUTRO_DURATION, _outro_duration_for_remaining_music

    # Picture ends at timeline 40s (== master 40s here); End trim at 65s
    # leaves 25s of song, so the outro stretches to cover it.
    assert _outro_duration_for_remaining_music("/tmp/m.wav", 0.0, 0.0, 40.0, song_end_sec=65.0) == pytest.approx(25.0)
    # Only a couple of seconds left: the outro keeps its normal length.
    assert _outro_duration_for_remaining_music("/tmp/m.wav", 0.0, 0.0, 40.0, song_end_sec=43.0) == OUTRO_DURATION
    # Song already finished before the picture did.
    assert _outro_duration_for_remaining_music("/tmp/m.wav", 0.0, 0.0, 40.0, song_end_sec=30.0) == OUTRO_DURATION
    # Never unbounded.
    assert _outro_duration_for_remaining_music("/tmp/m.wav", 0.0, 0.0, 40.0, song_end_sec=9999.0) == MAX_OUTRO_DURATION
    # Unknown End trim must NOT fall back to the master file's length.
    assert _outro_duration_for_remaining_music("/tmp/m.wav", 0.0, 0.0, 40.0, song_end_sec=None) == OUTRO_DURATION


@pytest.mark.slow
def test_360_export_of_hevc_source_decodes_cleanly_not_black(tmp_path, monkeypatch):
    """Regression test for a real "black frame with audio" bug: many 360
    cameras record HEVC tagged 'hev1', and even after fixing that tag, a
    stream-copied concat of the (independently-encoded) intro/outro logos
    with an HEVC body produces a file whose decoder can't resolve reference
    pictures across the encoder boundary -- confirmed empirically (ffmpeg
    throws "Could not find ref with POC N" / "Error constructing the frame
    RPS" throughout the body) even though intro, body, and outro each decode
    perfectly fine on their own. The fix re-encodes only the join step for
    HEVC sources. This test decodes the real exported file end-to-end and
    fails if ffmpeg reports any reference/frame construction errors -- the
    exact symptom a user would see as a black screen with sound.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Hevc360", str(tmp_path / "Hevc360.zuckervid"))
    master = tmp_path / "master.wav"
    source = tmp_path / "360source_hevc.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=20", str(master)], check=True, capture_output=True, text=True)
    # Many non-Apple 360/action cameras tag their HEVC video 'hev1' rather
    # than 'hvc1' -- reproduce that here rather than assuming 'hvc1'.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x320:rate=25:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=8",
            "-c:v", "libx265", "-tag:v", "hev1", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    record = file_record(str(source))
    record.update({"projection": "equirect", "probe": {"valid_video": True, "projection": "equirect", "duration": 8.0, "width": 640, "height": 320, "fps": 25.0, "video_codec": "hevc"}})
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "360"}
    write_artifact_json(
        project.artifacts_dir / "edit_plan.json",
        {
            "platform": "360",
            "segments": [
                {
                    "filename": "360source_hevc.mp4",
                    "clip_path": str(source),
                    "source_path": str(source),
                    "clip_start_sec": 0.0,
                    "master_start_sec": 10.0,
                    "duration_sec": 5.0,
                    "projection": "equirect",
                }
            ],
        },
    )

    ExportStage().run(project, lambda percent, message: None)

    manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
    export_entry = manifest["exports"][0]
    output = Path(export_entry["path"])
    assert output.exists()
    assert any("re-encoded once when joining" in warning for warning in export_entry["warnings"])

    tag = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_tag_string", "-of", "default=nw=1:nk=1", str(output)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert tag == "hvc1"

    # The actual regression check: decode the whole file and fail on any
    # reference/frame-construction error -- this is precisely what produces
    # a black picture with working audio in a real player.
    decode = subprocess.run(
        ["ffmpeg", "-y", "-i", str(output), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    lowered = decode.stderr.lower()
    assert "could not find ref" not in lowered
    assert "error constructing the frame rps" not in lowered


@pytest.mark.slow
def test_360_export_fails_loudly_when_spherical_metadata_verification_fails(tmp_path, monkeypatch):
    """If the spherical metadata injector ever produces a file that fails
    verification (a regression, a corrupt box, an ffprobe that can't see the
    side_data), the export must hard-fail rather than silently ship a 360
    file that plays as a flat rectangle -- this is the automated guard
    requested so this class of bug can never ship silently again.
    """
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    from core.ffmpeg import FFmpegError
    from core.spherical_metadata import SphericalMetadataError

    def _boom(*_args, **_kwargs):
        raise SphericalMetadataError("simulated verification failure")

    monkeypatch.setattr("core.stages.export.inject_spherical_metadata", _boom)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    project = create_project("Passthrough360Fail", str(tmp_path / "Passthrough360Fail.zuckervid"))
    master = tmp_path / "master.wav"
    source = tmp_path / "360source.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=10", str(master)], check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x320:rate=25:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    record = file_record(str(source))
    record.update({"projection": "equirect", "probe": {"valid_video": True, "projection": "equirect", "duration": 5.0, "width": 640, "height": 320, "fps": 25.0, "video_codec": "h264"}})
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    project.data["settings"]["wizard"] = {"platform": "360"}
    write_artifact_json(
        project.artifacts_dir / "edit_plan.json",
        {
            "platform": "360",
            "segments": [
                {
                    "filename": "360source.mp4",
                    "clip_path": str(source),
                    "source_path": str(source),
                    "clip_start_sec": 0.0,
                    "master_start_sec": 5.0,
                    "duration_sec": 3.0,
                    "projection": "equirect",
                }
            ],
        },
    )

    with pytest.raises(FFmpegError, match="spherical metadata verification"):
        ExportStage().run(project, lambda percent, message: None)


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


@pytest.mark.slow
def test_fractional_segment_export_keeps_audio_video_duration_and_markers_in_sync(tmp_path, monkeypatch):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr("core.stages.export.measure_clip_color", lambda path: ({}, None))
    monkeypatch.setattr("core.stages.export._watermark_path", lambda: None)
    monkeypatch.setattr("core.stages.export._ffmpeg_supports_filter", lambda name: False)
    project = create_project("Drift Guard", str(tmp_path / "Drift Guard.zuckervid"))
    master = tmp_path / "master.wav"
    clip = tmp_path / "clip.mp4"
    raw_durations = [2.03 if index % 2 == 0 else 3.07 for index in range(22)]
    segments = []
    cursor = 0.0
    marker_times = []
    marker_offset = 1.0
    for index, duration in enumerate(raw_durations):
        marker_times.append(cursor + marker_offset)
        segments.append({"filename": "clip.mp4", "clip_path": str(clip), "source_path": str(clip), "clip_start_sec": cursor, "master_start_sec": cursor, "duration_sec": duration})
        cursor += duration
    normalized = _frame_normalized_segments(segments)
    source_duration = cursor + 8.0
    _write_marker_master(master, source_duration, marker_times)
    drawboxes = ",".join(f"drawbox=enable='between(t,{time:.3f},{time + 0.100:.3f})':x=0:y=0:w=iw:h=ih:color=white@1:t=fill" for time in marker_times)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=30:duration={source_duration:.3f}",
            "-vf",
            drawboxes,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    record = file_record(str(clip))
    record["probe"] = {"valid_video": True, "video_codec": "h264", "duration": source_duration, "width": 640, "height": 360, "fps": 30.0, "cfr": True}
    record["cache_key"] = "drift-guard"
    record["normalized"] = {"path": str(clip), "cache_key": "drift-guard", "kind": "original", "proxy_skipped": True}
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [record]
    write_artifact_json(project.artifacts_dir / "edit_plan.json", {"platform": "youtube", "segments": segments})

    ExportStage().run(project, lambda percent, message: None)

    output = Path(json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))["exports"][0]["path"])
    streams = _probe_stream_durations(output)
    assert abs(streams["video"] - streams["audio"]) <= 1.0 / TARGET_EXPORT_FPS
    for index in (0, 9, len(normalized) - 1):
        timeline = 10.2 + sum(float(segment["duration_sec"]) for segment in normalized[:index]) + marker_offset
        assert _audio_rms(output, timeline - 0.025, 0.050) > 0.20
        assert _frame_luma(output, timeline) > 150.0
    _verify_video_cadence(output, "drift guard final", duration=streams["video"])


@pytest.mark.slow
def test_final_audio_verifier_allows_source_level_change_and_smooth_fade_stack(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    master = tmp_path / "master.wav"
    output = tmp_path / "output.mp4"
    source_duration = 30.0
    _write_step_master(master, source_duration, quiet_after=23.467)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=640x360:rate=30:duration={source_duration:.3f}",
            "-i",
            str(master),
            "-vf",
            "fps=30,setpts=N/(30*TB),format=yuv420p",
            "-af",
            f"afade=t=in:st=0.000:d=1.500,afade=t=out:st={source_duration - 1.5:.3f}:d=1.500",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    segments = [
        {"duration_sec": 5.0, "master_start_sec": 0.0},
        {"duration_sec": 4.0, "master_start_sec": 5.0},
        {"duration_sec": 4.267, "master_start_sec": 9.0},
        {"duration_sec": 3.0, "master_start_sec": 13.267},
    ]

    _verify_final_audio(
        output,
        segments,
        str(master),
        audio_start=0.0,
        timeline_offset=10.2,
        duration=source_duration,
        content_start=10.2,
        content_end=source_duration,
    )
    curve = _audio_gain_curve_samples(source_duration, 10.2, source_duration)
    fade_in = [gain for timestamp, gain in curve if 0.0 <= timestamp <= 1.5]
    steady = [gain for timestamp, gain in curve if 1.5 < timestamp < source_duration - 1.5]
    assert fade_in == sorted(fade_in)
    assert all(gain == pytest.approx(1.0) for gain in steady)


def _write_marker_master(path: Path, duration: float, marker_times: list[float]) -> None:
    sample_rate = 48_000
    markers = {int(time * sample_rate) for time in marker_times}
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        frames = bytearray()
        blip_len = int(0.08 * sample_rate)
        total = int(duration * sample_rate)
        for index in range(total):
            value = 0.06 * math.sin(2 * math.pi * 220 * index / sample_rate)
            if any(start <= index < start + blip_len for start in markers):
                value += 0.85 * math.sin(2 * math.pi * 1200 * index / sample_rate)
            sample = max(-1.0, min(1.0, value))
            frames.extend(int(sample * 32767).to_bytes(2, "little", signed=True))
        fh.writeframes(bytes(frames))


def _write_step_master(path: Path, duration: float, quiet_after: float) -> None:
    sample_rate = 48_000
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        frames = bytearray()
        total = int(duration * sample_rate)
        quiet_index = int(quiet_after * sample_rate)
        for index in range(total):
            amplitude = 0.24 if index < quiet_index else 0.015
            value = amplitude * math.sin(2 * math.pi * 440 * index / sample_rate)
            frames.extend(int(value * 32767).to_bytes(2, "little", signed=True))
        fh.writeframes(bytes(frames))


def _make_silent_wav(path: Path, duration: float) -> None:
    sample_rate = 48_000
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sample_rate)
        fh.writeframes(b"\0\0" * int(sample_rate * duration))


def _make_test_video(path: Path, duration: float, fps: float, size: str) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={duration}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _probe_stream_durations(path: Path) -> dict[str, float]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    durations = {}
    for stream in json.loads(result.stdout)["streams"]:
        if stream.get("codec_type") in {"video", "audio"}:
            durations[stream["codec_type"]] = float(stream["duration"])
    return durations


def test_reel_overlay_global_timing_and_position_are_converted_per_segment(tmp_path: Path) -> None:
    from PIL import Image

    flyer = tmp_path / "flyer.png"
    Image.new("RGBA", (120, 80), (255, 0, 0, 255)).save(flyer)
    config = {
        "reel_origin_sec": 0.0,
        "reel_texts": [{
            "text": "MIDDLE", "x": 0.18, "y": 0.22, "size": 54,
            "color": "#ffffff", "start_sec": 10.0, "duration_sec": 4.0,
            "outline_color": "#ff0000", "outline_width": 3,
            "shadow_color": "#000000", "shadow_blur": 2,
        }],
        "reel_images": [{"path": str(flyer), "x": 0.75, "y": 0.7, "width": 0.2, "start_sec": 11.0, "duration_sec": 2.0}],
    }
    assert _reel_overlay_items({"master_start_sec": 0.0, "duration_sec": 8.0}, config, "reel", tmp_path) == []
    items = _reel_overlay_items({"master_start_sec": 8.0, "duration_sec": 8.0}, config, "reel", tmp_path)
    assert len(items) == 2
    assert items[0]["start_sec"] == pytest.approx(2.0)
    assert items[0]["end_sec"] == pytest.approx(6.0)
    assert items[1]["start_sec"] == pytest.approx(3.0)
    assert items[1]["end_sec"] == pytest.approx(5.0)
    alpha = Image.open(items[0]["path"]).getchannel("A")
    bbox = alpha.getbbox()
    assert bbox is not None
    assert bbox[0] < 0.25 * 1080 and bbox[1] < 0.3 * 1920


def _frame_luma(path: Path, timestamp: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(path),
            "-vf",
            "signalstats,metadata=print",
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
    match = re.search(r"lavfi.signalstats.YAVG=([0-9.]+)", f"{result.stdout}\n{result.stderr}")
    return float(match.group(1)) if match else 0.0
