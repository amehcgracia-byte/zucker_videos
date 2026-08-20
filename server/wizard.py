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
from core.coverage_guard import CoverageInvariantError, assert_all_dropbox_videos_used
from core.messages import t
from core.project import Project, create_project
from core.stages.cut import CutStage
from core.stages.edit import EditStage
from core.stages.export import ExportStage
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
    proceed_anyway_available: bool = False
    continue_without_video_available: bool = False
    input_warnings: list[str] = field(default_factory=list)


@dataclass
class WizardRunner:
    """Runs one wizard render job at a time."""

    _lock: threading.RLock = field(default_factory=threading.RLock)
    _job: WizardJob | None = None
    _thread: threading.Thread | None = None
    _prepared_project: Project | None = None
    _cancel_event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> bool:
        """Request that the currently running job stop as soon as possible.

        Cooperative: the running stage notices this the next time it reports
        progress (see ``_run_stage``) and unwinds via ``WizardCancelled``,
        which also kills any in-flight ffmpeg subprocess immediately.
        """
        with self._lock:
            if not self._job or self._job.status != "running":
                return False
            self._cancel_event.set()
            return True

    def _mark_cancelled(self, job: WizardJob) -> None:
        job.status = "cancelled"
        job.message = t("cancelled")
        job.error = None
        job.technical_details = None

    def prepare(self, *, name: str, master_path: str, songs_path: str | None, video_paths: list[str]) -> WizardJob:
        """Create/register a project and run ingest + sync while the user chooses an edit type."""
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("A video is already being processed")
            job = WizardJob(id="current", message=t("listening"))
            self._job = job
            self._prepared_project = None
            self._cancel_event.clear()
            thread = threading.Thread(
                target=self._prepare_project,
                kwargs={"job": job, "name": name, "master_path": master_path, "songs_path": songs_path, "video_paths": video_paths},
                daemon=True,
                name="zucker-wizard-prepare",
            )
            self._thread = thread
            thread.start()
            return job

    def prepare_existing(self, project: Project) -> WizardJob:
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
                self._cancel_event.set()
                previous_thread = self._thread
        if previous_thread and previous_thread.is_alive():
            previous_thread.join(timeout=10.0)
        with self._lock:
            if self._job and self._job.status == "running":
                self._job.status = "cancelled"
                self._job.message = t("cancelled")
                self._job.error = None
                self._job.technical_details = None
            job = WizardJob(id="current", message=t("listening"))
            _attach_project(job, project)
            self._job = job
            self._prepared_project = None
            self._cancel_event.clear()
            thread = threading.Thread(
                target=self._prepare_existing_project,
                kwargs={"job": job, "project": project},
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
        spherical_landmarks: dict[str, float] | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        reel_aspect: str | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> WizardJob:
        """Create/register a project and run the simplified render chain."""
        with self._lock:
            if self._job and self._job.status == "running":
                job = self._job
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
                        "camera_role_weights": camera_role_weights,
                        "fixed_rear_motion": fixed_rear_motion,
                        "spherical_motion": spherical_motion,
                        "spherical_mode": spherical_mode,
                        "spherical_sweep": spherical_sweep,
                        "sweep_speed_deg_per_sec": sweep_speed_deg_per_sec,
                        "reel_duration_sec": reel_duration_sec,
                        "reel_aspect": reel_aspect,
                        "reel_text_overlays": reel_text_overlays,
                        "reel_image_overlays": reel_image_overlays,
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
            self._cancel_event.clear()
            project = self._prepared_project
            target = self._finish if project else self._run
            kwargs = {
                "job": job,
                "name": name,
                "platform": platform,
                "song_choice": song_choice,
                "audio_trim": audio_trim,
                "spherical_landmarks": spherical_landmarks,
                "camera_role_weights": camera_role_weights,
                "fixed_rear_motion": fixed_rear_motion,
                "spherical_motion": spherical_motion,
                "spherical_mode": spherical_mode,
                "spherical_sweep": spherical_sweep,
                "sweep_speed_deg_per_sec": sweep_speed_deg_per_sec,
                "reel_duration_sec": reel_duration_sec,
                "reel_aspect": reel_aspect,
                "reel_text_overlays": reel_text_overlays,
                "reel_image_overlays": reel_image_overlays,
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

    def status(self) -> dict[str, Any]:
        """Return the current wizard job snapshot."""
        with self._lock:
            if not self._job:
                return {"status": "idle", "progress": 0, "message": "Idle"}
            return dict(self._job.__dict__)

    def render_review(self, project: Project | None) -> WizardJob:
        """Resume a completed edit plan after the user approves its shots."""
        with self._lock:
            if not project or not self._job or self._job.status != "waiting_review":
                raise RuntimeError("There is no shot review waiting to render")
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A video is already being processed")
            job = self._job
            self._cancel_event.clear()
            thread = threading.Thread(target=self._render_review_job, args=(job, project), daemon=True, name="zucker-review-render")
            self._thread = thread
            thread.start()
            return job

    def proceed_anyway(self, project: Project | None) -> WizardJob:
        """Retry Cut/Edit with the explicit low-confidence sync escape hatch."""
        with self._lock:
            if not project or not self._job or not self._job.proceed_anyway_available:
                raise RuntimeError("Proceed anyway is not available for this job")
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A video is already being processed")
            job = self._job
            project.data.setdefault("settings", {}).setdefault("wizard", {})["proceed_anyway"] = True
            project.save()
            job.proceed_anyway_available = False
            job.status = "running"
            job.stage = "cut"
            job.progress = 0
            job.message = t("cutting_song")
            job.detail = "Proceeding anyway with the best available offsets. Sync may be imprecise."
            job.error = None
            job.technical_details = None
            self._cancel_event.clear()
            thread = threading.Thread(
                target=self._proceed_anyway_job,
                args=(job, project),
                daemon=True,
                name="zucker-wizard-proceed-anyway",
            )
            self._thread = thread
            thread.start()
            return job

    def continue_without_video(self, project: Project | None) -> WizardJob:
        """Explicitly accept sync-only Drop box omissions and resume the job."""
        with self._lock:
            if not project or not self._job or not self._job.continue_without_video_available:
                raise RuntimeError("Continue without this video is not available for this job")
            if self._thread and self._thread.is_alive():
                raise RuntimeError("A video is already being processed")
            job = self._job
            project.data.setdefault("settings", {}).setdefault("wizard", {})["continue_without_video"] = True
            project.save()
            job.proceed_anyway_available = False
            job.continue_without_video_available = False
            job.status = "running"
            job.error = None
            job.technical_details = None
            job.message = t("building_edit")
            job.detail = "Continuing without the video that could not be synchronized."
            self._cancel_event.clear()
            thread = threading.Thread(
                target=self._continue_without_video_job,
                args=(job, project),
                daemon=True,
                name="zucker-wizard-continue-without-video",
            )
            self._thread = thread
            thread.start()
            return job

    def _continue_without_video_job(self, job: WizardJob, project: Project) -> None:
        try:
            _assert_coverage(project)
            platform = str(project.data.get("settings", {}).get("wizard", {}).get("platform") or "youtube")
            if platform in {"youtube", "reel"}:
                review = review_items(project)
                if any(not item.get("thumbnail") for item in review):
                    raise RuntimeError("Shot review frames were not fully generated")
                job.status = "waiting_review"
                job.stage = "review"
                job.progress = max(70, job.progress)
                job.message = "Review shots"
                job.detail = "Continuing without the unsynchronized video."
                return
            outputs = self._run_stage(job, project, ExportStage(), max(70, job.progress), 100, t("exporting_video"))
            _finish_export_job(job, project, outputs)
        except WizardCancelled:
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Continue-without-video rerun failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")

    def _proceed_anyway_job(self, job: WizardJob, project: Project) -> None:
        try:
            self._run_stage(job, project, CutStage(), 0, 35, t("cutting_song"))
            self._run_stage(job, project, EditStage(), 35, 70, t("building_edit"))
            _assert_coverage(project)
            platform = str(project.data.get("settings", {}).get("wizard", {}).get("platform") or "youtube")
            if platform in {"youtube", "reel"}:
                review = review_items(project)
                if any(not item.get("thumbnail") for item in review):
                    raise RuntimeError("Shot review frames were not fully generated")
                job.status = "waiting_review"
                job.stage = "review"
                job.progress = 70
                job.message = "Review shots"
                job.detail = "Proceeding anyway: check the shots before rendering; sync may be imprecise."
                _write_stage_log(project, "wizard", "PROCEED ANYWAY: SHOT REVIEW READY before export")
                return
            raise RuntimeError("Proceed anyway is only available for YouTube edits")
        except WizardCancelled:
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Proceed-anyway rerun failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_finish")

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

    def reset(self) -> None:
        """Forget the process-local wizard state without deleting project files."""
        with self._lock:
            self._job = None
            self._thread = None
            self._prepared_project = None
            self._cancel_event.clear()

    def rescue(self, project: Project, *, clip_id: str, offset_sec: float) -> WizardJob:
        """Apply a manual sync override and rerender cut/edit/export for the wizard."""
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("A video is already being processed")
            job = WizardJob(id="current", message=t("building_edit"))
            _attach_project(job, project)
            self._job = job
            self._prepared_project = None
            self._cancel_event.clear()
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
            self._cancel_event.clear()
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
        spherical_landmarks: dict[str, float] | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        reel_aspect: str | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        try:
            project = _create_wizard_project(name)
            _attach_project(job, project)
            selected_master_path, selected_video_paths = _select_360_inputs(master_path, video_paths) if platform == "360" else (master_path, video_paths)
            register_selected_inputs(project, master_path=selected_master_path, songs_path=songs_path, video_paths=selected_video_paths, append_videos=False)
            job.input_warnings = list(project.data.get("inputs", {}).get("warnings") or [])
            _write_stage_log(project, "wizard", f"Master audio selected: {Path(master_path).name}")
            project.data["settings"]["wizard"] = {
                "platform": platform,
                "song_choice": song_choice,
                "audio_trim": audio_trim or {},
                "placeholder_logic": platform in {"instagram", "tiktok"},
                "reel_duration_sec": reel_duration_sec,
                "reel_aspect": reel_aspect,
                "reel_text_overlays": reel_text_overlays or [],
                "reel_image_overlays": reel_image_overlays or [],
                "personal_logo_path": (load_global_config().get("personal_logo_path") or ""),
            }
            _store_audio_trim(master_path, audio_trim)
            _store_spherical_landmarks(project, spherical_landmarks)
            _store_camera_role_weights(project, camera_role_weights)
            _store_fixed_rear_motion(project, fixed_rear_motion)
            _store_spherical_motion(project, spherical_motion)
            _store_spherical_mode(project, spherical_mode)
            _store_spherical_sweep(project, spherical_sweep, sweep_speed_deg_per_sec)
            project.save()

            self._run_stage(job, project, IngestStage(), 0, 15 if platform == "360" else 22, t("listening"))
            sync_start = 15 if platform == "360" else 22
            sync_end = 30 if platform == "360" else 48
            self._run_stage(job, project, SyncStage(), sync_start, sync_end, t("syncing_audio"))
            self._finish(
                job=job,
                project=project,
                name=name,
                platform=platform,
                song_choice=song_choice,
                audio_trim=audio_trim,
                spherical_landmarks=spherical_landmarks,
                camera_role_weights=camera_role_weights,
                fixed_rear_motion=fixed_rear_motion,
                spherical_motion=spherical_motion,
                spherical_mode=spherical_mode,
                spherical_sweep=spherical_sweep,
                sweep_speed_deg_per_sec=sweep_speed_deg_per_sec,
                reel_duration_sec=reel_duration_sec,
                reel_aspect=reel_aspect,
                reel_text_overlays=reel_text_overlays,
                reel_image_overlays=reel_image_overlays,
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

    def _prepare_project(self, *, job: WizardJob, name: str, master_path: str, songs_path: str | None, video_paths: list[str]) -> None:
        try:
            project = _create_wizard_project(name)
            _attach_project(job, project)
            register_selected_inputs(project, master_path=master_path, songs_path=songs_path, video_paths=video_paths, append_videos=False)
            job.input_warnings = list(project.data.get("inputs", {}).get("warnings") or [])
            _write_stage_log(project, "wizard", f"Master audio selected: {Path(master_path).name}")
            self._run_stage(job, project, IngestStage(), 0, 22, t("listening"))
            self._run_stage(job, project, SyncStage(), 22, 48, t("syncing_audio"))
            with self._lock:
                self._prepared_project = project
            job.status = "waiting_choice"
            job.progress = 48
            job.message = t("ready_to_edit")
            job.detail = t("choose_edit_type")
        except WizardCancelled:
            LOGGER.info("Wizard prepare cancelled")
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Wizard prepare failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_prepare")

    def _prepare_existing_project(self, *, job: WizardJob, project: Project) -> None:
        try:
            _attach_project(job, project)
            self._run_stage(job, project, IngestStage(), 0, 22, t("listening"))
            self._run_stage(job, project, SyncStage(), 22, 48, t("syncing_audio"))
            with self._lock:
                self._prepared_project = project
            job.status = "waiting_choice"
            job.progress = 48
            job.message = t("ready_to_edit")
            job.detail = t("choose_edit_type")
        except WizardCancelled:
            LOGGER.info("Wizard prepare (existing project) cancelled")
            self._mark_cancelled(job)
        except Exception as exc:
            LOGGER.exception("Wizard prepare existing project failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = t("cannot_prepare")

    def _finish(
        self,
        *,
        job: WizardJob,
        project: Project,
        name: str,
        platform: str,
        song_choice: int | str | None,
        audio_trim: dict[str, float] | None,
        spherical_landmarks: dict[str, float] | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        reel_aspect: str | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        started_at = time.monotonic()
        try:
            _attach_project(job, project)
            project.data["settings"]["wizard"] = {
                "platform": platform,
                "song_choice": song_choice,
                "audio_trim": audio_trim or {},
                "placeholder_logic": platform in {"instagram", "tiktok"},
                "reel_duration_sec": reel_duration_sec,
                "reel_aspect": reel_aspect,
                "reel_text_overlays": reel_text_overlays or [],
                "reel_image_overlays": reel_image_overlays or [],
                "personal_logo_path": (load_global_config().get("personal_logo_path") or ""),
            }
            _store_audio_trim(master_path, audio_trim)
            _store_spherical_landmarks(project, spherical_landmarks)
            _store_camera_role_weights(project, camera_role_weights)
            _store_fixed_rear_motion(project, fixed_rear_motion)
            _store_spherical_motion(project, spherical_motion)
            _store_spherical_mode(project, spherical_mode)
            _store_spherical_sweep(project, spherical_sweep, sweep_speed_deg_per_sec)
            project.save()
            job.started_at = time.time()
            cut_start, cut_end, edit_start, edit_end = ((30, 35, 35, 40) if platform == "360" else (48, 58, 58, 70))
            export_start = edit_end
            self._run_stage(job, project, CutStage(), cut_start, cut_end, t("cutting_song"))
            # The cut stage is what establishes the song window, so this is the
            # earliest point a grounded estimate can be made -- and it is still
            # before any rendering, which is the part that actually takes time.
            job.estimated_total_seconds = _predicted_total_seconds(project, platform)
            self._run_stage(job, project, EditStage(), edit_start, edit_end, t("building_edit"))
            # This is deliberately after EditStage and before ExportStage: the
            # user must see a concrete reason instead of receiving a silent
            # single-camera export.
            _assert_coverage(project)
            if platform in {"youtube", "reel"}:
                # Review-ready is a user-visible promise. Materialize every
                # thumbnail first, including the authored crop/motion frame.
                review = review_items(project)
                if any(not item.get("thumbnail") for item in review):
                    raise RuntimeError("Shot review frames were not fully generated")
                job.status = "waiting_review"
                job.stage = "review"
                job.progress = edit_end
                job.message = "Review shots"
                job.detail = "I'm artificial, but not that intelligent — help me check whether these shots are any good."
                _write_stage_log(project, "wizard", "SHOT REVIEW READY before export")
                return
            outputs = self._run_stage(job, project, ExportStage(), export_start, 100, t("exporting_video"))
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
            if isinstance(exc, CoverageInvariantError) and exc.sync_only:
                job.proceed_anyway_available = True
                job.continue_without_video_available = True
            else:
                job.proceed_anyway_available = (
                    platform == "youtube"
                    and job.stage == "cut"
                    and "proceed anyway" in job.error.lower()
                )

    def _finish_after_prepare(
        self,
        *,
        job: WizardJob,
        prepare_thread: threading.Thread | None,
        name: str,
        platform: str,
        song_choice: int | str | None,
        audio_trim: dict[str, float] | None,
        spherical_landmarks: dict[str, float] | None,
        camera_role_weights: dict[str, float] | None,
        fixed_rear_motion: bool | None,
        spherical_motion: bool | None,
        spherical_mode: str | None,
        spherical_sweep: bool | None,
        sweep_speed_deg_per_sec: float | None,
        reel_duration_sec: float | None,
        reel_aspect: str | None,
        reel_text_overlays: list[dict[str, Any]] | None,
        reel_image_overlays: list[dict[str, Any]] | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        job.message = t("waiting_for_sync")
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
            camera_role_weights=camera_role_weights,
            fixed_rear_motion=fixed_rear_motion,
            spherical_motion=spherical_motion,
            spherical_mode=spherical_mode,
            spherical_sweep=spherical_sweep,
            sweep_speed_deg_per_sec=sweep_speed_deg_per_sec,
            reel_duration_sec=reel_duration_sec,
            reel_aspect=reel_aspect,
            reel_text_overlays=reel_text_overlays,
            reel_image_overlays=reel_image_overlays,
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
        _write_stage_log(project, stage.name, f"START {stage.name}: {message}")
        stage_state = project.data["stages"][stage.name]
        stage_state.update({"status": "running", "error": None})
        project.save()
        last_logged_percent = -1
        last_logged_at = 0.0
        completed_segments: set[int] = set()
        segment_total: int | None = None

        def progress(percent: int, detail: str) -> None:
            nonlocal last_logged_percent, last_logged_at, segment_total
            if self._cancel_event.is_set():
                raise WizardCancelled()
            safe_percent = max(0, min(100, int(percent)))
            segment_match = re.search(r"Rendering segment (\d+)/(\d+):\s*(.*)$", str(detail or ""))
            if segment_match:
                segment_index = int(segment_match.group(1))
                segment_total = int(segment_match.group(2))
                if segment_match.group(3).strip().lower() == "complete":
                    completed_segments.add(segment_index)
                if completed_segments and segment_total:
                    # Worker-local progress is deliberately not surfaced as a
                    # step counter: parallel workers report out of order. The
                    # label and the stage progress use completed work only.
                    safe_percent = max(safe_percent, int(100 * len(completed_segments) / segment_total))
                    detail = f"Rendering segments ({len(completed_segments)} of {segment_total} complete)"
            candidate = start + int((end - start) * safe_percent / 100)
            job.progress = max(job.progress, candidate)
            if job.progress >= end and segment_total and len(completed_segments) < segment_total:
                # Never expose a stage as complete while workers are pending.
                job.progress = min(job.progress, end - 1)
            job.detail = detail
            now = time.monotonic()
            if percent != last_logged_percent or now - last_logged_at >= 5:
                _write_stage_log(project, stage.name, f"{percent}% {detail}")
                last_logged_percent = percent
                last_logged_at = now

        outputs = stage.run(project, progress)
        stage_state.update({"status": "done", "outputs": outputs, "error": None, "fingerprint": stage.inputs_fingerprint(project)})
        project.save()
        job.progress = max(job.progress, end)
        _write_stage_log(project, stage.name, f"DONE {stage.name}: {outputs}")
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
        log_dir = Path(project_path) / "cache" / "logs"
        lines.append("last log lines:")
        lines.extend(_combined_log_tail(log_dir, 160))
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
    wizard = project.data.get("settings", {}).get("wizard", {})
    allow_sync_missing = bool(wizard.get("proceed_anyway") or wizard.get("continue_without_video"))
    assert_all_dropbox_videos_used(project, allow_sync_missing=allow_sync_missing)


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


def _store_spherical_landmarks(project: Project, landmarks: dict[str, float] | None) -> None:
    explicit = bool(landmarks)
    config = load_global_config()
    existing = config.get("spherical_landmarks") or {}
    if not explicit:
        landmarks = existing
    merged = merge_spherical_landmarks(existing, landmarks or {})
    if not merged:
        return
    project.data.setdefault("settings", {})["spherical_landmarks"] = merged
    if not explicit:
        return
    config["spherical_landmarks"] = merged
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
    # Export stage errors already contain the segment, source, exit code, and
    # ffmpeg stderr tail. Never replace those diagnostics with the old generic
    # "ffmpeg is missing" message.
    if getattr(exc, "segment_index", None) is not None or text.startswith("Not enough free space") or text.startswith("The drive containing"):
        return text
    if "ffmpeg" in text.lower() or "ffprobe" in text.lower():
        return t("ffmpeg_problem")
    if "songs.json" in text:
        return t("songs_problem")
    return text or t("unexpected_error")
