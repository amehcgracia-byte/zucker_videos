"""Fast, cached shot-review assets and conservative slot replacement."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from core.project import Project
from core.ffmpeg import locate_executable
from core.spherical_view import view_parameters
from core.stages.base import artifact_path
from core.stages.cut import load_coverage


def _plan(project: Project) -> dict[str, Any]:
    path = artifact_path(project, "edit_plan.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _source_for(segment: dict[str, Any]) -> str:
    # A spherical review frame must come from the equirectangular source so it
    # can be reframed with this segment's landmark.  The ordinary proxy is a
    # single default view and therefore makes every landmark thumbnail look
    # identical.
    if segment.get("spherical_shot") and segment.get("source_path"):
        return str(segment["source_path"])
    return str(segment.get("proxy_path") or segment.get("clip_path") or segment.get("source_path") or "")


def _thumbnail_filter(segment: dict[str, Any]) -> str:
    shot = segment.get("spherical_shot") or {}
    if not shot:
        return "scale=360:-2:force_original_aspect_ratio=decrease"
    projection = str(segment.get("projection") or "equirect")
    if projection not in {"equirect", "raw_insv"}:
        return "scale=360:-2:force_original_aspect_ratio=decrease"
    view = view_parameters(
        float(shot.get("yaw") or 0.0),
        float(shot.get("pitch") or 0.0),
        float(shot.get("fov") or 95.0),
        16.0 / 9.0,
        str(shot.get("type") or ""),
    )
    if projection == "raw_insv":
        insv_fov = float(segment.get("insv_fov") or 190.0)
        prefix = f"v360=input=dfisheye:output=e:ih_fov={insv_fov:.3f}:iv_fov={insv_fov:.3f}:interp=lanczos,"
    else:
        prefix = ""
    return (
        f"{prefix}v360=input=equirect:output={view['projection']}:"
        f"yaw={float(view['yaw']):.3f}:pitch={float(view['pitch']):.3f}:"
        f"h_fov={float(view['h_fov']):.3f}:v_fov={float(view['v_fov']):.3f}:"
        "w=360:h=202:interp=lanczos,format=yuvj420p"
    )


def _candidate_key(candidate: dict[str, Any]) -> str:
    """Return the stable identity used by a slot's replacement history.

    A source can contribute more than one usable moment, so the path alone is
    not enough to identify a candidate.  Keep the format human-readable for
    backwards compatibility with the existing review_attempts data.
    """
    path = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path") or ""
    try:
        start = float(candidate.get("clip_start_sec", candidate.get("offset_sec", 0)) or 0.0)
    except (TypeError, ValueError):
        start = 0.0
    return f"{path}|{start:.6f}"


def _candidate_keys(candidate: dict[str, Any]) -> set[str]:
    """Return current and legacy spellings of a candidate identity."""
    path = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path") or ""
    raw_start = candidate.get("clip_start_sec", candidate.get("offset_sec", 0)) or 0
    return {_candidate_key(candidate), f"{path}|{raw_start}"}


def _candidate_covers_slot(candidate: dict[str, Any], segment: dict[str, Any], platform: str) -> bool:
    """Return whether a replacement is valid for this exact master-time slot."""
    if platform != "youtube":
        return True
    try:
        confidence = float(candidate.get("confidence") or 0.0)
        threshold = float(candidate.get("threshold") or 6.0)
    except (TypeError, ValueError):
        return False
    if confidence < threshold or (candidate.get("low_confidence") and not candidate.get("manual_override")):
        return False
    if candidate.get("unstable_sync") and not candidate.get("manual_override"):
        return False
    master_start = float(segment.get("master_start_sec") or 0.0)
    duration = max(0.1, float(segment.get("duration_sec") or 0.1))
    coverage_start = float(candidate.get("offset_sec") or 0.0)
    coverage_end = coverage_start + max(0.0, float(candidate.get("duration_sec") or 0.0))
    return coverage_start <= master_start + 0.001 and coverage_end >= master_start + duration - 0.001


def review_items(project: Project) -> list[dict[str, Any]]:
    """Return cached midpoint thumbnails for the current edit plan."""
    plan = _plan(project)
    segments = plan.get("segments") or []
    signature = hashlib.sha256(json.dumps(segments, sort_keys=True, default=str).encode()).hexdigest()[:16]
    root = project.cache_dir / "shot_review" / signature
    root.mkdir(parents=True, exist_ok=True)
    unavailable = {int(value) for value in project.data.get("settings", {}).get("wizard", {}).get("review_unavailable", [])}
    items: list[dict[str, Any]] = []
    ffmpeg = locate_executable("ffmpeg") or "ffmpeg"
    for index, segment in enumerate(segments):
        source = _source_for(segment)
        duration = max(0.1, float(segment.get("duration_sec") or 0.1))
        clip_start = float(segment.get("clip_start_sec") or 0.0)
        timestamp = clip_start + duration / 2.0
        source_stat = Path(source).stat() if Path(source).exists() else None
        shot = segment.get("spherical_shot") or {}
        pose = json.dumps({key: shot.get(key) for key in ("type", "yaw", "pitch", "fov")}, sort_keys=True)
        key = hashlib.sha256(f"{source}|{source_stat.st_mtime_ns if source_stat else 0}|{timestamp:.4f}|{pose}".encode()).hexdigest()[:20]
        output = root / f"shot-{index:04d}-{key}.jpg"
        if not output.exists() and source:
            command = [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{timestamp:.3f}",
                "-i", source, "-frames:v", "1", "-vf", _thumbnail_filter(segment),
                "-q:v", "5", "-y", str(output),
            ]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True)
            except (OSError, subprocess.CalledProcessError):
                output = Path("")
        shot = segment.get("spherical_shot") or {}
        # Render from the proxy where appropriate, but identify the card by
        # the registered source filename.  Otherwise Review shots displays a
        # cache hash (and hides which camera supplied the shot).
        display_source = str(segment.get("filename") or Path(source).name or "Unknown source")
        items.append({
            "index": index,
            "thumbnail": f"/api/v1/wizard/review/thumbnail/{signature}/{output.name}" if output.name else None,
            "source": display_source,
            "duration_sec": round(duration, 3),
            "master_start_sec": float(segment.get("master_start_sec") or 0.0),
            "landmark": shot.get("label") if shot else None,
            "keep": True,
            "no_alternative": index in unavailable,
        })
    return items


def replace_slots(project: Project, rejected: list[int]) -> dict[str, Any]:
    """Replace rejected slots with unused source/moment candidates."""
    plan = _plan(project)
    segments = plan.get("segments") or []
    coverage = load_coverage(project)
    pool = list(coverage.get("sources") or [])
    if not pool:
        pool = list(segments)
    wizard = project.data.setdefault("settings", {}).setdefault("wizard", {})
    platform = str(coverage.get("platform") or wizard.get("platform") or plan.get("platform") or "generic")
    attempts = wizard.setdefault("review_attempts", {})
    # Unlike the old single-attempt behaviour, this is a permanent per-slot
    # record of every candidate that has been displayed to the reviewer.
    exclusions = wizard.setdefault("review_exclusions", {})
    unavailable = set(int(value) for value in wizard.setdefault("review_unavailable", []))
    replaced: list[int] = []
    for raw_index in rejected:
        index = int(raw_index)
        if index < 0 or index >= len(segments):
            continue
        segment = segments[index]
        slot = str(index)
        tried = set(exclusions.setdefault(slot, []))
        # Migrate projects created before review_exclusions existed.
        tried.update(attempts.setdefault(slot, []))
        tried.update(_candidate_keys(segment))
        candidates = []
        for candidate in pool:
            path = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path")
            key = _candidate_key(candidate)
            if path and not (_candidate_keys(candidate) & tried) and _candidate_covers_slot(candidate, segment, platform):
                candidates.append((float(candidate.get("shot_quality_score") or candidate.get("motion_score") or 0.0), candidate, key))
        if not candidates:
            unavailable.add(index)
            continue
        _score, candidate, key = sorted(candidates, key=lambda item: -item[0])[0]
        new_segment = dict(segment)
        new_segment["clip_path"] = candidate.get("path") or candidate.get("clip_path") or candidate.get("source_path")
        new_segment["source_path"] = candidate.get("source_path") or new_segment["clip_path"]
        new_segment["filename"] = candidate.get("filename") or Path(str(new_segment["clip_path"])).name
        if platform == "youtube":
            # The candidate's offset is its position on the master timeline;
            # convert the reviewed slot back into that camera's own timeline.
            new_segment["clip_start_sec"] = max(
                0.0,
                float(segment.get("master_start_sec") or 0.0) - float(candidate.get("offset_sec") or 0.0),
            )
        else:
            new_segment["clip_start_sec"] = float(candidate.get("clip_start_sec") or 0.0)
        if candidate.get("projection"):
            new_segment["projection"] = candidate["projection"]
        if candidate.get("spherical_shot"):
            new_segment["spherical_shot"] = candidate["spherical_shot"]
        tried.add(key)
        exclusions[slot] = sorted(tried)
        # Keep the legacy field populated for readers of older project data.
        attempts[slot] = sorted(tried)
        segments[index] = new_segment
        unavailable.discard(index)
        replaced.append(index)
    plan["segments"] = segments
    artifact_path(project, "edit_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    project.data["settings"]["wizard"]["review_attempts"] = attempts
    project.data["settings"]["wizard"]["review_exclusions"] = exclusions
    project.data["settings"]["wizard"]["review_unavailable"] = sorted(unavailable)
    project.save()
    return {"replaced": replaced, "unavailable": sorted(unavailable), "items": review_items(project)}
