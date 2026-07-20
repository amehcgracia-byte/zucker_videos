"""ffmpeg export stage for wizard edit plans."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from core.ffmpeg import FFmpegError, tool_status
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage
from core.stages.edit import load_edit_plan

MAX_EXPORT_BYTES = int(1.9 * 1024 * 1024 * 1024)
AUDIO_BITRATE = 192_000
MIN_ACCEPTABLE_VIDEO_BITRATE = 2_500_000
MAX_VIDEO_BITRATE = 18_000_000


class ExportStage(Stage):
    """Render the wizard edit plan to MP4."""

    name = "export"
    dependencies = ["edit"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint edit output and export settings."""
        return stable_fingerprint(
            {
                "edit": project.data["stages"]["edit"].get("fingerprint"),
                "wizard": project.data["settings"].get("wizard", {}),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the export manifest artifact path."""
        return {"export_manifest": str(artifact_path(project, "export_manifest.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Render the wizard export with streamed ffmpeg progress."""
        progress_callback(5, "Preparando exportación")
        try:
            plan = load_edit_plan(project)
        except FileNotFoundError:
            plan = load_coverage(project)
        segments = plan.get("segments") or []
        if not segments:
            raise ValueError("No hay segmentos para exportar")
        master = project.data["inputs"].get("master")
        if not master:
            raise ValueError("Falta el audio master")
        platform = plan.get("platform") or project.data["settings"].get("wizard", {}).get("platform") or "youtube"
        output_path = _output_path(project, platform)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        duration = _plan_duration(segments)
        bitrate_info = _bitrate_for_duration(duration)
        if bitrate_info["warning"]:
            progress_callback(6, bitrate_info["warning"])
        _render_plan(segments, master["path"], output_path, platform, bitrate_info["video_bitrate"], progress_callback)
        progress_callback(95, "Guardando resultado")
        path = artifact_path(project, "export_manifest.json")
        warnings = list(plan.get("warnings") or [])
        if bitrate_info["warning"]:
            warnings.append(bitrate_info["warning"])
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "render_logic": "edit-plan segments with master-audio slices; concat",
                "warnings": warnings,
                "max_export_bytes": MAX_EXPORT_BYTES,
                "target_video_bitrate": bitrate_info["video_bitrate"],
                "exports": [
                    {
                        "platform": platform,
                        "path": str(output_path),
                        "filename": output_path.name,
                        "duration_sec": duration,
                        "warnings": warnings,
                        "cut_count": int(plan.get("cut_count") or max(0, len(segments) - 1)),
                        "camera_usage": plan.get("camera_usage") or _camera_usage(segments),
                    }
                ],
            },
        )
        progress_callback(100, "Exportación lista")
        return self.outputs(project)


def _output_path(project: Project, platform: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in project.data["name"]).strip() or "video"
    return project.exports_dir / f"{safe_name}-{platform}.mp4"


def _render_plan(
    segments: list[dict[str, Any]],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    progress_callback: ProgressCallback,
) -> None:
    ffmpeg = _ffmpeg_path()
    temp_dir = output_path.parent / f".{output_path.stem}-segments"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    segment_paths: list[Path] = []
    total_duration = _plan_duration(segments)
    rendered_duration = 0.0
    try:
        for index, segment in enumerate(segments, start=1):
            segment_path = temp_dir / f"segment-{index:04d}.mp4"
            segment_duration = max(0.1, float(segment["duration_sec"]))
            base_percent = 10 + int(70 * rendered_duration / max(total_duration, 0.1))

            def segment_progress(local_percent: int, detail: str, *, index: int = index) -> None:
                segment_share = 70 * segment_duration / max(total_duration, 0.1)
                percent = base_percent + int(segment_share * local_percent / 100)
                progress_callback(min(84, percent), f"Renderizando segmento {index}/{len(segments)}: {detail}")

            _render_segment(segment, master_path, segment_path, platform, video_bitrate, segment_progress)
            segment_paths.append(segment_path)
            rendered_duration += segment_duration
        concat_path = temp_dir / "concat.txt"
        concat_path.write_text("".join(_concat_file_line(path) for path in segment_paths), encoding="utf-8")
        _run_ffmpeg_progress(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-progress",
                "pipe:1",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-c",
                "copy",
                str(output_path),
            ],
            total_duration,
            "Uniendo segmentos",
            lambda percent, detail: progress_callback(84 + int(percent * 11 / 100), detail),
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _render_segment(
    segment: dict[str, Any],
    master_path: str,
    output_path: Path,
    platform: str,
    video_bitrate: int,
    progress_callback: ProgressCallback | None = None,
) -> None:
    ffmpeg = _ffmpeg_path()
    duration = max(0.1, float(segment["duration_sec"]))
    command_base = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-progress",
        "pipe:1",
        "-ss",
        f"{float(segment['clip_start_sec']):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(segment["clip_path"])),
        "-ss",
        f"{float(segment.get('master_start_sec') or 0.0):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(master_path)),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        _video_filter(platform),
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
    ]
    hardware = _video_encode_args("h264_videotoolbox", video_bitrate)
    software = _video_encode_args("libx264", video_bitrate)
    try:
        _run_ffmpeg_progress(command_base + hardware + [str(output_path)], duration, Path(str(segment["clip_path"])).name, progress_callback)
    except FFmpegError as exc:
        if output_path.exists():
            output_path.unlink()
        if progress_callback:
            progress_callback(0, "h264_videotoolbox no disponible; usando libx264")
        try:
            _run_ffmpeg_progress(command_base + software + [str(output_path)], duration, Path(str(segment["clip_path"])).name, progress_callback)
        except FFmpegError as fallback_exc:
            raise FFmpegError(f"{fallback_exc}\nFallback after h264_videotoolbox failed with: {exc}") from fallback_exc


def _run_ffmpeg_progress(command: list[str], duration: float, label: str, progress: ProgressCallback | None) -> None:
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    current = -1
    for line in process.stdout:
        match = re.match(r"out_time_ms=(\d+)", line.strip())
        if not match or duration <= 0:
            continue
        seconds = int(match.group(1)) / 1_000_000
        percent = max(current, min(99, int(seconds / duration * 100)))
        if progress and percent > current:
            current = percent
            progress(percent, f"{label} — {percent}%")
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError((stderr or "").strip() or "ffmpeg export failed")
    if progress:
        progress(100, f"{label} — 100%")


def _video_filter(platform: str) -> str:
    if platform in {"instagram", "tiktok"}:
        return "scale=608:1080:force_original_aspect_ratio=increase,crop=608:1080,setsar=1,format=yuv420p"
    return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"


def _concat_file_line(path: Path) -> str:
    return "file '{}'\n".format(path.as_posix().replace("'", "'\\''"))


def _video_encode_args(codec: str, video_bitrate: int) -> list[str]:
    if codec == "h264_videotoolbox":
        return [
            "-c:v",
            codec,
            "-b:v",
            str(video_bitrate),
            "-maxrate",
            str(int(video_bitrate * 1.2)),
            "-bufsize",
            str(int(video_bitrate * 2)),
            "-profile:v",
            "high",
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(video_bitrate),
        "-maxrate",
        str(int(video_bitrate * 1.2)),
        "-bufsize",
        str(int(video_bitrate * 2)),
    ]


def _bitrate_for_duration(duration: float) -> dict[str, Any]:
    seconds = max(1.0, duration)
    budget_bits = MAX_EXPORT_BYTES * 8 * 0.94
    target = int(max(300_000, budget_bits / seconds - AUDIO_BITRATE))
    warning = None
    if target < MIN_ACCEPTABLE_VIDEO_BITRATE:
        warning = "El vídeo es largo: lo exporté con bitrate limitado para quedar por debajo de 1.9 GB."
    return {"video_bitrate": min(target, MAX_VIDEO_BITRATE), "warning": warning}


def _plan_duration(segments: list[dict[str, Any]]) -> float:
    return sum(max(0.0, float(segment.get("duration_sec") or 0.0)) for segment in segments)


def _camera_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        name = Path(str(segment.get("clip_path"))).name
        usage[name] = usage.get(name, 0) + 1
    return usage


def _ffmpeg_path() -> str:
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    return str(ffmpeg)
