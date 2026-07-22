from __future__ import annotations

from core.stages.edit import MAX_SEGMENT_SEC, MIN_SEGMENT_SEC, build_spherical_shot_segments, estimate_bar_starts, migrate_spherical_landmarks, _youtube_multicam_plan
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


def test_youtube_plan_role_weight_zero_excludes_role():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 12.0},
        "sources": [
            {"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 8.0},
            {"path": "/tmp/sony.mp4", "filename": "sony.mp4", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 10.0},
            {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 9.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats, {"edit": {"camera_role_weights": {"360": 0, "handheld": 1, "fixed_rear": 0}}})

    assert set(plan["camera_usage"]) == {"sony.mp4"}


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


def test_spherical_landmark_map_allows_missing_entries_without_crash():
    base = [{"clip_path": "/tmp/360.mp4", "source_path": "/tmp/360.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 10, "projection": "equirect"}]

    segments = build_spherical_shot_segments(base, {"singer_yaw": 35})

    types = [segment["spherical_shot"]["type"] for segment in segments]
    assert "singer" in types
    assert "drummer" not in types
    assert "full_stage" in types


def test_spherical_rotation_avoids_repeats_and_caps_planet():
    base = [{"clip_path": "/tmp/360.mp4", "source_path": "/tmp/360.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 28, "projection": "equirect"}]

    segments = build_spherical_shot_segments(base, {"singer_yaw": 20, "drummer_yaw": 110, "audience_yaw": 250})

    types = [segment["spherical_shot"]["type"] for segment in segments]
    assert all(a != b for a, b in zip(types, types[1:]) if a != "planet" and b != "planet")
    assert types.count("planet") <= 1
    assert all(MIN_SEGMENT_SEC <= segment["duration_sec"] <= MAX_SEGMENT_SEC for segment in segments)


def test_spherical_landmark_schema_migrates_yaw_only_values():
    migrated = migrate_spherical_landmarks({"singer_yaw": -23.2})

    assert migrated["singer"]["yaw"] == 336.8
    assert migrated["singer"]["pitch"] == 0.0
    assert migrated["singer"]["fov"] == 74.8


def test_spherical_landmark_schema_preserves_saved_singer_values():
    migrated = migrate_spherical_landmarks({"singer": {"yaw": 336.8, "pitch": -28.8, "fov": 74.8, "weight": 1.0}})

    assert migrated["singer"] == {"yaw": 336.8, "pitch": -28.8, "fov": 74.8, "weight": 1.0}


def test_spherical_weights_exclude_zero_and_bias_frequency():
    base = [{"clip_path": "/tmp/360.mp4", "source_path": "/tmp/360.mp4", "clip_start_sec": 0, "master_start_sec": 0, "duration_sec": 60, "projection": "equirect"}]

    segments = build_spherical_shot_segments(
        base,
        {
            "singer": {"yaw": 20, "pitch": 0, "fov": 74.8, "weight": 4},
            "left": {"yaw": 90, "pitch": 0, "fov": 74.8, "weight": 1},
            "audience": {"yaw": 180, "pitch": 0, "fov": 74.8, "weight": 0},
        },
    )

    types = [segment["spherical_shot"]["type"] for segment in segments]
    assert "audience" not in types
    assert types.count("singer") > types.count("left")


def test_real_like_right_side_zero_weight_never_appears_in_youtube_plan():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 24.0},
        "sources": [{"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 8.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0], "sections_sec": []}
    landmarks = {
        "full_stage": {"yaw": 11.5, "pitch": -34.3, "fov": 110.0, "weight": 50.0},
        "singer": {"yaw": 336.8, "pitch": -28.8, "fov": 74.8, "weight": 10.0},
        "left": {"yaw": 47.6, "pitch": -15.9, "fov": 74.8, "weight": 30.0},
        "right": {"yaw": 322.1, "pitch": -15.0, "fov": 74.8, "weight": 0.0},
    }

    plan = _youtube_multicam_plan(coverage, beats, {"spherical_landmarks": landmarks})

    types = [(segment.get("spherical_shot") or {}).get("type") for segment in plan["segments"]]
    assert "right" not in types
    assert {"full_stage", "singer", "left"}.issubset(set(types))


def test_fixed_rear_segments_get_subtle_motion_on_some_holds():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 12.0},
        "sources": [{"path": "/tmp/iphone.mov", "filename": "iphone.mov", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 9.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    motions = [segment.get("motion") for segment in plan["segments"] if segment.get("motion")]
    assert motions
    assert all(motion["type"] == "ken_burns" for motion in motions)


def test_youtube_plan_assigns_spherical_shots_to_360_segments():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [{"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats, {"spherical_landmarks": {"singer": {"yaw": 10, "weight": 1}, "left": {"yaw": 90, "weight": 1}}})

    assert any(segment.get("spherical_shot") for segment in plan["segments"])
    assert plan["spherical_shot_usage"]
