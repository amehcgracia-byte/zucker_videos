from __future__ import annotations

import shutil
import subprocess

import pytest

from core.stages.edit import FIXED_CAMERA_SAFE_ZOOM_MAX, FIXED_CAMERA_SAFE_TARGET_X, FIXED_CAMERA_SAFE_TARGET_Y, IPHONE_CROP_TOP_LIMIT, MAX_SEGMENT_SEC, MIN_SEGMENT_SEC, MOTION_CATALOG, REEL_MAX_CUT_SEC, REEL_MIN_CUT_SEC, SPHERICAL_MAX_MOTION_FRACTION_PER_SEC, build_spherical_shot_segments, estimate_bar_starts, migrate_spherical_landmarks, _available_spherical_shots, _ken_burns_motion, _motion_active_axes, _spherical_motion_profile, _youtube_multicam_plan, _reel_promo_plan, _framing_nearly_identical, _valid_motion_recipe, _sony_non_music_filler, _choose_source_avoiding_identical_framing, validate_plan_camera_source_consistency, _visible_iphone_target
from core.stages.cut import _pick_energetic_window, _segment_for_360, _select_360_clip, _tighten_window_to_video_coverage
from core.stages.edit import _covering_sources, _segment_from_source


def test_plan_rejects_camera_id_that_does_not_match_render_source():
    plan = {"segments": [{
        "camera_id": "iphone",
        "source_path": "/tmp/VID_360.mp4",
        "clip_path": "/tmp/360-proxy.mp4",
        "filename": "VID_360.mp4",
        "projection": "equirect",
    }]}
    with pytest.raises(ValueError, match="camera/source mismatch"):
        validate_plan_camera_source_consistency(plan)


def test_visible_iphone_target_inherits_previous_subject_but_never_ceiling():
    source = {"path": "/tmp/fixed.mp4"}
    cache = {str(source["path"]): [{
        "t": 20.0, "area_fraction": 0.03, "cx": 0.5, "cy": 0.5,
        "subject_area_fraction": 0.03, "subject_cx": 0.66, "subject_cy": 0.72,
    }]}
    x, y = _visible_iphone_target(source, 30.0, 2.0, cache)
    assert x == 0.66
    assert y == 0.65


def test_fixed_camera_motion_stays_inside_safe_zoom_and_subject_envelope():
    for index in range(100):
        motion = _ken_burns_motion(index, force_full_zoom=index % 2 == 0, force_close=index % 2 == 1, allow_static=False)
        assert max(float(motion["zoom_start"]), float(motion["zoom_end"])) <= FIXED_CAMERA_SAFE_ZOOM_MAX
        assert FIXED_CAMERA_SAFE_TARGET_X[0] <= float(motion["target_x"]) <= FIXED_CAMERA_SAFE_TARGET_X[1]
        assert FIXED_CAMERA_SAFE_TARGET_Y[0] <= float(motion["target_y"]) <= FIXED_CAMERA_SAFE_TARGET_Y[1]
        assert 0.25 <= float(motion["pan_x_start"]) <= 0.75
        assert 0.25 <= float(motion["pan_x_end"]) <= 0.75
        assert 0.35 <= float(motion["pan_y_start"]) <= 0.65
        assert 0.35 <= float(motion["pan_y_end"]) <= 0.65


def test_fixed_camera_vertical_motion_stays_small_and_directional():
    for index in range(200):
        motion = _ken_burns_motion(index, allow_static=False)
        start = float(motion["pan_y_start"])
        end = float(motion["pan_y_end"])
        assert 0.35 <= start <= 0.65
        assert 0.35 <= end <= 0.65
        if motion.get("vertical_motion") == "up":
            assert end <= start
        elif motion.get("vertical_motion") == "down":
            assert end >= start


def test_estimate_bar_starts_groups_beats_in_fours():
    beats = [index * 0.5 for index in range(17)]

    bars = estimate_bar_starts(beats, 0.0, 8.0)

    assert bars[:5] == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_generated_landmark_plan_preserves_configured_zoom_and_planet_range():
    shots = _available_spherical_shots(
        migrate_spherical_landmarks(
            {
                "singer": {"yaw": 10, "fov": 74},
                "audience": {"yaw": 20, "fov": 111.4},
                "full_stage": {"yaw": 30, "fov": 120},
                "planet": {"yaw": 40, "fov": 280},
            }
        ),
        sweep_enabled=True,
    )
    by_type = {shot["type"]: shot for shot in shots}
    assert by_type["singer"]["fov"] == 74.0
    assert by_type["audience"]["fov"] == 111.4
    assert by_type["full_stage"]["fov"] == 120.0
    assert by_type["planet"]["fov"] == 280.0
    assert by_type["singer"]["sweep_enabled"] is True
    assert by_type["audience"]["sweep_enabled"] is True
    assert by_type["planet"]["sweep_enabled"] is True
    assert by_type["planet"]["spin_deg_per_sec"] == 5.0


def test_360_hold_extension_is_phrase_aligned_and_never_exceeds_twelve_seconds():
    from core.stages.edit import _extend_360_hold_index

    source = {"offset_sec": 0.0, "duration_sec": 20.0}
    index = _extend_360_hold_index([0.0, 3.3, 6.6, 9.9, 13.2], 1, 0.0, 13.2, source)
    assert index == 3


def test_next_landmark_prefers_nearby_yaw_while_preserving_variety():
    from core.stages.edit import _next_weighted_spherical_shot

    shots = [
        {"type": "singer", "yaw": 10.0, "weight": 1.0},
        {"type": "audience", "yaw": 70.0, "weight": 1.0},
        {"type": "left", "yaw": 250.0, "weight": 1.0},
    ]
    chosen = _next_weighted_spherical_shot(shots, {}, previous_yaw=10.0, previous_type="singer")
    assert chosen["type"] == "audience"


def test_spherical_target_weights_normalize_requested_60_10_10_20_split():
    from core.stages.edit import _spherical_target_weights

    weights = _spherical_target_weights({"spherical_shot_target_weights": {"singer": 60, "full_stage": 10, "audience": 10}})

    assert weights["singer"] == pytest.approx(0.60)
    assert weights["full_stage"] == pytest.approx(0.10)
    assert weights["audience"] == pytest.approx(0.10)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_weighted_landmark_rotation_does_not_lock_to_nearest_yaw():
    from core.stages.edit import _next_weighted_spherical_shot

    shots = [
        {"type": "left", "yaw": 250.0, "weight": 40.0},
        {"type": "audience", "yaw": 172.0, "weight": 15.0},
        {"type": "full_stage", "yaw": 0.0, "weight": 15.0},
        {"type": "singer", "yaw": 335.0, "weight": 5.0},
    ]
    usage, recent, previous_type, previous_yaw = {}, [], None, None
    chosen = []
    for _ in range(20):
        shot = _next_weighted_spherical_shot(
            shots, usage, previous_yaw=previous_yaw,
            previous_type=previous_type, recent_types=recent,
        )
        chosen.append(shot["type"])
        usage[shot["type"]] = usage.get(shot["type"], 0) + 1
        previous_type, previous_yaw = shot["type"], shot["yaw"]
        recent.append(previous_type)
        del recent[:-4]
    assert set(chosen) == {"left", "audience", "full_stage", "singer"}
    assert chosen.count("left") >= 7
    assert all(a != b for a, b in zip(chosen, chosen[1:]))


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
    assert plan["cut_count"] == 1
    assert all("eligible_segments" in item for item in plan["selection_diagnostics"])


def test_youtube_plan_switches_camera_when_another_source_covers_the_cut():
    """A covered alternative must win over the immediately previous camera."""
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 24.0},
        "sources": [
            {"path": "/tmp/sony.mp4", "filename": "sony.mp4", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 10.0},
            {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "offset_sec": 0.0, "duration_sec": 24.0, "confidence": 9.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    names = [segment["filename"] for segment in plan["segments"]]
    assert names
    assert all(previous != current for previous, current in zip(names, names[1:]))


def test_reel_uses_real_multicam_plan_not_the_placeholder():
    # Reel must reuse the exact same real bar-aligned multicam logic as
    # YouTube, not the old "single best clip, middle excerpt" placeholder --
    # and the plan must correctly report platform="reel" (previously
    # _youtube_multicam_plan hardcoded "youtube" regardless of the actual
    # platform, which would have mislabeled reel exports).
    coverage = {
        "platform": "reel",
        "window": {"title": "Highlight", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [
            {"path": "/tmp/a.mp4", "filename": "a.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0},
            {"path": "/tmp/b.mp4", "filename": "b.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": [4.0]}

    plan = _youtube_multicam_plan(coverage, beats)

    assert plan["platform"] == "reel"
    assert plan["cut_count"] == 1
    assert "beat-aligned multicam" in plan["real_edit_logic"]


def test_reel_plan_is_unsynchronised_and_carries_options():
    coverage = {
        "platform": "reel",
        "window": {"title": "Promo", "start_sec": 100.0, "duration_sec": 30.0},
        "sources": [
            {"path": "/tmp/a.mp4", "source_path": "/tmp/a.mp4", "duration_sec": 40.0, "projection": None},
            {"path": "/tmp/b.mp4", "source_path": "/tmp/b.mp4", "duration_sec": 40.0, "projection": None},
        ],
    }
    plan = _reel_promo_plan(
        coverage,
        {"bars_sec": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, 112.0, 114.0, 116.0, 118.0, 120.0, 122.0, 124.0, 126.0, 128.0]},
        {"wizard": {"reel_duration_sec": 30, "reel_aspect": "9:16", "reel_text_overlays": [{"text": "LIVE"}]}, "spherical_landmarks": {}},
    )
    assert plan["platform"] == "reel"
    assert plan["reel_duration_sec"] == 30
    assert plan["reel_aspect"] == "9:16"
    assert plan["reel_text_overlays"][0]["text"] == "LIVE"
    assert len({segment["clip_path"] for segment in plan["segments"]}) == 2
    assert all(segment["master_start_sec"] != segment["clip_start_sec"] for segment in plan["segments"])


def test_reel_uses_every_source_round_robin_with_independent_short_cuts():
    sources = [
        {"path": f"/tmp/reel-{index}.mp4", "source_path": f"/tmp/reel-{index}.mp4", "duration_sec": 40.0}
        for index in range(10)
    ]
    plan = _reel_promo_plan(
        {"platform": "reel", "window": {"title": "Promo", "start_sec": 0.0, "duration_sec": 30.0}, "sources": sources},
        {"bars_sec": []},
        {"wizard": {"reel_duration_sec": 30.0}, "spherical_landmarks": {}},
    )
    durations = [float(segment["duration_sec"]) for segment in plan["segments"]]
    assert len(plan["segments"]) == 18
    assert min(durations) >= REEL_MIN_CUT_SEC
    assert max(durations) <= REEL_MAX_CUT_SEC
    assert {segment["source_path"] for segment in plan["segments"]} == {source["path"] for source in sources}
    assert [segment["source_path"] for segment in plan["segments"][:10]] == [source["path"] for source in sources]


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


def test_final_segment_softly_prefers_battery_camera_when_covered():
    sony = {"path": "/tmp/sony.mp4", "filename": "C0064.MP4", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 10.0}
    iphone = {"path": "/tmp/iphone.mov", "filename": "IMG_0022.MOV", "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 9.0}
    chosen = _choose_source_avoiding_identical_framing(
        [sony, iphone], None, None, {}, {}, {"360": 0.0, "handheld": 1.0, "fixed_rear": 1.0},
        8.0, 12.0, 0, {}, False, [], prefer_battery_camera=True,
    )
    assert chosen["filename"] == "IMG_0022.MOV"


def test_youtube_plan_hard_limits_same_camera_to_two_cuts_when_alternative_exists():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 36.0},
        "sources": [
            {"path": "/tmp/sony/C0064.MP4", "filename": "C0064.MP4", "offset_sec": 0.0, "duration_sec": 36.0, "confidence": 100.0},
            {"path": "/tmp/sony/C0065.MP4", "filename": "C0065.MP4", "offset_sec": 0.0, "duration_sec": 36.0, "confidence": 100.0},
            {"path": "/tmp/iphone/IMG_0022.MOV", "filename": "IMG_0022.MOV", "offset_sec": 0.0, "duration_sec": 36.0, "confidence": 1.0},
        ],
    }
    beats = {"bars_sec": list(range(0, 37, 2)), "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    runs = []
    for segment in plan["segments"]:
        camera = segment["camera_id"]
        if runs and runs[-1][0] == camera:
            runs[-1][1] += 1
        else:
            runs.append([camera, 1])
    assert max(length for _camera, length in runs) <= 2
    assert all(
        length <= 2 or not plan["segments"][index]["camera_alternative_available"]
        for index, (_camera, length) in enumerate(runs)
    )


def test_video_coverage_tightens_audio_window_without_extending_trim():
    window, warnings = _tighten_window_to_video_coverage(
        {"title": "Song", "start_sec": 0.0, "duration_sec": 30.0, "trim_start_sec": 0.0, "trim_end_sec": 30.0},
        [{"offset_sec": 4.0, "duration_sec": 20.0}],
    )
    assert window["start_sec"] == 4.0
    assert window["duration_sec"] == 20.0
    assert window["requested_start_sec"] == 0.0
    assert window["requested_end_sec"] == 30.0
    assert warnings == [
        "Audio trimmed to start at 00:04 where video coverage begins.",
        "Audio trimmed to end at 00:24 where video coverage ends; the remaining audio tail has no footage.",
    ]


def test_youtube_range_override_only_covers_confirmed_source_window():
    source = {
        "path": "/tmp/iphone.mov", "offset_sec": 386.775, "duration_sec": 276.7,
        "offset_ranges": [{"clip_start_sec": 184.5, "clip_end_sec": 276.7, "offset_sec": 386.775}],
    }
    assert _covering_sources([source], 570.0, 575.0, platform="youtube") == []
    assert _covering_sources([source], 575.0, 580.0, platform="youtube") == [source]
    segment = _segment_from_source(source, 575.0, 580.0, "Song", platform="youtube")
    assert segment["clip_offset_sec"] == 386.775
    assert segment["clip_start_sec"] == pytest.approx(188.225, abs=0.04)
    assert _covering_sources([source], 575.0, 580.0, platform="reel") == [source]


def test_youtube_plan_role_weight_zero_excludes_role():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 12.0},
        "sources": [
            {"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 8.0},
            {"path": "/tmp/sony.mp4", "filename": "sony.mp4", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 10.0},
            {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 9.0},
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


_DRIFT_KEYS = ("drift_yaw_fraction", "drift_pitch_fraction", "fov_delta_fraction")


def test_spherical_motion_is_opt_in_and_off_by_default():
    """Automatic motion is off unless explicitly enabled.

    The user prefers a still hold over unpredictable movement, so the default
    (enabled=False) must produce zero drift on every axis -- a completely still
    shot -- for every landmark type.
    """
    for shot_type in ("singer", "left", "audience", "full_stage", "unknown_type", ""):
        shot = _spherical_motion_profile({"type": shot_type, "yaw": 10.0, "pitch": -15.0, "fov": 95}, 0)
        assert all(shot[key] == 0.0 for key in _DRIFT_KEYS), (shot_type, shot)
    # Planet's signature spin is also stilled when motion is off.
    planet = _spherical_motion_profile({"type": "planet", "yaw": 6, "pitch": -23.5, "fov": 150}, 0)
    assert planet["spin_fov_fraction_per_sec"] == 0.0


def test_spherical_motion_profile_uses_near_static_hold_motion_when_enabled():
    # The project motion toggle adds only the configured subtle hold rate.
    for shot_type in ("singer", "left", "right", "audience", "full_stage", "audience_stage_wide", "unknown_type", ""):
        for index in range(6):
            shot = _spherical_motion_profile({"type": shot_type, "yaw": 10.0, "pitch": -15.0, "fov": 95}, index, enabled=True)
            assert all(shot[key] == 0.0 for key in _DRIFT_KEYS), (shot_type, index, shot)
            assert shot["sweep_enabled"] is False
            assert shot["hold_motion_rate_deg_per_sec"] == 0.4
            assert shot["drift_pitch_fraction"] == 0.0
            assert shot["fov_delta_fraction"] == 0.0


def test_spherical_motion_profile_is_expressed_as_a_fraction_of_the_visible_field():
    """Motion magnitudes must be FOV-relative, never absolute degrees.

    Absolute degrees was the original bug: the same drift reads as gentle
    across a 140-degree shot and as a swing across a 73-degree one. Storing a
    fraction is what makes a shot feel equally subtle at every zoom level.
    """
    for fov in (73.0, 95.0, 140.0):
        shot = _spherical_motion_profile({"type": "singer", "yaw": 10.0, "pitch": -15.0, "fov": fov}, 1, enabled=True)
        # Static landmark plans carry explicit zero fractions...
        assert all(key in shot for key in _DRIFT_KEYS)
        # ...and no legacy absolute-degree key is written any more.
        assert "drift_yaw_deg" not in shot
        assert "drift_pitch_deg" not in shot
        assert "fov_delta_deg" not in shot
        assert all(shot[key] == 0.0 for key in _DRIFT_KEYS)
        assert shot["hold_motion_rate_deg_per_sec"] == 0.4


def test_spherical_motion_profile_is_identical_across_static_instances():
    # Static landmark holds must not vary by segment index.
    profiles = [_spherical_motion_profile({"type": "singer", "yaw": 10.0, "pitch": -15.0, "fov": 95}, index, enabled=True) for index in range(8)]
    signatures = {tuple(p[key] for key in _DRIFT_KEYS) for p in profiles}
    assert len(signatures) == 1


def test_spherical_motion_profile_is_deterministic_for_cache_stability():
    first = _spherical_motion_profile({"type": "left", "yaw": 47.6, "pitch": -15.9, "fov": 95}, 2, enabled=True)
    second = _spherical_motion_profile({"type": "left", "yaw": 47.6, "pitch": -15.9, "fov": 95}, 2, enabled=True)
    assert first == second


def test_spherical_motion_profile_keeps_planet_spin_untouched():
    planet = _spherical_motion_profile({"type": "planet", "yaw": 6, "pitch": -23.5, "fov": 150}, 7, enabled=True)
    assert planet["projection"] == "tiny_planet"
    assert planet["pitch"] == -90.0
    # The signature tiny-planet spin is held to the same fraction-of-field
    # budget as every other automatic motion.
    assert planet["spin_fov_fraction_per_sec"] == 0.0
    assert planet["spin_deg_per_sec"] == 5.0


def test_spherical_motion_profile_treats_missing_shot_as_a_static_hold_when_enabled():
    shot = _spherical_motion_profile({}, 3, enabled=True)
    assert all(shot[key] == 0.0 for key in _DRIFT_KEYS)


@pytest.mark.parametrize("segment_duration", [0.5, 2.0, 3.5, 6.0])
def test_automatic_360_motion_never_exceeds_the_fov_fraction_budget(segment_duration):
    """Regression guard for "automatic 360 shots swing wildly".

    Renders a realistic automatic-mode plan (landmarks spread right around
    the sphere, as in a real venue setup) through the SAME per-frame sampler
    the export's v360 sendcmd uses, and asserts no axis ever moves faster
    than the published budget as a fraction of the visible field.

    The original defect measured up to 295 deg/s -- 265% of the visible field
    every second -- because a landmark change panned 100-plus degrees across a
    0.45s transition, and because drift magnitudes were absolute degrees that
    ignored how wide the shot actually was.
    """
    from core.stages.edit import _available_spherical_shots, _next_weighted_spherical_shot, _spherical_type_usage
    from core.stages.export import _continuous_spherical_render_segments, _effective_flat_fov, _v360_motion_at

    # Landmarks deliberately spread around the full sphere: this is what makes
    # a naive inter-shot pan whip across the frame.
    landmarks = {
        "full_stage": {"yaw": 355.0, "pitch": -26.3, "fov": 114.8, "weight": 15.0},
        "singer": {"yaw": 21.0, "pitch": -23.4, "fov": 73.9, "weight": 5.0},
        "drummer": {"yaw": 324.0, "pitch": -25.3, "fov": 73.3, "weight": 5.0},
        "left": {"yaw": 308.0, "pitch": -15.9, "fov": 100.0, "weight": 40.0},
        "audience": {"yaw": 175.0, "pitch": -12.9, "fov": 111.4, "weight": 15.0},
        "audience_stage_wide": {"yaw": 72.0, "pitch": -14.3, "fov": 138.8, "weight": 15.0},
    }
    shots = _available_spherical_shots(landmarks)
    segments = []
    for index in range(14):
        shot = _next_weighted_spherical_shot(shots, _spherical_type_usage(segments))
        segments.append(
            {
                "clip_start_sec": index * segment_duration,
                "master_start_sec": index * segment_duration,
                "duration_sec": segment_duration,
                "spherical_shot": _spherical_motion_profile(shot or {}, index, enabled=True),
            }
        )

    moved_at_all = 0
    for segment in _continuous_spherical_render_segments(segments):
        shot = segment["spherical_shot"]
        fov = _effective_flat_fov(shot)
        frames = [
            _v360_motion_at(shot, segment_duration, min(segment_duration, step / 30.0))
            for step in range(int(segment_duration * 30) + 1)
        ]
        for (yaw_a, pitch_a, fov_a), (yaw_b, pitch_b, fov_b) in zip(frames, frames[1:]):
            dt = 1.0 / 30.0
            yaw_delta = abs(((yaw_b - yaw_a + 180.0) % 360.0) - 180.0)
            for delta in (yaw_delta, abs(pitch_b - pitch_a), abs(fov_b - fov_a)):
                rate_fraction = delta / dt / fov
                assert rate_fraction <= SPHERICAL_MAX_MOTION_FRACTION_PER_SEC + 1e-6, (
                    f"automatic 360 motion of {rate_fraction * 100:.1f}% of the visible field per second "
                    f"exceeds the {SPHERICAL_MAX_MOTION_FRACTION_PER_SEC * 100:.0f}% budget"
                )
        travel = max(
            abs(((frames[-1][0] - frames[0][0] + 180.0) % 360.0) - 180.0),
            abs(frames[-1][1] - frames[0][1]),
            abs(frames[-1][2] - frames[0][2]),
        )
        if travel / fov >= 0.002:
            moved_at_all += 1

    # Runtime v360 motion is disabled by default because sendcmd corrupts
    # command-instants; all automatic holds remain effectively static here.
    assert moved_at_all == 0


def test_youtube_plan_keeps_recorded_360_curve_in_directed_mode():
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
    assert "recorded_move" in {segment["spherical_shot"]["type"] for segment in plan["segments"]}
    assert plan["spherical_recording_usage"]["recorded_segments"] > 0
    assert plan["spherical_recording_usage"]["mode"] == "directed"


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

    assert {segment["spherical_shot"]["type"] for segment in plan["segments"]} <= {"full_stage", "singer"}
    assert plan["spherical_recording_usage"]["recorded_segments"] == 0


def test_fixed_camera_motion_uses_one_movement_axis_at_a_time():
    motions = [_ken_burns_motion(index) for index in range(500)]
    assert {motion["movement"] for motion in motions} == set(MOTION_CATALOG)
    for motion in motions:
        assert _valid_motion_recipe(motion)
        assert len(_motion_active_axes(motion)) in {0, 1, 2}
        assert max(float(motion["zoom_start"]), float(motion["zoom_end"])) <= FIXED_CAMERA_SAFE_ZOOM_MAX


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
    window = {"title": "Full video", "start_sec": 5.0, "duration_sec": 12.0}

    clip = _select_360_clip(clips)
    segment = _segment_for_360(clip, window)

    assert clip["filename"] == "studio.mp4"
    assert segment["clip_start_sec"] == 0.0
    assert segment["master_start_sec"] == 5.0
    assert segment["duration_sec"] == 12.0
    assert segment["projection"] == "equirect"


def test_360_segment_trims_to_the_song_window_not_the_clips_own_extent():
    """Regression guard: passthrough 360 export must follow the song's own
    Start/End range. Previously the window was replaced entirely by the 360
    clip's own recording extent, so a song trimmed to a short highlight would
    silently export the camera's ENTIRE (much longer) recording instead, or a
    camera that started late would silently shrink the exported song range.
    """
    clip = {"path": "/tmp/360.mp4", "filename": "360.mp4", "projection": "equirect", "offset_sec": 100.0, "duration_sec": 600.0}

    # Song window asks for a short 30s highlight starting well inside the clip.
    window = {"title": "Highlight", "start_sec": 200.0, "duration_sec": 30.0}
    segment = _segment_for_360(clip, window)
    assert segment["master_start_sec"] == 200.0
    assert segment["duration_sec"] == 30.0
    assert segment["clip_start_sec"] == 100.0

    # Song window extends past where the camera stops recording -- clipped to
    # what's actually available, not silently expanded or left at full length.
    window_overrun = {"title": "Full video", "start_sec": 650.0, "duration_sec": 200.0}
    segment_overrun = _segment_for_360(clip, window_overrun)
    assert segment_overrun["master_start_sec"] == 650.0
    assert segment_overrun["duration_sec"] == pytest.approx(50.0)

    # Song window doesn't overlap the clip's coverage at all.
    window_none = {"title": "Full video", "start_sec": 0.0, "duration_sec": 50.0}
    segment_none = _segment_for_360(clip, window_none)
    assert segment_none["duration_sec"] == 0.0


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
    assert set(types) <= {"full_stage", "singer", "left"}
    assert types


def test_fixed_rear_segments_get_subtle_motion_on_some_holds():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 12.0},
        "sources": [{"path": "/tmp/iphone.mov", "filename": "iphone.mov", "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 12.0, "confidence": 9.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    motions = [segment.get("motion") for segment in plan["segments"] if segment.get("motion")]
    assert motions
    assert all(motion["type"] == "ken_burns" for motion in motions)


def test_fixed_rear_keeps_close_up_when_synced_coverage_is_sparse():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [{
            "path": "/tmp/iphone.mov", "filename": "iphone.mov", "camera_id": "iphone",
            "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0,
        }],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}

    plan = _youtube_multicam_plan(coverage, beats)

    fixed = [segment for segment in plan["segments"] if segment["camera_id"] == "iphone"]
    assert fixed
    assert all(segment["fixed_camera_alternative_count"] == 0 for segment in fixed)
    assert all(segment["fixed_camera_zoom_policy"] == "safe_subject_motion_low_coverage" for segment in fixed)
    assert all(max(float(segment["motion"]["zoom_start"]), float(segment["motion"]["zoom_end"])) <= FIXED_CAMERA_SAFE_ZOOM_MAX for segment in fixed)


def test_fixed_rear_uses_gentle_center_motion_when_coverage_is_available():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [
            {
                "path": "/tmp/iphone.mov", "filename": "iphone.mov", "camera_id": "iphone",
                "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0,
            },
            {
                "path": "/tmp/sony.mp4", "filename": "sony.mp4", "camera_id": "sony",
                "camera_role": "handheld", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0,
            },
            {
                "path": "/tmp/360.mp4", "filename": "360.mp4", "camera_id": "360",
                "camera_role": "360", "projection": "equirect", "offset_sec": 0.0,
                "duration_sec": 8.0, "confidence": 9.0,
            },
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}
    settings = {"edit": {"camera_role_weights": {"360": 0, "handheld": 0, "fixed_rear": 1}}}

    plan = _youtube_multicam_plan(coverage, beats, settings)

    fixed = [segment for segment in plan["segments"] if segment["camera_id"] == "iphone"]
    assert fixed
    assert all(segment["fixed_camera_alternative_count"] == 2 for segment in fixed)
    assert all(segment["fixed_camera_zoom_coverage_threshold"] == 2 for segment in fixed)
    assert all(segment["fixed_camera_zoom_policy"] == "gentle_subject_motion_sufficient_coverage" for segment in fixed)
    assert all(abs(float(segment["motion"]["zoom_end"]) - float(segment["motion"]["zoom_start"])) <= 0.15 for segment in fixed)
    assert all(1.0 <= float(segment["motion"]["zoom_start"]) <= 1.15 for segment in fixed)
    assert all(1.0 <= float(segment["motion"]["zoom_end"]) <= 1.15 for segment in fixed)
    assert all(segment["motion"].get("subject_fallback") is True for segment in fixed)
    assert all(segment["motion"]["target_x"] == segment["motion"]["target_y"] == 0.5 for segment in fixed)


def test_fixed_camera_coverage_threshold_is_configurable():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 4.0},
        "sources": [
            {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "camera_id": "iphone", "camera_role": "fixed_rear", "offset_sec": 0.0, "duration_sec": 4.0, "confidence": 9.0},
            {"path": "/tmp/sony.mp4", "filename": "sony.mp4", "camera_id": "sony", "camera_role": "handheld", "offset_sec": 0.0, "duration_sec": 4.0, "confidence": 9.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0], "sections_sec": []}
    plan = _youtube_multicam_plan(coverage, beats, {"edit": {"fixed_camera_zoom_coverage_threshold": 1, "camera_role_weights": {"360": 0, "handheld": 0, "fixed_rear": 1}}})

    fixed = [segment for segment in plan["segments"] if segment["camera_id"] == "iphone"]
    assert fixed
    assert all(segment["fixed_camera_zoom_coverage_threshold"] == 1 for segment in fixed)
    assert all(segment["motion"]["zoom_start"] == 1.0 for segment in fixed)


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


def test_youtube_plan_360_segments_always_carry_a_shot_and_move_only_when_motion_is_on():
    # Regression guard: with no spherical landmarks configured at all (so
    # _next_weighted_spherical_shot has nothing to pick), a 360 segment must
    # still get a spherical_shot -- never fall through to an unset shot, which
    # renders as a frozen equirect passthrough. Motion itself is opt-in.
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 8.0},
        "sources": [{"path": "/tmp/360.mp4", "filename": "wide360.mp4", "projection": "equirect", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 8.0}],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0], "sections_sec": []}

    def spherical_segments(plan):
        return [s for s in plan["segments"] if s.get("clip_path") == "/tmp/360.mp4" or s.get("source_path") == "/tmp/360.mp4"]

    # Default: motion off -> a shot is present, but completely still.
    still_plan = _youtube_multicam_plan(coverage, beats, {"spherical_landmarks": {}, "edit": {"spherical_motion": False}})
    still = spherical_segments(still_plan)
    assert still
    for segment in still:
        shot = segment.get("spherical_shot")
        assert shot, "360 segment must always carry a spherical_shot, not a frozen passthrough"
        assert all(abs(shot.get(key) or 0.0) == 0.0 for key in _DRIFT_KEYS)

        # Motion explicitly on -> normal landmark shots get the subtle hold.
    moving_plan = _youtube_multicam_plan(coverage, beats, {"spherical_landmarks": {}, "edit": {"spherical_motion": True}})
    moving = spherical_segments(moving_plan)
    assert moving
    for segment in moving:
        shot = segment.get("spherical_shot")
        assert shot
        assert all(abs(shot.get(key) or 0.0) == 0.0 for key in _DRIFT_KEYS)
        assert shot.get("sweep_enabled") is False


# ---------------------------------------------------------------------------
# Reel mode: auto-highlight window picking
# ---------------------------------------------------------------------------


def test_pick_energetic_window_finds_the_loud_section(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")
    master = tmp_path / "master.wav"
    # Quiet (0-20s), loud (20-30s), quiet again (30-50s). The loud section is
    # exactly where a highlight reel should land.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20:sample_rate=22050",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10:sample_rate=22050",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20:sample_rate=22050",
            "-filter_complex",
            "[0:a]volume=0.02[quiet1];[1:a]volume=1.0[loud];[2:a]volume=0.02[quiet2];[quiet1][loud][quiet2]concat=n=3:v=0:a=1[out]",
            "-map", "[out]",
            str(master),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    window = {"title": "Full video", "start_sec": 0.0, "duration_sec": 50.0}

    picked = _pick_energetic_window(str(master), window, target_duration=8.0)

    assert picked["duration_sec"] == pytest.approx(8.0)
    # The picked window should sit within the loud stretch (20-30s), not spill
    # far into either quiet section.
    assert 18.0 <= picked["start_sec"] <= 24.0


def test_pick_energetic_window_falls_back_to_middle_when_shorter_than_target():
    window = {"title": "Full video", "start_sec": 10.0, "duration_sec": 5.0}

    picked = _pick_energetic_window("/nonexistent/master.wav", window, target_duration=20.0)

    assert picked["start_sec"] == 10.0
    assert picked["duration_sec"] == 5.0


def test_sony_gap_fill_never_repeats_a_frame_within_180_seconds():
    sources = [
        {
            "path": "/tmp/ZZS_sony_C0064.MP4",
            "filename": "ZZS_sony_C0064.MP4",
            "offset_sec": 0.0,
            "non_music_windows": [
                {"start_sec": 10.0, "end_sec": 20.0, "score": 0.8},
                {"start_sec": 30.0, "end_sec": 40.0, "score": 0.8},
            ],
        },
        {
            "path": "/tmp/ZZS_sony_C0065.MP4",
            "filename": "ZZS_sony_C0065.MP4",
            "offset_sec": 0.0,
            "non_music_windows": [
                {"start_sec": 50.0, "end_sec": 60.0, "score": 0.8},
                {"start_sec": 70.0, "end_sec": 80.0, "score": 0.8},
            ],
        },
    ]
    history = []
    selected = []
    for index, master_start in enumerate((0.0, 5.0, 10.0, 200.0)):
        replacement = _sony_non_music_filler(
            sources,
            {"master_start_sec": master_start, "duration_sec": 4.0, "title": "Full video"},
            index,
            history,
        )
        assert replacement is not None
        selected.append((master_start, replacement["non_music_frame_key"]))
    for left_index, (left_time, left_key) in enumerate(selected):
        for right_time, right_key in selected[left_index + 1:]:
            if left_key == right_key:
                assert right_time - left_time >= 180.0
