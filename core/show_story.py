"""Storytelling: a narration (recorded or brought in) told over pictures and clips.

The narration is the master: it is transcribed (and translated to English, which is also
what the picture matching understands) and split into moments of a few seconds, ending at the
end of a sentence when possible. Each moment gets the clip or photo that goes best with what
is said (CLIP text-picture similarity, core.show_resources), not one already used, in story
order when nothing stands out. Clips play muted under the voice, trimmed to their moment
(slowed a little or held on their last frame when short); photos move slowly. The result is
the program the Show then captions and decorates like any other video.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

BEAT_MIN = 3.5          # a picture stays at least this long...
BEAT_MAX = 9.0          # ...and changes after this, at the end of a sentence if possible
SLOWEST = 0.6           # a short clip may play this slowly to fill its moment


def beats(words: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Moments of the narration: {"start", "end", "text"} back to back from 0 to the end."""
    out: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    start = 0.0
    for word in words:
        current.append(word)
        length = float(word["end"]) - start
        sentence = str(word["text"]).rstrip().endswith((".", "!", "?", "…", ":", ";"))
        if (length >= BEAT_MIN and sentence) or length >= BEAT_MAX:
            out.append({"start": round(start, 3), "end": round(float(word["end"]), 3), "text": " ".join(str(w["text"]).strip() for w in current)})
            start, current = float(word["end"]), []
    if current or not out:
        out.append({"start": round(start, 3), "end": round(duration, 3), "text": " ".join(str(w["text"]).strip() for w in current)})
    out[-1]["end"] = round(max(out[-1]["end"], duration), 3)
    for a, b in zip(out, out[1:]):
        a["end"] = b["start"]
    return [beat for beat in out if beat["end"] - beat["start"] > 0.05]


def choose(moments: list[dict[str, Any]], resources: list[dict[str, Any]]) -> list[tuple[int, int] | None]:
    """(resource, interval) for each moment: the best match not used yet; in story order
    (the next unused picture) when the matching has nothing to say."""
    from core.show_resources import match
    import hashlib
    pool = [(r, i) for r, resource in enumerate(resources) for i, _ in enumerate(resource.get("intervals") or [])]
    # When nothing stands out: any picture not seen yet, never the order of the files' names
    # (cut from an edit, they would just repeat it).
    pool.sort(key=lambda item: hashlib.sha1(f"{resources[item[0]].get('path')}|{item[1]}".encode()).hexdigest())
    if not pool:
        return [None for _ in moments]
    ranked = match([moment["text"] or "…" for moment in moments], resources, top=12)
    used: set[tuple[int, int]] = set()
    shown: set[int] = set()                             # clips already seen (another part of one is still a repeat)
    following = 0
    out: list[tuple[int, int] | None] = []
    for options in ranked:
        previous = out[-1][0] if out and out[-1] else None
        fresh = [o for o in options if o["score"] > 0.01 and (o["resource"], o["interval"]) not in used and o["resource"] != previous]
        fresh.sort(key=lambda o: o["resource"] in shown)                       # stable: best unseen clip first
        pick = (fresh[0]["resource"], fresh[0]["interval"]) if fresh else None
        if pick is None:
            order = pool[following:] + pool[:following]
            free = ([item for item in order if item not in used and item[0] not in shown and item[0] != previous]
                    or [item for item in order if item not in used and item[0] != previous] or order)
            pick = free[0]
            following = (pool.index(pick) + 1) % len(pool)
        used.add(pick)
        shown.add(pick[0])
        if len(used) == len(pool):
            used.clear()                                # everything shown: pictures may come back
            shown.clear()
        out.append(pick)
    return out


def _piece(resource: dict[str, Any], interval: dict[str, Any], length: float, output: Path, ffmpeg: str,
           width: int, height: int, fps: int) -> Path:
    frames = max(1, round(length * fps))
    fit = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if resource.get("kind") == "photo":
        # A photo moves slowly: a gentle push in over its moment.
        move = (f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,crop={width * 2}:{height * 2},"
                f"zoompan=z='1+0.12*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={width}x{height}:fps={fps}")
        command = [ffmpeg, "-v", "error", "-y", "-nostdin", "-loop", "1", "-i", str(resource["path"]), "-frames:v", str(frames),
                   "-vf", f"{move},format=yuv420p"]
    else:
        start, end = float(interval["start"]), float(interval["end"])
        available = max(0.2, end - start)
        speed = max(SLOWEST, min(1.0, available / length))
        command = [ffmpeg, "-v", "error", "-y", "-nostdin", "-ss", f"{start:.3f}", "-i", str(resource["path"]), "-an",
                   "-frames:v", str(frames),
                   "-vf", f"setpts=PTS/{speed:.4f},{fit},fps={fps},tpad=stop_mode=clone:stop_duration={length + 1:.2f},format=yuv420p"]
    subprocess.run([*command, "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2", str(output)],
                   check=True, capture_output=True)
    return output


def _shares(lengths: list[float], total: float, fps: int) -> list[int]:
    """Frames for each clip of a moment: as much of each as there is, the moment filled."""
    frames = max(len(lengths), round(total * fps))
    want = [max(1.0, min(length, total)) for length in lengths]
    out = [max(1, round(frames * w / sum(want))) for w in want]
    out[-1] = max(1, frames - sum(out[:-1]))
    return out


def render(narration: Path, moments: list[dict[str, Any]], picks: list[Any], resources: list[dict[str, Any]],
           output: Path, ffmpeg: str, *, width: int = 1920, height: int = 1080, fps: int = 30,
           progress: Callable[[float], None] | None = None, cancel: Callable[[], None] | None = None) -> Path:
    """The program: each moment's pictures (one, or a few one after the other), back to back,
    under the narration. ``picks``: per moment a (resource, interval), a list of them, or None."""
    work = output.with_name(f".{output.stem}-pieces")
    work.mkdir(parents=True, exist_ok=True)
    pieces = []
    for index, (moment, pick) in enumerate(zip(moments, picks)):
        if cancel:
            cancel()
        total = round(moment["end"] * fps) - round(moment["start"] * fps)
        chosen = [pick] if isinstance(pick, tuple) else list(pick or [])
        if not chosen:
            piece = work / f"{index:05d}-0.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-f", "lavfi", "-i", f"color=black:s={width}x{height}:r={fps}",
                            "-frames:v", str(max(1, total)), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(piece)],
                           check=True, capture_output=True)
            pieces.append(piece)
        else:
            lengths = [max(0.5, float(resources[r]["intervals"][i]["end"]) - float(resources[r]["intervals"][i]["start"]))
                       if resources[r].get("kind") != "photo" else 4.0 for r, i in chosen]
            for part, ((r, i), frames) in enumerate(zip(chosen, _shares(lengths, total / fps, fps))):
                piece = work / f"{index:05d}-{part}.mp4"
                _piece(resources[r], resources[r]["intervals"][i], frames / fps, piece, ffmpeg, width, height, fps)
                pieces.append(piece)
        if progress:
            progress((index + 1) / len(moments))
    listing = work / "pieces.txt"
    listing.write_text("".join(f"file '{piece.name}'\n" for piece in pieces), encoding="utf-8")
    pending = output.with_name(f".{output.stem}.part{output.suffix}")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(narration),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", str(pending)], check=True, capture_output=True)
    pending.replace(output)
    for piece in pieces:
        piece.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)
    work.rmdir()
    return output


def setup(project) -> dict[str, Any]:
    return dict(project.data.get("settings", {}).get("wizard", {}).get("storytelling") or {})


def build(project, *, progress: Callable[[int, str], None], cancel: Callable[[], None], model_name: str = "small",
          width: int = 1920, height: int = 1080) -> Path:
    """The story's program (assets/story/program.mp4) from its narration and material."""
    from core.backstage_transcription import transcribe_sources
    from core.ffmpeg import ffprobe, tool_status
    from core.show_resources import analyze
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    chosen = setup(project)
    narration = Path(str(chosen.get("narration") or ""))
    if not narration.is_file():
        raise RuntimeError("Add the narration: record it or bring an audio of the story")
    material = [Path(p) for p in chosen.get("clips") or [] if Path(p).is_file()]
    if not material:
        raise RuntimeError("Add the pictures and clips the story is told over")
    if chosen.get('manual_plan'):
        from core.show_story_manual import build as build_reviewed
        return build_reviewed(project,chosen['manual_plan'],narration,material,progress,cancel)
    progress(2, "Listening to the narration (local Whisper)")
    # Heard the way the Show hears its talk, so the Show finds it already heard (Whisper's cache).
    from core.show import DISFLUENT_PROMPT
    transcript = transcribe_sources([{"path": str(narration), "filename": narration.name}], project.artifacts_dir / "show_transcript.json",
                                    model_name=model_name, vad_filter=True, initial_prompt=DISFLUENT_PROMPT["auto"])
    source = (transcript.get("sources") or [{}])[0]
    language = str(source.get("language") or "")
    words = [{"text": str(w.get("word") or w.get("text") or "").strip(), "start": float(w.get("start_sec", w.get("start", 0.0))),
              "end": float(w.get("end_sec", w.get("end", 0.0)))} for segment in source.get("segments") or [] for w in segment.get("words") or []]
    english = words
    if language and not language.startswith("en"):
        progress(10, f"Translating the narration ({language}) to English (your Claude)")
        from core.show_translate import phrases_of, translate_phrases
        english = translate_phrases(phrases_of(transcript), language) or []
    if language and not language.startswith("en") and not english:
        progress(10, f"Translating the narration ({language}) to English (local Whisper)")
        translated = transcribe_sources([{"path": str(narration), "filename": narration.name, "language_hint": language}],
                                        project.artifacts_dir / "story_transcript_en.json", model_name=model_name, task="translate", vad_filter=True)
        english = [{"text": str(w.get("word") or w.get("text") or "").strip(), "start": float(w.get("start_sec", w.get("start", 0.0))),
                    "end": float(w.get("end_sec", w.get("end", 0.0)))}
                   for segment in (translated.get("sources") or [{}])[0].get("segments") or [] for w in segment.get("words") or []] or words
    duration = float((ffprobe(str(narration)).get("format") or {}).get("duration") or 0.0)
    moments = beats(english, duration)
    for moment in moments:                                  # what was said, in its own language
        moment["original"] = " ".join(w["text"] for w in words if moment["start"] <= (w["start"] + w["end"]) / 2 < moment["end"])
    import hashlib
    # In an order unrelated to the files' names: material cut from an edit is numbered in that
    # edit's order, and the editor must decide from what the clips show, not from their numbers.
    material.sort(key=lambda path: hashlib.sha1(str(path).encode()).hexdigest())
    resources = []
    for index, path in enumerate(material):
        cancel()
        progress(18 + int(20 * index / len(material)), f"Looking at your pictures and clips: {path.name}")
        resources.append(analyze(path, project.cache_dir, ffmpeg, transcribe=False, cancel=cancel))
    picks: list[Any]
    catalogue: list[dict[str, str]] = []
    try:
        from core import story_editor
        progress(38, "Your Claude is looking at every clip (once per clip)")
        catalogue = story_editor.describe(resources, project.cache_dir / "story_catalogue.json",
                                          progress=lambda share: progress(38 + int(share * 2), f"Your Claude is looking at the clips — {share:.0%}"))
        progress(40, f"Your Claude is editing the story: pictures for {len(moments)} moments")
        chosen = story_editor.plan(moments, resources, catalogue, language,
                                   progress=lambda share: progress(40 + int(share * 2), f"Your Claude is editing the story — {share:.0%}"))
        longest = lambda r: max(range(len(resources[r]["intervals"])), key=lambda i: float(resources[r]["intervals"][i]["end"]) - float(resources[r]["intervals"][i]["start"]))
        picks = [[(r, longest(r)) for r in clips if resources[r].get("intervals")] for clips in chosen]
        if not any(picks):
            raise story_editor.EditorUnavailable("no pictures chosen")
        empty = [i for i, p in enumerate(picks) if not p]
        if empty:                                           # the few Claude left out: picture matching
            fallback = choose([moments[i] for i in empty], resources)
            for i, pick in zip(empty, fallback):
                picks[i] = [pick] if pick else []
    except Exception as exc:  # no Claude: picture matching (CLIP)
        progress(40, f"Choosing a picture for each of {len(moments)} moments (picture matching: {str(exc)[:80]})")
        picks = choose(moments, resources)
    folder = project.folder / "assets" / "story"
    folder.mkdir(parents=True, exist_ok=True)
    program = render(narration, moments, picks, resources, folder / "program.mp4", ffmpeg, width=width, height=height, cancel=cancel,
                     progress=lambda share: progress(42 + int(share * 10), f"Putting the pictures under the voice — {share:.0%}"))
    (project.artifacts_dir / "story_plan.json").write_text(json.dumps({
        "narration": str(narration), "language": language, "program": str(program),
        "catalogue": [{"path": resource["path"], **item} for resource, item in zip(resources, catalogue)],
        "moments": [{**moment, "pictures": [{"path": resources[r]["path"], "interval": i}
                                            for r, i in ([pick] if isinstance(pick, tuple) else list(pick or []))]}
                    for moment, pick in zip(moments, picks)]}, ensure_ascii=False, indent=1), encoding="utf-8")
    return program
