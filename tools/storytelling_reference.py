"""Make a Storytelling test kit from a narrated video that is already edited.

    python tools/storytelling_reference.py NARRATED_VIDEO.mp4 OUTPUT_FOLDER

The narration becomes an audio file of its own (MP3), and the pictures become loose clips,
one per shot of the edit (cut where the video itself cuts, short flashes left out), as if
they were the material found for the story. The Storytelling mode then has to put the
pictures back over the narration. Media stay in OUTPUT_FOLDER, never in Git.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import source_cuts  # noqa: E402
from core.ffmpeg import ffprobe, tool_status  # noqa: E402

FPS = 25.0
SHORTEST = 2.0        # seconds: shorter shots (flashes, titles) are not kept as clips


def main(source: Path, out: Path) -> None:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    out.mkdir(parents=True, exist_ok=True)
    clips = out / "clips"
    clips.mkdir(exist_ok=True)
    narration = out / f"{source.stem}-narration.mp3"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(source), "-vn", "-c:a", "libmp3lame", "-b:a", "160k", str(narration)], check=True)
    print("narration", narration.name, flush=True)
    duration = float((ffprobe(str(source)).get("format") or {}).get("duration") or 0.0)
    cuts = [frame / FPS for frame in source_cuts.detect(source, out / ".cache", ffmpeg, fps=FPS)]
    edges = [0.0, *cuts, duration]
    made = []
    for number, (a, b) in enumerate(zip(edges, edges[1:]), 1):
        if b - a < SHORTEST:
            continue
        clip = clips / f"clip-{number:03d}.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{a + 0.04:.3f}", "-i", str(source), "-t", f"{b - a - 0.08:.3f}", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(clip)], check=True)
        made.append({"file": clip.name, "from": round(a, 2), "to": round(b, 2)})
    (out / "kit.json").write_text(json.dumps({"source": str(source), "narration": narration.name, "clips": made}, indent=1))
    print(f"{len(made)} clips from {len(edges) - 1} shots", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
