from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.project import create_project
from core.shot_review import replace_slots, review_items
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
