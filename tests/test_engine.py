from __future__ import annotations

from pathlib import Path

import pytest

from core.engine import PipelineEngine
from core.project import Project, create_project
from core.stages.base import ProgressCallback, Stage, stable_fingerprint
from core.stages.cut import CutStage
from core.stages.edit import EditStage
from core.stages.base import artifact_path, write_artifact_json


class RecordingStage(Stage):
    def __init__(self, name: str, calls: list[str], dependencies: list[str] | None = None, fail: bool = False):
        self.name = name
        self.dependencies = dependencies or []
        self.calls = calls
        self.fail = fail

    def inputs_fingerprint(self, project: Project) -> str:
        return stable_fingerprint({"name": self.name, "settings": project.data["settings"].get(self.name, {})})

    def outputs(self, project: Project) -> dict[str, str]:
        return {}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, str]:
        self.calls.append(self.name)
        progress_callback(100, f"{self.name} done")
        if self.fail:
            raise RuntimeError(f"{self.name} failed")
        return {}


def make_project(tmp_path: Path) -> Project:
    return create_project("Test", str(tmp_path / "Test.zuckervid"))


def test_engine_runs_dependencies_in_order_and_uses_cache(tmp_path):
    calls: list[str] = []
    engine = PipelineEngine()
    engine.stages = {
        "a": RecordingStage("a", calls),
        "b": RecordingStage("b", calls, ["a"]),
    }
    project = make_project(tmp_path)
    project.data["stages"] = {
        "a": {"status": "pending", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": None},
        "b": {"status": "pending", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": None},
    }

    engine.run_sync(project, "b")
    engine.run_sync(project, "b")

    assert calls == ["a", "b"]
    assert project.data["stages"]["a"]["status"] == "done"
    assert project.data["stages"]["b"]["status"] == "done"


def test_engine_marks_downstream_stale_when_upstream_reruns(tmp_path):
    calls: list[str] = []
    engine = PipelineEngine()
    engine.stages = {
        "a": RecordingStage("a", calls),
        "b": RecordingStage("b", calls, ["a"]),
    }
    project = make_project(tmp_path)
    project.data["stages"] = {
        "a": {"status": "pending", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": None},
        "b": {"status": "done", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": "old"},
    }
    project.data["settings"]["a"] = {"changed": True}

    engine.run_sync(project, "a")

    assert calls == ["a"]
    assert project.data["stages"]["b"]["status"] == "stale"


def test_reel_pipeline_plan_has_no_sync_dependency(tmp_path):
    project = make_project(tmp_path)
    project.data["settings"]["wizard"] = {"platform": "reel"}
    engine = PipelineEngine()
    assert engine._plan("cut", project) == ["cut"]


def test_non_reel_cut_pipeline_keeps_sync_dependency(tmp_path):
    project = make_project(tmp_path)
    project.data["settings"]["wizard"] = {"platform": "youtube"}
    engine = PipelineEngine()
    assert engine._plan("cut", project) == ["ingest", "sync", "cut"]


def test_engine_failure_marks_downstream_blocked(tmp_path):
    calls: list[str] = []
    engine = PipelineEngine()
    engine.stages = {
        "a": RecordingStage("a", calls, fail=True),
        "b": RecordingStage("b", calls, ["a"]),
    }
    project = make_project(tmp_path)
    project.data["stages"] = {
        "a": {"status": "pending", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": None},
        "b": {"status": "pending", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": None},
    }

    with pytest.raises(RuntimeError):
        engine.run_sync(project, "b")

    assert project.data["stages"]["a"]["status"] == "failed"
    assert project.data["stages"]["b"]["status"] == "blocked"


def test_cut_and_edit_fingerprints_follow_artifact_contents(tmp_path):
    project = make_project(tmp_path)
    sync_path = artifact_path(project, "sync_map.json")
    write_artifact_json(sync_path, {"clips": {"sony": {"offset_sec": 10.0}}})
    cut = CutStage()
    first_cut = cut.inputs_fingerprint(project)
    write_artifact_json(sync_path, {"clips": {"sony": {"offset_sec": 20.0}}})
    assert cut.inputs_fingerprint(project) != first_cut

    coverage_path = artifact_path(project, "coverage.json")
    write_artifact_json(coverage_path, {"sources": [{"filename": "sony.MP4"}]})
    edit = EditStage()
    first_edit = edit.inputs_fingerprint(project)
    write_artifact_json(coverage_path, {"sources": [{"filename": "iphone.MOV"}]})
    assert edit.inputs_fingerprint(project) != first_edit
