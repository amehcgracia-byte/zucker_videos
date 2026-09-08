from __future__ import annotations

from core.reel_framing import REEL_FRAMING_VERSION, subject_box_for_window
from core.stages.edit import _reel_promo_plan, _reel_target_for_source


def test_reel_subject_box_unions_detected_window_with_margin_input():
    profile = {
        "version": REEL_FRAMING_VERSION,
        "samples": [
            {"t": 1.0, "boxes": [{"x1": 0.20, "y1": 0.35, "x2": 0.42, "y2": 0.80, "confidence": 0.8}]},
            {"t": 2.0, "boxes": [{"x1": 0.38, "y1": 0.32, "x2": 0.62, "y2": 0.78, "confidence": 0.8}]},
        ],
    }
    box = subject_box_for_window(profile, 1.0, 1.0)
    assert box == {"x1": 0.2, "y1": 0.32, "x2": 0.62, "y2": 0.8}


def test_sony_reel_target_is_crop_offset_that_contains_subject():
    source = {
        "filename": "Sony_C001.MP4",
        "probe": {"width": 1920, "height": 1080},
        "reel_framing": {
            "samples": [{"t": 0.0, "boxes": [{"x1": 0.42, "y1": 0.30, "x2": 0.68, "y2": 0.82, "confidence": 0.9}]}]
        },
    }
    x, y = _reel_target_for_source(source, 0.0, 1.5)
    assert 0.0 <= x <= 1.0
    assert 0.0 <= y <= 1.0
    assert (x, y) != (0.5, 0.5)


def test_reel_plan_keeps_360_out_of_phase_one_framing():
    coverage = {
        "platform": "reel",
        "window": {"start_sec": 0.0, "duration_sec": 3.0, "title": "test"},
        "sources": [{
            "path": "/tmp/camera.mp4", "source_path": "/tmp/camera.mp4", "filename": "camera.mp4",
            "duration_sec": 3.0, "projection": "equirect", "reel_framing": {"samples": []},
        }],
    }
    plan = _reel_promo_plan(coverage, {}, {"wizard": {"reel_duration_sec": 20, "reel_aspect": "9:16"}})
    assert plan["segments"]
    assert not any("reel_framing" in segment for segment in plan["segments"])
    assert all(segment.get("spherical_shot") for segment in plan["segments"])


def test_reel_vertical_horizontal_mix_uses_confidence_and_keeps_rhythm():
    sources = [
        {
            "path": "/tmp/subject.mp4", "source_path": "/tmp/subject.mp4", "duration_sec": 30.0,
            "reel_framing": {"samples": [{"t": 0.0, "boxes": [{"x1": .3, "y1": .2, "x2": .6, "y2": .8, "confidence": .9}]}, {"t": 2.0, "boxes": [{"x1": .32, "y1": .2, "x2": .62, "y2": .8, "confidence": .9}]}]},
        },
        {"path": "/tmp/general.mp4", "source_path": "/tmp/general.mp4", "duration_sec": 30.0, "reel_framing": {"samples": []}},
    ]
    plan = _reel_promo_plan(
        {"platform": "reel", "window": {"start_sec": 0.0, "duration_sec": 20.0, "title": "test"}, "sources": sources},
        {},
        {"wizard": {"reel_duration_sec": 20.0, "reel_aspect": "mix_vertical_horizontal", "reel_mix_vertical_ratio": "auto"}},
    )
    treatments = [segment["reel_mix_treatment"] for segment in plan["segments"]]
    assert "vertical" in treatments and "horizontal" in treatments
    assert all(segment["reel_mix_treatment"] in {"vertical", "horizontal"} for segment in plan["segments"])


def test_reel_vertical_horizontal_mix_honors_configured_ratio():
    sources = [{"path": "/tmp/camera.mp4", "source_path": "/tmp/camera.mp4", "duration_sec": 30.0}]
    plan = _reel_promo_plan(
        {"platform": "reel", "window": {"start_sec": 0.0, "duration_sec": 20.0}, "sources": sources},
        {},
        {"wizard": {"reel_duration_sec": 20.0, "reel_aspect": "mix_vertical_horizontal", "reel_mix_vertical_ratio": 0.25}},
    )
    vertical_count = sum(segment["reel_mix_treatment"] == "vertical" for segment in plan["segments"])
    assert vertical_count == round(len(plan["segments"]) * 0.25)


def test_single_source_reel_is_one_continuous_take_at_audio_offset():
    plan = _reel_promo_plan(
        {
            "platform": "reel",
            "window": {"start_sec": 42.0, "duration_sec": 30.0, "title": "song"},
            "single_source_reel": True,
            "sources": [{"path": "/tmp/take.mp4", "source_path": "/tmp/take.mp4", "filename": "take.mp4", "duration_sec": 90.0}],
        },
        {},
        {"wizard": {"reel_duration_sec": 30.0, "reel_aspect": "9:16", "reel_cuts_per_source": 3}},
    )
    assert len(plan["segments"]) == 1
    assert plan["cut_count"] == 0
    assert plan["segments"][0]["clip_start_sec"] == 42.0
    assert plan["segments"][0]["duration_sec"] == 30.0
    assert plan["segments"][0]["single_source_continuous"] is True
    assert plan["real_edit_logic"].startswith("single-source Reel")


def test_single_source_reel_warns_when_video_does_not_reach_audio_offset():
    plan = _reel_promo_plan(
        {
            "platform": "reel",
            "window": {"start_sec": 42.0, "duration_sec": 30.0},
            "single_source_reel": True,
            "sources": [{"path": "/tmp/take.mp4", "source_path": "/tmp/take.mp4", "duration_sec": 20.0}],
        },
        {},
        {"wizard": {"reel_duration_sec": 30.0}},
    )
    assert plan["segments"][0]["clip_start_sec"] == 0.0
    assert any("shorter than the selected audio offset" in warning for warning in plan["warnings"])
