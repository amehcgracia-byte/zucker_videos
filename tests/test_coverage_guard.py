import json

import pytest

from core.coverage_guard import CoverageInvariantError, assert_all_dropbox_videos_used, reel_capacity_warning
from core.project import create_project


def _project(tmp_path, *, used_second=True):
    first = tmp_path / "first.mp4"
    second = tmp_path / "iphone.mp4"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    project = create_project("coverage", str(tmp_path / "coverage.zuckervid"))
    project.data["inputs"]["videos"] = [{"path": str(first.resolve())}, {"path": str(second.resolve())}]
    segments = [{"source_path": str(first.resolve()), "duration_sec": 4.0}]
    if used_second:
        segments.append({"source_path": str(second.resolve()), "duration_sec": 4.0})
    plan = {
        "segments": segments,
        "clip_diagnostics": [
            {
                "source_path": str(second.resolve()),
                "confidence": 4.932,
                "threshold": 6.0,
                "low_confidence": True,
                "unstable_sync": True,
                "verification": {"delta_sec": 310.102},
            }
        ],
        "excluded_clips": [
            {
                "diagnostic": {"source_path": str(second.resolve())},
                "reason": "unstable sync (310102 ms between checks)",
            }
        ],
    }
    (project.artifacts_dir / "edit_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return project


def test_all_registered_videos_must_have_a_segment(tmp_path):
    with pytest.raises(CoverageInvariantError, match="iphone.mp4.*310.102s"):
        assert_all_dropbox_videos_used(_project(tmp_path, used_second=False))


def test_all_registered_videos_used_passes(tmp_path):
    assert_all_dropbox_videos_used(_project(tmp_path, used_second=True))


def test_sync_only_gap_can_be_explicitly_allowed(tmp_path):
    project = _project(tmp_path, used_second=False)
    assert_all_dropbox_videos_used(project, allow_sync_missing=True)


def test_non_sync_gap_cannot_be_allowed(tmp_path):
    project = _project(tmp_path, used_second=False)
    plan = json.loads((project.artifacts_dir / "edit_plan.json").read_text(encoding="utf-8"))
    plan["clip_diagnostics"] = [{"source_path": str((tmp_path / "iphone.mp4").resolve())}]
    plan["excluded_clips"] = []
    (project.artifacts_dir / "edit_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(CoverageInvariantError):
        assert_all_dropbox_videos_used(project, allow_sync_missing=True)


def test_reel_allows_only_physically_impossible_source_shortfall(tmp_path):
    project = _project(tmp_path, used_second=False)
    plan = json.loads((project.artifacts_dir / "edit_plan.json").read_text(encoding="utf-8"))
    plan["platform"] = "reel"
    plan["segments"] = plan["segments"][:1]
    (project.artifacts_dir / "edit_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    assert reel_capacity_warning(project)
    assert_all_dropbox_videos_used(project)


def test_reel_still_blocks_omission_when_there_are_enough_slots(tmp_path):
    project = _project(tmp_path, used_second=False)
    plan = json.loads((project.artifacts_dir / "edit_plan.json").read_text(encoding="utf-8"))
    plan["platform"] = "reel"
    plan["segments"] = plan["segments"] * 2
    (project.artifacts_dir / "edit_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(CoverageInvariantError):
        assert_all_dropbox_videos_used(project)
