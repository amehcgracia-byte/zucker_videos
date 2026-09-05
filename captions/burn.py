from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .model import CueTrack, Style
from .render import render_ass


def burn(video_path: str | Path, cue_track: CueTrack, style: Style, *, output_path: str | Path | None = None, ffmpeg: str = "ffmpeg") -> Path:
    source = Path(video_path).resolve()
    destination = Path(output_path).resolve() if output_path else source.with_name(f"{source.stem}_captions.mp4")
    if destination == source:
        raise ValueError("caption burn must not overwrite the source video")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="captions-") as directory:
        ass = Path(directory) / "captions.ass"
        ass.write_text(render_ass(cue_track, style), encoding="utf-8")
        escaped = str(ass).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(source), "-vf", "subtitles=filename=%s" % escaped, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(destination)]
        subprocess.run(command, check=True)
    return destination
