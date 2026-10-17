"""Show scenes: the one model shared by the storyboard and the effect editor.

A Show is: identity (a title card with an optional logo) -> an optional intro
-> scenes. A scene is a stretch of the edited talk (a few sentences) with:
  id, name, start, end   edited seconds
  text                   what is said in it
  proposal               one plain sentence: what is proposed and why
  effects                ids of its proposals (show_proposals.json)
  overlays               pictures the person placed: asset, x, y, width in
                         percent of the frame (as the storyboard prototype)
  decision               pending | accepted | modified | plain (no effect)
A scene does not have to be decorated: "plain" keeps the talk as it is.
Everything lives in the project (artifacts/show_scenes.json); media are
referenced by path, never copied into the JSON. ``decisions`` exports the
storyboard prototype's JSON shape.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCENES_VERSION = 1
DECISIONS = ("pending", "accepted", "modified", "plain")
TARGET_SEC, MIN_SEC, MAX_SEC = 25.0, 10.0, 45.0
KIND_WORDS = {"cutaway": "a close-up", "character": "a character answering with its own voice", "drawing": "a drawing",
              "word": "a shouted word", "warp": "the picture bending", "steam": "rainbow steam over the mug",
              "math": "formulas around you", "jingle": "a jingle",
              "clip": "a video clip with its own sound"}


def split(words: list[tuple[str, float, float]], pauses: list[float], duration: float) -> list[tuple[float, float]]:
    """Scene edges at sentence ends or real pauses, near TARGET_SEC apart."""
    candidates = sorted({round(end, 3) for text, _, end in words if re.search(r"[.!?…]$", text.strip())} | {round(p, 3) for p in pauses})
    edges, start = [], 0.0
    while duration - start > MAX_SEC:
        options = [c for c in candidates if start + MIN_SEC <= c <= start + MAX_SEC]
        cut = min(options, key=lambda c: abs(c - start - TARGET_SEC)) if options else start + TARGET_SEC
        edges.append((round(start, 3), round(cut, 3)))
        start = cut
    edges.append((round(start, 3), round(duration, 3)))
    return edges


def describe(effects: list[dict[str, Any]]) -> str:
    """One plain sentence explaining what the scene proposes."""
    live = [item for item in effects if item["status"] != "discarded"]
    if not live:
        return "Nothing proposed: the talk stays as it is."
    parts = []
    for item in live[:3]:
        reason = re.sub(r'^You say ', "when you say ", item["reason"])
        reason = re.sub(r'^Director: ', "of ", reason)          # "a drawing of a guitar"
        reason = re.sub(r'^Added by you$', "added by you", reason)
        parts.append(f"{KIND_WORDS.get(item['kind'], item['kind'])} {reason[0].lower() + reason[1:] if reason else ''}".strip())
    more = f" (and {len(live) - 3} more)" if len(live) > 3 else ""
    return "Proposed: " + "; ".join(parts) + more + "."


def build(plan: dict[str, Any], words: list[tuple[str, float, float]], pauses: list[float], proposals: list[dict[str, Any]],
          *, title: str = "", suggested_title: str = "") -> dict[str, Any]:
    """Scenes with their effects. No title card unless a title or logo is chosen
    (the project name is only suggested)."""
    scenes = []
    for index, (start, end) in enumerate(split(words, pauses, float(plan["edited_duration"]))):
        effects = [item for item in proposals if start <= item["at"] < end]
        text = " ".join(word for word, a, _ in words if start <= a < end)
        scenes.append({"id": f"scene-{index + 1:02d}", "name": f"Scene {index + 1}", "start": start, "end": end,
                       "text": text, "proposal": describe(effects), "effects": [item["id"] for item in effects],
                       "overlays": [], "decision": "pending"})
    return {"version": SCENES_VERSION, "identity": {"title": title, "suggested_title": suggested_title, "logo": None, "seconds": 2.0},
            "intro": None, "scenes": scenes}


def decide(document: dict[str, Any], proposals: list[dict[str, Any]], scene_id: str, decision: str) -> dict[str, Any]:
    """accepted: approve the scene's effects that are ready; plain: discard them
    all; modified: keep the person's own choices effect by effect."""
    if decision not in DECISIONS:
        raise ValueError(f"Unknown decision: {decision}")
    scene = next((item for item in document["scenes"] if item["id"] == scene_id), None)
    if scene is None:
        raise KeyError(scene_id)
    own = [item for item in proposals if item["id"] in scene["effects"]]
    if decision == "accepted":
        from core.show_drafts import needs_drawing
        for item in own:
            # A drawing with no picture yet is not approved blind: it is drawn as a draft first.
            if item["status"] != "discarded" and not item.get("needs") and not needs_drawing(item):
                item["status"] = "approved"
    elif decision == "plain":
        for item in own:
            item["status"] = "discarded"
        scene["overlays"] = []
    scene["decision"] = decision
    scene["proposal"] = describe(own) if decision != "plain" else "Left without effect."
    return scene


def attach(document: dict[str, Any], proposal: dict[str, Any]) -> None:
    """A new effect joins the scene it falls in."""
    for scene in document["scenes"]:
        if scene["start"] <= proposal["at"] < scene["end"]:
            if proposal["id"] not in scene["effects"]:
                scene["effects"].append(proposal["id"])
            if scene["decision"] == "pending":
                scene["decision"] = "modified"
            return


def decisions(document: dict[str, Any]) -> dict[str, Any]:
    """The storyboard prototype's decision JSON (no media inside)."""
    return {"prototype": False, "title": document["identity"].get("title", ""), "intro": bool(document.get("intro")),
            "scenes": [{"name": scene["name"], "kind": "video", "decision": {"modified": "accepted"}.get(scene["decision"], scene["decision"]),
                        "proposal": scene["proposal"], "start": scene["start"], "end": scene["end"],
                        "x": scene["overlays"][0]["x"] if scene["overlays"] else None,
                        "y": scene["overlays"][0]["y"] if scene["overlays"] else None,
                        "width": scene["overlays"][0]["width"] if scene["overlays"] else None,
                        "hasSource": True, "hasStill": False, "hasOverlay": bool(scene["overlays"])} for scene in document["scenes"]]}


def save(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


ACTIONS = {
    # action: (fun effect, path preset) - what "Animate element" offers.
    "none": ("none", None), "up": ("none", "up"), "down": ("none", "down"), "jump": ("bounce", None), "grow": ("pop", None),
    "grow_return": ("jelly", None), "pulse": ("pulse", None), "swing": ("wobble", None), "spin": ("spin", None),
    "talk": ("none", None), "look": ("none", None), "path": ("none", "path"), "draw": ("draw", None),
}


ENTRANCES = ("cut", "zoom", "bounce")
PUSH_ZOOM, PUSH_SEC = 1.25, 0.7          # a talking cut-out's push-in (the storyboard prototype's 0.7 s)


def _character_rig(asset: str) -> dict[str, Any]:
    """The rig saved with a character from a photo (mouth, pivot, voice), if the picture is one."""
    path = Path(asset).parent / "character.json"
    if Path(asset).name != "cutout.png" or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def overlay_items(document: dict[str, Any], frame_aspect: float = 16 / 9) -> list[dict[str, Any]]:
    """Pictures and characters the person placed in accepted or modified scenes,
    as gag-pass items. A character that talks with its own voice becomes an
    intervention: the host's picture freezes and the voice plays."""
    from PIL import Image
    items = []
    for scene in document["scenes"]:
        if scene["decision"] not in ("accepted", "modified"):
            continue
        for overlay in scene["overlays"]:
            # Placed like the storyboard: top-left corner and width in percent of the frame.
            width = float(overlay.get("width", 23)) / 100
            stretch = float(overlay.get("stretch") or 1.0)
            with Image.open(overlay["asset"]) as image:
                height = width * frame_aspect * image.height / image.width * stretch
            x = float(overlay.get("x", 12)) / 100 + width / 2
            y = float(overlay.get("y", 12)) / 100 + height / 2
            action = overlay.get("action") if overlay.get("action") in ACTIONS else ("none" if overlay.get("effect") is None else "custom")
            effect, preset = ACTIONS.get(action, (overlay.get("effect") or "pop", None))
            amount = float(overlay.get("intensity", 1.0))
            item: dict[str, Any] = {"image": overlay["asset"], "at": float(overlay.get("at", scene["start"])),
                                    "duration": float(overlay.get("duration", min(4.0, scene["end"] - scene["start"]))),
                                    "x": x, "y": y, "width": width, "effect": effect,
                                    **{key: overlay[key] for key in ("stretch", "rotate", "flip", "opacity") if key in overlay}}
            if preset in ("up", "down"):
                shift = 0.15 * amount * (-1 if preset == "up" else 1)
                item["path"] = [[0.0, x, y], [1.0, x, y + shift]]
            elif preset == "path" and overlay.get("path"):
                item["path"] = [[float(share), float(px) / 100, float(py) / 100] for share, px, py in overlay["path"]]
                item["hops"] = int(overlay.get("hops", 4))
            rig = _character_rig(overlay["asset"])
            if rig.get("pivot"):
                item["pivot"] = rig["pivot"]
            if action == "look" and rig.get("eyes"):
                item["eyes"] = rig["eyes"]
                item["eye_movement"] = min(0.5, max(0.0, 0.05 * amount))
            if action == "talk" and (rig.get("mouth") or overlay.get("voice") or rig.get("voice")):
                # Its jaw moves when a mouth was marked; its voice is heard either way.
                if rig.get("mouth"):
                    item["mouth"] = rig["mouth"]
                    item["mouth_open"] = 0.08 * amount
                voice = overlay.get("voice") or rig.get("voice")
                if voice:
                    # It enters at once, rising from below, or the camera pushes in on it.
                    entrance = overlay.get("entrance") if overlay.get("entrance") in ENTRANCES else "cut"
                    sticker = {key: value for key, value in item.items() if key not in ("at", "duration")}
                    if entrance == "bounce":
                        sticker["effect"] = "rise"
                    push = entrance == "zoom"
                    items.append({"insert": "character", "at": item["at"], "source": "host", "entrance": entrance,
                                  "zoom": PUSH_ZOOM if push else 1.0, "focus": [x, y] if push else [0.5, 0.5],
                                  "zoom_sec": PUSH_SEC if push else 0, "voice": voice, "overlays": [sticker]})
                    continue
            items.append(item)
    return items
