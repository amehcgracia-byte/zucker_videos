"""Input validation and video metadata extraction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
from typing import Any

from core.ffmpeg import ffprobe
from core.media_validation import is_raw_360_path, raw_360_model_fov, validate_camera_video_metadata
from core.messages import t
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
            progress_callback(int((index - 1) / total * 25), t("checking_video", filename=Path(path).name))
            if not Path(path).exists():
                raise ValueError(f"Missing video file: {path}")
            metadata = ffprobe(path)
            validation = validate_camera_video_metadata(metadata)
            record["probe"] = validation.summary
            if is_raw_360_path(path):
                record["projection"] = "raw_insv"
                record["raw_360"] = True
                record["probe"]["projection"] = "raw_insv"
                record["probe"]["raw_360"] = True
                record["probe"]["input_projection"] = "dfisheye"
                record["probe"]["insv_fov"] = int(project.data["settings"].get("ingest", {}).get("insv_fov") or raw_360_model_fov(metadata))
                record.setdefault("info", "360 stitched automatically — for best quality, export from Insta360 Studio instead")
            if validation.valid:
                record.pop("status", None)
                record.pop("not_a_video_reason", None)
            else:
                record["status"] = "not_a_video"
                record["not_a_video_reason"] = validation.reason
                progress_callback(int(index / total * 90), t("ignoring_file", filename=Path(path).name, reason=validation.reason))
                continue
            valid_records.append(record)
        if sum(int(record.get("size") or 0) for record in valid_records) > 500 * 1024 * 1024 or len(valid_records) > 3:
            progress_callback(25, t("long_videos_note"))
        ensure_normalized_space(project, valid_records)
        prepare_videos(project, valid_records, progress_callback)
        progress_callback(100, "Ingest complete")
        return {}


def prepare_videos(project: Project, records: list[dict[str, Any]], progress_callback: ProgressCallback) -> None:
    """Prepare analysis proxies concurrently and aggregate progress."""
    if not records:
        return
    workers = max(1, min(len(records), int(project.data["settings"].get("ingest", {}).get("proxy_workers", 2))))
    progresses: dict[int, int] = {index: 0 for index in range(len(records))}
    labels: dict[int, str] = {index: Path(record["path"]).name for index, record in enumerate(records)}
    lock = threading.Lock()

    def set_progress(index: int, percent: int) -> None:
        with lock:
            progresses[index] = max(progresses[index], int(percent))

    def emit() -> None:
        with lock:
            overall = min(95, 25 + int(sum(progresses.values()) / max(1, len(records)) * 70 / 100))
            active = " · ".join(f"{labels[index]} {progresses[index]}%" for index in sorted(progresses) if progresses[index] < 100) or "complete"
        progress_callback(overall, t("preparing_videos", count=len(records), details=active))

    def run_one(index: int, record: dict[str, Any]) -> None:
        def clip_progress(percent: int, message: str) -> None:
            set_progress(index, percent)
            emit()

        normalize_video_record(project, record, clip_progress)
        set_progress(index, 100)
        emit()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run_one, index, record) for index, record in enumerate(records)]
        for future in as_completed(futures):
            future.result()
