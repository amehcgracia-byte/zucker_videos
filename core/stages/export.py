"""Simple ffmpeg export stage for the wizard."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from core.ffmpeg import FFmpegError, tool_status
from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage


class ExportStage(Stage):
    """Render a minimal one-segment MP4.

    Creative selection is placeholder logic. The stage only proves the
    end-to-end wizard path with deterministic ffmpeg output.
    """

    name = "export"
    dependencies = ["cut"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint cut output and export settings."""
        return stable_fingerprint(
            {
                "coverage": project.data["stages"]["cut"].get("fingerprint"),
                "wizard": project.data["settings"].get("wizard", {}),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the export manifest artifact path."""
        return {"export_manifest": str(artifact_path(project, "export_manifest.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Render the simple wizard export."""
        progress_callback(10, "Preparando exportación")
        coverage = load_coverage(project)
        segments = coverage.get("segments") or []
        if not segments:
            raise ValueError("No hay segmentos para exportar")
        segment = segments[0]
        master = project.data["inputs"].get("master")
        if not master:
            raise ValueError("Falta el audio master")
        platform = coverage.get("platform") or project.data["settings"].get("wizard", {}).get("platform") or "youtube"
        output_path = _output_path(project, platform)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        progress_callback(35, "Renderizando con ffmpeg")
        _render_segment(segment, master["path"], output_path, platform)
        progress_callback(85, "Guardando resultado")
        path = artifact_path(project, "export_manifest.json")
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "placeholder_logic": "single selected clip; master audio; simple crop only",
                "warnings": coverage.get("warnings") or segment.get("warnings") or [],
                "exports": [
                    {
                        "platform": platform,
                        "path": str(output_path),
                        "filename": output_path.name,
                        "duration_sec": segment["duration_sec"],
                        "warnings": segment.get("warnings") or [],
                    }
                ],
            },
        )
        progress_callback(100, "Exportación lista")
        return self.outputs(project)


def _output_path(project: Project, platform: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in " ._-" else "-" for ch in project.data["name"]).strip() or "video"
    return project.exports_dir / f"{safe_name}-{platform}.mp4"


def _render_segment(segment: dict[str, Any], master_path: str, output_path: Path, platform: str) -> None:
    status = tool_status()
    ffmpeg = status.get("ffmpeg_path")
    if not ffmpeg:
        raise FFmpegError("ffmpeg is missing. Install it with: brew install ffmpeg")
    duration = max(1.0, float(segment["duration_sec"]))
    command = [
        str(ffmpeg),
        "-y",
        "-ss",
        f"{float(segment['clip_start_sec']):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(segment["clip_path"])),
        "-ss",
        f"{float(segment['master_start_sec']):.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(Path(master_path)),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
    ]
    if platform in {"instagram", "tiktok"}:
        command.extend(["-vf", "scale=-2:720,crop=404:720"])
    else:
        command.extend(["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"])
    command.append(str(output_path))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(result.stderr.strip() or "ffmpeg export failed")
