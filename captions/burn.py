from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .model import CueTrack, Style
from .render import render_ass


def burn(video_path: str | Path, cue_track: CueTrack, style: Style, *, output_path: str | Path | None = None, ffmpeg: str = "ffmpeg", header: dict | None = None, logo_path: str | Path | None = None, letterbox: dict | None = None) -> Path:
    source = Path(video_path).resolve()
    destination = Path(output_path).resolve() if output_path else source.with_name(f"{source.stem}_captions.mp4")
    if destination == source:
        raise ValueError("caption burn must not overwrite the source video")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="captions-") as directory:
        ass = Path(directory) / "captions.ass"
        ass.write_text(render_ass(cue_track, style, header=header), encoding="utf-8")
        escaped = str(ass).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
        vf = "subtitles=filename=%s" % escaped
        if letterbox and letterbox.get("enabled"):
            width, height = int(letterbox.get("width", 1080)), int(letterbox.get("height", 1920))
            sigma = max(1, float(letterbox.get("blur", 18)))
            vf = f"split=2[bg][fg];[bg]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma={sigma}[blur];[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[main];[blur][main]overlay=(W-w)/2:(H-h)/2,{vf}"
        if header and header.get("title_enabled") and header.get("title"):
            title = str(header["title"]).upper().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("\n", r"\\n")
            vf += f",drawbox=x=0.12*iw:y=40:w=0.76*iw:h=110:color=white:t=fill,drawtext=fontcolor=black:fontsize=42:text='{title}':x=(w-text_w)/2:y=70"
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source)]
        logo_enabled = bool(header and (header.get("logo_enabled") or header.get("logo_source") in {"custom", "default"}))
        if logo_path and logo_enabled:
            command += ["-loop", "1", "-i", str(Path(logo_path).resolve())]
            command += ["-filter_complex", f"[0:v]{vf}[captioned];[1:v]format=rgba,scale=-1:{int(header.get('logo_height', 120))}[logo];[captioned][logo]overlay=(W-w)/2:{int(header.get('logo_top', 150))}:shortest=1[v]"]
            command += ["-map", "[v]", "-map", "0:a?", "-shortest"]
        else:
            command += ["-vf", vf]
        command += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(destination)]
        subprocess.run(command, check=True)
    return destination
