"""Three-step wizard orchestration over the existing pipeline stages."""

from __future__ import annotations

import logging
import time
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.project import Project, create_project
from core.stages.cut import CutStage
from core.stages.export import ExportStage
from core.stages.ingest import IngestStage
from core.stages.sync import SyncStage, load_song_boundaries
from server.inbox import app_home, register_selected_inputs

LOGGER = logging.getLogger(__name__)


@dataclass
class WizardJob:
    """Process-local wizard job state."""

    id: str
    status: str = "running"
    progress: int = 0
    message: str = "Preparando..."
    detail: str | None = None
    error: str | None = None
    technical_details: str | None = None
    result: dict[str, Any] | None = None
    project_path: str | None = None
    logs_path: str | None = None


@dataclass
class WizardRunner:
    """Runs one wizard render job at a time."""

    _lock: threading.RLock = field(default_factory=threading.RLock)
    _job: WizardJob | None = None
    _thread: threading.Thread | None = None
    _prepared_project: Project | None = None

    def prepare(self, *, name: str, master_path: str, songs_path: str | None, video_paths: list[str]) -> WizardJob:
        """Create/register a project and run ingest + sync while the user chooses an edit type."""
        with self._lock:
            if self._job and self._job.status == "running":
                raise RuntimeError("Ya hay un vídeo en proceso")
            job = WizardJob(id="current", message="Escuchando tus vídeos...")
            self._job = job
            self._prepared_project = None
            thread = threading.Thread(
                target=self._prepare_project,
                kwargs={"job": job, "name": name, "master_path": master_path, "songs_path": songs_path, "video_paths": video_paths},
                daemon=True,
                name="zucker-wizard-prepare",
            )
            self._thread = thread
            thread.start()
            return job

    def start(
        self,
        *,
        name: str,
        platform: str,
        song_choice: int | str | None,
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
            project = self._prepared_project
            target = self._finish if project else self._run
            kwargs = {
                "job": job,
                "name": name,
                "platform": platform,
                "song_choice": song_choice,
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
                return {"status": "idle", "progress": 0, "message": "Sin trabajo"}
            return dict(self._job.__dict__)

    def _run(
        self,
        *,
        job: WizardJob,
        name: str,
        platform: str,
        song_choice: int | str | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        try:
            project = _create_wizard_project(name)
            _attach_project(job, project)
            register_selected_inputs(project, master_path=master_path, songs_path=songs_path, video_paths=video_paths, append_videos=False)
            _write_stage_log(project, "wizard", f"Audio master elegido: {Path(master_path).name}")
            project.data["settings"]["wizard"] = {
                "platform": platform,
                "song_choice": song_choice,
                "placeholder_logic": True,
            }
            project.save()

            self._run_stage(job, project, IngestStage(), 0, 22, "Escuchando tus vídeos...")
            self._run_stage(job, project, SyncStage(), 22, 48, "Sincronizando con el audio...")
            self._finish(job=job, project=project, name=name, platform=platform, song_choice=song_choice, master_path=master_path, songs_path=songs_path, video_paths=video_paths)
        except Exception as exc:
            LOGGER.exception("Wizard job failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = "No pude terminar el vídeo"

    def _prepare_project(self, *, job: WizardJob, name: str, master_path: str, songs_path: str | None, video_paths: list[str]) -> None:
        try:
            project = _create_wizard_project(name)
            _attach_project(job, project)
            register_selected_inputs(project, master_path=master_path, songs_path=songs_path, video_paths=video_paths, append_videos=False)
            _write_stage_log(project, "wizard", f"Audio master elegido: {Path(master_path).name}")
            self._run_stage(job, project, IngestStage(), 0, 45, "Escuchando tus vídeos...")
            self._run_stage(job, project, SyncStage(), 45, 95, "Sincronizando con el audio...")
            with self._lock:
                self._prepared_project = project
            job.status = "waiting_choice"
            job.progress = 95
            job.message = "Listo para montar"
            job.detail = "Elige el tipo de edición"
        except Exception as exc:
            LOGGER.exception("Wizard prepare failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = "No pude preparar los archivos"

    def _finish(
        self,
        *,
        job: WizardJob,
        project: Project,
        name: str,
        platform: str,
        song_choice: int | str | None,
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
                "placeholder_logic": True,
            }
            project.save()
            self._run_stage(job, project, CutStage(), 48, 64, "Cortando la canción...")
            _write_stage_log(project, "wizard", "Skipping edit stage in wizard until real edit logic exists")
            project.data["stages"]["edit"].update({"status": "stale", "error": None})
            outputs = self._run_stage(job, project, ExportStage(), 64, 100, "Exportando el vídeo...")
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
            job.message = "Listo"
            job.result = {
                "project_path": str(project.folder),
                "filename": export_path.name,
                "path": str(export_path),
                "media_url": "/api/v1/wizard/result",
                "platform": platform,
                "logs_path": str(project.cache_dir / "logs"),
            }
            elapsed = time.monotonic() - started_at
            if project.data["inputs"].get("videos") and elapsed < 1.0:
                _write_stage_log(project, "wizard", f"WARNING suspiciously fast finish: {elapsed:.2f}s")
        except Exception as exc:
            LOGGER.exception("Wizard finish failed")
            _write_stage_log(project, "wizard", f"FAILED {traceback.format_exc()}")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = "No pude terminar el vídeo"

    def _finish_after_prepare(
        self,
        *,
        job: WizardJob,
        prepare_thread: threading.Thread | None,
        name: str,
        platform: str,
        song_choice: int | str | None,
        master_path: str,
        songs_path: str | None,
        video_paths: list[str],
    ) -> None:
        job.message = "Esperando la sincronización..."
        if prepare_thread:
            prepare_thread.join()
        if job.status == "failed":
            return
        with self._lock:
            project = self._prepared_project
            self._prepared_project = None
        if not project:
            job.status = "failed"
            job.error = "No encontré el proyecto preparado"
            job.message = "No pude terminar el vídeo"
            return
        self._finish(
            job=job,
            project=project,
            name=name,
            platform=platform,
            song_choice=song_choice,
            master_path=master_path,
            songs_path=songs_path,
            video_paths=video_paths,
        )

    def _run_stage(self, job: WizardJob, project: Project, stage: Any, start: int, end: int, message: str) -> dict[str, str]:
        job.message = message
        _write_stage_log(project, stage.name, f"START {stage.name}: {message}")
        stage_state = project.data["stages"][stage.name]
        stage_state.update({"status": "running", "error": None})
        project.save()

        def progress(percent: int, detail: str) -> None:
            job.progress = start + int((end - start) * max(0, min(100, percent)) / 100)
            job.detail = detail
            _write_stage_log(project, stage.name, f"{percent}% {detail}")

        outputs = stage.run(project, progress)
        stage_state.update({"status": "done", "outputs": outputs, "error": None, "fingerprint": stage.inputs_fingerprint(project)})
        project.save()
        job.progress = end
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
    raise RuntimeError("No pude crear el proyecto")


def wizard_song_options(songs_path: str | None) -> list[dict[str, Any]]:
    """Parse songs for the platform song picker without registering a project."""
    if not songs_path:
        return []
    project = Project(Path("."), {"inputs": {"songs": {"path": songs_path}}})
    return load_song_boundaries(project)


def wizard_report(status: dict[str, Any]) -> str:
    """Build a pasteable wizard report with logs and stage statuses."""
    lines = ["Zucker Editor wizard report", "version: 0.1"]
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
        for log_path in sorted(log_dir.glob("*.log")):
            lines.append(f"--- {log_path.name} ---")
            lines.extend(_tail_lines(log_path, 100))
    technical = status.get("technical_details")
    if technical:
        lines.append("--- technical_details ---")
        lines.append(str(technical))
    return "\n".join(lines)


def _attach_project(job: WizardJob, project: Project) -> None:
    job.project_path = str(project.folder)
    job.logs_path = str(project.cache_dir / "logs")


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


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "ffmpeg" in text.lower() or "ffprobe" in text.lower():
        return "Falta ffmpeg o hubo un problema leyendo los archivos de vídeo."
    if "songs.json" in text:
        return "El songs.json no se pudo leer correctamente."
    return text or "Error inesperado"
