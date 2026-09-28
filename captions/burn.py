from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from .model import CueTrack, Style
from .render import render_ass


def _video_dimensions(video_path: Path, ffmpeg: str) -> tuple[int, int]:
    """Read the real output geometry without importing any mode pipeline."""
    ffmpeg_path = Path(ffmpeg)
    ffprobe = str(ffmpeg_path.with_name("ffprobe")) if ffmpeg_path.parent != Path(".") else "ffprobe"
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(video_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        stream = (json.loads(result.stdout).get("streams") or [{}])[0]
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    except (OSError, subprocess.CalledProcessError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return 1920, 1080


def burn(video_path: str | Path, cue_track: CueTrack, style: Style, *, output_path: str | Path | None = None, ffmpeg: str = "ffmpeg", header: dict | None = None, logo_path: str | Path | None = None, letterbox: dict | None = None, progress_callback=None) -> Path:
    source = Path(video_path).resolve()
    destination = Path(output_path).resolve() if output_path else source.with_name(f"{source.stem}_captions.mp4")
    if destination == source:
        raise ValueError("caption burn must not overwrite the source video")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="captions-") as directory:
        ass = Path(directory) / "captions.ass"
        width, height = _video_dimensions(source, ffmpeg)
        if letterbox and letterbox.get("enabled"):
            width = int(letterbox.get("width", width))
            height = int(letterbox.get("height", height))
        caption_enabled = bool(cue_track.cues) or bool(
            header and header.get("title_enabled") and header.get("title")
        ) or bool(letterbox and letterbox.get("enabled"))
        if caption_enabled:
            ass.write_text(render_ass(cue_track, style, width=width, height=height, header=header), encoding="utf-8")
            escaped = str(ass).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
            vf = "subtitles=filename=%s" % escaped
        else:
            # A YouTube logo-only composition must not pass through an empty
            # ASS filter; some FFmpeg builds turn that no-op subtitle graph
            # into a black video.
            vf = "null"
        if letterbox and letterbox.get("enabled"):
            width, height = int(letterbox.get("width", 1080)), int(letterbox.get("height", 1920))
            sigma = max(1, float(letterbox.get("blur", 18)))
            vf = f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma={sigma}[blur];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[main];[blur][main]overlay=(W-w)/2:(H-h)/2,{vf}"
        if header and header.get("title_enabled") and header.get("title"):
            title = str(header["title"]).upper().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("\n", r"\\n")
            vf += f",drawbox=x=0.12*iw:y=40:w=0.76*iw:h=110:color=white:t=fill,drawtext=fontcolor=black:fontsize=42:text='{title}':x=(w-text_w)/2:y=70"
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if progress_callback is not None:
            command += ["-progress", "pipe:1", "-nostats"]
        command += ["-i", str(source)]
        logo_enabled = bool(header and (header.get("logo_enabled") or header.get("logo_source") in {"custom", "default"}))
        if logo_path and logo_enabled:
            command += ["-loop", "1", "-i", str(Path(logo_path).resolve())]
            overlay = header.get("logo_overlay") if isinstance(header.get("logo_overlay"), dict) else {}
            logo_width = max(40, int(width * max(0.05, min(0.9, float(overlay.get("width", 0.22))))))
            logo_x = max(0.0, min(1.0, float(overlay.get("x", 0.5))))
            logo_y = max(0.0, min(1.0, float(overlay.get("y", 0.08))))
            # Keep the source video as the clock.  The logo input is looped,
            # so -shortest can terminate on the wrong stream or produce a
            # black/empty tail on some FFmpeg builds.
            command += ["-filter_complex", f"[0:v]{vf}[captioned];[1:v]format=rgba,scale={logo_width}:-1[logo];[captioned][logo]overlay=(W-w)*{logo_x:.5f}:(H-h)*{logo_y:.5f}:eof_action=repeat:shortest=0[v]"]
            command += ["-map", "[v]", "-map", "0:a?"]
        else:
            command += ["-vf", vf]
        # Bound the output to the real source duration because the logo
        # input is intentionally infinite.
        try:
            duration_probe = subprocess.run(
                [
                    str(Path(ffmpeg).with_name("ffprobe")) if Path(ffmpeg).parent != Path(".") else "ffprobe",
                    "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(source),
                ],
                check=True, capture_output=True, text=True,
            )
            source_duration = float(duration_probe.stdout.strip() or 0.0)
        except (OSError, subprocess.CalledProcessError, ValueError):
            source_duration = 0.0
        duration_args = ["-t", f"{source_duration:.3f}"] if source_duration > 0 else []
        command += duration_args + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(destination)]
        if progress_callback is None:
            subprocess.run(command, check=True)
        else:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            assert process.stdout is not None
            for line in process.stdout:
                if line.startswith("out_time_ms="):
                    try:
                        progress_callback(float(line.split("=", 1)[1]) / 1_000_000.0)
                    except (TypeError, ValueError):
                        pass
            stderr = process.stderr.read() if process.stderr is not None else ""
            return_code = process.wait()
            if return_code:
                raise subprocess.CalledProcessError(return_code, command, stderr=stderr)
    return destination
