from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .model import Cue, CueTrack, Word


def _time(value: str) -> float:
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(value)


def _lines(text: str) -> tuple[str, ...]:
    return tuple(text.splitlines()) or ("",)


def from_lyrics(text: str, *, default_duration: float = 4.0, start: float = 0.0, lang: str = "und") -> CueTrack:
    cues: list[Cue] = []
    cursor = float(start)
    for block in re.split(r"(?:\r?\n){2,}", text):
        if block == "":
            continue
        lines = _lines(block)
        words = tuple(Word(token, cursor, cursor + default_duration) for token in re.findall(r"\S+", " ".join(lines)))
        cues.append(Cue(lines, cursor, cursor + default_duration, words))
        cursor += default_duration
    return CueTrack(tuple(cues), lang)


def from_srt(path: str | Path, *, lang: str = "und") -> CueTrack:
    cues: list[Cue] = []
    text = Path(path).read_text(encoding="utf-8-sig")
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        rows = block.splitlines()
        timing_index = next((i for i, row in enumerate(rows) if "-->" in row), None)
        if timing_index is None:
            continue
        left, right = [part.strip().split()[0] for part in rows[timing_index].split("-->", 1)]
        cues.append(Cue(tuple(rows[timing_index + 1:]), _time(left), _time(right)))
    return CueTrack(tuple(cues), lang)


def from_lrc(path: str | Path, *, lang: str = "und") -> CueTrack:
    cues: list[Cue] = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        tags = list(re.finditer(r"\[(\d+):(\d+(?:\.\d+)?)\]", line))
        lyric = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", line)
        for tag in tags:
            start = int(tag.group(1)) * 60 + float(tag.group(2))
            cues.append(Cue((lyric,), start, start + 4.0))
    return CueTrack(tuple(sorted(cues, key=lambda cue: cue.start)), lang)


def from_whisper(segments: Iterable[Any], *, lang: str = "und") -> CueTrack:
    cues: list[Cue] = []
    for segment in segments:
        get = segment.get if isinstance(segment, dict) else lambda key, default=None: getattr(segment, key, default)
        words: list[Word] = []
        for item in get("words", []) or []:
            wget = item.get if isinstance(item, dict) else lambda key, default=None: getattr(item, key, default)
            words.append(Word(str(wget("word", wget("text", ""))), float(wget("start", 0)), float(wget("end", 0))))
        cues.append(Cue(_lines(str(get("text", "")).strip()), float(get("start", 0)), float(get("end", 0)), tuple(words)))
    return CueTrack(tuple(cues), lang)


def to_srt(track: CueTrack) -> str:
    def stamp(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, rest = divmod(total_ms, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        secs, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    return "\n\n".join(f"{i}\n{stamp(cue.start)} --> {stamp(cue.end)}\n{cue.text}" for i, cue in enumerate(track.cues, 1)) + ("\n" if track.cues else "")
