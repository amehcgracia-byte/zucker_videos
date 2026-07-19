"""Pipeline stage registry and single-worker execution engine."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from core.project import Project, utc_now
from core.stages.base import Stage
from core.stages.cut import CutStage
from core.stages.edit import EditStage
from core.stages.export import ExportStage
from core.stages.ingest import IngestStage
from core.stages.sync import SyncStage

LOGGER = logging.getLogger(__name__)


class StageNotFoundError(ValueError):
    """Raised when an unknown stage is requested."""


class StageBlockedError(RuntimeError):
    """Raised when a stage cannot run because a dependency failed."""


class PipelineEngine:
    """Run registered stages in dependency order with cache and progress tracking."""

    def __init__(self) -> None:
        self.stages: dict[str, Stage] = {
            stage.name: stage
            for stage in (IngestStage(), SyncStage(), CutStage(), EditStage(), ExportStage())
        }
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zucker-stage")
        self._lock = threading.RLock()
        self._current: dict[str, Any] | None = None
        self._future: Future[None] | None = None

    def shutdown(self) -> None:
        """Stop the background executor."""
        self._executor.shutdown(wait=False, cancel_futures=False)

    def status(self, project: Project | None) -> dict[str, Any]:
        """Return all stage states and current progress."""
        with self._lock:
            current = dict(self._current) if self._current else None
        stages = project.snapshot()["stages"] if project else {}
        readiness = self._readiness(project)
        return {
            "stages": stages,
            "readiness": readiness,
            "current": current,
            "busy": current is not None,
        }

    def submit(self, project: Project, stage_name: str) -> Future[None]:
        """Submit a stage run in the single background worker."""
        self._require_stage(stage_name)
        readiness = self._readiness(project).get(stage_name, {})
        if not readiness.get("ready", False):
            reasons = "; ".join(readiness.get("reasons") or ["stage is not ready"])
            raise StageBlockedError(f"{stage_name} is not ready: {reasons}")
        with self._lock:
            if self._current is not None:
                raise RuntimeError("A stage is already running")
            self._current = {"stage": stage_name, "percent": 0, "message": "Queued"}
            self._future = self._executor.submit(self._run_and_clear, project, stage_name)
            return self._future

    def run_sync(self, project: Project, stage_name: str) -> None:
        """Run a stage synchronously; primarily used by tests."""
        self._require_stage(stage_name)
        with self._lock:
            if self._current is not None:
                raise RuntimeError("A stage is already running")
            self._current = {"stage": stage_name, "percent": 0, "message": "Queued"}
        self._run_and_clear(project, stage_name)

    def _run_and_clear(self, project: Project, stage_name: str) -> None:
        try:
            self._run_with_dependencies(project, stage_name)
        finally:
            with self._lock:
                self._current = None
                self._future = None

    def _run_with_dependencies(self, project: Project, stage_name: str) -> None:
        project.refresh_input_records()
        planned = self._plan(stage_name)
        reran: list[str] = []
        for name in planned:
            stage = self.stages[name]
            dependency_failure = self._first_failed_dependency(stage, project)
            if dependency_failure:
                self._mark_blocked(project, name, dependency_failure)
                self._mark_downstream_stale(project, name)
                project.save()
                raise StageBlockedError(f"{name} blocked by failed dependency {dependency_failure}")

            fingerprint = stage.inputs_fingerprint(project)
            state = project.data["stages"][name]
            cache_hit = state.get("status") == "done" and state.get("fingerprint") == fingerprint
            if cache_hit:
                self._set_progress(name, 100, "Cache hit")
                continue

            if reran:
                project.data["stages"][name]["status"] = "stale"

            self._run_one(project, stage, fingerprint)
            reran.append(name)
            self._mark_downstream_stale(project, name)
            project.save()

    def _run_one(self, project: Project, stage: Stage, fingerprint: str) -> None:
        log = _stage_logger(project, stage.name)
        state = project.data["stages"][stage.name]
        state.update(
            {
                "status": "running",
                "started_at": utc_now(),
                "finished_at": None,
                "error": None,
            }
        )
        project.save()

        def progress(percent: int, message: str) -> None:
            bounded = max(0, min(100, int(percent)))
            self._set_progress(stage.name, bounded, message)
            log.info("%s%% %s", bounded, message)

        try:
            progress(0, f"Starting {stage.name}")
            outputs = stage.run(project, progress)
            state.update(
                {
                    "status": "done",
                    "finished_at": utc_now(),
                    "outputs": outputs,
                    "error": None,
                    "fingerprint": fingerprint,
                }
            )
            progress(100, f"{stage.name} complete")
        except Exception as exc:
            LOGGER.exception("Stage %s failed", stage.name)
            log.exception("Stage failed")
            state.update(
                {
                    "status": "failed",
                    "finished_at": utc_now(),
                    "error": str(exc),
                }
            )
            project.save()
            self._mark_downstream_blocked(project, stage.name)
            project.save()
            raise

    def _set_progress(self, stage_name: str, percent: int, message: str) -> None:
        with self._lock:
            self._current = {"stage": stage_name, "percent": percent, "message": message}

    def _plan(self, stage_name: str) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []

        def visit(name: str) -> None:
            if name in seen:
                return
            self._require_stage(name)
            seen.add(name)
            for dependency in self.stages[name].dependencies:
                visit(dependency)
            ordered.append(name)

        visit(stage_name)
        return ordered

    def _require_stage(self, stage_name: str) -> None:
        if stage_name not in self.stages:
            raise StageNotFoundError(f"Unknown stage: {stage_name}")

    def _first_failed_dependency(self, stage: Stage, project: Project) -> str | None:
        for dependency in stage.dependencies:
            status = project.data["stages"][dependency]["status"]
            if status in {"failed", "blocked"}:
                return dependency
        return None

    def _mark_blocked(self, project: Project, stage_name: str, dependency: str) -> None:
        state = project.data["stages"][stage_name]
        state.update(
            {
                "status": "blocked",
                "finished_at": utc_now(),
                "error": f"Blocked by failed dependency: {dependency}",
            }
        )

    def _mark_downstream_stale(self, project: Project, stage_name: str) -> None:
        names = list(self.stages)
        start = names.index(stage_name) + 1
        for name in names[start:]:
            state = project.data["stages"][name]
            if state["status"] in {"done", "failed", "blocked"}:
                state["status"] = "stale"
                state["error"] = None

    def _mark_downstream_blocked(self, project: Project, stage_name: str) -> None:
        names = list(self.stages)
        start = names.index(stage_name) + 1
        for name in names[start:]:
            state = project.data["stages"][name]
            state["status"] = "blocked"
            state["error"] = f"Blocked by failed dependency: {stage_name}"

    def _readiness(self, project: Project | None) -> dict[str, Any]:
        if project is None:
            return {name: {"ready": False, "reasons": ["No project is open"]} for name in self.stages}
        readiness: dict[str, Any] = {}
        for name, stage in self.stages.items():
            reasons: list[str] = []
            if name == "ingest":
                inputs = project.data.get("inputs", {})
                if not inputs.get("videos"):
                    reasons.append("No videos are registered")
            if name == "sync":
                inputs = project.data.get("inputs", {})
                if not inputs.get("master"):
                    reasons.append("Master audio is not registered")
            if name in {"cut", "edit", "export"}:
                inputs = project.data.get("inputs", {})
                if not inputs.get("songs"):
                    reasons.append("songs.json is not registered")
            for dependency in stage.dependencies:
                status = project.data["stages"][dependency]["status"]
                if status != "done":
                    reasons.append(f"{dependency} is {status}")
            state = project.data["stages"][name]
            readiness[name] = {
                "ready": not reasons and state["status"] not in {"running", "blocked"},
                "reasons": reasons,
            }
        return readiness


def _stage_logger(project: Project, stage_name: str) -> logging.Logger:
    logger = logging.getLogger(f"zucker_videos.stage.{stage_name}.{id(project)}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        log_path = Path(project.cache_dir) / "logs" / f"{stage_name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
