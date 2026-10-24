"""English captions for a talk in another language.

The phrases Whisper already heard are translated by the person's own Claude (Claude Code,
their subscription; nothing else is paid), in batches, keeping one English line per phrase;
the English words are then spread over each phrase's time. A second Whisper pass ("translate")
took as long as the first one on this Mac and translated worse: it is only the fallback when
Claude is not there.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

BATCH = 60

SCHEMA = {"type": "object", "properties": {"lines": {"type": "array", "items": {"type": "string"}}}, "required": ["lines"]}


def _ask(lines: list[str], language: str, timeout: float = 300.0) -> list[str] | None:
    from core.gag_director import claude_cli
    cli = claude_cli()
    if not cli:
        return None
    numbered = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    prompt = (f"Translate these {len(lines)} subtitle lines from {language} to natural spoken English. "
              f"Return exactly {len(lines)} lines, in the same order, one per input line, without numbers.\n\n{numbered}")
    command = [cli, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(SCHEMA), "--tools", "",
               "--no-session-persistence", "--system-prompt", "You translate subtitles faithfully and briefly."]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=str(Path.home()), check=False)
        payload = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    out = (payload.get("structured_output") or {}).get("lines") if isinstance(payload, dict) and not payload.get("is_error") else None
    if not isinstance(out, list) or len(out) != len(lines):
        return None
    return [str(line).strip() for line in out]


def translate_phrases(phrases: list[dict[str, Any]], language: str, *,
                      progress: Callable[[float], None] | None = None) -> list[dict[str, Any]] | None:
    """English words ({"text", "start", "end"}, on the phrases' clock) for phrases
    ({"text", "start", "end"}) in ``language``, or None when Claude cannot translate."""
    words: list[dict[str, Any]] = []
    for first in range(0, len(phrases), BATCH):
        batch = phrases[first:first + BATCH]
        english = _ask([str(p["text"]).strip() or "…" for p in batch], language)
        if english is None:
            return None
        for phrase, line in zip(batch, english):
            words += spread(line, float(phrase["start"]), float(phrase["end"]))
        if progress:
            progress(min(1.0, (first + BATCH) / max(1, len(phrases))))
    return words


def spread(line: str, start: float, end: float) -> list[dict[str, Any]]:
    """A line's words over its time, each as long as its letters."""
    tokens = line.split()
    total = sum(len(token) + 1 for token in tokens) or 1
    out, at = [], start
    for token in tokens:
        length = (end - start) * (len(token) + 1) / total
        out.append({"text": token, "start": round(at, 3), "end": round(at + length, 3)})
        at += length
    return out


def phrases_of(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    """The phrases (segments) of a transcription result, first source."""
    source = (transcript.get("sources") or [{}])[0]
    out = []
    for segment in source.get("segments") or []:
        start = float(segment.get("start_sec", segment.get("start", 0.0)))
        end = float(segment.get("end_sec", segment.get("end", start)))
        text = str(segment.get("text") or "").strip()
        if text and end > start:
            out.append({"text": text, "start": start, "end": end})
    return out
