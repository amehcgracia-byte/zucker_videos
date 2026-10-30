"""Drafts of the storyboard's drawings: the person sees the real drawing before approving it.

Accepting a scene first looks in the library: a picture you already have with that name
(core.show_library.best) is offered as the draft instead of drawing it again. Otherwise
(and always for "Draw it" / "Draw another") each drawing that has no picture yet is
made by the person's own Claude when Claude Code is here, else by the local painter. The
result is kept in the project's library (assets/show-library/drawn/) and linked to the
proposal as its picture, with its SHA-256 and ``draft: true``; the scene's preview then
shows that exact file. The person approves it, asks for another one, or removes it.
Previews and the final render never draw anything: they use linked pictures only.
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="zucker-drafts")
_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.Lock()


def _key(project, proposal_id: str) -> str:
    return f"{project.folder}|{proposal_id}"


def needs_drawing(item: dict[str, Any]) -> bool:
    """A drawing or character proposal with no picture linked yet."""
    params = item.get("params") or {}
    return (item.get("kind") == "drawing" and item.get("status") != "discarded" and not params.get("picture")
            and params.get("kind", "drawing") in ("drawing", "character") and bool(params.get("subject")))


def state(project, proposal_id: str) -> dict[str, Any] | None:
    """{"status": "drawing"} while it is being drawn, {"status": "failed", "error": …} if it could not be."""
    with _state_lock:
        found = _state.get(_key(project, proposal_id))
        return dict(found) if found else None


def snapshot(project, path):
    """Read picture links and draft status consistently while workers finish."""
    from core import show_proposals
    with _state_lock:
        document = show_proposals.load(path)
        for item in document["proposals"]:
            pending = _state.get(_key(project, item["id"]))
            if pending:
                item["drafting"] = dict(pending)
        return document


def forget(project, proposal_id: str) -> None:
    """A picture was chosen for it: a failed drawing no longer matters."""
    with _state_lock:
        found = _state.get(_key(project, proposal_id))
        if found and found.get("status") == "failed":
            _state.pop(_key(project, proposal_id), None)


def request(project, proposal_ids: list[str], *, again: bool = False, done: Callable[[str], None] | None = None,
            library: Callable[[str], dict[str, Any] | None] | None = None) -> list[str]:
    """Draw these proposals in the background (three at a time); ``again`` draws another one.
    ``library`` finds a picture you already have for a subject (then nothing is drawn)."""
    started = []
    for proposal_id in proposal_ids:
        key = _key(project, proposal_id)
        with _state_lock:
            if (_state.get(key) or {}).get("status") == "drawing":
                continue
            _state[key] = {"status": "drawing"}
        _pool.submit(_draw, project, proposal_id, again, done, library)
        started.append(proposal_id)
    return started


def _draw(project, proposal_id: str, again: bool, done: Callable[[str], None] | None,
          library: Callable[[str], dict[str, Any] | None] | None = None) -> None:
    from core import gag_paint, gags, show_proposals
    path = project.artifacts_dir / "show_proposals.json"
    key = _key(project, proposal_id)
    try:
        item = next(entry for entry in show_proposals.load(path)["proposals"] if entry["id"] == proposal_id)
        params = item["params"]
        before = params.get("picture")
        found = library(params["subject"]) if library and not again else None
        if found:
            _link(path, proposal_id, before, found["path"], found["sha256"], found["engine"])
            with _state_lock:
                _state.pop(key, None)
            if done:
                done(proposal_id)
            return
        style = params.get("style") if params.get("style") in gag_paint.STYLES else "crayon"
        # Another one is another seed; the same request keeps giving the same drawing.
        seed = gags.paint_seed({"proposal": proposal_id}, 0) + (int(time.time() * 1000) % 100003 if again else 0)
        if gags.drawing_engine() == "claude":
            picture = gags.claude_picture(params["subject"], style, seed=seed, prompt=params.get("prompt") or None)
            engine = "Claude · drawn stroke by stroke"
        else:
            picture = gags.painted_picture(params["subject"], style, seed=seed, prompt=params.get("prompt") or None)
            engine = "Local painter"
        kept = keep(project, picture, params["subject"], f"{engine} · draft for {proposal_id}")
        _link(path, proposal_id, before, kept["path"], kept["sha256"], engine)
        with _state_lock:
            _state.pop(key, None)
    except Exception as exc:  # shown on the drawing: Claude's sign-in or limit, an unusable painting…
        with _state_lock:
            _state[key] = {"status": "failed", "error": str(exc)[:300]}
    if done:
        done(proposal_id)


def _link(path: Path, proposal_id: str, before: str | None, picture: str, digest: str, engine: str) -> None:
    """The draft becomes the drawing's picture, unless the person chose one meanwhile."""
    from core import show_proposals
    with show_proposals.LOCK:
        document = show_proposals.load(path)
        for entry in document["proposals"]:
            if entry["id"] == proposal_id and entry["params"].get("picture") == before:
                entry["params"].update(picture=picture, picture_sha256=digest, draft=True, image_engine=engine)
        show_proposals.save(path, document["proposals"], **{k: v for k, v in document.items() if k not in ("proposals", "version")})


def keep(project, picture, subject: str, origin: str) -> dict[str, Any]:
    """The drawing as a PNG in the project's library (an identical one is reused), catalogued."""
    folder = project.folder / "assets" / "show-library" / "drawn"
    folder.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    stem = re.sub(r"[^\w -]+", "", subject).strip()[:50].replace(" ", "-") or "drawing"
    target = folder / f"{stem}-{digest[:8]}.png"
    if not target.is_file():
        pending = target.with_name(f".{target.stem}.part.png")
        pending.write_bytes(payload)
        pending.replace(target)
    # Hidden until approved: then it stays in the project's library (core.show_library.visible).
    from core import show_library
    show_library.catalog_set(project.folder, target, name=subject[:80], origin=origin, retired=True, sha256=digest,
                             source="drawn for the storyboard")
    return {"path": str(target.resolve()), "sha256": digest}
