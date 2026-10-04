from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.project import create_project, load_project
import pytest

from core.shot_review import ThumbnailRenderError, _candidate_covers_slot, _candidate_keys, _review_candidate_pool, _review_segment, _review_signature, _thumbnail_filter, replace_slots, review_items
from core.stages.base import artifact_path


def test_flat_review_thumbnail_includes_authored_crop_motion() -> None:
    graph = _thumbnail_filter({
        "motion": {
            "type": "ken_burns", "movement": "pan_right_top",
            "zoom_start": 3.0, "zoom_end": 3.0,
            "pan_x_start": 0.2, "pan_x_end": 0.8,
            "pan_y_start": 0.25, "pan_y_end": 0.25,
        }
    })
    assert "crop=360:202" in graph
    assert "0.500000" in graph
    assert "ceil(360*3.000000" in graph


def test_review_thumbnails_are_cached_and_replacement_marks_unavailable(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=320x180:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ], check=True)
    project = create_project("Review", str(tmp_path / "Review.zuckervid"))
    plan = {"platform": "reel", "segments": [{
        "clip_path": str(clip), "source_path": str(clip), "clip_start_sec": 0.0,
        "master_start_sec": 0.0, "duration_sec": 1.0, "filename": clip.name,
    }]}
    artifact_path(project, "edit_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    artifact_path(project, "coverage.json").write_text(json.dumps({"sources": [{"path": str(clip)}]}), encoding="utf-8")
    first = review_items(project)
    second = review_items(project)
    assert first[0]["thumbnail"] == second[0]["thumbnail"]
    assert (project.cache_dir / "shot_review").exists()
    result = replace_slots(project, [0])
    assert result["unavailable"] == [0]
    assert result["items"][0]["no_alternative"] is True


def test_review_thumbnail_render_failure_is_propagated_and_visible(tmp_path: Path) -> None:
    clip = tmp_path / "broken.mp4"
    clip.write_bytes(b"not media")
    project = create_project("Review failure", str(tmp_path / "Review failure.zuckervid"))
    artifact_path(project, "edit_plan.json").write_text(json.dumps({"segments": [{
        "clip_path": str(clip), "source_path": str(clip), "clip_start_sec": 0.0,
        "duration_sec": 1.0, "filename": clip.name,
    }]}), encoding="utf-8")

    with pytest.raises(ThumbnailRenderError):
        review_items(project)

    failed = review_items(project, render_missing=False)[0]
    assert failed["thumbnail"] is None
    assert failed["thumbnail_status"] == "failed"
    assert failed["thumbnail_error"]


def test_replacements_never_repeat_a_candidate_for_the_same_slot(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=320x180:rate=10:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ], check=True)
    project = create_project("Review", str(tmp_path / "Review.zuckervid"))
    segments = [{
        "clip_path": str(clip), "source_path": str(clip), "clip_start_sec": 0.0,
        "master_start_sec": 0.0, "duration_sec": 1.0, "filename": clip.name,
    }]
    artifact_path(project, "edit_plan.json").write_text(json.dumps({"segments": segments}), encoding="utf-8")
    sources = [{"path": str(clip), "clip_start_sec": float(start), "shot_quality_score": 10 - start} for start in range(1, 6)]
    artifact_path(project, "coverage.json").write_text(json.dumps({"sources": sources}), encoding="utf-8")

    shown_starts = [0.0]
    for attempt in range(5):
        # Re-loading mid-session exercises the project.json persistence path.
        if attempt == 2:
            project = load_project(str(project.folder))
        result = replace_slots(project, [0])
        assert result["replaced"] == [0]
        current_plan = json.loads(artifact_path(project, "edit_plan.json").read_text(encoding="utf-8"))
        shown_starts.append(current_plan["segments"][0]["clip_start_sec"])

    assert len(set(shown_starts)) == 6
    assert project.data["settings"]["wizard"]["review_exclusions"]["0"]


def test_reel_review_pool_exposes_five_distinct_moments_per_source(tmp_path: Path) -> None:
    clip = tmp_path / "long.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=320x180:rate=10:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ], check=True)
    project = create_project("Review pool", str(tmp_path / "Review pool.zuckervid"))
    segment = {"clip_path": str(clip), "source_path": str(clip), "clip_start_sec": 0.0, "master_start_sec": 0.0, "duration_sec": 1.0}
    coverage = {"platform": "reel", "sources": [{"path": str(clip), "filename": clip.name, "duration_sec": 10.0}]}
    artifact_path(project, "edit_plan.json").write_text(json.dumps({"platform": "reel", "segments": [segment]}), encoding="utf-8")
    artifact_path(project, "coverage.json").write_text(json.dumps(coverage), encoding="utf-8")

    shown_starts = [0.0]
    for _ in range(5):
        result = replace_slots(project, [0])
        assert result["replaced"] == [0]
        plan = json.loads(artifact_path(project, "edit_plan.json").read_text(encoding="utf-8"))
        shown_starts.append(plan["segments"][0]["clip_start_sec"])

    assert len(set(shown_starts)) == 6


def test_replacement_excludes_current_synced_frame_by_clip_offset(tmp_path: Path) -> None:
    clip = tmp_path / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=320x180:rate=10:duration=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
    ], check=True)
    project = create_project("Review", str(tmp_path / "Review.zuckervid"))
    segment = {
        "clip_path": str(clip), "source_path": str(clip), "clip_start_sec": 25.0,
        "clip_offset_sec": 75.0, "master_start_sec": 100.0, "duration_sec": 2.0,
        "filename": clip.name,
    }
    artifact_path(project, "edit_plan.json").write_text(json.dumps({"platform": "youtube", "segments": [segment]}), encoding="utf-8")
    artifact_path(project, "coverage.json").write_text(json.dumps({"platform": "youtube", "sources": [{
        "path": str(clip), "offset_sec": 75.0, "duration_sec": 100.0, "confidence": 9.0,
    }]}), encoding="utf-8")
    result = replace_slots(project, [0])
    assert result["replaced"] == []
    assert result["unavailable"] == [0]
    assert _candidate_keys(segment) & {f"{clip}|75.0", f"{clip}|75.000000"}


def test_spherical_thumbnail_uses_the_segment_landmark_pose() -> None:
    singer = _thumbnail_filter({
        "source_path": "/tmp/equirect.mp4", "projection": "equirect",
        "spherical_shot": {"type": "singer", "yaw": 25, "pitch": -10, "fov": 82},
    })
    drummer = _thumbnail_filter({
        "source_path": "/tmp/equirect.mp4", "projection": "equirect",
        "spherical_shot": {"type": "drummer", "yaw": 210, "pitch": 8, "fov": 125},
    })
    assert singer != drummer
    assert "yaw=25.000" in singer and "h_fov=82.000" in singer
    assert "yaw=-150.000" in drummer and "h_fov=110.000" in drummer


def test_review_uses_current_saved_landmark_and_invalidates_pose_cache(tmp_path: Path) -> None:
    project = create_project("Review spherical", str(tmp_path / "Review spherical.zuckervid"))
    segment = {
        "source_path": "/tmp/equirect.mp4", "projection": "equirect",
        "spherical_shot": {"type": "singer", "shot_id": "singer", "label": "Cantante", "yaw": 10, "pitch": 0, "fov": 90},
    }
    project.data["settings"]["spherical_landmarks"] = {"singer": {"yaw": 330.068, "pitch": -26.556, "fov": 74.8}}
    effective = _review_segment(project, segment)
    assert effective["spherical_shot"]["yaw"] == 330.068
    assert effective["spherical_shot"]["pitch"] == -26.556
    first = _review_signature([effective])
    project.data["settings"]["spherical_landmarks"]["singer"]["yaw"] = 323.336
    changed = _review_segment(project, segment)
    assert _review_signature([changed]) != first
    project.data["settings"]["spherical_landmarks"]["singer"]["yaw"] = -29.932
    signed = _review_segment(project, segment)
    project.data["settings"]["spherical_landmarks"]["singer"]["yaw"] = 330.068
    canonical = _review_segment(project, segment)
    assert _review_signature([signed]) == _review_signature([canonical])


def test_youtube_replacement_requires_verified_coverage_for_the_slot() -> None:
    segment = {"master_start_sec": 20.0, "duration_sec": 4.0}
    valid = {"offset_sec": 10.0, "duration_sec": 20.0, "confidence": 9.0}
    wrong_time = {"offset_sec": 30.0, "duration_sec": 20.0, "confidence": 9.0}
    weak = {"offset_sec": 10.0, "duration_sec": 20.0, "confidence": 4.0, "low_confidence": True}
    assert _candidate_covers_slot(valid, segment, "youtube")
    assert not _candidate_covers_slot(wrong_time, segment, "youtube")
    assert not _candidate_covers_slot(weak, segment, "youtube")


def test_youtube_replacement_keeps_high_confidence_unstable_coverage_available() -> None:
    segment = {"master_start_sec": 20.0, "duration_sec": 4.0}
    iphone = {
        "offset_sec": 10.0, "duration_sec": 20.0, "confidence": 50.959,
        "unstable_sync": True, "low_confidence": False,
    }
    assert _candidate_covers_slot(iphone, segment, "youtube")


def test_spherical_review_pool_uses_slot_record_and_many_distinct_poses(tmp_path: Path) -> None:
    clip = tmp_path / "wide.mp4"
    segment = {
        "clip_path": str(clip), "source_path": str(clip), "projection": "equirect",
        "clip_start_sec": 2.0, "master_start_sec": 102.0, "duration_sec": 2.0,
        "spherical_shot": {"type": "singer", "shot_id": "singer", "label": "Cantante", "yaw": 40.0, "pitch": 0.0, "fov": 95.0},
    }
    coverage = {"sources": [
        {"path": str(clip), "offset_sec": 0.0, "duration_sec": 8.0, "filename": clip.name},
        {"path": str(clip), "offset_sec": 100.0, "duration_sec": 8.0, "filename": clip.name},
    ]}
    pool, _origin = _review_candidate_pool(coverage, [segment], segment, "youtube")
    spherical = [item for item in pool if item.get("spherical_shot")]
    assert len(spherical) >= 12
    assert {round(float(item["clip_start_sec"]), 3) for item in spherical} == {2.0}


def test_replacing_one_pose_keeps_other_project_thumbnails(tmp_path, monkeypatch):
    project = create_project('Stable frames', str(tmp_path / 'stable.zuckervid'))
    source = tmp_path / 'camera.mp4'; source.write_bytes(b'source')
    segments = [dict(source_path=str(source),clip_path=str(source),clip_start_sec=i,master_start_sec=i,duration_sec=1) for i in (0,2)]
    artifact_path(project,'edit_plan.json').write_text(json.dumps(dict(segments=segments)))
    artifact_path(project,'coverage.json').write_text('{}')
    first = review_items(project,render_missing=False)
    root = project.cache_dir / 'shot_review' / 'assets-v2'
    for item in first:(root / item['thumbnail_asset']).write_bytes(b'complete thumbnail')
    segments[0]['clip_start_sec'] = .5
    artifact_path(project,'edit_plan.json').write_text(json.dumps(dict(segments=segments)))
    after = review_items(project,render_missing=False)
    assert after[0]['thumbnail_status'] == 'missing'
    assert after[1]['thumbnail'] == review_items(project,render_missing=False)[1]['thumbnail']
    assert after[1]['thumbnail_status'] == 'ready'


def test_project_candidate_reserve_survives_reopen(tmp_path, monkeypatch):
    import core.shot_review as module
    project = create_project('Reserve', str(tmp_path / 'reserve.zuckervid'))
    segment = dict(source_path='/tmp/source.mp4',master_start_sec=10,duration_sec=2)
    calls=[]
    monkeypatch.setattr(module,'_review_candidate_pool',lambda *args:(calls.append(1) or [dict(path='/tmp/alternative.mp4')], 'test reserve'))
    first=module._project_candidate_pool(project,{},[segment],segment,'youtube')
    reopened=load_project(str(project.folder))
    assert module._project_candidate_pool(reopened,{},[segment],segment,'youtube') == first
    assert calls == [1]
