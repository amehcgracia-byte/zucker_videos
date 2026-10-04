"""Forensic 360 projection audit against a real .zuckervid project."""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from core.media_validation import record_media_path
from core.normalization import EQUIRECT_FILTER
from core.project import load_project
from core.shot_review import _review_segments, _review_signature, _thumbnail_filter, review_items
from core.spherical_view import view_parameters
from core.stages.base import artifact_path
from core.stages.export import _export_source_filter


WANTED = {
    "singer": "cantante",
    "full_stage": "escenario",
    "audience": "publico",
}
PARAMETER_RE = re.compile(r"(yaw|pitch|h_fov|v_fov)=(-?\d+(?:\.\d+)?)")


def _values(filter_graph: str) -> dict[str, float]:
    return {key: float(value) for key, value in PARAMETER_RE.findall(filter_graph)}


def _record_for_source(project, source: str) -> dict:
    target = Path(source).expanduser().resolve()
    for record in project.data.get("inputs", {}).get("videos", []):
        candidates = {
            Path(str(record.get("path") or "")).expanduser().resolve(),
            Path(record_media_path(record)).expanduser().resolve(),
        }
        if target in candidates:
            return record
    return {}


def _thumbnail_file(project, signature: str, url: str) -> Path:
    return project.cache_dir / "shot_review" / signature / Path(url).name


def _mean_difference(first: Path, second: Path) -> float:
    left = Image.open(first).convert("RGB")
    right = Image.open(second).convert("RGB")
    if left.size != right.size:
        return 255.0
    return sum(ImageStat.Stat(ImageChops.difference(left, right)).mean) / 3.0


def run(project_path: str) -> int:
    project = load_project(project_path)
    plan = __import__("json").loads(artifact_path(project, "edit_plan.json").read_text(encoding="utf-8"))
    segments = _review_segments(project)
    signature = _review_signature(segments)
    print("360 proxy diagnostic:")
    print(f"  analysis filter: {EQUIRECT_FILTER}")
    print("  shot views: original source + authored pose (not the flat proxy)")

    items = review_items(project, render_missing=True)
    selected = {}
    for index, segment in enumerate(segments):
        shot = segment.get("spherical_shot") or {}
        shot_id = str(shot.get("shot_id") or shot.get("type") or "")
        if shot_id in WANTED and shot_id not in selected:
            selected[shot_id] = (index, segment, items[index])
    missing = sorted(set(WANTED) - set(selected))
    if missing:
        print(f"FAIL: project has no review shots for: {', '.join(missing)}")
        return 1

    rows = []
    paths = []
    for shot_id, (index, segment, item) in selected.items():
        url = str(item.get("thumbnail") or "")
        image_path = _thumbnail_file(project, signature, url)
        if not image_path.is_file():
            print(f"FAIL: thumbnail was not rendered for {shot_id}: {image_path}")
            return 1
        shot = segment.get("spherical_shot") or {}
        thumb_graph = _thumbnail_filter(segment)
        source = str(segment.get("source_path") or segment.get("clip_path") or "")
        record = _record_for_source(project, source)
        probe = record.get("probe") or {}
        export_graph = _export_source_filter(probe, shot, duration=float(segment.get("duration_sec") or 1.0))
        expected = view_parameters(float(shot.get("yaw") or 0.0), float(shot.get("pitch") or 0.0), float(shot.get("fov") or 95.0), 16 / 9, str(shot.get("type") or ""))
        thumb_values = _values(thumb_graph)
        export_values = _values(export_graph)
        rows.append((shot_id, float(shot.get("yaw") or 0.0) % 360.0, thumb_values, export_values, image_path, expected))
        paths.append(image_path)

    print("\\nPose agreement table:")
    print("shot          saved yaw  thumb yaw/pitch/h/v                 export yaw/pitch/h/v")
    for shot_id, yaw, thumb, export, _path, _expected in rows:
        print(f"{shot_id:12s} {yaw:9.3f}  {thumb}  {export}")
    for _shot_id, _yaw, thumb, export, _path, expected in rows:
        for key in ("yaw", "pitch", "h_fov", "v_fov"):
            if abs(float(thumb[key]) - float(expected[key])) > 0.01 or abs(float(export[key]) - float(expected[key])) > 0.01:
                print(f"FAIL: {key} disagrees with canonical view_parameters")
                return 1

    print("\\nThree real thumbnails:")
    for shot_id, _yaw, _thumb, _export, path, _expected in rows:
        print(f"  {shot_id:12s} {path}")
    differences = [_mean_difference(paths[index], paths[index + 1]) for index in range(len(paths) - 1)]
    print(f"  adjacent mean pixel differences: {[round(value, 2) for value in differences]}")
    if not differences or max(differences) < 5.0:
        print("FAIL: selected 360 thumbnails are not visibly distinct")
        return 1

    changed = copy.deepcopy(segments)
    changed_index = selected["singer"][0]
    changed[changed_index]["spherical_shot"]["yaw"] = float(changed[changed_index]["spherical_shot"].get("yaw") or 0.0) + 1.0
    print(f"\\nViewer change invalidates thumbnail cache: {_review_signature(segments) != _review_signature(changed)}")
    if _review_signature(segments) == _review_signature(changed):
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m tools.audit_360_projection PATH_TO_PROJECT.zuckervid")
    raise SystemExit(run(sys.argv[1]))
