from __future__ import annotations

from core.stages.edit import estimate_bar_starts, _youtube_multicam_plan


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
    assert {segment["master_start_sec"] for segment in plan["segments"]}.issubset({0.0, 4.0})
    assert plan["cut_count"] == 1
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
