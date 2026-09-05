from __future__ import annotations

from dataclasses import replace

from .model import CueTrack


def remap(track: CueTrack, *, scale: float = 1.0, offset: float = 0.0, cut_start: float = 0.0, cut_end: float | None = None) -> CueTrack:
    cues = []
    for cue in track.cues:
        start, end = cue.start * scale + offset, cue.end * scale + offset
        if cut_end is not None and (end <= cut_start or start >= cut_end):
            continue
        start = max(start, cut_start)
        end = min(end, cut_end) if cut_end is not None else end
        if end > start:
            cues.append(replace(cue, start=start, end=end))
    return CueTrack(tuple(cues), track.lang)
