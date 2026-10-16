"""Show mode job: transcribe a talking video, plan the edit, render it.

The edited video is published like any other export (export_manifest.json),
so the Overlay & Captions step opens on it, already carrying captions made
from the kept words, timed on the edited video.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
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


def pause_points(plan: dict[str, Any]) -> list[float]:
    """Moments of the edited video where the host had paused (a silence was cut)."""
    fps = int(plan.get("fps") or 30)
    points, offset = [], 0.0
    for shot in plan["shots"]:
        if offset > 0 and shot.get("cut_before") == "silence":
            points.append(round(offset, 3))
        offset += (shot["end_frame"] - shot["start_frame"]) / fps
    return points


def between_words(plan: dict[str, Any], entries: list[dict[str, Any]], reach: float = 1.0) -> list[dict[str, Any]]:
    """Interventions stop the host at a pause (the nearest within ``reach``
    seconds) or else at the end of the word being said: never mid-word."""
    pauses = pause_points(plan)
    words = edited_words(plan)
    out = []
    for entry in entries:
        if entry.get("insert"):
            at = float(entry["at"])
            near = [point for point in pauses if abs(point - at) <= reach]
            at = min(near, key=lambda point: abs(point - at)) if near else next((b for _, a, b in words if a < at < b), at)
            entry = {**entry, "at": round(at, 3)}
        out.append(entry)
    return out


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


def _transcribe_and_plan(project, path: Path, *, style_name: str, language: str | None, model_name: str,
                         progress: Callable[[int, str], None], cancel: Callable[[], None], span: tuple[int, int] = (3, 60)) -> dict[str, Any]:
    """Transcript -> edit plan -> each shot framed on the host's face."""
    from core.backstage_transcription import transcribe_sources
    from core.show_framing import apply_focus, face_track
    style = STYLES.get(style_name, STYLES["choe"])
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    first, last = span
    progress(first, "Listening to every word (local Whisper)")
    transcript = transcribe_sources(
        [{"path": str(path), "filename": path.name, **({"language_hint": language} if language else {})}],
        project.artifacts_dir / "show_transcript.json",
        progress_callback=lambda value, detail: progress(first + int(value * (last - first) * 0.6 / 100), detail or "Transcribing"),
        model_name=model_name,
        initial_prompt=DISFLUENT_PROMPT.get(language or "", DISFLUENT_PROMPT["auto"]),
        # Voice detection is required: without it Whisper picked the language on
        # the silent first seconds (Norwegian for an English talk) and skipped
        # whole stretches (34 words instead of 497 in five minutes). It keeps
        # every voiced sound, hesitations included, and only drops silences.
        vad_filter=True,
    )
    sources = transcript.get("sources") or []
    if transcript.get("status") != "ready" or not sources or not sources[0].get("segments"):
        raise RuntimeError(transcript.get("reason") or "No speech was found in this video")
    cancel()
    progress(first + int((last - first) * 0.62), "Cutting pauses, hesitations and repeated takes")
    plan = plan_show(sources[0], _duration(path), style)
    plan.update(source=str(path), style=style_name, language=sources[0].get("language"))
    if not plan["shots"]:
        raise RuntimeError("Nothing was left after removing pauses: is there speech in this video?")
    progress(first + int((last - first) * 0.65), "Finding your face to frame close shots")
    apply_focus(plan, face_track(path, ffmpeg, cancel=cancel))
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    cancel()
    return plan


PREVIEW_SIZE = (640, 360)          # previews of scenes and effects: small and quick to make
PREVIEW_ENCODING = {"preset": "ultrafast", "crf": 26}


def _destination(project) -> Path:
    name = re.sub(r"[^\w.-]+", "-", str(project.data.get("name") or "Show")).strip("-") or "Show"
    return project.exports_dir / f"{name}-show-{time.strftime('%Y%m%d-%H%M%S')}.mp4"


def _publish(project, plan: dict[str, Any], destination: Path, *, style_name: str, language: str, started: float,
             spans: list[tuple[float, float]] | None = None, summary: dict[str, Any] | None = None, warnings: list[str] | None = None,
             studio: list[dict[str, Any]] | None = None, effects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Captions on the final timeline, the cue track and the export manifest
    (with the result of every Show Studio effect)."""
    from core.show_inserts import shift
    spans = spans or []
    cues = edited_captions(plan, style_name)
    if spans:
        for cue in cues:
            cue["start"], cue["end"] = round(shift(cue["start"], spans), 3), round(shift(cue["end"] - 1e-3, spans) + 1e-3, 3)
            cue["words"] = [{**word, "start": round(shift(word["start"], spans), 3), "end": round(shift(word["end"] - 1e-3, spans) + 1e-3, 3)}
                            for word in cue["words"]]
    duration = float(plan["edited_duration"]) + sum(length for _, length in spans)
    (project.folder / "cue_track.json").write_text(json.dumps({
        "version": 1, "platform": "show", "lang": plan.get("language") or language or "", "cues": cues,
        "style": "autoread_fixed_white", "header": {}, "letterbox": {"enabled": False},
    }, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "platform": "show",
        "exports": [{"path": str(destination), "platform": "show", "duration_sec": round(duration, 3)}],
        "show": {"source_duration_sec": plan["source_duration"], "edited_duration_sec": round(duration, 3),
                 # Title card, intro and characters' turns pushed the edit later by these (at, seconds).
                 "inserted": [[round(at, 3), round(length, 3)] for at, length in spans],
                 "cuts": plan["cuts"], "removed_words": plan["removed_words"], "captions": len(cues),
                 "elapsed_sec": round(time.monotonic() - started, 1), **(summary or {})},
        "warnings": list(warnings or []),
    }
    if studio:
        manifest["studio_effects"] = studio
    if effects:
        manifest["show_effects"] = effects                                       # every storyboard proposal, by id
    (project.artifacts_dir / "export_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def run_show(project, source: str, *, style_name: str = "choe", language: str | None = None, gags: str = "claude",
             model_name: str = DEFAULT_MODEL, progress: Callable[[int, str], None], cancel: Callable[[], None]) -> tuple[Path, dict[str, Any]]:
    """One go, no review: cut, frame, gags from the director, render."""
    path = Path(source)
    if not path.is_file():
        raise RuntimeError(f"The talking video is not on disk: {path.name}")
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    started = time.monotonic()
    language = (language or "").strip().lower() or None
    plan = _transcribe_and_plan(project, path, style_name=style_name, language=language, model_name=model_name,
                                progress=progress, cancel=cancel)
    destination = _destination(project)
    with_gags = gags in {"claude", "keywords"}
    cut = project.cache_dir / "show-cut.mp4" if with_gags else destination
    span = 14 if with_gags else 38
    progress(60, f"Rendering {plan['cuts']} cuts")
    render_show(path, plan, cut, ffmpeg=ffmpeg, cancel=cancel, progress=lambda value: progress(60 + int(value * span), "Rendering the show"))
    gag_summary: dict[str, Any] = {}
    if with_gags:
        gag_summary = add_gags(project, cut, plan, destination, director=gags, language=str(plan.get("language") or language or ""),
                               ffmpeg=ffmpeg, progress=progress, cancel=cancel)
        cut.unlink(missing_ok=True)
    manifest = _publish(project, plan, destination, style_name=style_name, language=language or "", started=started,
                        summary={"gags": gag_summary}, warnings=list(gag_summary.get("notes") or []))
    progress(100, "Show ready")
    return destination, manifest


def prepare_show(project, source: str, resources: list[str], *, style_name: str = "choe", language: str | None = None,
                 gags: str = "claude", model_name: str = DEFAULT_MODEL, progress: Callable[[int, str], None],
                 cancel: Callable[[], None]) -> dict[str, Any]:
    """Everything up to the review: the cut, the framing and every proposed effect."""
    from core import gags as gag_module
    from core import show_proposals, show_resources
    from core.gag_library import index_library
    path = Path(source)
    if not path.is_file():
        raise RuntimeError(f"The talking video is not on disk: {path.name}")
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    language = (language or "").strip().lower() or None
    plan = _transcribe_and_plan(project, path, style_name=style_name, language=language, model_name=model_name,
                                progress=progress, cancel=cancel, span=(3, 55))
    analysed = []
    others = [Path(item) for item in resources if Path(item) != path and Path(item).is_file()]
    for index, item in enumerate(others):
        progress(56 + int(24 * index / max(1, len(others))), f"Looking at {item.name}: usable shots and what is said")
        analysed.append(show_resources.analyze(item, project.cache_dir, ffmpeg, cancel=cancel))
        cancel()
    progress(82, "Matching what you say with your close-ups")
    words = edited_words(plan)
    lines = gag_module.gag_director._timed_lines([{"text": w, "start": a, "end": b} for w, a, b in words])
    matches = show_resources.match([text for _, _, text in lines], analysed)
    cutaways = show_proposals.propose_cutaways(lines, analysed, matches, people=set(show_proposals.face_intervals(analysed)))
    characters = show_proposals.propose_characters(words, pause_points(plan), analysed, cutaways)
    taken = [(cut["at"], cut["at"] + cut["duration"]) for cut in cutaways]
    reactions = [item for item in show_proposals.propose_reactions(lines, pause_points(plan), analysed)
                 if not any(a - 1.0 < item["at"] < b + 1.0 for a, b in taken)]
    director_items: list[dict[str, Any]] = []
    note = ""
    if gags in {"claude", "keywords"}:
        progress(88, "Asking your Claude for gags" if gags == "claude" else "Choosing gags from the words")
        face = "right" if sum(shot["focus"][0] for shot in plan["shots"]) / max(1, len(plan["shots"])) >= 0.5 else "left"
        try:
            library = index_library()
        except Exception:  # an unreadable library never stops the show
            library = []
        planned, used, note = gag_module.plan_gags(words, float(plan["edited_duration"]), director=gags,
                                                   language=str(plan.get("language") or language or ""), face=face, library=library)
        director_items = show_proposals.from_gags(planned)
    proposals = sorted(cutaways + reactions + characters + director_items, key=lambda item: item["at"])
    show_proposals.save(project.artifacts_dir / "show_proposals.json", proposals, source=str(path),
                        resources=[{key: value for key, value in item.items() if key != "intervals"} | {"intervals": [
                            {k: v for k, v in interval.items() if k != "vector"} for interval in item["intervals"]]} for item in analysed],
                        note=note)
    from core import show_scenes
    document = show_scenes.build(plan, words, pause_points(plan), proposals, suggested_title=str(project.data.get("name") or ""))
    show_scenes.save(project.artifacts_dir / "show_scenes.json", document)
    progress(100, f"{len(document['scenes'])} scenes, {len(proposals)} effects proposed: review them")
    counts: dict[str, int] = {}
    for item in proposals:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"proposals": len(proposals), "by_kind": counts, "resources": len(analysed), "note": note, "scenes": len(document["scenes"]),
            "edited_duration_sec": plan["edited_duration"], "cuts": plan["cuts"]}


def render_reviewed_show(project, *, progress: Callable[[int, str], None], cancel: Callable[[], None]) -> tuple[Path, dict[str, Any]]:
    """Render the edit with the approved effects only."""
    from core import gags as gag_module
    from core import show_proposals, show_scenes
    from core.gag_render import render_gags
    started = time.monotonic()
    plan = json.loads((project.artifacts_dir / "show_plan.json").read_text(encoding="utf-8"))
    items = show_proposals.load(project.artifacts_dir / "show_proposals.json")["proposals"]
    cutaways, layer, director = show_proposals.render_inputs(items)
    plan["cutaways"] = cutaways
    layer = framed_layer(plan, layer)
    scenes_path = project.artifacts_dir / "show_scenes.json"
    document = show_scenes.load(scenes_path) if scenes_path.is_file() else None
    if document:
        layer += show_scenes.overlay_items(document)
    layer = between_words(plan, layer)
    jingles = [entry for entry in layer if entry.get("jingle")]
    layer = [entry for entry in layer if not entry.get("jingle")]
    path = Path(plan["source"])
    if not path.is_file():
        raise RuntimeError(f"The talking video is not on disk: {path.name}")
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    destination = _destination(project)
    cut = project.cache_dir / "show-cut.mp4"
    progress(2, f"Rendering {plan['cuts']} cuts and {len(cutaways)} close-ups")
    render_show(path, plan, cut, ffmpeg=ffmpeg, cancel=cancel, progress=lambda value: progress(2 + int(value * 48), "Rendering the show"))
    face = "right" if sum(shot.get("focus", [0.5])[0] for shot in plan["shots"]) / max(1, len(plan["shots"])) >= 0.5 else "left"
    notes: list[str] = []
    pictures: list[dict[str, Any]] = []
    outcomes: dict[str, tuple[str, str]] = {}
    if director:
        from core.show_framing import face_on_screen
        pictures, notes = gag_module.make_items(director, project.folder / "gags", face=face, cancel=cancel, allow_generation=False,
                                                face_at=lambda at: face_on_screen(plan, at), outcomes=outcomes,
                                                progress=lambda share, text: progress(50 + int(share * 30), text))
    show_effects = show_proposals.results(items, outcomes)
    notes += [f"Storyboard · {line['kind']} “{line['name']}” at {line['at']:.1f} s ({line['id']}): {line['detail']}"
              for line in show_effects if line["status"] == "failed"]
    from core.show_bridge import studio_layers, studio_warnings
    studio, studio_report = studio_layers(project.folder, plan, hidden=[(c["at"], c["at"] + c["duration"]) for c in cutaways])
    notes += studio_warnings(studio_report)
    spans: list[tuple[float, float]] = []
    body = project.cache_dir / "show-body.mp4"
    if pictures or layer or studio:
        progress(80, "Drawing the effects on the video")
        render_gags(cut, pictures + layer, body, ffmpeg=ffmpeg, cancel=cancel, timeline=spans, drawings=studio,
                    progress=lambda share: progress(80 + int(share * 15), "Drawing the effects on the video"))
        cut.unlink(missing_ok=True)
    else:
        cut.replace(body)
    if jingles:
        progress(95, "Laying the jingles")
        _lay_jingles(body, jingles, spans, ffmpeg)
    identity = (document or {}).get("identity") or {}
    intro = (document or {}).get("intro") or None
    if identity.get("title") or identity.get("logo") or intro:
        from core.show_identity import with_opening
        progress(96, "Adding your title card and intro")
        added = with_opening(body, destination, title=str(identity.get("title") or ""), logo=identity.get("logo"),
                             intro=(intro or {}).get("path") if isinstance(intro, dict) else intro,
                             seconds=float(identity.get("seconds") or 2.0), ffmpeg=ffmpeg, cancel=cancel)
        spans.insert(0, (0.0, added))
        body.unlink(missing_ok=True)
    else:
        body.replace(destination)
    manifest = _publish(project, plan, destination, style_name=str(plan.get("style") or "choe"), language=str(plan.get("language") or ""),
                        started=started, spans=spans, warnings=notes,
                        summary={"approved": len(show_proposals.approved(items)), "close_ups": len(cutaways),
                                 "characters": sum(1 for item in layer if item.get("insert")), "pictures": len(pictures)},
                        studio=studio_report, effects=show_effects)
    progress(100, "Show ready")
    return destination, manifest


def _lay_jingles(video: Path, jingles: list[dict[str, Any]], spans: list[tuple[float, float]], ffmpeg: str) -> None:
    """Mix the chosen jingles into ``video`` in place, moved past any interruption before them."""
    if not jingles:
        return
    from core.show_inserts import shift
    from core.show_music import mix
    placed = [{**item, "at": round(shift(float(item["at"]), spans), 3)} for item in jingles]
    mixed = video.with_name(f".{video.stem}.music{video.suffix}")
    mix(video, placed, mixed, ffmpeg=ffmpeg)
    mixed.replace(video)


def framed_layer(plan: dict[str, Any], layer: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Effects placed on the source picture, re-placed for each shot's framing."""
    from core.show_framing import steam_on_screen
    out: list[dict[str, Any]] = []
    for entry in layer:
        out.extend(steam_on_screen(plan, entry) if entry.get("steam") else [entry])
    return out


def scene_segments(plan: dict[str, Any], start: float, end: float) -> list[list[float]]:
    """The source stretches (seconds) a scene of the edit plays, in order: the page
    plays them from the original video, skipping what was cut, without rendering."""
    fps = int(plan.get("fps") or 30)
    return [[round(shot["start_frame"] / fps, 3), round(shot["end_frame"] / fps, 3)] for shot in window_plan(plan, start, end)["shots"]]


def _cached_cut(source: Path, part: dict[str, Any], folder: Path, name: str, *, ffmpeg: str, width: int, height: int,
                cancel: Callable[[], None] | None, progress: Callable[[float], None] | None) -> Path:
    """The window's cut (its framing, close-ups and voice, no effects), kept between edits:
    stickers, drawings and words do not change it, so after an edit a preview only
    composites the effects again instead of cutting the source video again."""
    import hashlib
    stat = source.stat() if source.exists() else None          # a changed source is another cut
    key = hashlib.sha1(json.dumps(["preview-cut-v1", str(source), stat and stat.st_size, stat and stat.st_mtime_ns, part, width, height, PREVIEW_ENCODING],
                                  sort_keys=True, default=str).encode()).hexdigest()[:12]
    cuts = folder / "cuts"
    cuts.mkdir(parents=True, exist_ok=True)
    cut = cuts / f"{name}-{key}.mp4"
    with _CUTTING_GUARD:
        lock = _CUTTING.setdefault(str(cut), threading.Lock())
    with lock:                                          # two previews of one window cut it once
        if cut.is_file():
            return cut
        return _cut_once(source, part, cut, cuts, name, ffmpeg=ffmpeg, width=width, height=height, cancel=cancel, progress=progress)


_CUTTING: dict[str, threading.Lock] = {}
_CUTTING_GUARD = threading.Lock()


def _cut_once(source: Path, part: dict[str, Any], cut: Path, cuts: Path, name: str, *, ffmpeg: str, width: int, height: int,
              cancel: Callable[[], None] | None, progress: Callable[[float], None] | None) -> Path:
    pending = cut.with_name(f"{cut.stem}-building.mp4")
    render_show(source, part, pending, ffmpeg=ffmpeg, width=width, height=height, cancel=cancel, **PREVIEW_ENCODING, progress=progress)
    pending.replace(cut)
    # A few cuts per window are kept (an edit of a close-up and its undo); older ones go.
    kept = sorted((path for path in cuts.glob(f"{name}-*.mp4") if not path.stem.endswith("-building")),
                  key=lambda path: path.stat().st_mtime, reverse=True)
    for old in kept[3:]:
        old.unlink(missing_ok=True)
    return cut


def window_plan(plan: dict[str, Any], start: float, end: float) -> dict[str, Any]:
    """The part of the edit between ``start`` and ``end`` (edited seconds) as a plan of its own."""
    fps = int(plan.get("fps") or 30)
    shots, offset = [], 0.0
    for shot in plan["shots"]:
        length = (shot["end_frame"] - shot["start_frame"]) / fps
        a, b = max(start, offset), min(end, offset + length)
        if b - a >= 1.0 / fps:
            first = shot["start_frame"] + int(round((a - offset) * fps))
            shots.append({**shot, "start_frame": first, "end_frame": first + max(1, int(round((b - a) * fps)))})
        offset += length
    cutaways = [{**cut, "at": round(cut["at"] - start, 3)} for cut in plan.get("cutaways") or [] if start - cut["duration"] < cut["at"] < end]
    return {**plan, "shots": shots, "cutaways": cutaways}


def preview_proposal(project, proposal_id: str, *, width: int | None = None, height: int | None = None,
                     cancel: Callable[[], None] | None = None,
                     progress: Callable[[float, str], None] | None = None) -> Path:
    """A short real render of one proposal in its context (1.5 s around it)."""
    width, height = width or PREVIEW_SIZE[0], height or PREVIEW_SIZE[1]
    import hashlib

    from core import gags as gag_module
    from core import show_proposals
    from core.gag_render import render_gags
    plan = json.loads((project.artifacts_dir / "show_plan.json").read_text(encoding="utf-8"))
    items = show_proposals.load(project.artifacts_dir / "show_proposals.json")["proposals"]
    item = next(entry for entry in items if entry["id"] == proposal_id)
    if missing := show_proposals.check(item):
        raise ValueError("Complete this effect first: " + ", ".join(missing))
    trial = {**item, "status": "approved"}
    cutaways, layer, director = show_proposals.render_inputs([trial])
    start = max(0.0, item["at"] - 1.5)
    end = min(float(plan["edited_duration"]), item["at"] + max(1.0, item["duration"]) + 1.5)
    plan["cutaways"] = cutaways
    part = window_plan(plan, start, end)
    from core.show_bridge import studio_fingerprint
    signature = hashlib.sha1(json.dumps(["placement-preview-v4-approved-assets", trial, start, end, width, height, studio_fingerprint(project.folder)], sort_keys=True,
                                        default=str).encode()).hexdigest()[:12]
    folder = project.cache_dir / "show_previews"
    folder.mkdir(parents=True, exist_ok=True)
    completed = folder / f"{proposal_id}-{signature}.mp4"
    if completed.is_file():
        return completed
    output = folder / f"{proposal_id}-{signature}-building.mp4"
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    cut = _cached_cut(Path(plan["source"]), part, folder, proposal_id, ffmpeg=ffmpeg, width=width, height=height, cancel=cancel,
                      progress=(lambda fraction: progress(fraction, f"Rendering the scene cut — {fraction:.0%}")) if progress else None)
    shifted = [{**entry, "at": round(entry["at"] - start, 3)} for entry in framed_layer(plan, layer)]
    jingles = [entry for entry in shifted if entry.get("jingle")]
    shifted = [entry for entry in shifted if not entry.get("jingle")]
    pictures: list[dict[str, Any]] = []
    if director:
        moved = [{**gag, "at": round(gag["at"] - start, 3)} for gag in director]
        face = "right" if sum(shot.get("focus", [0.5])[0] for shot in plan["shots"]) / max(1, len(plan["shots"])) >= 0.5 else "left"
        from core.show_framing import face_on_screen
        pictures, notes = gag_module.make_items(moved, folder / f"{proposal_id}-{signature}-gags", face=face, cancel=cancel, frame_size=(width, height), allow_generation=False,
                                            face_at=lambda at: face_on_screen(plan, at + start))
        if notes:
            raise RuntimeError("The effect preview is incomplete: " + "; ".join(notes))
    from core.show_bridge import studio_layers
    studio, _ = studio_layers(project.folder, part, hidden=[(c["at"], c["at"] + c["duration"]) for c in part["cutaways"]])
    spans: list[tuple[float, float]] = []
    if pictures or shifted or studio:
        render_gags(cut, pictures + shifted, output, ffmpeg=ffmpeg, cancel=cancel, timeline=spans, drawings=studio, **PREVIEW_ENCODING)
    else:
        shutil.copyfile(cut, output)
    _lay_jingles(output, jingles, spans, ffmpeg)
    output.replace(completed)
    return completed


def _scene_preview_inputs(project, scene_id: str, width: int, height: int, only_sticker_id: str | None = None):
    """What a scene's preview shows, and the file it is (or will be) rendered to;
    ``only_sticker_id``: that placed picture alone, with a little context around it.
    An effect that still needs the person's settings is left out of it, and said so."""
    import hashlib

    from core import gag_paint, show_proposals, show_scenes
    from core.show_bridge import studio_fingerprint
    plan = json.loads((project.artifacts_dir / "show_plan.json").read_text(encoding="utf-8"))
    items = show_proposals.load(project.artifacts_dir / "show_proposals.json")["proposals"]
    document = show_scenes.load(project.artifacts_dir / "show_scenes.json")
    scene = next(entry for entry in document["scenes"] if entry["id"] == scene_id)
    if only_sticker_id is not None:
        sticker = next(entry for entry in scene["overlays"] if entry.get("id") == only_sticker_id)
        scene = {**scene, "effects": [], "overlays": [sticker], "decision": "modified",
                 "start": max(float(scene["start"]), float(sticker["at"]) - 1.5),
                 "end": min(float(scene["end"]), float(sticker["at"]) + float(sticker["duration"]) + 1.5)}
    for item in items:
        item["needs"] = show_proposals.check(item)
    incomplete = [item for item in items if scene["decision"] != "plain" and item["id"] in scene["effects"]
                  and item["status"] != "discarded" and item["needs"]]
    # The rest of the scene is still worth seeing: what needs settings waits, and is listed.
    left_out = [f"{KIND_NAMES.get(item['kind'], item['kind'])} at {item['at']:.1f} s needs its {' and '.join(item['needs'])}" for item in incomplete]
    live = [] if scene["decision"] == "plain" else [
        {**item, "status": "approved"} for item in items
        if item["id"] in scene["effects"] and item["status"] != "discarded" and not item.get("needs")]
    overlays = show_scenes.overlay_items({"scenes": [{**scene, "decision": "accepted"}]}) if scene["decision"] != "plain" else []
    start, end = float(scene["start"]), float(scene["end"])
    local_art_recipe = [gag_paint.NEGATIVE, {key: vars(style) for key, style in gag_paint.STYLES.items()}] if any(
        item["kind"] == "drawing" and not item["params"].get("picture") for item in live) else None
    signature = hashlib.sha1(json.dumps(["scene-preview-v4-approved-assets", live, overlays, start, end, width, height, studio_fingerprint(project.folder),
                                         local_art_recipe], sort_keys=True, default=str).encode()).hexdigest()[:12]
    folder = project.cache_dir / "show_previews"
    return plan, live, overlays, start, end, folder, left_out, folder / f"{scene_id}-{signature}.mp4"


KIND_NAMES = {"character": "Talking character", "drawing": "Drawing", "word": "Word", "cutaway": "Close-up", "warp": "Warp",
              "steam": "Steam", "math": "Formulas", "jingle": "Jingle"}


def scene_preview_notes(preview: Path) -> list[str]:
    """What a finished scene preview left out (an effect still needing settings, a drawing
    the painter could not make), kept beside it."""
    try:
        return list(json.loads(preview.with_suffix(".json").read_text(encoding="utf-8")).get("left_out") or [])
    except (OSError, ValueError):
        return []


def scene_preview_path(project, scene_id: str, *, width: int | None = None, height: int | None = None) -> Path:
    """Where the preview of what the scene shows now is (or will be) rendered."""
    return _scene_preview_inputs(project, scene_id, width or PREVIEW_SIZE[0], height or PREVIEW_SIZE[1])[-1]


def scene_preview_ready(project, scene_id: str, *, width: int | None = None, height: int | None = None) -> Path | None:
    """The scene's preview if it is already made for what the scene shows now."""
    try:
        output = scene_preview_path(project, scene_id, width=width, height=height)
    except (OSError, ValueError, KeyError, StopIteration):
        return None
    return output if output.is_file() else None


def preview_scene(project, scene_id: str, *, width: int | None = None, height: int | None = None, only_sticker_id: str | None = None,
                  cancel: Callable[[], None] | None = None, cancel_cut: Callable[[], None] | None = None,
                  progress: Callable[[float, str], None] | None = None) -> Path:
    """A real render of one scene with the effects it proposes (or the person kept),
    small and quick to make; published only once complete. ``cancel_cut`` (default
    ``cancel``) alone stops cutting the source: an edit to a sticker, drawing or word
    does not change the cut, so it may finish and serve the next preview."""
    from core import gags as gag_module
    from core import show_proposals
    from core.gag_render import render_gags
    from core.show_framing import face_on_screen
    width, height = width or PREVIEW_SIZE[0], height or PREVIEW_SIZE[1]
    plan, live, overlays, start, end, folder, left_out, completed = _scene_preview_inputs(project, scene_id, width, height, only_sticker_id)
    folder.mkdir(parents=True, exist_ok=True)
    if completed.is_file():
        return completed
    signature = completed.stem.rsplit("-", 1)[-1]
    output = folder / f"{scene_id}-{signature}-building.mp4"
    cutaways, layer, director = show_proposals.render_inputs(live)
    plan["cutaways"] = cutaways
    part = window_plan(plan, start, end)
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    cut = _cached_cut(Path(plan["source"]), part, folder, scene_id if only_sticker_id is None else f"{scene_id}-{only_sticker_id[:8]}",
                      ffmpeg=ffmpeg, width=width, height=height, cancel=cancel_cut if cancel_cut is not None else cancel,
                      progress=(lambda fraction: progress(fraction, f"Rendering the scene cut — {fraction:.0%}")) if progress else None)
    shifted = [{**entry, "at": round(entry["at"] - start, 3)} for entry in between_words(plan, framed_layer(plan, layer) + overlays)]
    jingles = [entry for entry in shifted if entry.get("jingle")]
    shifted = [entry for entry in shifted if not entry.get("jingle")]
    pictures: list[dict[str, Any]] = []
    if director:
        moved = [{**gag, "at": round(gag["at"] - start, 3)} for gag in director]
        face = "right" if sum(shot.get("focus", [0.5])[0] for shot in plan["shots"]) / max(1, len(plan["shots"])) >= 0.5 else "left"
        pictures, notes = gag_module.make_items(moved, folder / f"{scene_id}-gags", face=face, progress=progress, frame_size=(width, height), allow_generation=False,
                                                cancel=cancel_cut if cancel_cut is not None else cancel,
                                                face_at=lambda at: face_on_screen(plan, at + start))
        left_out += notes                              # a drawing the painter could not make: the rest is shown
    if progress:
        progress(0, "Compositing this scene with its effects")
    from core.show_bridge import studio_layers
    studio, _ = studio_layers(project.folder, part, hidden=[(c["at"], c["at"] + c["duration"]) for c in part["cutaways"]])
    spans: list[tuple[float, float]] = []
    if pictures or shifted or studio:
        render_gags(cut, pictures + shifted, output, ffmpeg=ffmpeg, cancel=cancel, timeline=spans, drawings=studio, **PREVIEW_ENCODING)
    else:
        shutil.copyfile(cut, output)
    _lay_jingles(output, jingles, spans, ffmpeg)
    completed.with_suffix(".json").write_text(json.dumps({"left_out": left_out}, ensure_ascii=False), encoding="utf-8")
    output.replace(completed)
    return completed
