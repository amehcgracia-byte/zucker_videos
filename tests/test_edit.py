from __future__ import annotations

from core.stages.edit import MAX_SEGMENT_SEC, MIN_SEGMENT_SEC, estimate_bar_starts, _youtube_multicam_plan
from core.stages.cut import _segment_for_360, _select_360_clip


def test_estimate_bar_starts_groups_beats_in_fours():
    beats = [index * 0.5 for index in range(17)]

    bars = estimate_bar_starts(beats, 0.0, 8.0)

    assert bars[:5] == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_youtube_plan_excludes_missing_sources_and_cuts_on_bars():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [
            {"path": "/tmp/a.mp4", "filename": "a.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0},
            {"path": "/tmp/b.mp4", "filename": "b.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0},
        ],
        "excluded_clips": [{"filename": "bad.mp4", "reason": "questionable sync — excluded"}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": [4.0]}

    plan = _youtube_multicam_plan(coverage, beats)

    assert plan["excluded_clips"][0]["filename"] == "bad.mp4"
    assert {segment["master_start_sec"] for segment in plan["segments"]}.issubset({0.0, 2.0, 4.0, 6.0})
    assert plan["cut_count"] == 2
    assert all("eligible_segments" in item for item in plan["selection_diagnostics"])


def test_youtube_plan_does_not_starve_lower_confidence_camera():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 24.0},
        "sources": [
            {"path": "/tmp/a.mp4", "filename": "a.mp4", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 10.0},
            {"path": "/tmp/b.mp4", "filename": "b.mp4", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 9.0},
            {"path": "/tmp/c.mp4", "filename": "c.mp4", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 8.0},
        ],
    }
    beats = {"bars_sec": [0.0, 4.0, 8.0, 12.0, 16.0, 20.0, 24.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    names = {segment["filename"] for segment in plan["segments"]}
    assert names == {"a.mp4", "b.mp4", "c.mp4"}
    stats = {item["filename"]: item for item in plan["selection_diagnostics"]}
    assert stats["c.mp4"]["chosen_segments"] > 0


def test_youtube_plan_role_weights_bias_eligible_camera_share():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 24.0},
        "sources": [
            {"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 8.0},
            {"path": "/tmp/sony.mp4", "filename": "sony.mp4", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 10.0},
            {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 9.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    usage = plan["camera_usage"]
    assert usage["360.mp4"] >= usage["sony.mp4"] >= usage["iphone.mov"]


def test_youtube_plan_segment_lengths_stay_within_bounds():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 18.0},
        "sources": [{"path": "/tmp/a.mp4", "filename": "a.mp4", "offset_sec": 0.0, "duration_sec": 18.0, "confidence": 10.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 8.0, 12.0, 16.0, 18.0], "sections_sec": [8.0]}

    plan = _youtube_multicam_plan(coverage, beats)

    durations = [segment["duration_sec"] for segment in plan["segments"]]
    assert all(MIN_SEGMENT_SEC <= duration <= MAX_SEGMENT_SEC for duration in durations)
    assert len(set(durations)) > 1


def test_360_selection_prefers_studio_export_and_uses_full_clip():
    clips = [
        {"path": "/tmp/raw.insv", "filename": "raw.insv", "projection": "raw_insv", "offset_sec": 3.0, "duration_sec": 10.0},
        {"path": "/tmp/studio.mp4", "filename": "studio.mp4", "projection": "equirect", "offset_sec": 5.0, "duration_sec": 12.0},
    ]

    clip = _select_360_clip(clips)
    segment = _segment_for_360(clip)

    assert clip["filename"] == "studio.mp4"
    assert segment["clip_start_sec"] == 0.0
    assert segment["master_start_sec"] == 5.0
    assert segment["duration_sec"] == 12.0
    assert segment["projection"] == "equirect"
