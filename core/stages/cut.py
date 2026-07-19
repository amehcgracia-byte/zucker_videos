"""Simple wizard cut planning stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.project import Project
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.sync import load_song_boundaries, load_sync_map


class CutStage(Stage):
    """Plan a minimal single-clip edit for the wizard.

    This is intentionally placeholder creative logic: pick the first clip that
    overlaps the requested song window, or the longest clip when there is no
    songs.json.
    """

    name = "cut"
    dependencies = ["sync"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint sync output and cut settings."""
        return stable_fingerprint(
            {
                "sync": project.data["stages"]["sync"].get("fingerprint"),
                "songs": project.data["inputs"].get("songs"),
                "settings": project.data["settings"].get(self.name, {}),
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return the coverage artifact path."""
        return {"coverage": str(artifact_path(project, "coverage.json"))}

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write a simple coverage plan consumed by export."""
        progress_callback(20, "Leyendo sincronización")
        sync_map = load_sync_map(project) or {}
        clips = list((sync_map.get("clips") or {}).values())
        if not clips:
            raise ValueError("No hay vídeos sincronizados para cortar")
        wizard = project.data["settings"].get("wizard", {})
        platform = str(wizard.get("platform") or "youtube")
        songs = load_song_boundaries(project)
        song_choice = wizard.get("song_choice")
        window = _selected_window(songs, song_choice, sync_map)
        clip = _first_covering_clip(clips, window) or _longest_clip(clips)
        segment = _segment_for_platform(clip, window, platform)

        progress_callback(70, "Creando plan simple")
        path = artifact_path(project, "coverage.json")
        write_artifact_json(
            path,
            {
                "stage": self.name,
                "placeholder_logic": "first covering clip; no multicam or highlight scoring yet",
                "platform": platform,
                "song_choice": song_choice,
                "songs": songs,
                "segments": [segment],
            },
        )
        progress_callback(100, "Cut stub complete")
        return self.outputs(project)


def load_coverage(project: Project) -> dict[str, Any]:
    """Load coverage.json."""
    path = artifact_path(project, "coverage.json")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _selected_window(songs: list[dict[str, Any]], song_choice: Any, sync_map: dict[str, Any]) -> dict[str, Any]:
    if songs and song_choice != "all":
        try:
            song = songs[int(song_choice or 0)]
            start = float(song.get("start_sec") or 0)
            end = song.get("end_sec")
            duration = max(1.0, float(end) - start) if end is not None else 60.0
            return {"title": song.get("title") or "Canción", "start_sec": start, "duration_sec": duration}
        except (IndexError, TypeError, ValueError):
            pass
    duration = float(sync_map.get("master_duration_sec") or 0) or 60.0
    return {"title": "Vídeo completo", "start_sec": 0.0, "duration_sec": duration}


def _first_covering_clip(clips: list[dict[str, Any]], window: dict[str, Any]) -> dict[str, Any] | None:
    start = float(window["start_sec"])
    end = start + float(window["duration_sec"])
    for clip in clips:
        clip_start = float(clip.get("offset_sec") or 0)
        clip_end = clip_start + float(clip.get("duration_sec") or 0)
        if clip_start <= start and clip_end >= min(end, start + 1):
            return clip
    return None


def _longest_clip(clips: list[dict[str, Any]]) -> dict[str, Any]:
    return max(clips, key=lambda clip: float(clip.get("duration_sec") or 0))


def _segment_for_platform(clip: dict[str, Any], window: dict[str, Any], platform: str) -> dict[str, Any]:
    clip_offset = float(clip.get("offset_sec") or 0)
    clip_duration = float(clip.get("duration_sec") or 1)
    window_start = float(window["start_sec"])
    window_duration = float(window["duration_sec"])
    duration = min(window_duration, clip_duration)
    if platform == "instagram":
        duration = min(45.0, duration)
    elif platform == "tiktok":
        duration = min(20.0, duration)
    source_start = max(0.0, window_start - clip_offset)
    if platform in {"instagram", "tiktok"} and clip_duration > duration:
        source_start = max(0.0, (clip_duration - duration) / 2)
    source_start = min(source_start, max(0.0, clip_duration - duration))
    master_start = max(0.0, clip_offset + source_start)
    return {
        "title": window["title"],
        "clip_path": clip["path"],
        "clip_start_sec": source_start,
        "master_start_sec": master_start,
        "duration_sec": max(1.0, duration),
        "clip_offset_sec": clip_offset,
    }
