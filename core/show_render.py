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

from core.ffmpeg import h264_args
from core.show_camera import FACE_HEIGHT, glide

SAMPLE_RATE = 48000
CROSSFADE_SEC = 0.008


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


def _per_shot(plan: dict[str, Any], value: Callable[..., str | None], default: str) -> str:
    """An expression of the output frame number ``on``: ``value(shot, first, length, index)``
    during each shot (shots returning None keep ``default``)."""
    terms: list[str] = []
    position = 0
    for index, shot in enumerate(plan["shots"]):
        length = int(shot["end_frame"]) - int(shot["start_frame"])
        part = value(shot, position, length, index)
        if part is not None:
            terms.append(f"between(on,{position},{position + length - 1})*({part}-({default}))")
        position += length
    return f"{default}+{_sum(terms)}" if terms else default


def _eased(plan: dict[str, Any], index: int, first: int, start: str, target: str) -> str:
    """``target`` reached from ``start`` by the camera move opening shot ``index`` (if any)."""
    move = glide(plan, index)
    if not move:
        return target
    _, frames, skip = move
    t = f"min(1,(on-{first}+{skip})/{frames})"
    return f"({start}+(({target})-{start})*{t}*{t}*(3-2*{t}))"


def zoom_expression(plan: dict[str, Any]) -> str:
    """zoompan z(on): each shot's zoom, moving linearly to ``zoom_end`` if it has one, and
    reached by a camera move from the previous framing when the shot glides in."""
    def value(shot: dict[str, Any], first: int, length: int, index: int) -> str | None:
        start = float(shot.get("zoom") or 1.0)
        end = float(shot.get("zoom_end") or start)
        move = glide(plan, index)
        if start == 1.0 and end == 1.0 and not move:
            return None
        target = f"{start:.4f}" if end == start else f"({start:.4f}+{(end - start) / max(1, length - 1):.6f}*(on-{first}))"
        return _eased(plan, index, first, f"{move[0][0]:.4f}", target) if move else target
    return _per_shot(plan, value, "1")


def focus_expressions(plan: dict[str, Any]) -> tuple[str, str]:
    """zoompan x and y: the crop window centred on each shot's ``focus`` (shares
    of the picture), kept inside it."""
    def axis(index: int, default: float) -> str:
        def value(shot: dict[str, Any], first: int, length: int, number: int) -> str | None:
            move = glide(plan, number)
            if not shot.get("focus") and not move:
                return None
            target = f"{(shot.get('focus') or (0.5, FACE_HEIGHT))[index]:.4f}"
            return _eased(plan, number, first, f"{move[0][1 + index]:.4f}", target) if move else target
        centre = _per_shot(plan, value, f"{default:.4f}")
        size = "iw" if index == 0 else "ih"
        return f"max(0,min({size}-{size}/zoom,{size}*({centre})-{size}/zoom/2))"
    return axis(0, 0.5), axis(1, FACE_HEIGHT)


def video_filter(plan: dict[str, Any], width: int = 1920, height: int = 1080) -> str:
    fps = int(plan.get("fps") or 30)
    selection = _sum([f"between(n,{a},{b - 1})" for a, b in kept_ranges(plan)])
    zoom = zoom_expression(plan)
    x, y = focus_expressions(plan)
    # Work at 4/3 of the output so a punch-in keeps its detail.
    work_w, work_h = width * 4 // 3 // 2 * 2, height * 4 // 3 // 2 * 2
    return (
        f"fps={fps},setpts=N/{fps}/TB,"
        f"scale={work_w}:{work_h}:force_original_aspect_ratio=increase,crop={work_w}:{work_h},"
        f"select='{selection}',setpts=N/{fps}/TB,"
        f"zoompan=z='{zoom}':d=1:s={width}x{height}:fps={fps}"
        f":x='{x}':y='{y}',"
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


def cutaway_filters(plan: dict[str, Any], first_input: int, width: int, height: int) -> tuple[list[str], list[str], str]:
    """Inputs and filter chains putting each cutaway (another video: a close-up
    of a thing the host talks about) over the edited picture during its time.
    The sound stays the host's: cutaways bring only their picture."""
    fps = int(plan.get("fps") or 30)
    inputs: list[str] = []
    chains: list[str] = []
    current = "v"
    for index, cut in enumerate(plan.get("cutaways") or []):
        # On whole frames: a start a fraction of a frame late showed one frame of the host first.
        first_frame = round(float(cut["at"]) * fps)
        at, duration = first_frame / fps, max(1, round((float(cut["at"]) + float(cut["duration"])) * fps) - first_frame) / fps
        from core.show_inserts import clip_speed
        speed = clip_speed(cut)                     # the clip's own speed: 0.5 is slow motion
        slow = f"setpts=(PTS-STARTPTS)/{speed:.6f}," if speed != 1.0 else ""
        inputs += ["-ss", f"{float(cut.get('source_start') or 0):.3f}", "-t", f"{duration * speed + 0.5:.3f}", "-i", str(cut["source"])]
        zoom = max(1.0, float(cut.get("zoom") or 1.0))
        fx, fy = cut.get("focus") or (0.5, 0.5)
        z = f"{zoom:.4f}"
        frame = (f"crop=w=iw/{z}:h=ih/{z}:x='max(0,min(iw-iw/{z},iw*{fx:.4f}-iw/{z}/2))'"
                 f":y='max(0,min(ih-ih/{z},ih*{fy:.4f}-ih/{z}/2))',") if zoom > 1.0 else ""
        label = f"cut{index}"
        if cut.get("fit") == "contain":
            from core.media_framing import filters
            chains.append(f"[{first_input + index}:v]{slow}fps={fps},trim=duration={duration:.4f},setpts=PTS-STARTPTS[{label}source]")
            chains.extend(filters(label+'source',label+'fitted',cut['source'],width,height))
            chains.append(f"[{label}fitted]setpts=PTS+{at:.6f}/TB[{label}]")
        else:
            chains.append(f"[{first_input + index}:v]{slow}fps={fps},trim=duration={duration:.4f},setpts=PTS-STARTPTS,{frame}"
                          f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,format=yuv420p,"
                          f"setpts=PTS+{at:.6f}/TB[{label}]")
        chains.append(f"[{current}][{label}]overlay=enable='between(t,{at - 0.5 / fps:.4f},{at + duration - 0.5 / fps:.4f})':eof_action=pass[{label}o]")
        current = f"{label}o"
    return inputs, chains, current


def render_show(source: str | Path, plan: dict[str, Any], output: str | Path, *, ffmpeg: str = "ffmpeg",
                width: int = 1920, height: int = 1080, progress: Callable[[float], None] | None = None,
                cancel: Callable[[], None] | None = None, preset: str = "veryfast", crf: int = 20,
                hardware: bool = False) -> Path:
    """Render the plan. ``cancel`` raises to stop: FFmpeg is stopped at once.
    ``hardware``: encode on the Mac's own H.264 hardware when it is there (final renders).
    The picture is rendered first and the edited sound joined to it after: fed
    together, FFmpeg can spin for minutes on a part of the edit (a scene preview)."""
    import threading
    source, output = Path(source), Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    from core.show_audio import process as process_audio
    noise=plan.get('noise_report') or {}
    clean=Path(noise['path']) if noise.get('path') else None
    audio_source=clean if (plan.get('audio_settings') or {}).get('denoise',True) and clean and clean.is_file() else source
    audio = process_audio(edit_audio(_decode_audio(ffmpeg, audio_source), plan),plan)
    if cancel:
        cancel()
    pending = output.with_name(f".{output.stem}.part{output.suffix}")
    picture = output.with_name(f".{output.stem}.picture{output.suffix}")
    frames = sum(b - a for a, b in kept_ranges(plan))
    extra_inputs, chains, last = cutaway_filters(plan, 1, width, height)
    # A part of the edit far into the recording (a scene preview) starts decoding a
    # second before its first frame instead of at the start of the recording.
    fps = int(plan.get("fps") or 30)
    skip = max(0, min((int(shot["start_frame"]) for shot in plan["shots"]), default=0) - fps)
    seek = ["-ss", f"{skip / fps:.6f}"] if skip else []
    local = {**plan, "shots": [{**shot, "start_frame": int(shot["start_frame"]) - skip, "end_frame": int(shot["end_frame"]) - skip}
                               for shot in plan["shots"]]}
    graph = ";".join([f"[0:v]{video_filter(local, width, height)}[v]", *chains])
    command = [
        ffmpeg, "-y", "-v", "error", "-nostdin", "-progress", "pipe:2",
        *seek, "-i", str(source),
        *extra_inputs,
        "-filter_complex", graph,
        "-map", f"[{last}]", "-frames:v", str(frames), "-an",
        *h264_args(ffmpeg, preset=preset, crf=crf, width=width, height=height, fps=fps, hardware=hardware), str(picture),
    ]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=False)
    errors: list[bytes] = []
    failures: list[BaseException] = []

    def read_progress() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            errors.append(line)
            if progress and line.startswith(b"frame=") and not failures:
                try:
                    progress(min(1.0, int(line.split(b"=")[1]) / max(1, frames)))
                except ValueError:
                    pass
                except BaseException as exc:  # a progress callback that cancels
                    failures.append(exc)

    reader = threading.Thread(target=read_progress, daemon=True)
    reader.start()
    try:
        while process.poll() is None:
            if failures:
                raise failures[0]
            if cancel:
                cancel()
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=5)
        if failures:
            raise failures[0]
        if process.returncode != 0:
            detail = b"".join(line for line in errors[-40:] if not line.startswith((b"frame=", b"fps=", b"out_", b"progress=", b"bitrate=", b"total_size=", b"speed=", b"dup_", b"drop_", b"stream_")))
            raise RuntimeError(f"Show render failed: {detail.decode(errors='replace').strip()[-2000:]}")
        if cancel:
            cancel()
        # The edited sound, joined to the finished picture without touching it.
        joined = subprocess.run([ffmpeg, "-y", "-v", "error", "-nostdin", "-i", str(picture),
                                 "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2", "-i", "pipe:0",
                                 "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                                 "-shortest", "-movflags", "+faststart", str(pending)],
                                input=audio.astype(np.float32).tobytes(), capture_output=True)
        if joined.returncode != 0:
            raise RuntimeError(f"Show render failed joining the sound: {joined.stderr.decode(errors='replace').strip()[-2000:]}")
        pending.replace(output)
        return output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        pending.unlink(missing_ok=True)
        picture.unlink(missing_ok=True)
