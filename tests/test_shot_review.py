from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.project import create_project, load_project
from core.shot_review import _thumbnail_filter, replace_slots, review_items
from core.stages.base import artifact_path


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
    assert "yaw=-150.000" in drummer and "h_fov=125.000" in drummer
