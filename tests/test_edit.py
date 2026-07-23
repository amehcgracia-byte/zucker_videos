from __future__ import annotations

from core.stages.edit import MAX_SEGMENT_SEC, MIN_SEGMENT_SEC, build_spherical_shot_segments, estimate_bar_starts, migrate_spherical_landmarks, _ken_burns_motion, _spherical_motion_profile, _youtube_multicam_plan, _framing_nearly_identical
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


def test_spherical_landmark_migration_accepts_comma_decimal_and_zero_weight():
    migrated = migrate_spherical_landmarks({"singer": {"yaw": "-23,2", "pitch": "-28,8", "fov": "74,8", "weight": "1"}, "right": {"yaw": "322,1", "weight": "0"}})

    assert migrated["singer"]["yaw"] == 336.8
    assert migrated["singer"]["pitch"] == -28.8
    assert migrated["right"]["yaw"] == 322.1
    assert migrated["right"]["weight"] == 0.0


def test_spherical_motion_profile_never_leaves_a_shot_frozen():
    # General rule: a static-source segment (here, every non-planet 360
    # landmark type) always gets a clearly perceptible drift on at least one
    # axis -- never all three near zero, which would look like a frozen hold.
    for shot_type in ("singer", "left", "right", "audience", "full_stage", "audience_stage_wide", "unknown_type", ""):
        for index in range(6):
            shot = _spherical_motion_profile({"type": shot_type, "yaw": 10.0, "pitch": -15.0, "fov": 95}, index)
            magnitudes = [abs(shot["drift_yaw_deg"]), abs(shot["drift_pitch_deg"]), abs(shot["fov_delta_deg"])]
            assert max(magnitudes) >= 2.0, (shot_type, index, shot)


def test_spherical_motion_profile_varies_across_instances():
    # Same shot type at different segment indices should not all move the
    # same fixed way -- this is what makes it "randomized per instance"
    # rather than a hardcoded per-type direction.
    profiles = [_spherical_motion_profile({"type": "singer", "yaw": 10.0, "pitch": -15.0, "fov": 95}, index) for index in range(8)]
    signatures = {(p["drift_yaw_deg"], p["drift_pitch_deg"], p["fov_delta_deg"]) for p in profiles}
    assert len(signatures) > 1


def test_spherical_motion_profile_is_deterministic_for_cache_stability():
    first = _spherical_motion_profile({"type": "left", "yaw": 47.6, "pitch": -15.9, "fov": 95}, 2)
    second = _spherical_motion_profile({"type": "left", "yaw": 47.6, "pitch": -15.9, "fov": 95}, 2)
    assert first == second


def test_spherical_motion_profile_keeps_planet_spin_untouched():
    planet = _spherical_motion_profile({"type": "planet", "yaw": 6, "pitch": -23.5, "fov": 150}, 7)
    assert planet["projection"] == "tiny_planet"
    assert planet["pitch"] == -90.0


def test_spherical_motion_profile_treats_missing_shot_as_a_moving_hold():
    # When no landmark shot is available at all (e.g. nothing configured),
    # the segment must still get motion rather than a frozen passthrough.
    shot = _spherical_motion_profile({}, 3)
    magnitudes = [abs(shot["drift_yaw_deg"]), abs(shot["drift_pitch_deg"]), abs(shot["fov_delta_deg"])]
    assert max(magnitudes) >= 3.0


def test_youtube_plan_prefers_recorded_360_curve_when_segment_is_covered():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [
            {"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}
    move = {
        "name": "Main take",
        "smoothed": [{"t": index / 10, "yaw": index * 2, "pitch": -10, "fov": 100} for index in range(0, 81)],
    }

    plan = _youtube_multicam_plan(coverage, beats, {"edit": {"spherical_mode": "directed"}}, recorded_moves=[move])

    assert plan["segments"]
    assert {segment["spherical_shot"]["type"] for segment in plan["segments"]} == {"recorded_move"}
    assert plan["spherical_recording_usage"]["recorded_segments"] == len(plan["segments"])
    assert plan["spherical_recording_usage"]["landmark_segments"] == 0


def test_youtube_plan_automatic_mode_ignores_recorded_360_curve():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [
            {"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}
    move = {"name": "Main take", "smoothed": [{"t": index / 10, "yaw": index, "pitch": -10, "fov": 100} for index in range(0, 81)]}

    plan = _youtube_multicam_plan(coverage, beats, {"spherical_landmarks": {"singer": {"yaw": 336.8, "weight": 1}}}, recorded_moves=[move])

    assert {segment["spherical_shot"]["type"] for segment in plan["segments"]} == {"full_stage", "singer"}
    assert plan["spherical_recording_usage"]["recorded_segments"] == 0


def test_fixed_camera_motion_varies_target_direction_and_zoom_direction():
    motions = [_ken_burns_motion(index) for index in range(20)]
    targets = {(motion["pan_x"], motion["pan_y"]) for motion in motions}
    zoom_in = [motion for motion in motions if motion["zoom_end"] > motion["zoom_start"]]
    zoom_out = [motion for motion in motions if motion["zoom_end"] < motion["zoom_start"]]

    assert len(targets) >= 3
    assert zoom_in
    assert zoom_out
    assert max(abs(motion["zoom_end"] - motion["zoom_start"]) for motion in motions) >= 0.09


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


# ---------------------------------------------------------------------------
# Issue C: consecutive near-identical shot detection
# ---------------------------------------------------------------------------


def test_framing_nearly_identical_different_sources_always_false():
    a = {"source": "/a.mp4", "type": "fixed_rear"}
    b = {"source": "/b.mp4", "type": "fixed_rear"}
    assert not _framing_nearly_identical(a, b)


def test_framing_nearly_identical_same_source_same_role_is_true():
    a = {"source": "/iphone.mov", "type": "fixed_rear"}
    b = {"source": "/iphone.mov", "type": "fixed_rear"}
    assert _framing_nearly_identical(a, b)


def test_framing_nearly_identical_recorded_move_is_always_false():
    a = {"source": "/360.mp4", "type": "recorded_move", "take": "Take 1"}
    b = {"source": "/360.mp4", "type": "recorded_move", "take": "Take 1"}
    assert not _framing_nearly_identical(a, b)


def test_framing_nearly_identical_spherical_same_shot_within_thresholds():
    a = {"source": "/360.mp4", "type": "spherical", "shot": "singer", "yaw": 100.0, "fov": 95.0}
    b = {"source": "/360.mp4", "type": "spherical", "shot": "singer", "yaw": 102.0, "fov": 96.0}
    assert _framing_nearly_identical(a, b)


def test_framing_nearly_identical_spherical_different_shot_type():
    a = {"source": "/360.mp4", "type": "spherical", "shot": "singer", "yaw": 100.0, "fov": 95.0}
    b = {"source": "/360.mp4", "type": "spherical", "shot": "drummer", "yaw": 102.0, "fov": 95.0}
    assert not _framing_nearly_identical(a, b)


def test_framing_nearly_identical_spherical_large_yaw_shift():
    a = {"source": "/360.mp4", "type": "spherical", "shot": "singer", "yaw": 100.0, "fov": 95.0}
    b = {"source": "/360.mp4", "type": "spherical", "shot": "singer", "yaw": 115.0, "fov": 95.0}
    assert not _framing_nearly_identical(a, b)


def test_youtube_plan_avoids_consecutive_identical_spherical_shots():
    """When only one 360 source is available, consecutive segments must differ in shot type."""
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 24.0},
        "sources": [
            {"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 8.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0], "sections_sec": []}
    settings = {"spherical_landmarks": {"singer": {"yaw": 0.0, "weight": 1}, "drummer": {"yaw": 90.0, "weight": 1}}}

    plan = _youtube_multicam_plan(coverage, beats, settings)

    # No two consecutive 360 segments should have identical shot types.
    segs_360 = [s for s in plan["segments"] if s.get("spherical_shot")]
    consecutive_same_type = sum(
        1 for i in range(1, len(segs_360))
        if segs_360[i]["spherical_shot"].get("type") == segs_360[i - 1]["spherical_shot"].get("type")
        and segs_360[i]["spherical_shot"].get("type") not in {"recorded_move"}
    )
    assert consecutive_same_type == 0, f"Found {consecutive_same_type} consecutive identical spherical shot pairs"


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
    assert migrated["singer"]["fov"] == 95.0


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


def test_youtube_plan_360_segments_always_get_a_moving_shot_even_with_no_landmarks_configured():
    # Regression guard: with no spherical landmarks configured at all (so
    # _next_weighted_spherical_shot has nothing to pick), a 360 segment must
    # still get a spherical_shot with real motion -- never fall through to an
    # unset shot, which renders as a frozen equirect passthrough.
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [{"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats, {"spherical_landmarks": {}})

    spherical_segments = [segment for segment in plan["segments"] if segment.get("clip_path") == "/tmp/360.mp4" or segment.get("source_path") == "/tmp/360.mp4"]
    assert spherical_segments
    for segment in spherical_segments:
        shot = segment.get("spherical_shot")
        assert shot, "360 segment must always carry a spherical_shot, not a frozen passthrough"
        magnitudes = [abs(shot.get("drift_yaw_deg") or 0.0), abs(shot.get("drift_pitch_deg") or 0.0), abs(shot.get("fov_delta_deg") or 0.0)]
        assert max(magnitudes) >= 2.0
