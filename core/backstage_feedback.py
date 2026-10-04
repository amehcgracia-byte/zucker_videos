"""Persistent, content-addressed editorial feedback for Backstage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from core.storage import data_root
from typing import Any


def feedback_path() -> Path:
    return data_root() / "Feedback" / "backstage_feedback.json"


def content_fingerprint(path: str) -> str:
    """Hash clip bytes, so renames and project moves do not break feedback."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def feedback_key(path: str, start: float, end: float) -> str:
    return f"{content_fingerprint(path)}:{float(start):.3f}:{float(end):.3f}"


def load_feedback() -> dict[str, Any]:
    path = feedback_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"version": 1, "examples": {}}
    except (OSError, ValueError, TypeError):
        return {"version": 1, "examples": {}}


def save_feedback(payload: dict[str, Any]) -> None:
    path = feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="backstage-feedback-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def record_feedback(path: str, start: float, end: float, mark: str, text_original: str = "", english_text: str = "", reason: str = "") -> dict[str, Any]:
    if mark not in {"keep", "drop", "closing"}:
        raise ValueError("mark must be keep, drop, or closing")
    key = feedback_key(path, start, end)
    payload = load_feedback()
    examples = payload.setdefault("examples", {})
    examples[key] = {
        "content_fingerprint": key.split(":", 1)[0],
        "start_sec": round(float(start), 3),
        "end_sec": round(float(end), 3),
        "mark": mark,
        "text_original": str(text_original or ""),
        "english_text": str(english_text or ""),
        "reason": str(reason or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["version"] = 1
    save_feedback(payload)
    return examples[key]


def few_shot_examples(limit: int = 12) -> list[dict[str, Any]]:
    rows = list((load_feedback().get("examples") or {}).values())
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return [row for row in rows if row.get("mark") in {"keep", "closing", "drop"}][:limit]
