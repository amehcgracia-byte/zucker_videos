from __future__ import annotations

import json

from core.operator_avoidance import (
    CACHE_VERSION,
    IPHONE_AVOIDANCE_CX,
    IPHONE_AVOIDANCE_CY,
    OPERATOR_AREA_THRESHOLD,
    SUBJECT_AREA_THRESHOLD,
    _avoidance_360,
    _avoidance_iphone,
    _cache_key_for_path,
    _secondary_subject,
    avoidance_for_segment,
    count_avoidance_adjustments,
    load_cached_operator_presence,
    role_for_record,
)
from core.normalization import global_cache_root


def test_role_for_record_uses_properties_not_filename_markers():
    assert role_for_record("equirect", "clip.mp4") == "360"
    assert role_for_record(None, "raw.insv") == "handheld"
    assert role_for_record(None, "VID_20260824_203210.mp4") == "handheld"
    assert role_for_record(None, "IMG_0018.MOV") == "handheld"
    assert role_for_record(None, "anything.mp4", {"raw_360": True}) == "360"
    assert role_for_record(None, "C0059.MP4") == "handheld"
    assert role_for_record(None, "phone-camera.mp4", {"is_static_camera": True}) == "fixed_rear"


def test_secondary_subject_rejects_duplicate_boxes_for_back_facing_operator():
    dominant = {"area_fraction": 0.20, "cx": 0.46, "cy": 0.64}
    duplicate = {"area_fraction": 0.09, "cx": 0.54, "cy": 0.70}
    real_subject = {"area_fraction": 0.08, "cx": 0.78, "cy": 0.42}
    assert _secondary_subject([dominant, duplicate, real_subject], dominant) == real_subject
    assert _secondary_subject([dominant, duplicate], dominant) is None


def test_avoidance_for_segment_skips_handheld_even_with_strong_detection():
    samples = [{"t": 5.0, "area_fraction": 0.5, "cx": 0.9, "cy": 0.6}]
    assert avoidance_for_segment("handheld", samples, 4.0, 2.0) is None


def test_avoidance_for_segment_ignores_detections_outside_window():
    samples = [{"t": 50.0, "area_fraction": 0.5, "cx": 0.9, "cy": 0.6}]
    assert avoidance_for_segment("fixed_rear", samples, 4.0, 2.0) is None


def test_avoidance_for_segment_ignores_small_blobs_below_threshold():
    samples = [{"t": 4.5, "area_fraction": OPERATOR_AREA_THRESHOLD - 0.01, "cx": 0.9, "cy": 0.6}]
    assert avoidance_for_segment("fixed_rear", samples, 4.0, 2.0) is None


def test_avoidance_for_segment_returns_zoom_crop_for_fixed_rear():
    samples = [{"t": 4.5, "area_fraction": 0.2, "cx": 0.9, "cy": 0.6}]
    adjustment = avoidance_for_segment("fixed_rear", samples, 4.0, 2.0)
    assert adjustment["type"] == "zoom_crop"
    assert adjustment["zoom"] > 1.0
    # No distinct subject detected -> fall back to right-of-centre, vertically-centred default.
    assert adjustment["cx"] == IPHONE_AVOIDANCE_CX
    assert adjustment["cy"] == IPHONE_AVOIDANCE_CY


def test_avoidance_for_segment_crops_toward_detected_subject():
    # A subject at the exact centre of the source frame should stay centred
    # in the output regardless of zoom (sanity check on the pan transform).
    samples = [
        {
            "t": 4.5,
            "area_fraction": 0.2,
            "cx": 0.1,
            "cy": 0.8,
            "subject_area_fraction": 0.1,
            "subject_cx": 0.5,
            "subject_cy": 0.5,
        }
    ]
    adjustment = avoidance_for_segment("fixed_rear", samples, 4.0, 2.0)
    assert adjustment["type"] == "zoom_crop"
    assert adjustment["cx"] == 0.5
    assert adjustment["cy"] == 0.5


def test_avoidance_iphone_centers_subject_off_from_frame_middle():
    # A subject right-of-centre in the source should land further right in
    # pan-space than its raw fraction (the zoom transform amplifies offsets).
    adjustment = _avoidance_iphone({"area_fraction": 0.2}, {"subject_cx": 0.6, "subject_cy": 0.5, "subject_area_fraction": 0.1})
    assert adjustment["cx"] > 0.6


def test_avoidance_for_segment_ignores_tiny_subject_blobs():
    samples = [
        {
            "t": 4.5,
            "area_fraction": 0.2,
            "cx": 0.1,
            "cy": 0.8,
            "subject_area_fraction": SUBJECT_AREA_THRESHOLD - 0.001,
            "subject_cx": 0.72,
            "subject_cy": 0.45,
        }
    ]
    adjustment = avoidance_for_segment("fixed_rear", samples, 4.0, 2.0)
    assert adjustment["cx"] == IPHONE_AVOIDANCE_CX
    assert adjustment["cy"] == IPHONE_AVOIDANCE_CY


def test_avoidance_for_segment_returns_yaw_shift_for_360():
    samples = [{"t": 4.5, "area_fraction": 0.2, "cx": 0.9, "cy": 0.6}]
    adjustment = avoidance_for_segment("360", samples, 4.0, 2.0, current_yaw=10.0)
    assert adjustment["type"] == "yaw_shift"
    # operator is right-of-centre (cx=0.9) -> shift left (negative)
    assert adjustment["yaw_deg"] < 0


def test_avoidance_360_returns_none_for_centered_operator():
    # cx close to 0.5 -> shift below the 2-degree floor -> no adjustment
    assert _avoidance_360({"cx": 0.5}, current_yaw=0.0) is None


def test_avoidance_360_clamps_to_max_shift():
    adjustment = _avoidance_360({"cx": 1.0}, current_yaw=0.0)
    assert adjustment["yaw_deg"] == -20.0


def test_avoidance_iphone_zoom_scales_with_area():
    small = _avoidance_iphone({"area_fraction": 0.08})
    large = _avoidance_iphone({"area_fraction": 0.3})
    assert large["zoom"] > small["zoom"]


def test_count_avoidance_adjustments_counts_only_adjusted_segments():
    segments = [
        {"operator_avoidance": {"type": "yaw_shift", "yaw_deg": -10.0}},
        {"operator_avoidance": None},
        {},
        {"operator_avoidance": {"type": "zoom_crop", "zoom": 1.4}},
    ]
    assert count_avoidance_adjustments(segments) == 2


def test_cache_key_for_proxy_path_uses_stem(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proxies_dir = global_cache_root() / "proxies"
    proxies_dir.mkdir(parents=True)
    proxy_path = proxies_dir / "abc123def456.mp4"
    proxy_path.write_bytes(b"fake")
    assert _cache_key_for_path(str(proxy_path)) == "abc123def456"


def test_load_cached_operator_presence_reads_valid_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proxies_dir = global_cache_root() / "proxies"
    proxies_dir.mkdir(parents=True)
    proxy_path = proxies_dir / "cachekey123.mp4"
    proxy_path.write_bytes(b"fake")
    operator_dir = global_cache_root() / "operator"
    operator_dir.mkdir(parents=True)
    (operator_dir / "cachekey123.json").write_text(
        json.dumps({"cache_version": CACHE_VERSION, "samples": [{"t": 1.0, "area_fraction": 0.2, "cx": 0.5, "cy": 0.5}]}),
        encoding="utf-8",
    )
    samples = load_cached_operator_presence(str(proxy_path))
    assert samples == [{"t": 1.0, "area_fraction": 0.2, "cx": 0.5, "cy": 0.5}]


def test_load_cached_operator_presence_returns_empty_for_missing_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proxies_dir = global_cache_root() / "proxies"
    proxies_dir.mkdir(parents=True)
    proxy_path = proxies_dir / "nocache123.mp4"
    proxy_path.write_bytes(b"fake")
    assert load_cached_operator_presence(str(proxy_path)) == []


def test_load_cached_operator_presence_ignores_stale_cache_version(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proxies_dir = global_cache_root() / "proxies"
    proxies_dir.mkdir(parents=True)
    proxy_path = proxies_dir / "stalekey123.mp4"
    proxy_path.write_bytes(b"fake")
    operator_dir = global_cache_root() / "operator"
    operator_dir.mkdir(parents=True)
    (operator_dir / "stalekey123.json").write_text(
        json.dumps({"cache_version": 0, "samples": [{"t": 1.0, "area_fraction": 0.9, "cx": 0.5, "cy": 0.5}]}),
        encoding="utf-8",
    )
    assert load_cached_operator_presence(str(proxy_path)) == []
