"""Input validation and video metadata extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.ffmpeg import ffprobe
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
        for index, record in enumerate(videos, start=1):
            path = record["path"]
            progress_callback(int((index - 1) / total * 90), f"Probing {Path(path).name}")
            if not Path(path).exists():
                raise ValueError(f"Missing video file: {path}")
            metadata = ffprobe(path)
            record["probe"] = _summarize_probe(metadata)
        progress_callback(100, "Ingest complete")
        return {}


def _summarize_probe(metadata: dict[str, Any]) -> dict[str, Any]:
    streams = metadata.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    fmt = metadata.get("format", {})
    return {
        "duration": _float_or_none(fmt.get("duration") or video_stream.get("duration")),
        "format_name": fmt.get("format_name"),
        "video_codec": video_stream.get("codec_name"),
        "audio_codec": audio_stream.get("codec_name"),
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "rotation": _rotation(video_stream),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict[str, Any]) -> int:
    tags = stream.get("tags") or {}
    side_data = stream.get("side_data_list") or []
    if "rotate" in tags:
        return int(tags["rotate"])
    for item in side_data:
        if "rotation" in item:
            return int(item["rotation"])
    return 0
