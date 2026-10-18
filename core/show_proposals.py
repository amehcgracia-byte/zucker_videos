"""Show proposals: every effect is proposed, previewed, adjusted, then approved
or discarded before anything is rendered.

Kinds (all times on the edited video):
  cutaway    a close-up of what the host mentions, shown over the host's sound
  character  a frozen close-up or photo that speaks with its own recorded
             voice while the host pauses (its region moves with its pixels)
  drawing, word, warp   gags planned by the director
  steam      rainbow steam over a mug, placed by the person
  jingle     a short sound the person chose, laid over the show's sound at its moment
Statuses: proposed -> approved | discarded. "needs" lists what is missing
before a proposal can be approved (for example a voice for a character).
Regions are kept in the coordinates of their own picture (shares of the
close-up or photo), never of the screen, so re-framing does not move them;
``check`` reports any region that the chosen zoom would leave off screen.
"""

from __future__ import annotations

import json
import threading
import re
import uuid
from pathlib import Path
from typing import Any

PROPOSALS_VERSION = 1
STATUSES = ("proposed", "approved", "discarded")
KINDS = ("cutaway", "character", "drawing", "word", "warp", "steam", "texture", "math", "jingle", "clip", "background")
# Measured on a real talk: matches above ~0.015 mostly showed what was said;
# below it they were close to random. Spaced out so not every line is decorated.
CUTAWAY_MIN_GAP = 12.0
CUTAWAY_MIN_SCORE = 0.015
TALK_WORDS = re.compile(r"\b(speak|speaks|talk|talks|say|says|tell|tells|habla|hablar|dice|decir|cuenta)\b", re.I)


# Every load-change-save of show_proposals.json (the person's edits, drawings finishing) holds it.
LOCK = threading.RLock()

def _id(kind: str) -> str:
    return f"{kind}-{uuid.uuid4().hex[:8]}"


def proposal(kind: str, at: float, duration: float, /, *, reason: str, confidence: float = 0.0, **params: Any) -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError(f"Unknown proposal kind: {kind}")
    return {"id": _id(kind), "kind": kind, "at": round(float(at), 3), "duration": round(float(duration), 3),
            "status": "proposed", "reason": reason, "confidence": round(float(confidence), 4), "params": params, "needs": []}


def propose_cutaways(lines: list[tuple[float, float, str]], resources: list[dict[str, Any]],
                     matches: list[list[dict[str, Any]]], *, people: set[tuple[int, int]] | None = None) -> list[dict[str, Any]]:
    """A close-up for lines whose best match stands out, spaced out, each interval used once.
    The other top matches stay as alternatives to choose from in the review. Shots of
    people (``people``: the host from another angle) are kept for reactions, not here."""
    out: list[dict[str, Any]] = []
    used: set[tuple[int, int]] = set(people or set())
    free_at = 0.0
    for (start, end, text), ranked in zip(lines, matches):
        options = [m for m in ranked if (m["resource"], m["interval"]) not in used]
        if not options or options[0]["score"] < CUTAWAY_MIN_SCORE or start < free_at or len(text.split()) < 3:
            continue
        best = options[0]
        resource = resources[best["resource"]]
        interval = resource["intervals"][best["interval"]]
        length = interval["end"] - interval["start"] if resource["kind"] == "video" else 2.5
        duration = max(1.2, min(end - start + 0.4, length, 3.0))
        source_start = interval["start"] + max(0.0, (length - duration) / 2) if resource["kind"] == "video" else 0.0
        out.append(proposal("cutaway", start, duration, reason=f'You say "{text}"', confidence=best["score"],
                            source=resource["path"], source_start=round(source_start, 2), zoom=1.0, focus=[0.5, 0.5],
                            thumb=interval.get("thumb"), alternatives=[{**m, "thumb": resources[m["resource"]]["intervals"][m["interval"]].get("thumb")} for m in options[1:]]))
        used.add((best["resource"], best["interval"]))
        free_at = start + duration + CUTAWAY_MIN_GAP
    return out


def propose_characters(words: list[tuple[str, float, float]], pauses: list[float], resources: list[dict[str, Any]],
                       cutaways: list[dict[str, Any]], *, wait: float = 6.0) -> list[dict[str, Any]]:
    """Where the host says something could talk, a frozen close-up answers in
    the next pause the host made (where a silence was cut), else right after.
    Its picture is the nearest proposed close-up; its voice must be chosen
    among the resources' recorded lines (none is assumed)."""
    voices = [{"source": resource["path"], "start": line["start"], "length": round(line["end"] - line["start"], 2), "text": line["text"]}
              for resource in resources for line in resource.get("speech") or []]
    out: list[dict[str, Any]] = []
    for index, (text, _, end) in enumerate(words):
        if not TALK_WORDS.search(text) or not cutaways or (out and end < out[-1]["at"] + wait):
            continue
        near = min(cutaways, key=lambda cut: abs(cut["at"] - end))
        if abs(near["at"] - end) > 20:
            continue
        at = next((pause for pause in pauses if end <= pause <= end + wait), end)
        context = " ".join(word for word, _, _ in words[max(0, index - 4):index + 3])
        item = proposal("character", at, 0.0, reason=f'You say "{context}"', confidence=near["confidence"],
                        source=near["params"]["source"], still_time=near["params"]["source_start"], zoom=1.25, focus=[0.5, 0.5],
                        zoom_sec=1.2, region=None, action="mouth", amount=0.02, offset=[0.0, 0.0], voice=None, voices=voices)
        item["needs"] = ["region", "voice"]
        out.append(item)
    return out


def from_gags(gags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Director gags as proposals (photos from the library count as drawings)."""
    out = []
    for gag in gags:
        kind = {"photo": "drawing", "character": "drawing"}.get(gag["kind"], gag["kind"])
        params = {key: value for key, value in gag.items() if key not in ("at", "duration", "from_idea")}
        if kind == "background":
            params.setdefault("picture", "")              # chosen from the library: a photo of that place
        what = gag.get("subject") or gag.get("text") or gag.get("warp") or gag.get("query") or ("formulas around you" if gag["kind"] == "math" else "")
        out.append(proposal(kind, gag["at"], gag["duration"], reason=f"{'Your idea' if gag.get('from_idea') else 'Director'}: {what}", **params))
    return out


def check(item: dict[str, Any]) -> list[str]:
    """What still stops a proposal from being approved."""
    params = item["params"]
    needs = []
    if item['kind'] == 'word' and params.get('lettering_text') and (not params.get('picture') or params.get('text') != params['lettering_text']):
        needs.append('regenerate lettering for the changed text')
    if item["kind"] == "clip" and not Path(str(params.get("clip") or "")).is_file():
        needs.append("the clip's video file")
    if item["kind"] == "background" and not Path(str(params.get("picture") or "")).is_file():
        needs.append("a picture of the new background")
    if item["kind"] == "character":
        if not params.get("region"):
            needs.append("region")
        if not params.get("voice") and str(params.get("source", "")).lower().rsplit(".", 1)[-1] not in ("jpg", "jpeg", "png", "webp", "heic"):
            needs.append("voice")
        region = params.get("region") or {}
        if region and not _region_visible(region, float(params.get("zoom") or 1.0), params.get("focus") or [0.5, 0.5]):
            needs.append("adjust region: the zoom leaves it off screen")
    return needs


def _region_visible(region: dict[str, Any], zoom: float, focus: list[float]) -> bool:
    from core.show_inserts import region_polygon
    points = region_polygon(region)
    window = 1.0 / zoom
    x0 = min(max(focus[0] - window / 2, 0.0), 1.0 - window)
    y0 = min(max(focus[1] - window / 2, 0.0), 1.0 - window)
    return all(x0 <= x <= x0 + window and y0 <= y <= y0 + window for x, y in points)


def update(items: list[dict[str, Any]], proposal_id: str, *, status: str | None = None, **changes: Any) -> dict[str, Any]:
    """Adjust a proposal's timing or parameters, approve or discard it."""
    item = next((entry for entry in items if entry["id"] == proposal_id), None)
    if item is None:
        raise KeyError(proposal_id)
    for key in ("at", "duration"):
        if key in changes:
            item[key] = round(max(0.0, float(changes.pop(key))), 3)
    item["params"].update(changes)
    item["needs"] = check(item)
    if status is not None:
        if status not in STATUSES:
            raise ValueError(f"Unknown status: {status}")
        if status == "approved" and item["needs"]:
            raise ValueError("Still needed before approving: " + ", ".join(item["needs"]))
        item["status"] = status
    return item


WHOLE = ("character", "clip")      # inserts that stop the host and play whole: copied, never split


def piece(item: dict[str, Any], at: float, duration: float, *, offset: float | None = None) -> dict[str, Any]:
    """Another piece of an effect on its own timeline row (``params.group``): pasted at the
    playhead, or the part after a split (``offset``: how far into the effect it starts, so
    a close-up or a jingle carries on where the first part stops instead of starting over)."""
    import copy
    if offset is not None and item["kind"] in WHOLE:
        raise ValueError("A talking character or a clip plays whole: copy it instead of splitting it")
    params = copy.deepcopy(item["params"])
    params["group"] = params.get("group") or item["id"]
    if offset:
        for key in {"cutaway": ("source_start",), "jingle": ("start",)}.get(item["kind"], ()):
            params[key] = round(float(params.get(key) or 0.0) + float(offset), 3)
    made = proposal(item["kind"], at, duration, reason=item.get("reason") or "Added by you", confidence=item.get("confidence") or 0.0, **params)
    made["needs"] = check(made)
    wanted = item["status"] if item["status"] != "discarded" else "approved"   # a cut piece pasted back is kept
    made["status"] = "proposed" if wanted == "approved" and made["needs"] else wanted
    return made


def save(path: Path, items: list[dict[str, Any]], **meta: Any) -> None:
    path.write_text(json.dumps({"version": PROPOSALS_VERSION, **meta, "proposals": items}, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def approved(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item["status"] == "approved"]


def render_inputs(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(cutaways for the Show render, gag-pass items, director gags still to be pictured)."""
    cutaways, items_out, gags = [], [], []
    for item in approved(items):
        params = item["params"]
        if item["kind"] == "cutaway":
            cutaways.append({"proposal": item["id"], "at": item["at"], "duration": item["duration"], "source": params["source"],
                             "source_start": params["source_start"], "zoom": params.get("zoom", 1.0), "focus": params.get("focus", [0.5, 0.5])})
            if params.get('pixel_parts'):
                items_out.append({'proposal':item['id'],'pixel_parts':True,'at':item['at'],'duration':item['duration'],
                                  **params['pixel_parts'],'intensity':params.get('intensity',1),'zoom':params.get('zoom',1),'focus':params.get('focus',[.5,.5])})
        elif item["kind"] == "character":
            voice = params.get("voice")
            items_out.append({"proposal": item["id"], "insert": "character", "at": item["at"], "source": params["source"], "still_time": params.get("still_time"),
                              "zoom": params.get("zoom", 1.25), "focus": params.get("focus", [0.5, 0.5]), "zoom_sec": params.get("zoom_sec", 1.2),
                              "voice": [voice["source"], voice["start"], voice["length"]] if voice else None,
                              "hold": params.get("hold", 2.0), "region": params.get("region"), "action": params.get("action", "mouth"),
                              **({"volume":params["volume"]} if "volume" in params else {}), "amount": params.get("amount", 0.02), "offset": params.get("offset", [0.0, 0.0]),
                              "entrance": params.get("entrance", "zoom")})
        elif item['kind']=='clip' and params.get('audio_mode')=='mix' and params.get('matte','none')=='none':
            cutaways.append({'proposal':item['id'],'at':item['at'],'duration':item['duration'],'source':params['clip'],
                             'source_start':params.get('clip_start',0),'zoom':1.,'focus':[.5,.5]})
            if params.get('clip_audio',True):
                items_out.append({'proposal':item['id'],'jingle':params['clip'],'at':item['at'],'duration':item['duration'],
                                  'start':params.get('clip_start',0),'volume':params.get('audio_volume',.7),'duck':1.,'fade':.02,'under_voice':params.get('under_voice',True)})
        elif item["kind"] == "clip" and params.get("matte", "none") != "none":
            items_out.append({"proposal": item["id"], "video_layer": params["clip"], "at": item["at"], "duration": item["duration"],
                              **{k:params[k] for k in ("matte","clip_start","picture","person_style","person_scale","person_offset") if k in params}})
            if params.get('clip_audio',True):
                items_out.append({'proposal':item['id'],'jingle':params['clip'],'at':item['at'],'duration':item['duration'],
                                  'start':params.get('clip_start',0),'volume':params.get('audio_volume',1.),'duck':1.,'fade':0.,**({'under_voice':True} if params.get('under_voice') else {})})
        elif item["kind"] == "clip" and params.get("clip_audio") is False:
            # Picture only: the clip replaces the image while the host's voice goes on, like a close-up.
            cutaways.append({"proposal": item["id"], "at": item["at"], "duration": item["duration"], "source": params["clip"],
                             "source_start": params.get("clip_start", 0.0), "zoom": 1.0, "focus": [0.5, 0.5]})
        elif item["kind"] == "clip":
            # A video clip played whole with its own sound while the host pauses (core.show_inserts.load_clip).
            items_out.append({"proposal": item["id"], "insert": "clip", "at": item["at"], "source": params["clip"],
                              **({"audio_volume":params["audio_volume"]} if "audio_volume" in params else {}), "clip_start": params.get("clip_start", 0.0), "clip_length": min(float(item["duration"]), max(0.,float(params["clip_length"])-float(params.get("clip_start") or 0)))})
        elif item["kind"] in ("steam", "texture"):
            items_out.append({"proposal": item["id"], "steam": params["point"], "anchor": params.get("anchor"), "at": item["at"], "duration": item["duration"],
                              "scale": params.get("scale", 1.0), "texture": params.get("texture", "rainbow"), **{key: params[key] for key in ("density", "strength", "rise", "colour") if key in params}})
        elif item["kind"] == "jingle":
            items_out.append({"proposal": item["id"], "jingle": params["path"], "at": item["at"], "duration": item["duration"], "start": params.get("start", 0.0),
                              "volume": params.get("volume", 0.8), "fade": params.get("fade", 0.05), "duck": params.get("duck", 0.5), **({"under_voice":True} if params.get("under_voice") else {})})
        elif item["kind"] == "warp":
            items_out.append({"proposal": item["id"], "warp": params.get("warp", "wave"), "at": item["at"], "duration": item["duration"], "strength": params.get("strength", 0.8)})
        elif item["kind"] == "background":
            # The person stays; the picture goes behind them (core.show_backgrounds).
            items_out.append({"proposal": item["id"], "background": params.get("picture"), "at": item["at"], "duration": item["duration"],
                              **{key: params[key] for key in ('person_style', 'person_scale', 'person_offset') if key in params}})
        else:
            # The director's own kind (drawing, character, photo, word) travels in params.
            gags.append({**params, "proposal": item["id"], "at": item["at"], "duration": item["duration"]})
    return cutaways, items_out, gags


def results(items: list[dict[str, Any]], outcomes: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    """One line per proposal of the storyboard: what the render did with it.
    ``outcomes`` holds what picturing the director's gags gave (applied or failed)."""
    out = []
    for item in items:
        params = item.get("params") or {}
        name = params.get("subject") or params.get("text") or params.get("query") or item["kind"]
        line = {"id": item["id"], "kind": item["kind"], "name": str(name), "at": item["at"], "duration": item["duration"]}
        if item["status"] == "discarded":
            out.append({**line, "status": "disabled", "detail": "Discarded in the storyboard"})
        elif item["status"] != "approved":
            out.append({**line, "status": "disabled", "detail": "Not approved in the storyboard"})
        elif item["id"] in outcomes:
            status, detail = outcomes[item["id"]]
            out.append({**line, "status": status, "detail": detail})
        else:
            detail = {"cutaway": f"close-up from {Path(str(params.get('source', ''))).name}", "character": "a character that speaks",
                      "steam": "steam over the mug", "jingle": f"jingle {Path(str(params.get('path', ''))).name}",
                      "warp": f"{params.get('warp', 'wave')} warp of the picture"}.get(item["kind"], item["kind"])
            out.append({**line, "status": "applied", "detail": detail})
    return out


REACTION_GAP = 30.0
REACTION_SEC = 1.2


def face_intervals(resources: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(resource, interval) of steady shots with a face in them: someone listening."""
    import cv2

    from core.show_framing import face_detectors
    detectors = [detector for detector, _ in face_detectors()]
    if not detectors:
        return []
    clahe = cv2.createCLAHE(2.0, (8, 8))
    found = []
    for r, resource in enumerate(resources):
        if resource.get("kind") != "video":
            continue
        for i, interval in enumerate(resource.get("intervals") or []):
            if interval.get("kind") != "still" or not interval.get("thumb") or interval["end"] - interval["start"] < REACTION_SEC + 0.4:
                continue
            if "faces" not in interval:
                picture = cv2.imread(str(interval["thumb"]), cv2.IMREAD_GRAYSCALE)
                interval["faces"] = 0 if picture is None else max(len(d.detectMultiScale(clahe.apply(picture), 1.1, 4, minSize=(20, 20))) for d in detectors)
            if interval["faces"]:
                found.append((r, i))
    return found


def propose_reactions(lines: list[tuple[float, float, str]], pauses: list[float], resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A short cut to someone listening, in the pause after a question or an exclamation."""
    faces = face_intervals(resources)
    if not faces:
        return []
    out: list[dict[str, Any]] = []
    for start, end, text in lines:
        if not re.search(r"[?!¿¡]\s*$", text.strip()) or (out and start < out[-1]["at"] + REACTION_GAP):
            continue
        at = next((pause for pause in pauses if end - 0.3 <= pause <= end + 3.0), None)
        if at is None:
            continue
        r, i = faces[len(out) % len(faces)]
        resource, interval = resources[r], resources[r]["intervals"][i]
        offset = (len(out) * 2.3) % max(0.1, interval["end"] - interval["start"] - REACTION_SEC)
        out.append(proposal("cutaway", at, REACTION_SEC, reason=f'A reaction after "{text.strip()}"', confidence=0.0,
                            source=resource["path"], source_start=round(interval["start"] + offset, 2), zoom=1.0, focus=[0.5, 0.5],
                            thumb=interval.get("thumb"), alternatives=[], reaction=True))
    return out
