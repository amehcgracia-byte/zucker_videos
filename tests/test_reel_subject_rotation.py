"""Pulse/Reel never shows the same person twice in a row, across cameras and 360 views."""
from __future__ import annotations

from core.stages.edit import _reel_promo_plan


def _plan(sources, landmarks=None, camera_subjects=None, duration=30.0):
    return _reel_promo_plan(
        {"platform": "reel", "window": {"title": "Promo", "start_sec": 0.0, "duration_sec": duration}, "sources": sources},
        {"bars_sec": []},
        {"wizard": {"reel_duration_sec": duration}, "spherical_landmarks": landmarks or {},
         "edit": {"camera_subjects": camera_subjects or {}}},
    )


def _view(yaw, subject):
    return {"yaw": yaw, "pitch": 0.0, "fov": 90.0, "enabled": True, "subject": subject, "weight": 1.0}


def test_bass_player_is_never_repeated_across_camera_and_360_views():
    sources = [
        {"path": "/tmp/sony.mp4", "source_path": "/tmp/sony.mp4", "duration_sec": 60.0},
        {"path": "/tmp/fixed.mp4", "source_path": "/tmp/fixed.mp4", "duration_sec": 60.0},
        {"path": "/tmp/cam-360.mp4", "source_path": "/tmp/cam-360.mp4", "duration_sec": 60.0, "projection": "equirect"},
    ]
    landmarks = {
        "full_stage": _view(0.0, "full_stage"),
        "singer": _view(20.0, "singer"),
        "drummer": _view(-30.0, "drummer"),
        "right": _view(60.0, "bassist"),   # the same bass player the Sony films
        "pianist": _view(-60.0, "Keys"),
    }
    plan = _plan(sources, landmarks, {"/tmp/sony.mp4": "bassist", "/tmp/fixed.mp4": "general"})
    subjects = [segment["editorial_subject"] for segment in plan["segments"]]
    assert all(a != b for a, b in zip(subjects, subjects[1:])), subjects
    assert plan["subject_unavoidable_repeats"] == 0
    # Each person gets an even share of the reel.
    seconds = plan["subject_distribution_seconds"]
    people = {key: value for key, value in seconds.items() if key in {"bassist", "singer", "drummer", "pianist"}}
    assert set(people) == {"bassist", "singer", "drummer", "pianist"}
    assert max(people.values()) - min(people.values()) <= 2 * 30.0 / len(plan["segments"]) + 1e-6
    # Every dropped camera is still used.
    assert {segment["source_path"] for segment in plan["segments"]} == {source["path"] for source in sources}


def test_clips_without_subjects_keep_rotating_through_every_source():
    sources = [{"path": f"/tmp/clip-{i}.mp4", "source_path": f"/tmp/clip-{i}.mp4", "duration_sec": 40.0} for i in range(4)]
    plan = _plan(sources)
    paths = [segment["source_path"] for segment in plan["segments"]]
    assert all(a != b for a, b in zip(paths, paths[1:]))
    assert paths[:4] == [source["path"] for source in sources]


def test_youtube_skips_a_moving_handheld_camera_when_a_steady_one_covers_the_moment():
    from core.stages.edit import _quality_filtered_sources

    moving = {"motion": 0.9, "face_score": 0.9, "sharpness": 0.5, "exposure": 0.8}
    sony = {"path": "/tmp/sony.mp4", "source_path": "/tmp/sony.mp4", "camera_role": "handheld",
            "director_quality": {"windows": [{"master_start_sec": 0.0, "master_end_sec": 4.0, "signals": moving}]}}
    tripod = {"path": "/tmp/fixed.mp4", "source_path": "/tmp/fixed.mp4", "camera_role": "fixed_rear"}
    stats: dict = {}
    chosen = _quality_filtered_sources([sony, tripod], 0.0, 4.0, stats)
    assert [source["path"] for source in chosen] == ["/tmp/fixed.mp4"]
    assert stats["/tmp/sony.mp4"]["camera_moving_skipped"] == 1
    # Alone, the moving camera is still used (never a black slot).
    assert [source["path"] for source in _quality_filtered_sources([sony], 0.0, 4.0, {})] == ["/tmp/sony.mp4"]


def test_declared_tripod_role_overrides_the_filename_guess():
    from core.shot_quality import _source_role as quality_role
    from core.stages.edit import _with_camera_roles

    coverage = {"sources": [{"path": "/tmp/cam-fixed.mp4", "source_path": "/tmp/cam-fixed.mp4"}]}
    assert quality_role(coverage["sources"][0]) == "handheld"
    declared = _with_camera_roles(coverage, {"camera_roles": {"/tmp/cam-fixed.mp4": "fixed_rear"}})
    assert quality_role(declared["sources"][0]) == "fixed_rear"


def test_wide_shots_from_different_cameras_are_not_counted_as_repeats():
    sources = [{"path": f"/tmp/wide-{i}.mp4", "source_path": f"/tmp/wide-{i}.mp4", "duration_sec": 40.0} for i in range(3)]
    plan = _plan(sources, camera_subjects={source["path"]: "general" for source in sources})
    assert plan["subject_unavoidable_repeats"] == 0
    paths = [segment["source_path"] for segment in plan["segments"]]
    assert all(a != b for a, b in zip(paths, paths[1:]))


def test_generic_camera_types_set_the_steadiness_role():
    from core.shot_quality import _source_role as quality_role
    from core.stages.edit import _with_camera_roles

    coverage = {"sources": [{"path": "/tmp/phone.mp4", "source_path": "/tmp/phone.mp4"},
                            {"path": "/tmp/IMG_1.mov", "source_path": "/tmp/IMG_1.mov"}]}
    declared = _with_camera_roles(coverage, {"camera_types": {"/tmp/phone.mp4": "fixed", "/tmp/IMG_1.mov": "moving"}})
    assert [quality_role(s) for s in declared["sources"]] == ["fixed_rear", "handheld"]
