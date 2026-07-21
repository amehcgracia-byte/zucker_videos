"""Shared media validation rules for user-supplied camera clips."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".mts",
    ".m2ts",
    ".avi",
    ".mkv",
    ".3gp",
    ".3g2",
    ".mpg",
    ".mpeg",
    ".ts",
    ".mxf",
    ".insv",
    ".insp",
}
SIDE_CAR_REASONS = {
    ".lrv": "camera sidecar file (low-resolution proxy)",
    ".thm": "camera sidecar file",
    ".xml": "camera sidecar file",
    ".srt": "subtitles, not a camera video",
}
RAW_360_EXTENSIONS = {".insv", ".insp"}
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
        return "hidden or system file"
    if suffix in SIDE_CAR_REASONS:
        return SIDE_CAR_REASONS[suffix]
    return None


def is_raw_360_path(path: str | Path) -> bool:
    """Return True for raw Insta360 container extensions."""
    return Path(path).suffix.lower() in RAW_360_EXTENSIONS


def raw_360_model_fov(metadata: dict[str, Any]) -> int:
    """Return a practical dual-fisheye FOV estimate from metadata."""
    text = str(metadata).lower()
    if "insta360 one x2" in text or "x2" in text:
        return 204
    if "insta360 x3" in text or "insta360 x4" in text or " x3" in text or " x4" in text:
        return 190
    return 190


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
        "fps": dominant_fps(video_stream or {}),
        "cfr": is_probably_cfr(video_stream or {}),
        "projection": projection(metadata, width, height),
        "bit_depth": bit_depth(video_stream or {}),
        "hdr": is_hdr_video(video_stream or {}),
        "valid_video": False,
    }
    if not video_stream or codec not in SANE_VIDEO_CODECS:
        return VideoValidation(False, "not a camera video", summary)
    if duration is None or duration <= MIN_VIDEO_DURATION_SEC:
        return VideoValidation(False, "video too short", summary)
    if width is None or height is None or width < MIN_VIDEO_WIDTH or height < MIN_VIDEO_HEIGHT:
        return VideoValidation(False, "resolution too low for a camera clip", summary)
    summary["valid_video"] = True
    return VideoValidation(True, "camera video", summary)


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


def record_media_path(record: dict[str, Any]) -> str:
    """Return the normalized media path that downstream stages should consume."""
    normalized = record.get("normalized") or {}
    return str(normalized.get("path") or record["path"])


def dominant_fps(stream: dict[str, Any]) -> float:
    """Return the best available frame rate from ffprobe stream metadata."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        fps = _parse_rate(stream.get(key))
        if fps > 0:
            return fps
    return 30.0


def is_probably_cfr(stream: dict[str, Any]) -> bool:
    """Return True when ffprobe rates do not suggest variable frame rate."""
    avg = _parse_rate(stream.get("avg_frame_rate"))
    nominal = _parse_rate(stream.get("r_frame_rate"))
    if avg <= 0 or nominal <= 0:
        return False
    return abs(avg - nominal) < 0.01


def projection(metadata: dict[str, Any], width: int | None, height: int | None) -> str | None:
    """Detect equirectangular 360 exports by metadata or 2:1 geometry."""
    text = str(metadata).lower()
    if "equirectangular" in text or "spherical" in text:
        return "equirect"
    if width and height and height > 0:
        ratio = width / height
        if 1.95 <= ratio <= 2.05:
            return "equirect"
    return None


def bit_depth(stream: dict[str, Any]) -> int | None:
    """Infer bit depth from ffprobe stream metadata."""
    bits = _int_or_none(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample"))
    if bits:
        return bits
    pix_fmt = str(stream.get("pix_fmt") or "")
    for marker in ("12", "10"):
        if marker in pix_fmt:
            return int(marker)
    if pix_fmt:
        return 8
    return None


def is_hdr_video(stream: dict[str, Any]) -> bool:
    """Return True for common HDR transfer/primaries/pixel-format hints."""
    transfer = str(stream.get("color_transfer") or "").lower()
    primaries = str(stream.get("color_primaries") or "").lower()
    pix_fmt = str(stream.get("pix_fmt") or "").lower()
    return (
        transfer in {"smpte2084", "arib-std-b67"}
        or primaries in {"bt2020", "bt2020nc"}
        or "p10" in pix_fmt
        or "10le" in pix_fmt
        or "12le" in pix_fmt
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


def _parse_rate(value: Any) -> float:
    if not value:
        return 0.0
    text = str(value)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            denom = float(denominator)
            return float(numerator) / denom if denom else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0
