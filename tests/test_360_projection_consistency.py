from __future__ import annotations

from core.normalization import EQUIRECT_FILTER
from core.shot_review import _review_pose_for_cache, _thumbnail_filter
from core.spherical_view import spherical_view_filter, view_parameters
from core.stages.export import _export_source_filter


def test_analysis_proxy_is_fixed_and_point_of_view_independent() -> None:
    assert "v360=input=equirect:output=flat" in EQUIRECT_FILTER
    assert "yaw=0" in EQUIRECT_FILTER
    assert "pitch=0" in EQUIRECT_FILTER
    assert "h_fov=100" in EQUIRECT_FILTER
    assert "w=1280:h=720" in EQUIRECT_FILTER


def test_thumbnail_filter_uses_saved_spherical_pose() -> None:
    singer = _thumbnail_filter({
        "source_path": "/media/camera-360.mp4",
        "projection": "equirect",
        "spherical_shot": {"shot_id": "singer", "type": "singer", "yaw": 25, "pitch": -10, "fov": 82},
    })
    audience = _thumbnail_filter({
        "source_path": "/media/camera-360.mp4",
        "projection": "equirect",
        "spherical_shot": {"shot_id": "audience", "type": "audience", "yaw": 210, "pitch": 8, "fov": 125},
    })
    assert singer != audience
    assert "yaw=25.000" in singer and "pitch=-10.000" in singer and "h_fov=82.000" in singer
    assert "yaw=-150.000" in audience and "pitch=8.000" in audience and "h_fov=125.000" in audience


def test_changed_saved_pose_changes_review_cache_namespace() -> None:
    first = {"spherical_shot": {"shot_id": "singer", "type": "singer", "yaw": 25, "pitch": -10, "fov": 82}}
    changed = {"spherical_shot": {"shot_id": "singer", "type": "singer", "yaw": 26, "pitch": -10, "fov": 82}}
    assert _review_pose_for_cache(first) != _review_pose_for_cache(changed)


def test_thumbnail_and_export_use_the_same_view_parameters() -> None:
    shot = {"shot_id": "audience", "type": "audience", "yaw": 210, "pitch": 8, "fov": 125}
    expected = view_parameters(shot["yaw"], shot["pitch"], shot["fov"], 16 / 9, shot["type"])
    thumbnail = spherical_view_filter("equirect", shot["yaw"], shot["pitch"], shot["fov"], shot["type"])
    export = _export_source_filter({"projection": "equirect"}, shot, duration=4.0)
    for key in ("yaw", "pitch", "h_fov", "v_fov"):
        needle = f"{key}={float(expected[key]):.3f}"
        assert needle in thumbnail
        assert needle in export
    assert f"output={expected['projection']}" in thumbnail
    assert f"output={expected['projection']}" in export
