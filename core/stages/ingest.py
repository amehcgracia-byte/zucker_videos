"""Input validation and video metadata extraction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import threading
import logging
import time
from typing import Any

from core.ffmpeg import ffprobe
from core.media_validation import is_raw_360_path, raw_360_model_fov, validate_camera_video_metadata
from core.messages import t
from core.normalization import ensure_global_cache_dirs, ensure_normalized_space, normalize_video_record
from core.operator_avoidance import OPERATOR_AVOIDANCE_VERSION, analyze_and_cache_operator_presence, role_for_record
from core.reel_framing import REEL_FRAMING_VERSION, analyze_reel_framing_records
from core.project import Project
from core.stages.base import ProgressCallback, ProgressDetail, Stage, stable_fingerprint

LOGGER = logging.getLogger(__name__)


class IngestStage(Stage):
    """Validate registered inputs and store probed video metadata."""

    name = "ingest"
    dependencies: list[str] = []

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint registered input records and ingest settings."""
        platform = str(project.data.get("settings", {}).get("wizard", {}).get("platform") or "")
        return stable_fingerprint(
            {
                "videos": project.data["inputs"].get("videos", []),
                "settings": project.data["settings"].get(self.name, {}),
                "mode": platform,
                # Detection sampling/threshold changes invalidate the global
                # presence cache and must trigger a fresh ingest pass.
                "operator_avoidance_version": OPERATOR_AVOIDANCE_VERSION,
                "reel_framing_version": REEL_FRAMING_VERSION if platform == "reel" else None,
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Ingest writes metadata into project.json only."""
        return {}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Validate inputs and probe each video with ffprobe."""
        ensure_global_cache_dirs()
        inputs = project.data["inputs"]
        videos = inputs.get("videos", [])
        if not videos:
            raise ValueError("At least one video must be registered before ingest")

        total = len(videos)
        platform = str(project.data.get("settings", {}).get("wizard", {}).get("platform") or "")
        passthrough_360 = platform == "360"
        backstage = platform == "backstage"
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
            elif not record["probe"].get("projection"):
                width = record["probe"].get("width")
                height = record["probe"].get("height")
                record["projection_warning"] = (
                    f"No se detectó vídeo 360/equirectangular: {width}×{height} "
                    f"({(float(width) / float(height)):.3f}:1). Se tratará como cámara plana; "
                    "si debía ser 360, expórtalo cosido desde Insta360 Studio."
                    if width and height else
                    "No se detectó proyección 360/equirectangular; se tratará como cámara plana."
                )
            # Persist the resolved role at ingest. Downstream stages must not
            # have to re-infer a 360 source from a normalized proxy filename.
            record["camera_role"] = role_for_record(
                str(record.get("projection") or record["probe"].get("projection") or ""),
                Path(path).name,
                record,
            )
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
        if backstage:
            # Backstage is video-led and owns its own analysis. Do not create
            # music-mode proxies or run operator avoidance here.
            for record in valid_records:
                record["normalized"] = {"path": record["path"], "kind": "original"}
        elif passthrough_360:
            # A navigable 360 export must retain the original equirectangular
            # body. MP4 needs no preparation; only raw INSV is normalized to an
            # equirectangular MP4. Do not build proxies, score cameras, or run
            # operator avoidance in this mode.
            raw_records = [record for record in valid_records if record.get("raw_360")]
            for record in valid_records:
                if record not in raw_records:
                    record["normalized"] = {"path": record["path"], "kind": "original"}
            if raw_records:
                progress_callback(25, "360 raw source detected — converting to equirectangular video")
                ensure_normalized_space(project, raw_records)
                prepare_videos(project, raw_records, progress_callback)
            else:
                progress_callback(25, "360 equirectangular source ready — no proxy conversion needed")
        else:
            ensure_normalized_space(project, valid_records)
            prepare_videos(project, valid_records, progress_callback,
                analyze_ready=platform in {"reel", "youtube", "instagram", "tiktok"}
                    and not (platform == "reel" and len(valid_records) == 1))
        progress_callback(100, "Ingest complete")
        return {}


def prepare_videos(project: Project, records: list[dict[str, Any]], progress_callback: ProgressCallback, *, analyze_ready: bool = False) -> None:
    """Analyze completed proxies while the remaining sources normalize.

    One analysis consumer bounds CPU/memory pressure; proxy workers stay free
    to prepare the next files. Each source is analyzed exactly once.
    """
    if not records:
        return
    workers = max(1, min(len(records), int(project.data["settings"].get("ingest", {}).get("proxy_workers", 2))))
    proxy_share = 70 if analyze_ready else 100
    progresses: dict[int, int] = {index: 0 for index in range(len(records))}
    labels: dict[int, str] = {index: Path(record["path"]).name for index, record in enumerate(records)}
    lock = threading.Lock()

    def set_progress(index: int, percent: int) -> None:
        with lock:
            progresses[index] = max(progresses[index], int(percent))

    def emit(index: int, local_percent: float, phase: str = "proxy", detail: str = "") -> None:
        with lock:
            overall = min(95, 25 + int(sum(progresses.values()) / max(1, len(records)) * 70 / 100))
            active = " · ".join(f"{labels[index]} {progresses[index]}%" for index in sorted(progresses) if progresses[index] < 100) or "complete"
        progress_callback(overall, ProgressDetail(detail or t("preparing_videos", count=len(records), details=active),
            task_id=f"{phase}-{index}", label=f"{phase.capitalize()}: {labels[index]}", percent=local_percent))

    def run_one(index: int, record: dict[str, Any]) -> None:
        LOGGER.info("Ingest normalization queued index=%d source=%s", index, record.get("path"))
        def clip_progress(percent: int, message: str) -> None:
            set_progress(index, percent * proxy_share / 100)
            emit(index, percent)
        try:
            normalize_video_record(project, record, clip_progress)
            set_progress(index, proxy_share)
            emit(index, 100)
            LOGGER.info("Ingest normalization finished index=%d source=%s", index, record.get("path"))
        except Exception:
            LOGGER.exception("Ingest normalization failed index=%d source=%s", index, record.get("path"))
            raise

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_one, index, record): (index, record)
                   for index, record in enumerate(records)}
        for future in as_completed(futures):
            future.result()
            if not analyze_ready:
                continue
            index, record = futures[future]
            analysis_started = time.perf_counter()
            LOGGER.info("Ingest analysis started source=%s", record.get("path"))
            def analysis_progress(offset: int, span: int, phase: str):
                def callback(percent: int, detail: str) -> None:
                    task = detail.task if isinstance(detail, ProgressDetail) else None
                    local = float(task["percent"]) if task and task.get("percent") is not None else 0
                    set_progress(index, offset + span * max(0, min(100, local)) / 100)
                    emit(index, local, phase, str(detail))
                return callback
            if len(records) > 1:
                analyze_reel_framing_records([record], analysis_progress(70, 20, "framing"))
            set_progress(index, 90)
            analyze_operator_presence([record], analysis_progress(90, 10, "operator"))
            set_progress(index, 100)
            emit(index, 100, "prepared", f"Prepared and analyzed {labels[index]}")
            LOGGER.info("Ingest analysis finished source=%s elapsed_sec=%.3f", record.get("path"), time.perf_counter() - analysis_started)


def analyze_operator_presence(records: list[dict[str, Any]], progress_callback: ProgressCallback) -> None:
    """Scan non-Sony clips for a prominent camera operator, cached globally.

    Sony (handheld) is skipped: it IS the operator's own camera and is never
    the target of an avoidance adjustment.
    """
    candidates = [
        record
        for record in records
        if role_for_record(str((record.get("probe") or {}).get("projection") or record.get("projection") or ""), Path(str(record.get("path") or "")).name, record) != "handheld"
    ]
    if not candidates:
        return
    total = len(candidates)
    for index, record in enumerate(candidates, start=1):
        analysis_path = (record.get("normalized") or {}).get("path") or record.get("path")
        if not analysis_path:
            continue
        filename = Path(str(record.get("path") or "clip")).name

        def clip_progress(percent: int, message: str) -> None:
            overall = min(99, 97 + int((((index - 1) * 100) + percent) / max(1, total) * 2 / 100))
            progress_callback(overall, ProgressDetail(message or f"Scanning {filename} for camera operator",
                task_id=f"operator-{index}", label=f"Scanning {filename} for camera operator", percent=percent))

        try:
            analyze_and_cache_operator_presence(str(analysis_path), clip_progress)
        except Exception:
            # Detection is a best-effort quality pass; never fail ingest over it.
            continue
