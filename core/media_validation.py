"""Shared media validation rules for user-supplied camera clips."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mts", ".avi", ".mkv"}
SIDE_CAR_REASONS = {
    ".lrv": "archivo auxiliar de la cámara (versión en baja resolución)",
    ".thm": "archivo auxiliar de la cámara",
    ".xml": "archivo auxiliar de la cámara",
    ".srt": "subtítulos, no es un vídeo de cámara",
}
SANE_VIDEO_CODECS = {
    "h264",
    "hevc",
    "h265",
    "mpeg4",
    "prores",
    "vp9",
    "av1",
    "vp8",
    "mpeg2video",
}
MIN_VIDEO_DURATION_SEC = 2.0
MIN_VIDEO_WIDTH = 320
MIN_VIDEO_HEIGHT = 240


@dataclass(frozen=True)
class VideoValidation:
    """Result of validating one ffprobe metadata payload as a camera clip."""

    valid: bool
    reason: str
    summary: dict[str, Any]


def static_rejection_reason(path: Path) -> str | None:
    """Return a rejection reason that can be decided without probing."""
    name = path.name
    suffix = path.suffix.lower()
    if name == ".DS_Store" or name.startswith("."):
        return "archivo oculto o del sistema"
    if suffix in SIDE_CAR_REASONS:
        return SIDE_CAR_REASONS[suffix]
    if suffix not in VIDEO_EXTENSIONS:
        return "tipo de archivo no compatible"
    return None


def validate_camera_video_metadata(metadata: dict[str, Any]) -> VideoValidation:
    """Validate ffprobe JSON for a usable camera video stream."""
    streams = metadata.get("streams") or []
    fmt = metadata.get("format") or {}
    video_stream = _first_video_stream(streams)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    duration = _duration(fmt, video_stream)
    width = _int_or_none(video_stream.get("width")) if video_stream else None
    height = _int_or_none(video_stream.get("height")) if video_stream else None
    codec = str(video_stream.get("codec_name") or "").lower() if video_stream else None
    summary = {
        "duration": duration,
        "format_name": fmt.get("format_name"),
        "video_codec": codec,
        "audio_codec": audio_stream.get("codec_name"),
        "width": width,
        "height": height,
        "rotation": _rotation(video_stream or {}),
        "valid_video": False,
    }
    if not video_stream or codec not in SANE_VIDEO_CODECS:
        return VideoValidation(False, "no es un vídeo de cámara", summary)
    if duration is None or duration <= MIN_VIDEO_DURATION_SEC:
        return VideoValidation(False, "vídeo demasiado corto", summary)
    if width is None or height is None or width < MIN_VIDEO_WIDTH or height < MIN_VIDEO_HEIGHT:
        return VideoValidation(False, "resolución demasiado baja para ser un clip de cámara", summary)
    summary["valid_video"] = True
    return VideoValidation(True, "video de cámara", summary)


def record_is_usable_camera_video(record: dict[str, Any]) -> bool:
    """Return True when a project video record passed ingest validation."""
    if record.get("status") == "not_a_video":
        return False
    probe = record.get("probe") or {}
    if probe.get("valid_video") is True:
        return True
    codec = str(probe.get("video_codec") or "").lower()
    duration = _float_or_none(probe.get("duration"))
    width = _int_or_none(probe.get("width"))
    height = _int_or_none(probe.get("height"))
    return (
        codec in SANE_VIDEO_CODECS
        and duration is not None
        and duration > MIN_VIDEO_DURATION_SEC
        and width is not None
        and height is not None
        and width >= MIN_VIDEO_WIDTH
        and height >= MIN_VIDEO_HEIGHT
    )


def _first_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") == "video":
            return stream
    return None


def _duration(fmt: dict[str, Any], stream: dict[str, Any] | None) -> float | None:
    value = fmt.get("duration")
    if value is None and stream:
        value = stream.get("duration")
    return _float_or_none(value)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
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
