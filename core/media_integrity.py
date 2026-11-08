"""Find unreadable (zero-filled) stretches inside MP4/MOV camera files.

SD-card write failures leave runs of zeroed sample data: FFmpeg then reports
"Invalid NAL unit size (0 > N)" and refuses the shot once most frames fail.
In MP4/MOV every H.264/HEVC sample starts with a 4-byte NAL length, which is
never zero in a valid file, so reading those 4 bytes per packet finds the
damage without decoding. Only the requested window (plus one second of
reference frames before it) is read, never the whole file.
"""

from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

from core.ffmpeg import tool_status

CHECKED_SUFFIXES = {".mp4", ".mov", ".m4v"}
REFERENCE_PREROLL_SEC = 1.0
DAMAGE_MESSAGE = "The source video is damaged at this point (unreadable frames). Choose Other frame."


def looks_like_damage(ffmpeg_stderr: str) -> bool:
    text = ffmpeg_stderr or ""
    return "Invalid NAL unit size" in text or "Decode error rate" in text or "missing picture in access unit" in text


def damaged_spans(path: str | Path, start: float, end: float) -> list[tuple[float, float]]:
    """Damaged packet times within [start - preroll, end], merged into spans."""
    path = Path(path)
    if path.suffix.lower() not in CHECKED_SUFFIXES:
        return []
    try:
        stat = path.stat()
    except OSError:
        return []
    return list(_damaged_spans_cached(str(path), stat.st_size, stat.st_mtime_ns,
                                      round(max(0.0, start - REFERENCE_PREROLL_SEC), 2), round(end, 2)))


@lru_cache(maxsize=4096)
def _damaged_spans_cached(path: str, _size: int, _mtime_ns: int, start: float, end: float) -> tuple[tuple[float, float], ...]:
    ffprobe = str(tool_status().get("ffprobe_path") or "ffprobe")
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", f"{start}%{end}",
             "-show_entries", "packet=pts_time,pos,size", "-of", "json", path],
            capture_output=True, text=True, timeout=60, check=False)
        packets = json.loads(result.stdout or "{}").get("packets") or []
    except (OSError, subprocess.SubprocessError, ValueError):
        return ()
    damaged: list[float] = []
    try:
        with open(path, "rb") as handle:
            for packet in packets:
                try:
                    position, pts = int(packet["pos"]), float(packet["pts_time"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not start <= pts <= end:
                    continue
                handle.seek(position)
                if handle.read(4) == b"\x00\x00\x00\x00":
                    damaged.append(pts)
    except OSError:
        return ()
    spans: list[list[float]] = []
    for pts in sorted(damaged):
        if spans and pts - spans[-1][1] <= 0.25:
            spans[-1][1] = pts
        else:
            spans.append([pts, pts])
    return tuple((round(first, 3), round(last + 0.05, 3)) for first, last in spans)
