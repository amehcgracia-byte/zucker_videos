"""The Storytelling editor: the person's Claude looks at the clips and edits the story.

1. Catalogue: every clip is shown to Claude on sheets (one row per clip: its label and three
   moments of it) and described once: what kind of footage it is (an interview, an archive
   photo, a document, a place…) and what it shows. Kept per clip, so a clip is looked at once.
2. Edit: Claude reads the narration, moment by moment, with the catalogue, and chooses one to
   three clips for each moment the way a documentary editor would: what is being told, the
   interview when the interviewee speaks, people when they are named, documents for facts.
   Nothing comes from the clips' names or their order: only what they show and what is told.

It runs on Claude Code (the person's subscription); without it, Storytelling falls back on
CLIP picture matching (core.show_story.choose).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

PER_SHEET = 8
FRAME = (256, 144)
KINDS = ["interview", "person", "archive_photo", "document", "place", "object", "map", "title", "other"]
DESCRIBE_BATCH = 3          # sheets per question (24 clips)
PLAN_BATCH = 50             # moments per question


class EditorUnavailable(RuntimeError):
    pass


def _ask(prompt: str, schema: dict[str, Any], *, cwd: Path | None = None, read: bool = False, timeout: float = 600.0) -> dict[str, Any]:
    from core.gag_director import claude_cli
    cli = claude_cli()
    if not cli:
        raise EditorUnavailable("Claude Code is not installed")
    command = [cli, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema), "--tools", "Read" if read else "",
               "--no-session-persistence", "--system-prompt", "You are a careful documentary film editor. Answer only with the requested JSON."]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=str(cwd or Path.home()), check=False)
        payload = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise EditorUnavailable(f"Claude did not answer: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("is_error"):
        raise EditorUnavailable(str((payload or {}).get("result") or "Claude failed")[:300])
    answer = payload.get("structured_output")
    if not isinstance(answer, dict):
        raise EditorUnavailable("Claude's answer was not what was asked")
    return answer


# ---- 1. The catalogue -------------------------------------------------------------------------

def _key(path: str) -> str:
    stat = Path(path).stat()
    return f"{path}|{stat.st_size}|{stat.st_mtime_ns}"


def _frames(resource: dict[str, Any]) -> list[Any]:
    import cv2
    import numpy as np
    path = str(resource["path"])
    if resource.get("kind") == "photo":
        picture = cv2.imread(path)
        return [picture] * 3 if picture is not None else []
    capture = cv2.VideoCapture(path)
    out = []
    try:
        duration = float(resource.get("duration") or 0) or capture.get(cv2.CAP_PROP_FRAME_COUNT) / max(1.0, capture.get(cv2.CAP_PROP_FPS) or 25)
        for share in (0.15, 0.5, 0.85):
            capture.set(cv2.CAP_PROP_POS_MSEC, duration * share * 1000)
            ok, frame = capture.read()
            if ok:
                out.append(frame)
    finally:
        capture.release()
    return out or [np.zeros((FRAME[1], FRAME[0], 3), np.uint8)]


def sheets(resources: list[dict[str, Any]], indices: list[int], folder: Path) -> list[tuple[Path, list[int]]]:
    """Sheets of PER_SHEET clips (a row each: the label C<n> and three moments), JPEG."""
    import cv2
    import numpy as np
    out = []
    for start in range(0, len(indices), PER_SHEET):
        rows = []
        group = indices[start:start + PER_SHEET]
        for index in group:
            label = np.full((FRAME[1], 130, 3), 30, np.uint8)
            cv2.putText(label, f"C{index}", (10, FRAME[1] // 2 + 14), cv2.FONT_HERSHEY_SIMPLEX, 1.25, (255, 255, 255), 3, cv2.LINE_AA)
            frames = [cv2.resize(frame, FRAME, interpolation=cv2.INTER_AREA) for frame in _frames(resources[index])]
            frames += [frames[-1]] * (3 - len(frames))
            rows.append(np.hstack([label, *frames[:3]]))
        sheet = folder / f"sheet-{start // PER_SHEET + 1:03d}.jpg"
        cv2.imwrite(str(sheet), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 80])
        out.append((sheet, group))
    return out


def describe(resources: list[dict[str, Any]], cache: Path, *, progress: Callable[[float], None] | None = None) -> list[dict[str, str]]:
    """{"kind", "shows"} for every resource, looking only at those not described before."""
    try:
        known = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    keys = [_key(str(resource["path"])) for resource in resources]
    missing = [i for i, key in enumerate(keys) if key not in known]
    schema = {"type": "object", "properties": {"clips": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "integer"}, "kind": {"type": "string", "enum": KINDS}, "shows": {"type": "string"}}, "required": ["id", "kind", "shows"]}}},
        "required": ["clips"]}
    with tempfile.TemporaryDirectory(prefix="story-sheets-") as directory:
        made = sheets(resources, missing, Path(directory))
        for first in range(0, len(made), DESCRIBE_BATCH):
            batch = made[first:first + DESCRIBE_BATCH]
            names = ", ".join(sheet.name for sheet, _ in batch)
            ids = [i for _, group in batch for i in group]
            prompt = (f"These image files in the current folder show clips of a documentary, one clip per row: on the left its label "
                      f"(C<number>), then three moments of the clip (beginning, middle, end). Read every file: {names}. "
                      f"For each of the clips {', '.join(f'C{i}' for i in ids)} give its id (the number), its kind ({', '.join(KINDS)}; "
                      f"interview = someone speaking to the camera or to an interviewer) and what it shows in at most 15 words: "
                      f"who (describe people the same way each time they appear), where, what happens, any readable text.")
            answer = _ask(prompt, schema, cwd=Path(directory), read=True)
            for item in answer.get("clips") or []:
                if isinstance(item, dict) and item.get("id") in ids:
                    known[keys[int(item["id"])]] = {"kind": str(item.get("kind") or "other"), "shows": str(item.get("shows") or "")[:160]}
            cache.write_text(json.dumps(known, ensure_ascii=False), encoding="utf-8")
            if progress:
                progress(min(1.0, (first + DESCRIBE_BATCH) / max(1, len(made))))
    return [known.get(key) or {"kind": "other", "shows": ""} for key in keys]


# ---- 2. The edit -----------------------------------------------------------------------------

def plan(moments: list[dict[str, Any]], resources: list[dict[str, Any]], catalogue: list[dict[str, str]], language: str, *,
         progress: Callable[[float], None] | None = None) -> list[list[int]]:
    """One to three clip indices for every moment, chosen by Claude from what is told."""
    clips = "\n".join(f"C{i} ({float(resources[i].get('duration') or 0):.1f} s, {item['kind']}): {item['shows']}"
                      for i, item in enumerate(catalogue))
    schema = {"type": "object", "properties": {"moments": {"type": "array", "items": {"type": "object", "properties": {
        "m": {"type": "integer"}, "clips": {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 3}},
        "required": ["m", "clips"]}}}, "required": ["moments"]}
    chosen: dict[int, list[int]] = {}
    used: list[int] = []
    for first in range(0, len(moments), PLAN_BATCH):
        batch = list(range(first, min(len(moments), first + PLAN_BATCH)))
        told = "\n".join(f"M{i} [{moments[i]['start']:.1f}–{moments[i]['end']:.1f} s, {moments[i]['end'] - moments[i]['start']:.1f} s]: "
                         f"{moments[i].get('original') or moments[i]['text']}"
                         + (f"  ||  {moments[i]['text']}" if moments[i].get("original") and moments[i]["original"] != moments[i]["text"] else "")
                         for i in batch)
        before = "\n".join(f"M{i}: {moments[i].get('original') or moments[i]['text']}" for i in range(max(0, first - 3), first))
        prompt = (f"Edit a narrated documentary (spoken in {language or 'its language'}). Choose the pictures for moments "
                  f"M{batch[0]}–M{batch[-1]} of the narration from the clip catalogue below.\n\n"
                  f"How a good documentary editor chooses:\n"
                  f"- Show what is being told: the people, places, documents and events named.\n"
                  f"- When the interviewee speaks (first person about his own life, a voice other than the narrator), show the interview.\n"
                  f"- Photos of people when they are named or remembered; documents and newspapers for facts, dates and trials.\n"
                  f"- 1 to 3 clips per moment, in order; a moment longer than 6 s usually takes 2 or 3.\n"
                  f"- Do not repeat a clip unless nothing else fits or the interview resumes. Clips used so far: "
                  f"{', '.join(f'C{c}' for c in used[-120:]) or 'none'}.\n\n"
                  + (f"Just before (for context):\n{before}\n\n" if before else "")
                  + f"Moments:\n{told}\n\nClips:\n{clips}\n\nAnswer with every moment from M{batch[0]} to M{batch[-1]}, its clip numbers.")
        answer = _ask(prompt, schema)
        for item in answer.get("moments") or []:
            if not isinstance(item, dict) or item.get("m") not in batch:
                continue
            picks = [int(c) for c in item.get("clips") or [] if isinstance(c, int) and 0 <= c < len(resources)][:3]
            if picks:
                chosen[int(item["m"])] = picks
                used += picks
        if progress:
            progress(min(1.0, (first + PLAN_BATCH) / max(1, len(moments))))
    return [chosen.get(i) or [] for i in range(len(moments))]
