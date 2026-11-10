from __future__ import annotations

from pathlib import Path

import pytest

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
    # An unspecified preset is linear, whose safe perspective cap is 110°.
    assert "yaw=-150.000" in audience and "pitch=8.000" in audience and "h_fov=110.000" in audience


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


@pytest.mark.parametrize("preset,fov,projection", [("linear", 160, "flat"), ("megaview", 160, "sg"), ("dewarp", 150, "flat")])
def test_ingest_thumbnail_retains_projection_and_horizon(preset, fov, projection):
    shot = dict(type="full_stage", yaw=210, pitch=-15, fov=fov,
                projection_preset=preset, roll=12, projection_control=.4)
    thumbnail = spherical_view_filter(
        "equirect", shot["yaw"], shot["pitch"], shot["fov"], shot["type"],
        projection_preset=preset, roll=12, projection_control=.4,
    )
    review = _thumbnail_filter(dict(projection="equirect", spherical_shot=shot))
    export = _export_source_filter(dict(projection="equirect"), shot, duration=4)
    expected = view_parameters(210, -15, fov, 16/9, "full_stage",
                               projection_preset=preset, roll=12, projection_control=.4)
    for graph in (thumbnail, review, export):
        assert f"output={projection}" in graph
        for field in ("yaw", "pitch", "roll", "h_fov", "v_fov"):
            assert f"{field}={expected[field]:.3f}" in graph


def test_ingest_thumbnail_cache_changes_when_saved_projection_or_horizon_changes(tmp_path, monkeypatch):
    from core.project import Project
    from core.stages import sync

    record = dict(path="/media/sphere.mp4", projection="equirect")
    project = Project(tmp_path, dict(settings={}))
    monkeypatch.setattr(sync, "record_for_clip_id", lambda *args: record)
    monkeypatch.setattr(sync, "file_signature", lambda path: "source")
    monkeypatch.setattr(sync, "cache_key", lambda record: "clip")
    monkeypatch.setattr(sync, "global_thumbnail_path", lambda *args: tmp_path / "thumb.jpg")
    monkeypatch.setattr(sync, "media_duration", lambda path: 4)
    monkeypatch.setattr(sync, "ffmpeg_path", lambda: "ffmpeg")
    commands = []

    def encode(command):
        commands.append(command)
        Path(command[-1]).write_bytes(b"mock thumbnail")

    monkeypatch.setattr(sync, "run_ffmpeg", encode)
    shot = dict(type="full_stage", yaw=10, pitch=0, fov=160)
    linear = sync.generate_thumbnail(project, "clip", shot)
    wide = sync.generate_thumbnail(project, "clip", dict(shot, projection_preset="megaview"))
    tilted = sync.generate_thumbnail(project, "clip", dict(shot, projection_preset="megaview", roll=12))
    assert len({linear, wide, tilted}) == 3
    assert len(commands) == 3
    assert "output=sg" in commands[-1][commands[-1].index("-vf") + 1]
    assert "roll=12.000" in commands[-1][commands[-1].index("-vf") + 1]
    assert sync.generate_thumbnail(project, "clip", dict(shot, projection_preset="megaview", roll=12)) == tilted
    assert len(commands) == 3  # unchanged recipes reuse the existing image
