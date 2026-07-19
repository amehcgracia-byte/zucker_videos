"""Input validation and video metadata extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.ffmpeg import ffprobe
from core.media_validation import validate_camera_video_metadata
from core.normalization import ensure_normalized_space, normalize_video_record
from core.project import Project
from core.stages.base import ProgressCallback, Stage, stable_fingerprint


class IngestStage(Stage):
    """Validate registered inputs and store probed video metadata."""

    name = "ingest"
    dependencies: list[str] = []

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint registered input records and ingest settings."""
        return stable_fingerprint(
            {
                "videos": project.data["inputs"].get("videos", []),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Ingest writes metadata into project.json only."""
        return {}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Validate inputs and probe each video with ffprobe."""
        inputs = project.data["inputs"]
        videos = inputs.get("videos", [])
        if not videos:
            raise ValueError("At least one video must be registered before ingest")

        total = len(videos)
        valid_records: list[dict[str, Any]] = []
        for index, record in enumerate(videos, start=1):
            path = record["path"]
            progress_callback(int((index - 1) / total * 25), f"Revisando {Path(path).name}")
            if not Path(path).exists():
                raise ValueError(f"Missing video file: {path}")
            metadata = ffprobe(path)
            validation = validate_camera_video_metadata(metadata)
            record["probe"] = validation.summary
            if validation.valid:
                record.pop("status", None)
                record.pop("not_a_video_reason", None)
            else:
                record["status"] = "not_a_video"
                record["not_a_video_reason"] = validation.reason
                progress_callback(int(index / total * 90), f"Ignorando {Path(path).name}: {validation.reason}")
                continue
            valid_records.append(record)
        if sum(int(record.get("size") or 0) for record in valid_records) > 500 * 1024 * 1024 or len(valid_records) > 3:
            progress_callback(25, "Los vídeos largos tardan un poco la primera vez; luego quedan preparados")
        ensure_normalized_space(project, valid_records)
        for index, record in enumerate(valid_records, start=1):
            filename = Path(record["path"]).name
            base = 25 + int((index - 1) / max(1, len(valid_records)) * 70)
            span = max(1, int(70 / max(1, len(valid_records))))

            def clip_progress(percent: int, message: str, *, base: int = base, span: int = span, index: int = index, filename: str = filename) -> None:
                overall = min(95, base + int(percent / 100 * span))
                progress_callback(overall, f"Preparando vídeo {index}/{len(valid_records)}: {filename} — {percent}%")

            normalize_video_record(project, record, clip_progress)
        progress_callback(100, "Ingest complete")
        return {}
