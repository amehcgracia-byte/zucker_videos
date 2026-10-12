"""Show mode job: transcribe a talking video, plan the edit, render it.

The edited video is published like any other export (export_manifest.json),
so the Overlay & Captions step opens on it, already carrying captions made
from the kept words, timed on the edited video.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from core.ffmpeg import ffprobe, tool_status
from core.show_edit import STYLES, plan_show
from core.show_render import render_show

# Whisper tends to tidy hesitations away; a prompt full of them keeps them,
# so they can be cut.
DISFLUENT_PROMPT = {
    "es": "Eh, mmm, o sea... este, bueno, eh. Hola.",
    "en": "Um, uh, like... so, um, you know. Hello.",
    # Language unknown: hesitations only, so the prompt does not pick it.
    "auto": "Um, eh... mmm, uh. Hmm.",
}
DEFAULT_MODEL = "small"
CAPTION_WORDS = 4
CAPTION_SECONDS = 1.6
# A caption never ends on one of these: it moves to the next caption.
LEADING_WORDS = frozenset({
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "que", "y", "o", "a", "al", "en", "con",
    "por", "para", "lo", "mi", "tu", "su", "se", "me", "te", "no", "es",
    "the", "a", "an", "of", "to", "and", "or", "in", "on", "with", "for", "that", "my", "your", "is",
})


def _duration(path: Path) -> float:
    return float((ffprobe(str(path)).get("format") or {}).get("duration") or 0.0)


def edited_words(plan: dict[str, Any]) -> list[tuple[str, float, float]]:
    """The kept words with their times on the edited video."""
    fps = int(plan.get("fps") or 30)
    words = plan["words"]
    timed: list[tuple[str, float, float]] = []
    offset = 0.0
    for shot in plan["shots"]:
        start, end = shot["start_frame"] / fps, shot["end_frame"] / fps
        for index in shot["words"]:
            word = words[index]
            a = offset + max(0.0, word["start"] - start)
            b = offset + min(end - start, word["end"] - start)
            timed.append((str(word["text"]).strip(), round(a, 3), round(max(b, a + 0.05), 3)))
        offset += end - start
    return timed


def edited_captions(plan: dict[str, Any], style: str) -> list[dict[str, Any]]:
    """Short caption cues from the kept words, on the edited timeline."""
    timed = edited_words(plan)
    cues: list[dict[str, Any]] = []
    group: list[tuple[str, float, float]] = []
    loud = style == "choe"

    def flush() -> None:
        if not group:
            return
        text = " ".join(word for word, _, _ in group)
        override = {"color": "#ffffff", "size": 76 if loud else 58, "outline_color": "#000000", "outline_width": 5 if loud else 3,
                    "vertical": 74, "animation_in": "none" if loud else "fade", "animation_out": "none" if loud else "fade"}
        if loud:
            override["fun_effect"] = "pop"
        cues.append({"lines": [text], "start": group[0][1], "end": group[-1][2], "style_override": override,
                     "words": [{"text": word, "start": a, "end": b} for word, a, b in group]})
        group.clear()

    for word, a, b in timed:
        if group and (len(group) >= CAPTION_WORDS or b - group[0][1] > CAPTION_SECONDS or a - group[-1][2] > 0.25):
            carried = []
            while len(group) > 1 and re.sub(r"\W", "", group[-1][0].lower()) in LEADING_WORDS and a - group[-1][2] <= 0.25:
                carried.insert(0, group.pop())
            flush()
            group.extend(carried)
        group.append((word, a, b))
        if re.search(r"[.!?…,;:]$", word):
            flush()
    flush()
    # Back to back, never overlapping.
    for current, following in zip(cues, cues[1:]):
        current["end"] = min(current["end"], following["start"])
    return cues


def add_gags(project, cut: Path, plan: dict[str, Any], destination: Path, *, director: str, language: str,
             ffmpeg: str, progress: Callable[[int, str], None], cancel: Callable[[], None]) -> dict[str, Any]:
    """Plan, paint and draw the gags on the edited video; a summary for the manifest."""
    from core import gags
    from core.gag_library import index_library
    from core.gag_render import render_gags
    progress(75, "Looking for your face in the picture")
    face = gags.face_side(cut, ffmpeg)
    try:
        library = index_library()
    except Exception:  # an unreadable library never stops the show
        library = []
    progress(76, "Asking your Claude for gags" if director == "claude" else "Choosing gags from the words")
    plan_gags, used, note = gags.plan_gags(edited_words(plan), float(plan["edited_duration"]), director=director,
                                           language=language, face=face, library=library)
    cancel()
    folder = project.folder / "gags"
    items, notes = gags.make_items(plan_gags, folder, face=face, library=library, cancel=cancel,
                                   progress=lambda share, text: progress(77 + int(share * 16), text))
    progress(94, f"Drawing {len(items)} gags on the video")
    render_gags(cut, items, destination, ffmpeg=ffmpeg, cancel=cancel,
                progress=lambda share: progress(94 + int(share * 5), "Drawing the gags on the video"))
    return {"director": used, "planned": len(plan_gags), "drawn": len(items), "face_side": face,
            "notes": [text for text in (note, *notes) if text]}


def run_show(project, source: str, *, style_name: str = "choe", language: str | None = None, gags: str = "claude",
             model_name: str = DEFAULT_MODEL, progress: Callable[[int, str], None], cancel: Callable[[], None]) -> tuple[Path, dict[str, Any]]:
    from core.backstage_transcription import transcribe_sources
    style = STYLES.get(style_name, STYLES["choe"])
    path = Path(source)
    if not path.is_file():
        raise RuntimeError(f"The talking video is not on disk: {path.name}")
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    started = time.monotonic()

    progress(3, "Listening to every word (local Whisper)")
    language = (language or "").strip().lower() or None
    transcript = transcribe_sources(
        [{"path": str(path), "filename": path.name, **({"language_hint": language} if language else {})}],
        project.artifacts_dir / "show_transcript.json",
        progress_callback=lambda value, detail: progress(3 + int(value * 0.5), detail or "Transcribing"),
        model_name=model_name,
        initial_prompt=DISFLUENT_PROMPT.get(language or "", DISFLUENT_PROMPT["auto"]),
        vad_filter=False,
    )
    sources = transcript.get("sources") or []
    if transcript.get("status") != "ready" or not sources or not sources[0].get("segments"):
        raise RuntimeError(transcript.get("reason") or "No speech was found in this video")
    cancel()

    progress(55, "Cutting pauses, hesitations and repeated takes")
    plan = plan_show(sources[0], _duration(path), style)
    plan.update(source=str(path), style=style_name, language=sources[0].get("language"))
    if not plan["shots"]:
        raise RuntimeError("Nothing was left after removing pauses: is there speech in this video?")
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    cancel()

    name = re.sub(r"[^\w.-]+", "-", str(project.data.get("name") or "Show")).strip("-") or "Show"
    destination = project.exports_dir / f"{name}-show-{time.strftime('%Y%m%d-%H%M%S')}.mp4"
    with_gags = gags in {"claude", "keywords"}
    cut = project.cache_dir / "show-cut.mp4" if with_gags else destination
    span = 14 if with_gags else 38
    progress(60, f"Rendering {plan['cuts']} cuts")
    render_show(path, plan, cut, ffmpeg=ffmpeg, progress=lambda value: (cancel(), progress(60 + int(value * span), "Rendering the show")))
    gag_summary: dict[str, Any] = {}
    if with_gags:
        gag_summary = add_gags(project, cut, plan, destination, director=gags, language=str(plan.get("language") or language or ""),
                               ffmpeg=ffmpeg, progress=progress, cancel=cancel)
        cut.unlink(missing_ok=True)

    cues = edited_captions(plan, style_name)
    (project.folder / "cue_track.json").write_text(json.dumps({
        "version": 1, "platform": "show", "lang": plan.get("language") or language or "", "cues": cues,
        "style": "autoread_fixed_white", "header": {}, "letterbox": {"enabled": False},
    }, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "platform": "show",
        "exports": [{"path": str(destination), "platform": "show", "duration_sec": plan["edited_duration"]}],
        "show": {"source_duration_sec": plan["source_duration"], "edited_duration_sec": plan["edited_duration"],
                 "cuts": plan["cuts"], "removed_words": plan["removed_words"], "captions": len(cues),
                 "elapsed_sec": round(time.monotonic() - started, 1), "gags": gag_summary},
        "warnings": list(gag_summary.get("notes") or []),
    }
    (project.artifacts_dir / "export_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    progress(100, "Show ready")
    return destination, manifest
