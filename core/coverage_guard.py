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

    def __init__(self, message: str, *, missing: list[dict[str, Any]] | None = None, sync_only: bool = False) -> None:
        super().__init__(message)
        self.missing = missing or []
        self.sync_only = sync_only


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


def _sync_related_reason(reason: str) -> bool:
    text = reason.lower()
    return any(token in text for token in ("sync", "confidence", "offset", "sync_map", "correlation"))


def _coverage_gaps(project: Project) -> tuple[list[dict[str, Any]], bool]:
    """Return missing videos and whether every omission is sync-related."""
    from core.fillers import filler_paths
    # Filler footage only appears where no camera covers the song; an edit
    # fully covered by cameras rightly uses none of it.
    fillers = filler_paths(project)
    videos = [record for record in project.data.get("inputs", {}).get("videos") or []
              if str(record.get("path") or "") not in fillers]
    if not videos:
        return [], False
    plan = _load_edit_plan(project)
    used = set()
    for segment in plan.get("segments") or []:
        for key in ("source_path", "clip_path", "path"):
            value = _normal(segment.get(key))
            if value:
                used.add(value)
    missing = [record for record in videos if not any(_matches(record.get("path"), path) for path in used)]
    if not missing:
        return [], False
    lines = ["Export blocked: every video in the Drop box must appear in at least one edit segment."]
    gaps: list[dict[str, Any]] = []
    for record in missing:
        filename = Path(str(record.get("path") or record.get("label") or "video")).name
        reason = _reason_for(project, record, plan)
        lines.append(f"- {filename}: {reason}")
        gaps.append({"record": record, "filename": filename, "reason": reason})
    return gaps, all(_sync_related_reason(gap["reason"]) for gap in gaps)


def coverage_gaps(project: Project) -> list[dict[str, Any]]:
    """Return missing Drop box videos and their concrete reasons."""
    return _coverage_gaps(project)[0]


def reel_capacity_warning(project: Project, gaps: list[dict[str, Any]] | None = None) -> str | None:
    """Return a warning when a Reel has fewer cut slots than input videos.

    A Reel with fewer slots than sources cannot satisfy literal one-segment
    coverage.  This is the only Reel exception: genuine omissions when there
    are enough slots remain a hard export error.
    """
    from core.fillers import filler_paths
    plan = _load_edit_plan(project)
    fillers = filler_paths(project)
    videos = [record for record in project.data.get("inputs", {}).get("videos") or []
              if str(record.get("path") or "") not in fillers]
    segments = plan.get("segments") or []
    missing = gaps if gaps is not None else _coverage_gaps(project)[0]
    if str(plan.get("platform") or "").lower() != "reel" or not missing or len(segments) >= len(videos):
        return None
    return (
        f"Reel coverage warning: {len(videos)} Drop box videos are available but "
        f"the {len(segments)} available cuts cannot show every source. "
        "The export will continue because full coverage is physically impossible."
    )


def assert_all_dropbox_videos_used(project: Project, *, allow_sync_missing: bool = False) -> None:
    """Fail unless every video is used, except explicit sync-only consent."""
    gaps, sync_only = _coverage_gaps(project)
    if not gaps or (allow_sync_missing and sync_only) or reel_capacity_warning(project, gaps):
        return
    lines = ["Export blocked: every video in the Drop box must appear in at least one edit segment."]
    for gap in gaps:
        lines.append(f"- {gap['filename']}: {gap['reason']}")
    raise CoverageInvariantError("\n".join(lines), missing=gaps, sync_only=sync_only)
