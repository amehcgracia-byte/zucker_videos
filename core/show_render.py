"""Render a Show plan (core/show_edit.py) from one talking source.

Video: one FFmpeg pass. The source is first put on the plan's frame grid
(fps), the planned frames are selected by number, retimed back to back, and
zoompan gives every shot its framing (punch-ins), centred a little above the
middle where a face usually is.

Audio: decoded to float PCM, the same frame ranges are joined in Python with
short crossfades at every real cut (adjacent shots split only for pace stay
continuous), then muxed. Video and audio come from the same frame edges, so
they stay in sync shot after shot.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

SAMPLE_RATE = 48000
CROSSFADE_SEC = 0.008
FACE_HEIGHT = 0.42   # framing centre, share of the picture height


def _sum(terms: list[str]) -> str:
    """terms joined by + as a balanced tree: FFmpeg's expression parser
    refuses a flat sum of more than 100 terms ("Cannot allocate memory")."""
    if len(terms) <= 1:
        return terms[0] if terms else "0"
    middle = len(terms) // 2
    return f"({_sum(terms[:middle])}+{_sum(terms[middle:])})"


def kept_ranges(plan: dict[str, Any]) -> list[tuple[int, int]]:
    """Frame ranges of the edit; shots that touch are one range (no cut)."""
    ranges: list[list[int]] = []
    for shot in plan["shots"]:
        start, end = int(shot["start_frame"]), int(shot["end_frame"])
        if ranges and start <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    return [(a, b) for a, b in ranges]


def zoom_expression(plan: dict[str, Any]) -> str:
    """zoompan z(on): the zoom of the shot each output frame belongs to."""
    terms: dict[float, list[str]] = {}
    position = 0
    for shot in plan["shots"]:
        length = int(shot["end_frame"]) - int(shot["start_frame"])
        zoom = float(shot.get("zoom") or 1.0)
        if zoom != 1.0:
            terms.setdefault(zoom, []).append(f"between(on,{position},{position + length - 1})")
        position += length
    expression = "1"
    for zoom, parts in terms.items():
        expression += f"+{zoom - 1:.4f}*{_sum(parts)}"
    return expression


def video_filter(plan: dict[str, Any], width: int = 1920, height: int = 1080) -> str:
    fps = int(plan.get("fps") or 30)
    selection = _sum([f"between(n,{a},{b - 1})" for a, b in kept_ranges(plan)])
    zoom = zoom_expression(plan)
    # Work at 4/3 of the output so a 1.32 punch-in keeps its detail.
    work_w, work_h = width * 4 // 3 // 2 * 2, height * 4 // 3 // 2 * 2
    return (
        f"fps={fps},setpts=N/{fps}/TB,"
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=increase,crop={work_w}:{work_h},"
        f"select='{selection}',setpts=N/{fps}/TB,"
        f"zoompan=z='{zoom}':d=1:s={width}x{height}:fps={fps}"
        f":x='iw/2-iw/zoom/2':y='max(0,min(ih-ih/zoom,ih*{FACE_HEIGHT}-ih/zoom/2))',"
        "format=yuv420p"
    )


def _decode_audio(ffmpeg: str, source: Path) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(source), "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE),
                          "-f", "f32le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()


def edit_audio(samples: np.ndarray, plan: dict[str, Any]) -> np.ndarray:
    """The kept ranges joined with a short crossfade at each cut."""
    fps = int(plan.get("fps") or 30)
    fade = max(1, int(CROSSFADE_SEC * SAMPLE_RATE))
    pieces: list[np.ndarray] = []
    for a, b in kept_ranges(plan):
        start = int(round(a * SAMPLE_RATE / fps))
        end = int(round(b * SAMPLE_RATE / fps))
        piece = samples[start:end]
        if len(piece) < end - start:  # source shorter than the plan: pad silence
            piece = np.vstack([piece, np.zeros((end - start - len(piece), samples.shape[1]), dtype=np.float32)])
        piece = piece.copy()
        ramp = np.linspace(0.0, 1.0, min(fade, len(piece) // 2), dtype=np.float32)[:, None]
        if len(ramp):
            piece[:len(ramp)] *= ramp
            piece[-len(ramp):] *= ramp[::-1]
        pieces.append(piece)
    return np.vstack(pieces) if pieces else np.zeros((0, 2), dtype=np.float32)


def render_show(source: str | Path, plan: dict[str, Any], output: str | Path, *, ffmpeg: str = "ffmpeg",
                width: int = 1920, height: int = 1080, progress: Callable[[float], None] | None = None) -> Path:
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    audio = edit_audio(_decode_audio(ffmpeg, source), plan)
    pending = output.with_name(f".{output.stem}.part{output.suffix}")
    frames = sum(b - a for a, b in kept_ranges(plan))
    command = [
        ffmpeg, "-y", "-v", "error", "-nostdin", "-progress", "pipe:2",
        "-i", str(source),
        "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "pipe:0",
        "-filter_complex", f"[0:v]{video_filter(plan, width, height)}[v]",
        "-map", "[v]", "-map", "1:a", "-frames:v", str(frames),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(pending),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, text=False)
    try:
        assert process.stdin is not None and process.stderr is not None
        import threading
        errors: list[bytes] = []

        def read_progress() -> None:
            for line in process.stderr:
                errors.append(line)
                if progress and line.startswith(b"frame="):
                    try:
                        progress(min(1.0, int(line.split(b"=")[1]) / max(1, frames)))
                    except ValueError:
                        pass
        reader = threading.Thread(target=read_progress, daemon=True)
        reader.start()
        try:
            process.stdin.write(audio.astype(np.float32).tobytes())
            process.stdin.close()
        except BrokenPipeError:
            pass  # FFmpeg stopped early: its own error is reported below
        code = process.wait()
        reader.join(timeout=5)
        if code != 0:
            detail = b"".join(line for line in errors[-40:] if not line.startswith((b"frame=", b"fps=", b"out_", b"progress=", b"bitrate=", b"total_size=", b"speed=", b"dup_", b"drop_", b"stream_")))
            raise RuntimeError(f"Show render failed: {detail.decode(errors='replace').strip()[-2000:]}")
        pending.replace(output)
        return output
    finally:
        if process.poll() is None:
            process.kill()
        pending.unlink(missing_ok=True)
