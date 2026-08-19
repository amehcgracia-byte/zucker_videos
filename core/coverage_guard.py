"""Pre-export invariants for Drop box video coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.project import Project
from core.stages.base import artifact_path
from core.stages.sync import load_sync_map


class CoverageInvariantError(RuntimeError):
    """Raised when a registered Drop box video would be silently omitted."""


def _normal(path: Any) -> str:
    if not path:
        return ""
    try:
        return str(Path(str(path)).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(path)


def _matches(path: Any, *candidates: Any) -> bool:
    value = _normal(path)
    return bool(value) and any(value == _normal(candidate) for candidate in candidates if candidate)


def _load_edit_plan(project: Project) -> dict[str, Any]:
    path = artifact_path(project, "edit_plan.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _reason_for(project: Project, record: dict[str, Any], plan: dict[str, Any]) -> str:
    source_path = record.get("path")
    filename = Path(str(source_path or record.get("label") or "video")).name
    diagnostics = plan.get("clip_diagnostics") or []
    diagnostic = next(
        (item for item in diagnostics if _matches(source_path, item.get("source_path"), item.get("path"))),
        None,
    )
    excluded = plan.get("excluded_clips") or []
    excluded_item = next(
        (item for item in excluded if _matches(source_path, (item.get("diagnostic") or {}).get("source_path"), (item.get("diagnostic") or {}).get("path"))),
        None,
    )
    if excluded_item:
        reason = str(excluded_item.get("reason") or "excluded during sync/cut")
        if diagnostic:
            confidence = float(diagnostic.get("confidence") or 0.0)
            threshold = float(diagnostic.get("threshold") or 0.0)
            details = [f"confidence {confidence:.3f} (threshold {threshold:.3f})"]
            if diagnostic.get("unstable_sync"):
                verification = diagnostic.get("verification") or {}
                delta = verification.get("delta_sec")
                if isinstance(delta, (int, float)):
                    details.append(f"sync drift {float(delta):.3f}s between checks")
                else:
                    details.append("unstable sync")
            reason = f"{reason}; " + ", ".join(details)
        return reason
    if diagnostic:
        if diagnostic.get("low_confidence"):
            return f"low-confidence sync: {float(diagnostic.get('confidence') or 0.0):.3f} below threshold {float(diagnostic.get('threshold') or 0.0):.3f}"
        if diagnostic.get("unstable_sync"):
            return "unstable sync"
        if diagnostic.get("error"):
            return str(diagnostic["error"])
        return "it was detected but the edit plan assigned no segment to it"
    sync_map = load_sync_map(project, missing_ok=True) or {}
    if not sync_map.get("clips"):
        return "it was registered but no sync result exists for it"
    return "it was registered but is absent from sync_map.json/edit_plan.json"


def assert_all_dropbox_videos_used(project: Project) -> None:
    """Fail before ExportStage if any registered video has zero edit segments."""
    videos = project.data.get("inputs", {}).get("videos") or []
    if not videos:
        return
    plan = _load_edit_plan(project)
    used = set()
    for segment in plan.get("segments") or []:
        for key in ("source_path", "clip_path", "path"):
            value = _normal(segment.get(key))
            if value:
                used.add(value)
    missing = [record for record in videos if not any(_matches(record.get("path"), path) for path in used)]
    if not missing:
        return
    lines = ["Export blocked: every video in the Drop box must appear in at least one edit segment."]
    for record in missing:
        filename = Path(str(record.get("path") or record.get("label") or "video")).name
        lines.append(f"- {filename}: {_reason_for(project, record, plan)}")
    raise CoverageInvariantError("\n".join(lines))
