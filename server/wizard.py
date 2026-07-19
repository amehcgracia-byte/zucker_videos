"""Three-step wizard orchestration over the existing pipeline stages."""

from __future__ import annotations

import logging
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.project import Project, create_project
from core.stages.cut import CutStage
from core.stages.edit import EditStage
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
                raise RuntimeError("Ya hay un vídeo en proceso")
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
            register_selected_inputs(project, master_path=master_path, songs_path=songs_path, video_paths=video_paths, append_videos=False)
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
            register_selected_inputs(project, master_path=master_path, songs_path=songs_path, video_paths=video_paths, append_videos=False)
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
        try:
            project.data["settings"]["wizard"] = {
                "platform": platform,
                "song_choice": song_choice,
                "placeholder_logic": True,
            }
            project.save()
            self._run_stage(job, project, CutStage(), 48, 64, "Cortando la canción...")
            self._run_stage(job, project, EditStage(), 64, 76, "Montando el vídeo...")
            outputs = self._run_stage(job, project, ExportStage(), 76, 100, "Exportando el vídeo...")
            manifest_path = Path(outputs["export_manifest"])
            import json

            with manifest_path.open("r", encoding="utf-8") as fh:
                manifest = json.load(fh)
            export = manifest["exports"][0]
            job.status = "done"
            job.progress = 100
            job.message = "Listo"
            job.result = {
                "project_path": str(project.folder),
                "filename": Path(export["path"]).name,
                "path": export["path"],
                "media_url": "/api/v1/wizard/result",
                "platform": platform,
            }
        except Exception as exc:
            LOGGER.exception("Wizard finish failed")
            job.status = "failed"
            job.error = _friendly_error(exc)
            job.technical_details = traceback.format_exc()
            job.message = "No pude terminar el vídeo"

    def _run_stage(self, job: WizardJob, project: Project, stage: Any, start: int, end: int, message: str) -> dict[str, str]:
        job.message = message
        stage_state = project.data["stages"][stage.name]
        stage_state.update({"status": "running", "error": None})
        project.save()

        def progress(percent: int, detail: str) -> None:
            job.progress = start + int((end - start) * max(0, min(100, percent)) / 100)
            job.detail = detail

        outputs = stage.run(project, progress)
        stage_state.update({"status": "done", "outputs": outputs, "error": None, "fingerprint": stage.inputs_fingerprint(project)})
        project.save()
        job.progress = end
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


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    if "ffmpeg" in text.lower() or "ffprobe" in text.lower():
        return "Falta ffmpeg o hubo un problema leyendo los archivos de vídeo."
    if "songs.json" in text:
        return "El songs.json no se pudo leer correctamente."
    return text or "Error inesperado"
