"""Three-step wizard orchestration over the existing pipeline stages."""

from __future__ import annotations

import logging
import json
import re
import time
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.build_info import build_info
from core.coverage_guard import CoverageInvariantError, assert_all_dropbox_videos_used, coverage_gaps, reel_capacity_warning
from core.messages import t
from core.project import Project, create_project
from core.project_lock import ProjectPipelineLock
from core.stages.backstage import BackstageAnalysisStage, BackstageEditStage, BackstageExportStage
from core.stages.base import ProgressDetail, artifact_path, write_artifact_json
from core.stages.cut import CutStage
from core.stages.edit import EditStage
from core.stages.export import ExportStage, TRANSITION_LIBRARY
from core.stages.ingest import IngestStage
from core.stages.sync import SyncStage, load_song_boundaries, set_manual_override, set_manual_override_ranges
from core.shot_review import review_items
from core.throughput import estimated_export_seconds, record_export_throughput
from server.inbox import app_home, load_global_config, register_selected_inputs, save_global_config
from server.inbox import classify_file

LOGGER = logging.getLogger(__name__)


class WizardCancelled(Exception):
    """Raised from within a running stage's progress callback to unwind it cleanly."""


@dataclass
class WizardJob:
    """Process-local wizard job state."""

    id: str
    status: str = "running"
    progress: int = 0
    message: str = t("working")
    detail: str | None = None
    stage: str = "prepare"
    stage_progress: int = 0
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    progress_updated_at: float | None = None
    error: str | None = None
    technical_details: str | None = None
    result: dict[str, Any] | None = None
    project_path: str | None = None
    logs_path: str | None = None
    # Wall-clock seconds this machine is expected to take for the whole run,
    # predicted from previously measured throughput before any rendering
    # starts. None means "not enough history yet" -- the UI must then say it is
    # still estimating rather than show an optimistic guess.
    estimated_total_seconds: float | None = None
    started_at: float | None = None
    input_warnings: list[str] = field(default_factory=list)
    paper_edit_available: bool = False
    project_lock: ProjectPipelineLock | None = field(default=None, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    cancel_requested_at: float | None = None


def serialize_wizard_job(job: WizardJob) -> dict[str, Any]:
    """Return only JSON-safe wizard state for API responses."""
    snapshot = dict(job.__dict__)
    snapshot["tasks"] = [dict(task) for task in dict(job.tasks).values()]
    snapshot.pop("cancel_event", None)
    snapshot["project_lock"] = bool(job.project_lock)
    snapshot["cancel_requested"] = bool(job.cancel_requested_at)
    return snapshot


def is_single_source_reel(project: Project) -> bool:
    """Return whether a Reel plan is the one-take, no-review workflow."""
    wizard = (project.data.get("settings") or {}).get("wizard") or {}
    if wizard.get("platform") != "reel" or len(project.data.get("inputs", {}).get("videos") or []) != 1:
        return False
    plan_path = ((project.data.get("stages") or {}).get("edit") or {}).get("outputs", {}).get("edit_plan")
    if not plan_path:
        plan_path = str(project.artifacts_dir / "edit_plan.json")
    try:
        with Path(plan_path).open("r", encoding="utf-8") as fh:
            plan = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    segments = plan.get("segments") or []
    return bool(segments) and all(segment.get("single_source_continuous") for segment in segments)


def _platform_needs_sync(platform: str, project: Project | None = None, master_path: str | None = None) -> bool:
    """Return whether the selected mode has a separate master to synchronize."""
    if platform in {"reel", "backstage"}:
        return False
    if platform != "360":
        return True
    if str(master_path or "").strip():
        return True
    if project is not None:
        master = (project.data.get("inputs") or {}).get("master") or {}
        return bool(str(master.get("path") or "").strip())
    return False


def _aggregate_segment_progress(progress_by_index: dict[int, float], total: int) -> tuple[int, int]:
    """Return aggregate percentage and completed count for parallel segment work."""
    total = max(1, int(total))
    bounded = {
        int(index): max(0.0, min(1.0, float(value)))
        for index, value in progress_by_index.items()
    }
    units = sum(bounded.values())
    completed = sum(1 for value in bounded.values() if value >= 0.999)
    percent = int(round(100.0 * units / total))
    return max(0, min(100, percent)), completed


@dataclass
class WizardRunner:
    """Runs one wizard render job at a time."""

    _lock: threading.RLock = field(default_factory=threading.RLock)
    _job: WizardJob | None = None
    _thread: threading.Thread | None = None
    _prepared_project: Project | None = None
    _finish_requested: bool = False

    def cancel(self) -> bool:
        """Request cancellation for the current job without losing the signal.

        The event belongs to this job, so a reset or a later job cannot clear
        the cancellation request while the worker is still unwinding.
        """
        with self._lock:
            job = self._job
            if not job or job.status not in {"running", "cancelling"}:
                return False
            job.cancel_event.set()
            job.cancel_requested_at = time.time()
            if job.status == "running":
                job.status = "cancelling"
                job.message = "Cancelling…"
                job.detail = "Stopping the current stage…"
            LOGGER.info("Wizard cancellation requested project=%s stage=%s", job.project_path, job.stage)
            return True

    def _mark_cancelled(self, job: WizardJob) -> None:
        job.status = "cancelled"
        job.message = t("cancelled")
        job.error = None
        job.technical_details = None

    def prepare(self, *, name: str, master_path: str, songs_path: str | None, video_paths: list[str], platform: str = "youtube") -> WizardJob:
        """Create/register a project and run ingest + sync while the user chooses an edit type."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A previous wizard job is still stopping")
            if self._job and self._job.status == "running":
                raise RuntimeError("A video is already being processed")
            job = WizardJob(id="current", message=t("listening"))
            self._job = job
            self._prepared_project = None
            self._finish_requested = False
            thread = threading.Thread(
                target=self._prepare_project,
                kwargs={"job": job, "name": name, "master_path": master_path, "songs_path": songs_path, "video_paths": video_paths, "platform": platform},
                daemon=True,
                name="zucker-wizard-prepare",
            )
            self._thread = thread
            thread.start()
            return job

    def prepare_existing(self, project: Project, platform: str | None = None) -> WizardJob:
        """Run ingest + sync for an existing matching project."""
        previous_thread: threading.Thread | None = None
        with self._lock:
            if self._job and self._job.status == "running":
                # Opening another saved project is a project switch, not a
                # request to queue a second export. Stop the previous
                # prepare/resume job cooperatively before attaching the new
                # project; otherwise the desktop UI gets a misleading 500.
                if same_project_path(self._job.project_path, str(project.folder)):
                    return self._job
                self._job.cancel_event.set()
                self._job.cancel_requested_at = time.time()
                self._job.status = "cancelling"
                self._job.message = "Cancelling…"
                previous_thread = self._thread
        if previous_thread and previous_thread.is_alive():
            previous_thread.join(timeout=10.0)
        if previous_thread and previous_thread.is_alive():
            raise RuntimeError("The previous wizard job is still cancelling; wait a few seconds and try again")
        with self._lock:
            if self._job and self._job.status == "cancelling":
                self._job.status = "cancelled"
                self._job.message = t("cancelled")
                self._job.error = None
                self._job.technical_details = None
            job = WizardJob(id="current", message=t("listening"))
            _attach_project(job, project)
            self._job = job
            self._prepared_project = None
            self._finish_requested = False
            thread = threading.Thread(
                target=self._prepare_existing_project,
                kwargs={"job": job, "project": project, "platform": platform or str((project.data.get("settings", {}).get("wizard") or {}).get("platform") or "youtube")},
                daemon=True,
                name="zucker-wizard-prepare-existing",
            )
            self._thread = thread
            thread.start()
            return job

    def adopt_prepared_project(self, project: Project) -> WizardJob:
        """Use an already-prepared project as the wizard's Step 2 state."""
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("A video is already being processed")
            job = WizardJob(
                id="current",
                status="waiting_choice",
                progress=95,
                message=t("ready_to_edit"),
                detail=t("choose_edit_type"),
                stage="sync",
            )
            _attach_project(job, project)
            self._job = job
            self._thread = None
            self._prepared_project = project
            return job

    def start(
        self,
        *,
        name: str,
        platform: str,
        song_choice: int | str | None,
        audio_trim: dict[str, float] | None,
        spherical_landmarks: dict[str, Any] | None,
        spherical_landmark_profiles: dict[str, Any] | None,
        spherical_source_path: str | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        backstage_target_duration_sec: float | None,
        reel_aspect: str | None,
        reel_mix_vertical_ratio: str | float | None,
        reel_cuts_per_source: float | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        backstage_messages: list[str] | None,
        transition_type: str | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> WizardJob:
        """Create/register a project and run the simplified render chain."""
        with self._lock:
            if self._thread and self._thread.is_alive() and self._job and self._job.status != "running":
                raise RuntimeError("A previous wizard job is still stopping")
            if self._job and self._job.status == "running":
                if self._finish_requested or same_project_path(self._job.project_path, str(self._prepared_project.folder) if self._prepared_project else None):
                    return self._job
                job = self._job
                self._finish_requested = True
                prepare_thread = self._thread
                thread = threading.Thread(
                    target=self._finish_after_prepare,
                    kwargs={
                        "job": job,
                        "prepare_thread": prepare_thread,
                        "name": name,
                        "platform": platform,
                        "song_choice": song_choice,
                        "audio_trim": audio_trim,
                        "spherical_landmarks": spherical_landmarks,
                        "spherical_landmark_profiles": spherical_landmark_profiles,
                        "spherical_source_path": spherical_source_path,
                        "camera_role_weights": camera_role_weights,
                        "fixed_rear_motion": fixed_rear_motion,
                        "spherical_motion": spherical_motion,
                        "spherical_mode": spherical_mode,
                        "spherical_sweep": spherical_sweep,
                        "sweep_speed_deg_per_sec": sweep_speed_deg_per_sec,
                        "reel_duration_sec": reel_duration_sec,
                        "backstage_target_duration_sec": backstage_target_duration_sec,
                        "reel_aspect": reel_aspect,
                        "reel_mix_vertical_ratio": reel_mix_vertical_ratio,
                        "reel_cuts_per_source": reel_cuts_per_source,
                        "reel_text_overlays": reel_text_overlays,
                        "reel_image_overlays": reel_image_overlays,
                        "backstage_messages": backstage_messages,
                        "transition_type": transition_type,
                        "master_path": master_path,
                        "songs_path": songs_path,
                        "video_paths": video_paths,
                    },
                    daemon=True,
                    name="zucker-wizard-queued-finish",
                )
                self._thread = thread
                thread.start()
                return job
            job = WizardJob(id="current")
            self._job = job
            self._finish_requested = False
            project = self._prepared_project
            target = self._finish if project else self._run
            kwargs = {
                "job": job,
                "name": name,
                "platform": platform,
                "song_choice": song_choice,
                "audio_trim": audio_trim,
                "spherical_landmarks": spherical_landmarks,
                "spherical_landmark_profiles": spherical_landmark_profiles,
                "spherical_source_path": spherical_source_path,
                "camera_role_weights": camera_role_weights,
                "fixed_rear_motion": fixed_rear_motion,
                "spherical_motion": spherical_motion,
                "spherical_mode": spherical_mode,
                "spherical_sweep": spherical_sweep,
                "sweep_speed_deg_per_sec": sweep_speed_deg_per_sec,
                "reel_duration_sec": reel_duration_sec,
                "backstage_target_duration_sec": backstage_target_duration_sec if platform == "backstage" else 180.0,
                "reel_aspect": reel_aspect,
                "reel_mix_vertical_ratio": reel_mix_vertical_ratio,
                "reel_cuts_per_source": reel_cuts_per_source,
                "reel_text_overlays": reel_text_overlays,
                "reel_image_overlays": reel_image_overlays,
                "backstage_messages": backstage_messages,
                "transition_type": transition_type,
                "master_path": master_path,
                "songs_path": songs_path,
                "video_paths": video_paths,
            }
            if project:
                kwargs["project"] = project
                self._prepared_project = None
            thread = threading.Thread(
                target=target,
                kwargs={
                    **kwargs,
                },
                daemon=True,
                name="zucker-wizard",
            )
            self._thread = thread
            thread.start()
            return job

    def start_existing(self, project: Project, **options: Any) -> WizardJob:
        """Start the complete pipeline again for an opened project."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A previous wizard job is still stopping")
            if self._job and self._job.status == "running":
                return self._job
            job = WizardJob(id="current", message=t("listening"))
            _attach_project(job, project)
            if not _acquire_job_project_lock(job, project):
                raise RuntimeError("This project's pipeline is already running")
            self._job = job
            self._prepared_project = None
            self._finish_requested = False
            thread = threading.Thread(
                target=self._run_existing_from_scratch,
                kwargs={"job": job, "project": project, "options": options},
                daemon=True,
                name="zucker-wizard-start-existing",
            )
            self._thread = thread
            thread.start()
            return job

    def _run_existing_from_scratch(self, *, job: WizardJob, project: Project, options: dict[str, Any]) -> None:
        try:
            platform = str(options.get("platform") or "youtube")
            for stage in (project.data.get("stages") or {}).values():
                stage.update({"status": "pending", "started_at": None, "finished_at": None, "outputs": {}, "error": None, "fingerprint": None})
            register_selected_inputs(
                project,
                master_path=options.get("master_path") or None,
                songs_path=options.get("songs_path"),
                video_paths=options.get("video_paths") or [],
                append_videos=False,
            )
            project.save()
            self._run_stage(job, project, IngestStage(), 0, 22, t("listening"))
            if _platform_needs_sync(platform, project=project, master_path=options.get("master_path")):
                self._run_stage(job, project, SyncStage(), 22, 48, t("syncing_audio"))
            else:
                job.progress = 22
            self._finish(job=job, project=project, **options)
        except WizardCancelled:
            self._mark_cancelled(job)
            _release_job_project_lock(job)
        except Exception as exc:
            LOGGER.exception("Wizard fresh rerun failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")
            _release_job_project_lock(job)

    def status(self) -> dict[str, Any]:
        """Return the current wizard job snapshot."""
        with self._lock:
            if not self._job:
                return {"status": "idle", "progress": 0, "message": "Idle"}
            # The lock is an in-process resource, never API data.
            return serialize_wizard_job(self._job)

    def render_review(self, project: Project | None) -> WizardJob:
        """Resume a completed edit plan after the user approves its shots."""
        with self._lock:
            if not project or not self._job or self._job.status != "waiting_review":
                raise RuntimeError("There is no shot review waiting to render")
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A video is already being processed")
            job = self._job
            thread = threading.Thread(target=self._render_review_job, args=(job, project), daemon=True, name="zucker-review-render")
            self._thread = thread
            thread.start()
            return job

    def adopt_paper_edit(self, project: Project) -> WizardJob:
        """Reattach to Backstage's persisted paper edit without rendering."""
        with self._lock:
            if self._job and self._job.status == "running":
                return self._job
            job = WizardJob(
                id="current", status="waiting_paper_edit", progress=70,
                message="Paper edit ready", detail="Review the written sequence before rendering.",
                stage="paper_edit", paper_edit_available=True,
            )
            _attach_project(job, project)
            if not _acquire_job_project_lock(job, project):
                raise RuntimeError("This project's pipeline is already active; reconnect instead of starting another")
            self._job = job
            self._prepared_project = project
            self._thread = None
            return job

    def approve_paper_edit(self, project: Project | None, rejected: list[str] | None = None) -> WizardJob:
        """Apply paper-edit rejections and render only after explicit approval."""
        with self._lock:
            if not project:
                raise RuntimeError("There is no Backstage paper edit to approve")
            if not self._job or self._job.status != "waiting_paper_edit":
                self.adopt_paper_edit(project)
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A video is already being processed")
            job = self._job
            paper_path = artifact_path(project, "backstage_paper_edit.json")
            paper = json.loads(paper_path.read_text(encoding="utf-8"))
            rejected_set = {str(value) for value in (rejected or [])}
            allowed = [cut for cut in paper.get("cuts") or [] if str(cut.get("id")) not in rejected_set and cut.get("mark") != "drop"]
            closings = [cut for cut in allowed if cut.get("mark") == "closing"]
            allowed = [cut for cut in allowed if cut.get("mark") != "closing"] + closings
            if not allowed:
                raise RuntimeError("Paper edit must retain at least one cut")
            plan_path = artifact_path(project, "backstage_edit.json")
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            keep_ids = {str(cut.get("id")) for cut in allowed}
            segments = [segment for index, segment in enumerate(plan.get("segments") or []) if f"backstage-{index:04d}" in keep_ids and segment.get("paper_mark") != "drop"]
            closing_segments = [segment for segment in segments if segment.get("paper_mark") == "closing"]
            segments = [segment for segment in segments if segment.get("paper_mark") != "closing"] + closing_segments
            plan["segments"] = segments
            plan["cut_count"] = max(0, len(segments) - 1)
            plan["selected_duration_sec"] = round(sum(float(item.get("duration_sec") or 0.0) for item in segments), 3)
            plan["paper_edit_approved"] = True
            plan["paper_edit_rejected"] = sorted(rejected_set)
            write_artifact_json(plan_path, plan)
            for cut in paper.get("cuts") or []:
                cut["status"] = "rejected" if str(cut.get("id")) in rejected_set else "approved"
            paper["approved"] = True
            paper["cuts"] = [cut for cut in paper.get("cuts") or [] if cut["status"] == "approved"]
            write_artifact_json(paper_path, paper)
            job.status = "running"
            job.stage = "export"
            job.progress = 70
            job.message = "Rendering approved Backstage paper edit"
            job.detail = f"{len(segments)} approved cuts"
            job.paper_edit_available = False
            thread = threading.Thread(target=self._render_paper_edit_job, args=(job, project), daemon=True, name="zucker-paper-edit-render")
            self._thread = thread
            thread.start()
            return job

    def _render_paper_edit_job(self, job: WizardJob, project: Project) -> None:
        started_at = time.monotonic()
        try:
            outputs = self._run_stage(job, project, BackstageExportStage(), 70, 100, "Rendering approved Backstage paper edit")
            _finish_export_job(job, project, outputs)
            _remember_export_throughput(project, "backstage", json.loads(Path(outputs["export_manifest"]).read_text(encoding="utf-8"))["exports"][0], time.monotonic() - started_at)
        except WizardCancelled:
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Paper edit render failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")
        finally:
            _release_job_project_lock(job)

    def _render_review_job(self, job: WizardJob, project: Project) -> None:
        started_at = time.monotonic()
        try:
            job.status = "running"
            job.stage = "export"
            job.progress = max(70, int(job.progress or 70))
            job.message = t("exporting_video")
            _assert_coverage(project)
            self._run_stage(job, project, ExportStage(), job.progress, 100, t("exporting_video"))
            manifest_path = Path(project.data["stages"]["export"]["outputs"]["export_manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            export = manifest["exports"][0]
            export_path = Path(export["path"])
            if export_path.suffix.lower() != ".mp4" or not export_path.exists():
                raise RuntimeError(f"Export did not produce an MP4: {export_path}")
            _write_stage_log(project, "wizard", f"EXPORT OUTPUT {export_path.resolve()} ({export_path.stat().st_size} bytes)")
            job.status = "done"
            job.progress = 100
            job.message = t("done")
            job.result = {
                "project_path": str(project.folder), "filename": export_path.name, "path": str(export_path),
                "media_url": "/api/v1/wizard/result", "platform": project.data.get("settings", {}).get("wizard", {}).get("platform"),
                "logs_path": str(project.cache_dir / "logs"), "cut_count": export.get("cut_count"),
                "camera_usage": export.get("camera_usage"), "spherical_shot_usage": export.get("spherical_shot_usage") or {},
                "warnings": export.get("warnings") or [], "excluded_clips": export.get("excluded_clips") or [],
            }
            _remember_export_throughput(project, job.result.get("platform") or "reel", export, time.monotonic() - started_at)
        except WizardCancelled:
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Review render failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")

    def reset(self) -> bool:
        """Forget wizard state only after the worker has actually stopped.

        Reset used to release the project lock and clear the cancellation event
        while the daemon thread was still rendering. That made Start again
        revive the old worker and corrupt the runner state.
        """
        with self._lock:
            thread = self._thread
            if thread and thread.is_alive():
                if self._job:
                    self._job.cancel_event.set()
                    self._job.cancel_requested_at = time.time()
                    self._job.status = "cancelling"
                    self._job.message = "Cancelling…"
                    self._job.detail = "Waiting for the current stage to stop…"
                return False
            if self._job:
                _release_job_project_lock(self._job)
            self._job = None
            self._thread = None
            self._prepared_project = None
            return True

    def rescue(self, project: Project, *, clip_id: str, offset_sec: float) -> WizardJob:
        """Apply a manual sync override and rerender cut/edit/export for the wizard."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A previous wizard job is still stopping")
            if self._job and self._job.status == "running":
                raise RuntimeError("A video is already being processed")
            job = WizardJob(id="current", message=t("building_edit"))
            _attach_project(job, project)
            self._job = job
            self._prepared_project = None
            thread = threading.Thread(
                target=self._rescue_and_rerender,
                kwargs={"job": job, "project": project, "clip_id": clip_id, "offset_sec": offset_sec},
                daemon=True,
                name="zucker-wizard-rescue",
            )
            self._thread = thread
            thread.start()
            return job

    def rescue_ranges(
        self, project: Project, *, clip_id: str, offset_ranges: list[dict[str, Any]]
    ) -> WizardJob:
        """Apply confirmed clip-time offsets and rerender the current project."""
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("A video is already being processed")
            job = WizardJob(id="current", message=t("building_edit"))
            _attach_project(job, project)
            self._job = job
            self._prepared_project = None
            thread = threading.Thread(
                target=self._rescue_and_rerender_ranges,
                kwargs={"job": job, "project": project, "clip_id": clip_id, "offset_ranges": offset_ranges},
                daemon=True,
                name="zucker-wizard-range-rescue",
            )
            self._thread = thread
            thread.start()
            return job

    def _run(
        self,
        *,
        job: WizardJob,
        name: str,
        platform: str,
        song_choice: int | str | None,
        audio_trim: dict[str, float] | None,
        spherical_landmarks: dict[str, Any] | None,
        spherical_landmark_profiles: dict[str, Any] | None,
        spherical_source_path: str | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        backstage_target_duration_sec: float | None,
        reel_aspect: str | None,
        reel_mix_vertical_ratio: str | float | None,
        reel_cuts_per_source: float | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        backstage_messages: list[str] | None,
        transition_type: str | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        try:
            project = _create_wizard_project(name)
            _attach_project(job, project)
            if not _acquire_job_project_lock(job, project):
                raise RuntimeError("This project is already running in another pipeline; reattach to the existing run")
            selected_master_path, selected_video_paths = _select_360_inputs(master_path, video_paths) if platform == "360" else (master_path, video_paths)
            register_selected_inputs(project, master_path=selected_master_path, songs_path=songs_path, video_paths=selected_video_paths, append_videos=False)
            job.input_warnings = list(project.data.get("inputs", {}).get("warnings") or [])
            _write_stage_log(project, "wizard", f"Master audio selected: {Path(master_path).name}" if master_path else "Backstage: no master audio")
            project.data["settings"]["wizard"] = {
                "platform": platform,
                "song_choice": song_choice,
                "audio_trim": audio_trim or {},
                "placeholder_logic": platform in {"instagram", "tiktok"},
                "reel_duration_sec": reel_duration_sec,
                "backstage_target_duration_sec": backstage_target_duration_sec if platform == "backstage" else 180.0,
                "reel_aspect": reel_aspect,
                "reel_mix_vertical_ratio": reel_mix_vertical_ratio,
                "reel_cuts_per_source": reel_cuts_per_source,
                "reel_text_overlays": reel_text_overlays or [],
                "reel_image_overlays": reel_image_overlays or [],
                "backstage_messages": backstage_messages or [],
                "personal_logo_path": (load_global_config().get("personal_logo_path") or ""),
                "backstage_run_id": str(time.time_ns()) if platform == "backstage" else "",
            }
            _store_audio_trim(master_path, audio_trim)
            _store_spherical_landmarks(project, spherical_landmarks, spherical_landmark_profiles, spherical_source_path)
            _store_spherical_source_path(project, spherical_source_path)
            _store_camera_role_weights(project, camera_role_weights)
            _store_fixed_rear_motion(project, fixed_rear_motion)
            _store_spherical_motion(project, spherical_motion)
            _store_spherical_mode(project, spherical_mode)
            _store_spherical_sweep(project, spherical_sweep, sweep_speed_deg_per_sec)
            _store_transition_type(project, platform, transition_type)
            project.save()

            self._run_stage(job, project, IngestStage(), 0, 15 if platform == "360" else 22, t("listening"))
            sync_start = 15 if platform == "360" else 22
            sync_end = 30 if platform == "360" else 48
            if _platform_needs_sync(platform, project=project, master_path=master_path):
                self._run_stage(job, project, SyncStage(), sync_start, sync_end, t("syncing_audio"))
            else:
                job.progress = 22
            self._finish(
                job=job,
                project=project,
                name=name,
                platform=platform,
                song_choice=song_choice,
                audio_trim=audio_trim,
                spherical_landmarks=spherical_landmarks,
                spherical_landmark_profiles=spherical_landmark_profiles,
                spherical_source_path=spherical_source_path,
                camera_role_weights=camera_role_weights,
                fixed_rear_motion=fixed_rear_motion,
                spherical_motion=spherical_motion,
                spherical_mode=spherical_mode,
                spherical_sweep=spherical_sweep,
                sweep_speed_deg_per_sec=sweep_speed_deg_per_sec,
                reel_duration_sec=reel_duration_sec,
                backstage_target_duration_sec=backstage_target_duration_sec,
                reel_aspect=reel_aspect,
                reel_mix_vertical_ratio=reel_mix_vertical_ratio,
                reel_cuts_per_source=reel_cuts_per_source,
                reel_text_overlays=reel_text_overlays,
                reel_image_overlays=reel_image_overlays,
                backstage_messages=backstage_messages,
                master_path=master_path,
                songs_path=songs_path,
                video_paths=video_paths,
            )
        except WizardCancelled:
            LOGGER.info("Wizard job cancelled")
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Wizard job failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")
            _release_job_project_lock(job)

    def _prepare_project(self, *, job: WizardJob, name: str, master_path: str, songs_path: str | None, video_paths: list[str], platform: str = "youtube") -> None:
        try:
            project = _create_wizard_project(name)
            _attach_project(job, project)
            if not _acquire_job_project_lock(job, project):
                raise RuntimeError("This project is already running in another pipeline; reattach to the existing run")
            register_selected_inputs(project, master_path=master_path, songs_path=songs_path, video_paths=video_paths, append_videos=False)
            project.data.setdefault("settings", {}).setdefault("wizard", {}).update({"platform": platform, "master_path": master_path or ""})
            job.input_warnings = list(project.data.get("inputs", {}).get("warnings") or [])
            _write_stage_log(project, "wizard", f"Master audio selected: {Path(master_path).name}")
            self._run_stage(job, project, IngestStage(), 0, 22, t("listening"))
            if _platform_needs_sync(platform, project=project, master_path=master_path):
                self._run_stage(job, project, SyncStage(), 22, 48, t("syncing_audio"))
            else:
                job.progress = 22
            with self._lock:
                self._prepared_project = project
            job.status = "waiting_choice"
            job.progress = 48
            job.message = t("ready_to_edit")
            job.detail = t("choose_edit_type")
        except WizardCancelled:
            LOGGER.info("Wizard prepare cancelled")
            self._mark_cancelled(job)
            _release_job_project_lock(job)
        except Exception as exc:
            LOGGER.exception("Wizard prepare failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_prepare")
            _release_job_project_lock(job)

    def _prepare_existing_project(self, *, job: WizardJob, project: Project, platform: str = "youtube") -> None:
        try:
            _attach_project(job, project)
            if not _acquire_job_project_lock(job, project):
                # The persisted stage state is the source of truth while the
                # other process continues. The UI will poll and reattach.
                job.status = "running"
                job.stage = "reconnect"
                job.message = "This project is already running; reconnecting to it…"
                return
            ingest = (project.data.get("stages") or {}).get("ingest") or {}
            if ingest.get("status") == "done":
                job.progress = 22
                _write_stage_log(project, "wizard", "REUSING completed ingest; no re-ingest required")
            else:
                self._run_stage(job, project, IngestStage(), 0, 22, t("listening"))
            if _platform_needs_sync(platform, project=project):
                self._run_stage(job, project, SyncStage(), 22, 48, t("syncing_audio"))
            else:
                job.progress = 22
            with self._lock:
                self._prepared_project = project
            job.status = "waiting_choice"
            job.progress = 48
            job.message = t("ready_to_edit")
            job.detail = t("choose_edit_type")
        except WizardCancelled:
            LOGGER.info("Wizard prepare (existing project) cancelled")
            self._mark_cancelled(job)
            _release_job_project_lock(job)
        except Exception as exc:
            LOGGER.exception("Wizard prepare existing project failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_prepare")
            _release_job_project_lock(job)

    def _finish(
        self,
        *,
        job: WizardJob,
        project: Project,
        name: str,
        platform: str,
        song_choice: int | str | None,
        audio_trim: dict[str, float] | None,
        spherical_landmarks: dict[str, Any] | None,
        spherical_landmark_profiles: dict[str, Any] | None,
        spherical_source_path: str | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        backstage_target_duration_sec: float | None,
        reel_aspect: str | None,
        reel_mix_vertical_ratio: str | float | None,
        reel_cuts_per_source: float | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        backstage_messages: list[str] | None,
        transition_type: str | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        started_at = time.monotonic()
        try:
            _attach_project(job, project)
            previous_wizard = project.data["settings"].get("wizard") or {}
            variation_seed = str(previous_wizard.get("variation_seed") or time.time_ns())
            project.data["settings"]["wizard"] = {
                "platform": platform,
                "song_choice": song_choice,
                "audio_trim": audio_trim or {},
                "placeholder_logic": platform in {"instagram", "tiktok"},
                "reel_duration_sec": reel_duration_sec,
                "backstage_target_duration_sec": backstage_target_duration_sec if platform == "backstage" else None,
                "variation_seed": variation_seed,
                "reel_aspect": reel_aspect,
                "reel_mix_vertical_ratio": reel_mix_vertical_ratio,
                "reel_cuts_per_source": reel_cuts_per_source,
                "reel_text_overlays": reel_text_overlays or [],
                "reel_image_overlays": reel_image_overlays or [],
                "backstage_messages": backstage_messages or [],
                "personal_logo_path": (load_global_config().get("personal_logo_path") or ""),
                "master_path": master_path or "",
                "backstage_run_id": variation_seed if platform == "backstage" else "",
            }
            _store_audio_trim(master_path, audio_trim)
            _store_spherical_landmarks(project, spherical_landmarks, spherical_landmark_profiles, spherical_source_path)
            _store_spherical_source_path(project, spherical_source_path)
            _store_camera_role_weights(project, camera_role_weights)
            _store_fixed_rear_motion(project, fixed_rear_motion)
            _store_spherical_motion(project, spherical_motion)
            _store_spherical_mode(project, spherical_mode)
            _store_spherical_sweep(project, spherical_sweep, sweep_speed_deg_per_sec)
            _store_transition_type(project, platform, transition_type)
            project.save()
            job.started_at = time.time()
            cut_start, cut_end, edit_start, edit_end = ((30, 35, 35, 40) if platform == "360" else (48, 58, 58, 70))
            export_start = edit_end
            cut_stage = BackstageAnalysisStage() if platform == "backstage" else CutStage()
            edit_stage = BackstageEditStage() if platform == "backstage" else EditStage()
            export_stage = BackstageExportStage() if platform == "backstage" else ExportStage()
            self._run_stage(job, project, cut_stage, cut_start, cut_end, "Finding Backstage moments" if platform == "backstage" else t("cutting_song"))
            # The cut stage is what establishes the song window, so this is the
            # earliest point a grounded estimate can be made -- and it is still
            # before any rendering, which is the part that actually takes time.
            job.estimated_total_seconds = _predicted_total_seconds(project, platform)
            needs_review = platform in {"reel", "youtube"} and not (platform == "reel" and is_single_source_reel(project))
            self._run_stage(job, project, edit_stage, edit_start, edit_end - 5 if needs_review else edit_end, "Building Backstage narrative" if platform == "backstage" else t("building_edit"))
            # This is deliberately after EditStage and before ExportStage: the
            # user must see a concrete reason instead of receiving a silent
            # single-camera export.
            if platform != "backstage":
                _assert_coverage(project)
            if platform == "backstage":
                job.status = "waiting_paper_edit"
                job.stage = "paper_edit"
                job.progress = edit_end
                job.message = "Paper edit ready"
                job.detail = "Review the written Backstage sequence before rendering."
                job.paper_edit_available = True
                _write_stage_log(project, "wizard", "PAPER EDIT READY before export")
                return
            if platform in {"reel", "youtube"} and not (platform == "reel" and is_single_source_reel(project)):
                # Review-ready is a user-visible promise. Materialize every
                # thumbnail first, including the authored crop/motion frame.
                job.stage = "review_prepare"
                job.message = "Preparing shot review"
                job.stage_progress = 0
                job.tasks = {}
                def review_progress(percent: int, detail: str) -> None:
                    if job.cancel_event.is_set():
                        raise WizardCancelled()
                    task = detail.task if isinstance(detail, ProgressDetail) else {
                        "id": "thumbnails", "label": "Review thumbnails", "percent": percent, "detail": str(detail)}
                    job.tasks[task["id"]] = dict(task)
                    job.stage_progress = max(job.stage_progress, percent)
                    if not isinstance(detail, ProgressDetail):
                        job.progress = max(job.progress, edit_end - 5 + int(5 * percent / 100))
                    job.detail = str(detail)
                    job.progress_updated_at = time.time()
                review = review_items(project, progress_callback=review_progress)
                if any(not item.get("thumbnail") for item in review):
                    raise RuntimeError("Shot review frames were not fully generated")
                job.status = "waiting_review"
                job.stage = "review"
                job.progress = edit_end
                job.message = "Review shots"
                job.detail = "I'm artificial, but not that intelligent — help me check whether these shots are any good."
                _write_stage_log(project, "wizard", "SHOT REVIEW READY before export")
                return
            outputs = self._run_stage(job, project, export_stage, export_start, 100, "Exporting Backstage documentary" if platform == "backstage" else t("exporting_video"))
            manifest_path = Path(outputs["export_manifest"])
            import json

            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            export = manifest["exports"][0]
            export_path = Path(export["path"])
            if export_path.suffix.lower() != ".mp4" or not export_path.exists():
                raise RuntimeError(f"Export did not produce an MP4: {export_path}")
            _write_stage_log(project, "wizard", f"EXPORT OUTPUT {export_path.resolve()} ({export_path.stat().st_size} bytes)")
            job.status = "done"
            job.progress = 100
            job.message = t("done")
            job.result = {
                "project_path": str(project.folder),
                "filename": export_path.name,
                "path": str(export_path),
                "media_url": "/api/v1/wizard/result",
                "platform": platform,
                "logs_path": str(project.cache_dir / "logs"),
                "cut_count": export.get("cut_count"),
                "camera_usage": export.get("camera_usage"),
                "spherical_shot_usage": export.get("spherical_shot_usage") or manifest.get("spherical_shot_usage") or {},
                "spherical_recording_usage": export.get("spherical_recording_usage") or manifest.get("spherical_recording_usage") or {},
                "warnings": export.get("warnings") or manifest.get("warnings") or [],
                "excluded_clips": export.get("excluded_clips") or [],
                "clip_fates": export.get("clip_fates") or [],
            }
            elapsed = time.monotonic() - started_at
            if project.data["inputs"].get("videos") and elapsed < 1.0:
                _write_stage_log(project, "wizard", t("suspiciously_fast", elapsed=elapsed))
            _remember_export_throughput(project, platform, export, elapsed)
        except WizardCancelled:
            LOGGER.info("Wizard finish cancelled")
            _write_stage_log(project, "wizard", "CANCELLED by user")
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Wizard finish failed")
            _write_stage_log(project, "wizard", f"FAILED {traceback.format_exc()}")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")
        finally:
            _release_job_project_lock(job)

    def _finish_after_prepare(
        self,
        *,
        job: WizardJob,
        prepare_thread: threading.Thread | None,
        name: str,
        platform: str,
        song_choice: int | str | None,
        audio_trim: dict[str, float] | None,
        spherical_landmarks: dict[str, Any] | None,
        spherical_landmark_profiles: dict[str, Any] | None,
        spherical_source_path: str | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        backstage_target_duration_sec: float | None,
        reel_aspect: str | None,
        reel_mix_vertical_ratio: str | float | None,
        reel_cuts_per_source: float | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        backstage_messages: list[str] | None,
        transition_type: str | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        job.message = t("waiting_for_sync") if _platform_needs_sync(platform, master_path=master_path) else ("Finding Backstage moments" if platform == "backstage" else ("Preparing 360 source" if platform == "360" else t("building_coverage")))
        if prepare_thread:
            prepare_thread.join()
        if job.status in {"failed", "cancelled"}:
            return
        with self._lock:
            project = self._prepared_project
            self._prepared_project = None
        if not project:
            job.status = "failed"
            job.error = t("prepared_project_missing")
            job.message = t("cannot_finish")
            return
        self._finish(
            job=job,
            project=project,
            name=name,
            platform=platform,
            song_choice=song_choice,
            audio_trim=audio_trim,
            spherical_landmarks=spherical_landmarks,
            spherical_landmark_profiles=spherical_landmark_profiles,
            spherical_source_path=spherical_source_path,
            camera_role_weights=camera_role_weights,
            fixed_rear_motion=fixed_rear_motion,
            spherical_motion=spherical_motion,
            spherical_mode=spherical_mode,
            spherical_sweep=spherical_sweep,
            sweep_speed_deg_per_sec=sweep_speed_deg_per_sec,
            reel_duration_sec=reel_duration_sec,
            backstage_target_duration_sec=backstage_target_duration_sec,
            reel_aspect=reel_aspect,
            reel_mix_vertical_ratio=reel_mix_vertical_ratio,
            reel_cuts_per_source=reel_cuts_per_source,
            reel_text_overlays=reel_text_overlays,
            reel_image_overlays=reel_image_overlays,
            backstage_messages=backstage_messages,
            transition_type=transition_type,
            master_path=master_path,
            songs_path=songs_path,
            video_paths=video_paths,
        )

    def _rescue_and_rerender(self, *, job: WizardJob, project: Project, clip_id: str, offset_sec: float) -> None:
        started_at = time.monotonic()
        try:
            set_manual_override(project, clip_id, offset_sec)
            _write_stage_log(project, "wizard", f"RESCUE clip_id={clip_id} offset_sec={offset_sec:.3f}")
            self._run_stage(job, project, CutStage(), 0, 16, t("cutting_song"))
            self._run_stage(job, project, EditStage(), 16, 34, t("building_edit"))
            _assert_coverage(project)
            outputs = self._run_stage(job, project, ExportStage(), 34, 100, t("exporting_video"))
            manifest_path = Path(outputs["export_manifest"])
            import json

            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            export = manifest["exports"][0]
            export_path = Path(export["path"])
            if export_path.suffix.lower() != ".mp4" or not export_path.exists():
                raise RuntimeError(f"Export did not produce an MP4: {export_path}")
            job.status = "done"
            job.progress = 100
            job.message = t("done")
            job.result = {
                "project_path": str(project.folder),
                "filename": export_path.name,
                "path": str(export_path),
                "media_url": "/api/v1/wizard/result",
                "platform": export.get("platform"),
                "logs_path": str(project.cache_dir / "logs"),
                "cut_count": export.get("cut_count"),
                "camera_usage": export.get("camera_usage"),
                "spherical_shot_usage": export.get("spherical_shot_usage") or manifest.get("spherical_shot_usage") or {},
                "spherical_recording_usage": export.get("spherical_recording_usage") or manifest.get("spherical_recording_usage") or {},
                "warnings": export.get("warnings") or manifest.get("warnings") or [],
                "excluded_clips": export.get("excluded_clips") or [],
                "clip_fates": export.get("clip_fates") or [],
            }
            elapsed = time.monotonic() - started_at
            _write_stage_log(project, "wizard", f"RESCUE DONE clip_id={clip_id} elapsed={elapsed:.2f}s")
        except WizardCancelled:
            LOGGER.info("Wizard rescue rerender cancelled")
            _write_stage_log(project, "wizard", "RESCUE CANCELLED by user")
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Wizard rescue rerender failed")
            _write_stage_log(project, "wizard", f"RESCUE FAILED {traceback.format_exc()}")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")

    def _rescue_and_rerender_ranges(
        self,
        *,
        job: WizardJob,
        project: Project,
        clip_id: str,
        offset_ranges: list[dict[str, Any]],
    ) -> None:
        started_at = time.monotonic()
        try:
            set_manual_override_ranges(project, clip_id, offset_ranges)
            _write_stage_log(project, "wizard", f"RESCUE_RANGES clip_id={clip_id} ranges={offset_ranges!r}")
            self._run_stage(job, project, CutStage(), 0, 16, t("cutting_song"))
            self._run_stage(job, project, EditStage(), 16, 34, t("building_edit"))
            _assert_coverage(project)
            outputs = self._run_stage(job, project, ExportStage(), 34, 100, t("exporting_video"))
            manifest_path = Path(outputs["export_manifest"])
            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            export = manifest["exports"][0]
            export_path = Path(export["path"])
            if export_path.suffix.lower() != ".mp4" or not export_path.exists():
                raise RuntimeError(f"Export did not produce an MP4: {export_path}")
            job.status = "done"
            job.progress = 100
            job.message = t("export_ready")
            job.result = {"export": str(export_path), "elapsed_sec": round(time.monotonic() - started_at, 3)}
        except Exception as exc:
            LOGGER.exception("Range rescue failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")

    def _run_stage(self, job: WizardJob, project: Project, stage: Any, start: int, end: int, message: str) -> dict[str, str]:
        job.message = message
        job.stage = stage.name
        job.stage_progress = 0
        job.tasks = {}
        stage_started = time.perf_counter()
        _write_stage_log(project, stage.name, f"START {stage.name}: {message}")
        stage_state = project.data["stages"][stage.name]
        stage_state.update({"status": "running", "error": None})
        project.save()
        last_logged_percent = -1
        last_logged_at = 0.0
        completed_segments: set[int] = set()
        segment_progress: dict[int, float] = {}
        segment_total: int | None = None

        def progress(percent: int, detail: str) -> None:
            nonlocal last_logged_percent, last_logged_at, segment_total
            if job.cancel_event.is_set():
                raise WizardCancelled()
            safe_percent = max(0, min(100, float(percent)))
            measured_task = detail.task if isinstance(detail, ProgressDetail) else None
            if measured_task:
                job.tasks[measured_task["id"]] = dict(measured_task)
            segment_match = re.search(r"Rendering segment (\d+)/(\d+):\s*(.*)$", str(detail or ""))
            if segment_match:
                job.tasks.pop("stage", None)
                segment_index = int(segment_match.group(1))
                segment_total = int(segment_match.group(2))
                segment_detail = segment_match.group(3).strip()
                if measured_task and measured_task["id"].startswith("segment-"):
                    local_fraction = float(measured_task.get("percent") or 0) / 100
                elif measured_task:
                    local_fraction = segment_progress.get(segment_index, 0.0)
                elif segment_detail.lower() == "complete":
                    local_fraction = 1.0
                else:
                    local_match = re.search(r"(\d+)%\s*$", segment_detail)
                    local_fraction = (int(local_match.group(1)) / 100.0) if local_match else 0.0
                segment_progress[segment_index] = max(
                    segment_progress.get(segment_index, 0.0),
                    local_fraction,
                )
                if local_fraction >= 0.999:
                    completed_segments.add(segment_index)
                aggregate_percent, completed_count = _aggregate_segment_progress(
                    segment_progress,
                    segment_total,
                )
                # Parallel workers report out of order. Aggregate their
                # known progress instead of using the current worker's local
                # percentage, which previously made the global bar stall.
                # This is the global fraction of all segments. Do not use
                # the worker's local/export-position percentage here: segment
                # 128/128 must not make the whole job look 80% complete.
                safe_percent = getattr(detail, "aggregate_percent", 10 + 70 * aggregate_percent / 100)
                job.tasks[f"segment-{segment_index}"] = {
                    "id": f"segment-{segment_index}",
                    "label": f"Shot {segment_index}/{segment_total}",
                    "percent": round(local_fraction * 100),
                    "detail": segment_detail,
                }
                detail = (
                    f"Rendering segments ({completed_count} of {segment_total} complete; "
                    f"current {segment_index}: {round(local_fraction * 100)}%)"
                )
            candidate = round(start + (end - start) * safe_percent / 100, 2)
            job.progress = max(job.progress, candidate)
            if job.progress >= end and segment_total and len(completed_segments) < segment_total:
                # Never expose a stage as complete while workers are pending.
                job.progress = min(job.progress, end - 1)
            job.stage_progress = max(job.stage_progress, safe_percent)
            if not segment_match and not measured_task:
                local_match = re.search(r"^(.*?)\s*[—–]\s*(\d+)%\s*$", str(detail))
                job.tasks["stage"] = {"id": "stage", "label": local_match.group(1) if local_match else message,
                    "percent": int(local_match.group(2)) if local_match else 100 if safe_percent == 100 else None,
                    "detail": str(detail)}
            job.detail = detail
            job.progress_updated_at = time.time()
            now = time.monotonic()
            if candidate != last_logged_percent or now - last_logged_at >= 5:
                _write_stage_log(project, stage.name, f"{candidate}% {detail}")
                last_logged_percent = candidate
                last_logged_at = now

        try:
            outputs = stage.run(project, progress)
        except BaseException as exc:
            stage_state["elapsed_seconds"] = round(time.perf_counter() - stage_started, 3)
            stage_state.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            project.save()
            raise
        stage_state.update({"status": "done", "outputs": outputs, "error": None, "fingerprint": stage.inputs_fingerprint(project)})
        stage_state["elapsed_seconds"] = round(time.perf_counter() - stage_started, 3)
        project.save()
        job.progress = max(job.progress, end)
        job.stage_progress = 100
        _write_stage_log(project, stage.name, f"DONE {stage.name} in {stage_state['elapsed_seconds']} s: {outputs}")
        return outputs


def _create_wizard_project(name: str) -> Project:
    safe_name = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in name).strip() or "Jam"
    base = app_home() / "Projects"
    base.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10_000):
        suffix = "" if index == 1 else f"-{index}"
        folder = base / f"{safe_name}{suffix}.zuckervid"
        if not folder.exists():
            return create_project(name, str(folder))
    raise RuntimeError("I couldn't create the project")


def _360_only_video_paths(video_paths: list[str]) -> list[str]:
    """Return exactly one equirectangular/raw-360 input for passthrough mode."""
    spherical: list[tuple[int, str]] = []
    for path in video_paths:
        try:
            item = classify_file(Path(path))
        except Exception:
            item = {}
        projection = str(item.get("projection") or (item.get("probe") or {}).get("projection") or "").lower()
        if projection in {"equirect", "raw_insv"} or bool(item.get("raw_360")):
            # Prefer an already-exported equirect MP4 over raw INSV, because
            # it avoids the one permitted stitching step.
            spherical.append((0 if projection == "equirect" else 1, path))
    if not spherical:
        raise ValueError("360 mode requires an equirectangular MP4 or an INSV clip")
    spherical.sort(key=lambda value: value[0])
    return [spherical[0][1]]


def _select_360_inputs(master_path: str, input_paths: list[str]) -> tuple[str, list[str]]:
    """Keep audio candidates separate while narrowing only video inputs.

    The UI normally sends the master separately from ``videos``.  Keeping this
    boundary explicit also protects callers that pass a mixed Inbox selection:
    MP3/WAV candidates must survive the 360 video-only filter and remain
    available for the final audio mux.
    """
    audio_candidates: list[str] = []
    video_candidates: list[str] = []
    for path in input_paths:
        try:
            item = classify_file(Path(path))
        except Exception:  # noqa: BLE001 - an invalid extra candidate is ignored
            item = {}
        kind = str(item.get("kind") or "")
        if kind == "master":
            audio_candidates.append(path)
        elif kind == "videos":
            video_candidates.append(path)
    selected_master = master_path or (audio_candidates[0] if audio_candidates else "")
    return selected_master, _360_only_video_paths(video_candidates)


def wizard_song_options(songs_path: str | None) -> list[dict[str, Any]]:
    """Parse songs for the platform song picker without registering a project."""
    if not songs_path:
        return []
    project = Project(Path("."), {"inputs": {"songs": {"path": songs_path}}})
    return load_song_boundaries(project)


def wizard_report(status: dict[str, Any]) -> str:
    """Build a pasteable wizard report with logs and stage statuses."""
    info = build_info()
    lines = ["Zucker Editor wizard report", f"version: {info['version']}", f"git_commit: {info['git_commit']}"]
    result = status.get("result") or {}
    project_path = status.get("project_path") or result.get("project_path")
    lines.append(f"status: {status.get('status')}")
    lines.append(f"message: {status.get('message')}")
    lines.append(f"error: {status.get('error')}")
    lines.append(f"cancel_requested: {status.get('cancel_requested', False)}")
    if project_path:
        project = Project(Path(project_path), {})
        project_json = Path(project_path) / "project.json"
        if project_json.exists():
            import json

            with project_json.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            lines.append("stage statuses:")
            for name, stage in (data.get("stages") or {}).items():
                lines.append(f"- {name}: {stage.get('status')} {stage.get('error') or ''}".rstrip())
                if stage.get("elapsed_seconds") is not None:
                    lines.append(f"  measured duration: {stage['elapsed_seconds']} s")
        log_dir = Path(project_path) / "cache" / "logs"
        lines.append("last log lines:")
        lines.extend(_combined_log_tail(log_dir, 120))
        # Keep post-export composition and 360 evidence visible even when a
        # large segment render fills the combined log tail.
        for log_name in ("composition.log", "spherical.log"):
            log_path = log_dir / log_name
            if log_path.is_file():
                lines.append(f"--- {log_name} tail ---")
                lines.extend(_tail_lines(log_path, 40))
    technical = status.get("technical_details")
    if technical:
        lines.append("--- technical_details ---")
        lines.append(str(technical))
    return "\n".join(lines)


def _attach_project(job: WizardJob, project: Project) -> None:
    job.project_path = str(project.folder)
    job.logs_path = str(project.cache_dir / "logs")
    config = load_global_config()
    config["last_project_path"] = str(project.folder)
    save_global_config(config)


def _assert_coverage(project: Project) -> None:
    gaps = coverage_gaps(project)
    warning = reel_capacity_warning(project, gaps)
    if warning:
        plan_path = artifact_path(project, "edit_plan.json")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        warnings = list(plan.get("warnings") or [])
        if warning not in warnings:
            warnings.append(warning)
            plan["warnings"] = warnings
            write_artifact_json(plan_path, plan)
        LOGGER.warning(warning)
    assert_all_dropbox_videos_used(project)


def _finish_export_job(job: WizardJob, project: Project, outputs: dict[str, str]) -> None:
    manifest_path = Path(outputs["export_manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    export = manifest["exports"][0]
    export_path = Path(export["path"])
    if export_path.suffix.lower() != ".mp4" or not export_path.exists():
        raise RuntimeError(f"Export did not produce an MP4: {export_path}")
    job.status = "done"
    job.progress = 100
    job.message = t("done")
    job.result = {
        "project_path": str(project.folder),
        "filename": export_path.name,
        "path": str(export_path),
        "media_url": "/api/v1/wizard/result",
        "platform": export.get("platform") or project.data.get("settings", {}).get("wizard", {}).get("platform"),
        "logs_path": str(project.cache_dir / "logs"),
        "cut_count": export.get("cut_count"),
        "camera_usage": export.get("camera_usage"),
        "warnings": export.get("warnings") or manifest.get("warnings") or [],
        "excluded_clips": export.get("excluded_clips") or [],
    }


def same_project_path(left: str | None, right: str | None) -> bool:
    """Compare project paths consistently across macOS path spellings."""
    if not left or not right:
        return False
    return str(Path(left).expanduser().resolve()) == str(Path(right).expanduser().resolve())


def _store_transition_type(project: Project, platform: str, transition_type: str | None) -> None:
    """Persist the selected transition preset for this mode."""
    value = str(transition_type or "auto").strip().lower()
    if value != "auto" and value not in TRANSITION_LIBRARY:
        value = "crossfade"
    transitions = project.data.setdefault("settings", {}).setdefault("export", {}).setdefault("transitions", {})
    transitions.setdefault(str(platform or "youtube"), {})["type"] = value


def _store_spherical_landmarks(
    project: Project,
    landmarks: dict[str, Any] | None,
    profiles: dict[str, Any] | None = None,
    source_path: str | None = None,
) -> None:
    """Persist global landmarks and every source-specific authoring profile."""
    explicit = bool(landmarks)
    config = load_global_config()
    existing = config.get("spherical_landmarks") or {}
    if not explicit:
        landmarks = existing
    merged = merge_spherical_landmarks(existing, landmarks or {})
    if not merged:
        return
    settings = project.data.setdefault("settings", {})
    settings["spherical_landmarks"] = merged

    combined_profiles: dict[str, dict[str, dict[str, Any]]] = {}
    for key, raw in (settings.get("spherical_landmarks_by_source") or {}).items():
        if isinstance(raw, dict):
            normalized = merge_spherical_landmarks({}, raw)
            if normalized:
                combined_profiles[str(key)] = normalized
    for key, raw in (profiles or {}).items():
        if not isinstance(raw, dict):
            continue
        normalized = merge_spherical_landmarks({}, raw)
        if not normalized:
            continue
        raw_key = str(key).strip()
        if not raw_key:
            continue
        combined_profiles[raw_key] = normalized
        try:
            combined_profiles[str(Path(raw_key).expanduser().resolve())] = dict(normalized)
        except (OSError, RuntimeError, ValueError):
            pass

    if source_path:
        raw_key = str(source_path).strip()
        source_key = str(Path(raw_key).expanduser().resolve())
        active = (
            combined_profiles.get(raw_key)
            or combined_profiles.get(source_key)
            or merge_spherical_landmarks({}, merged)
        )
        combined_profiles[raw_key] = dict(active)
        combined_profiles[source_key] = dict(active)
        settings["spherical_source_path"] = source_key
    if combined_profiles:
        settings["spherical_landmarks_by_source"] = combined_profiles

    if not explicit:
        return
    config["spherical_landmarks"] = merged
    save_global_config(config)


def _store_spherical_source_path(project: Project, source_path: str | None) -> None:
    """Persist the exact 360 source used to author the saved landmarks."""
    if source_path is None:
        return
    value = str(source_path or "").strip()
    edit = project.data.setdefault("settings", {}).setdefault("edit", {})
    edit["spherical_source_path"] = value
    project.data.setdefault("settings", {}).setdefault("wizard", {})["spherical_source_path"] = value
    config = load_global_config()
    config["spherical_source_path"] = value
    save_global_config(config)


def merge_spherical_landmarks(existing: dict[str, Any] | None, incoming: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Merge landmark edits without allowing a partial form to erase defaults."""
    merged: dict[str, dict[str, Any]] = {}
    for key, value in (existing or {}).items():
        if isinstance(value, dict):
            merged[str(key)] = dict(value)
    for key, value in (incoming or {}).items():
        if isinstance(value, dict):
            merged.setdefault(str(key), {}).update(value)
    return merged


def _store_audio_trim(master_path: str, audio_trim: dict[str, float] | None) -> None:
    if not audio_trim:
        return
    config = load_global_config()
    trims = config.setdefault("audio_trim_by_master", {})
    trims[str(master_path)] = dict(audio_trim)
    save_global_config(config)


def _store_camera_role_weights(project: Project, weights: dict[str, float] | None) -> None:
    if weights is None:
        return
    project.data.setdefault("settings", {}).setdefault("edit", {})["camera_role_weights"] = dict(weights)
    config = load_global_config()
    config["camera_role_weights"] = dict(weights)
    save_global_config(config)


def _store_spherical_motion(project: Project, enabled: bool | None) -> None:
    """Persist the opt-in automatic 360 motion toggle (default off)."""
    if enabled is None:
        return
    edit_settings = project.data.setdefault("settings", {}).setdefault("edit", {})
    edit_settings["spherical_motion"] = bool(enabled)
    edit_settings["spherical_hold_motion"] = "subtle" if enabled else "none"
    config = load_global_config()
    config["spherical_motion"] = bool(enabled)
    config["spherical_hold_motion"] = "subtle" if enabled else "none"
    save_global_config(config)


def _store_fixed_rear_motion(project: Project, enabled: bool | None) -> None:
    if enabled is None:
        return
    project.data.setdefault("settings", {}).setdefault("edit", {})["fixed_rear_motion"] = bool(enabled)
    config = load_global_config()
    config["fixed_rear_motion"] = bool(enabled)
    save_global_config(config)


def _store_spherical_mode(project: Project, mode: str | None) -> None:
    if mode is None:
        return
    value = str(mode or "automatic").strip().lower()
    if value not in {"automatic", "directed"}:
        value = "automatic"
    project.data.setdefault("settings", {}).setdefault("edit", {})["spherical_mode"] = value
    config = load_global_config()
    config["spherical_mode"] = value
    save_global_config(config)


def _store_spherical_sweep(project: Project, enabled: bool | None, speed: float | None) -> None:
    if enabled is None and speed is None:
        return
    edit = project.data.setdefault("settings", {}).setdefault("edit", {})
    config = load_global_config()
    if enabled is not None:
        edit["spherical_sweep"] = bool(enabled)
        config["spherical_sweep"] = bool(enabled)
    if speed is not None:
        value = max(30.0, min(120.0, float(speed)))
        edit["sweep_speed_deg_per_sec"] = value
        config["sweep_speed_deg_per_sec"] = value
    save_global_config(config)


def _remember_export_throughput(project: Project, platform: str, export: dict[str, Any], elapsed: float) -> None:
    """Fold this run's measured speed into the machine's rolling average.

    Best-effort only: a failure to record throughput must never turn a
    successful export into a failed job, so every error is swallowed.
    """
    try:
        from core.ffmpeg import ffprobe

        export_path = str(export.get("path") or "")
        if not export_path:
            return
        probe = ffprobe(export_path)
        output_duration = float((probe.get("format") or {}).get("duration") or 0.0)
        segment_count = int(export.get("cut_count") or 0) or 1
        config = load_global_config()
        save_global_config(record_export_throughput(config, platform, output_duration, elapsed, segment_count))
    except Exception:  # noqa: BLE001 - telemetry must never break an export
        LOGGER.debug("Could not record export throughput", exc_info=True)


def _predicted_total_seconds(project: Project, platform: str) -> float | None:
    """Predict the whole run's wall-clock time before any rendering starts."""
    try:
        from core.stages.cut import load_coverage

        try:
            coverage = load_coverage(project)
        except (FileNotFoundError, OSError, ValueError):
            return None
        window = coverage.get("window") or {}
        content = float(window.get("duration_sec") or 0.0)
        if content <= 0:
            return None
        # Intro and outro logos are rendered too, so they count toward the wait.
        from core.stages.export import INTRO_DURATION, OUTRO_DURATION

        total_output = content + INTRO_DURATION + OUTRO_DURATION
        segments = len(coverage.get("segments") or []) or 1
        return estimated_export_seconds(load_global_config(), platform, total_output, segments)
    except Exception:  # noqa: BLE001 - an estimate is never worth failing over
        LOGGER.debug("Could not predict export duration", exc_info=True)
        return None


def _write_stage_log(project: Project, stage_name: str, line: str) -> None:
    log_dir = project.cache_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"{stage_name}.log").open("a", encoding="utf-8") as fh:
        fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def _tail_lines(path: Path, limit: int) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    except OSError:
        return []


def _combined_log_tail(log_dir: Path, limit: int) -> list[str]:
    rows: list[tuple[str, str]] = []
    for log_path in sorted(log_dir.glob("*.log")):
        for line in _tail_lines(log_path, limit):
            timestamp = line[:19] if len(line) >= 19 and line[4:5] == "-" else ""
            rows.append((timestamp, f"[{log_path.name}] {line}"))
    return [line for _timestamp, line in sorted(rows, key=lambda row: row[0])[-limit:]]


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    # Keep the concrete exception type and message. In particular, an
    # OSError such as E2BIG must never be relabelled as "ffmpeg is missing".
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _acquire_job_project_lock(job: WizardJob, project: Project) -> bool:
    """Acquire once and retain the lock through choice and export."""
    if job.project_lock is not None:
        return True
    lock = ProjectPipelineLock(project.folder)
    if not lock.acquire():
        return False
    job.project_lock = lock
    return True


def _release_job_project_lock(job: WizardJob) -> None:
    if job.project_lock is not None:
        job.project_lock.release()
        job.project_lock = None
