"""Beat-aligned edit decision generation."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from core.camera_moves import load_camera_moves, recorded_move_covering, recorded_shot_for_segment
from core.messages import t
from core.operator_avoidance import OPERATOR_AVOIDANCE_VERSION, avoidance_for_segment, load_cached_operator_presence
from core.project import Project
from core.shot_quality import DIRECTOR_SCORE_THRESHOLD, SHOT_QUALITY_VERSION, analyze_handheld_director_quality, director_quality_for_segment
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import load_coverage

MIN_SEGMENT_SEC = 2.0
MAX_SEGMENT_SEC = 6.0
MAX_BARS_PER_SEGMENT = 2
EDIT_FPS = 30.0
DEFAULT_CAMERA_ROLE_WEIGHTS = {"360": 50.0, "handheld": 30.0, "fixed_rear": 20.0}
SPHERICAL_PAN_SEC = 0.45  # legacy plan field; sweep timing is angular-speed based
SPHERICAL_MOTION_PLAN_VERSION = 9
REEL_PLAN_VERSION = 1
# Retained as a versioned emergency switch for diagnostics; normal builds use
# the shared gentle hold/sweep motion below.
FORCE_STATIC_360_ISOLATION = False
SPHERICAL_SWEEP_SPEED_DEG_PER_SEC = 20.0
SPHERICAL_MIN_SWEEP_SPEED_DEG_PER_SEC = 15.0
SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC = 20.0
# Automatic 360 motion budget, expressed as a fraction of the shot's visible
# field (h_fov) rather than in absolute degrees -- see
# _spherical_motion_profile for why absolute degrees was the bug. The target
# is motion a viewer barely registers as movement but which keeps the shot
# alive: a few percent of frame width across the WHOLE segment.
SPHERICAL_PRIMARY_DRIFT_FRACTION = (0.01, 0.03)
# Hard ceiling enforced at render time, in fraction of h_fov per second. Any
# automatic motion (drift, tiny-planet spin, inter-shot reframe) is clamped
# to this, so a short segment can never turn a whole-segment drift budget
# into a fast pan. Guarded by a regression test.
SPHERICAL_MAX_MOTION_FRACTION_PER_SEC = 0.06
# Planet is a special effect, not the default visual language of a normal
# 360 edit. Keep its optional rotation at a deliberately gentle absolute rate.
PLANET_SPIN_DEG_PER_SEC = 5.0
SPHERICAL_HOLD_MOTION_DEG_PER_SEC = 0.4
SPHERICAL_MIN_LANDMARK_HOLD_SEC = 6.0
SPHERICAL_TARGET_LANDMARK_HOLD_SEC = 8.0
SPHERICAL_MAX_LANDMARK_HOLD_SEC = 12.0
SPHERICAL_DEFAULT_FOV = 95.0
SPHERICAL_WIDE_FOV = 120.0
SPHERICAL_AUDIENCE_STAGE_FOV = 125.0
SPHERICAL_NORMAL_FOV_MIN = 70.0
SPHERICAL_NORMAL_FOV_MAX = 100.0
SPHERICAL_SHOT_ORDER = ("full_stage", "singer", "drummer", "left", "right", "audience", "audience_stage_wide", "planet")
SPHERICAL_LANDMARKS = {
    "singer": ("singer_yaw", "Cantante", SPHERICAL_DEFAULT_FOV),
    "drummer": ("drummer_yaw", "Bateria", SPHERICAL_DEFAULT_FOV),
    "left": ("left_yaw", "Lado izquierdo", SPHERICAL_DEFAULT_FOV),
    "right": ("right_yaw", "Lado derecho", SPHERICAL_DEFAULT_FOV),
    "audience": ("audience_yaw", "Publico", SPHERICAL_DEFAULT_FOV),
    "full_stage": ("full_stage_yaw", "Escenario completo", SPHERICAL_WIDE_FOV),
    "audience_stage_wide": ("audience_stage_wide_yaw", "Publico y escenario", SPHERICAL_AUDIENCE_STAGE_FOV),
    "planet": ("planet_yaw", "Planeta", 150.0),
}


class EditStage(Stage):
    """Build wizard edit decisions from coverage.

    YouTube uses a first-pass real multicam plan with beat-aligned cuts.
    Instagram, TikTok, and 360 still use the simple cut-stage segment until the
    short-form creative logic is implemented.
    """

    name = "edit"
    dependencies = ["cut"]

    def inputs_fingerprint(self, project: Project) -> str:
        """Fingerprint cut output and edit settings."""
        return stable_fingerprint(
            {
                "cut": project.data["stages"]["cut"].get("fingerprint"),
                "settings": project.data["settings"].get(self.name, {}),
                "spherical_landmarks": project.data["settings"].get("spherical_landmarks", {}),
                "camera_moves": _camera_moves_fingerprint(project),
                "shot_quality_version": SHOT_QUALITY_VERSION,
                "director_score_threshold": DIRECTOR_SCORE_THRESHOLD,
                "operator_avoidance_version": OPERATOR_AVOIDANCE_VERSION,
                "spherical_motion_plan_version": SPHERICAL_MOTION_PLAN_VERSION,
                "reel_plan_version": REEL_PLAN_VERSION,
            }
        )

    def outputs(self, project: Project) -> dict[str, str]:
        """Return edit decision artifacts."""
        return {
            "edit_plan": str(artifact_path(project, "edit_plan.json")),
            "beats": str(artifact_path(project, "beats.json")),
        }

    def run(self, project: Project, progress_callback: ProgressCallback) -> dict[str, Any]:
        """Write the edit plan consumed by export."""
        progress_callback(10, t("analyzing_rhythm"))
        coverage = load_coverage(project)
        platform = str(coverage.get("platform") or "youtube")
        recorded_moves = load_camera_moves(project)
        # Reel reuses the exact same real multicam logic as YouTube (bar-aligned
        # cuts, camera weights, operator avoidance, motion on static shots) --
        # only the window (narrowed to a short energetic highlight by cut.py)
        # and the render target size/framing differ.
        real_multicam = platform == "youtube"
        if real_multicam:
            coverage = _with_director_quality(project, coverage, progress_callback)
        if platform == "reel":
            beats = _load_or_analyze_beats(project, coverage, progress_callback)
            progress_callback(55, t("choosing_cameras"))
            plan = _reel_promo_plan(coverage, beats, project.data.get("settings", {}))
        elif not real_multicam:
            plan = _simple_plan(coverage, project.data.get("settings", {}), recorded_moves=recorded_moves)
            beats = {"stage": self.name, "platform": platform, "beats_sec": [], "bars_sec": [], "sections_sec": [], "tempo": None, "placeholder_short_form": platform != "360"}
        else:
            beats = _load_or_analyze_beats(project, coverage, progress_callback)
            progress_callback(55, t("choosing_cameras"))
            plan = _youtube_multicam_plan(coverage, beats, project.data.get("settings", {}), recorded_moves=recorded_moves)
        write_artifact_json(artifact_path(project, "beats.json"), beats)
        write_artifact_json(artifact_path(project, "edit_plan.json"), plan)
        progress_callback(100, t("edit_plan_ready"))
        return self.outputs(project)


def load_edit_plan(project: Project) -> dict[str, Any]:
    """Load edit_plan.json."""
    with artifact_path(project, "edit_plan.json").open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_or_analyze_beats(project: Project, coverage: dict[str, Any], progress_callback: ProgressCallback) -> dict[str, Any]:
    path = artifact_path(project, "beats.json")
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("fingerprint") == _beat_fingerprint(project, coverage):
            progress_callback(45, t("rhythm_cached"))
            return cached
    master = project.data.get("inputs", {}).get("master")
    if not master:
        raise ValueError(t("missing_master_for_edit"))
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    duration = max(1.0, float(window.get("duration_sec") or 1.0))
    try:
        import librosa

        y, sr = librosa.load(str(Path(master["path"])), sr=22050, mono=True, offset=start, duration=duration)
        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
        local_beats = [float(value) for value in librosa.frames_to_time(beat_frames, sr=sr)]
        beat_times = [round(start + value, 3) for value in local_beats]
        section_times = [round(start + value, 3) for value in estimate_section_changes(y, sr, local_beats)]
        tempo_value = float(tempo[0] if hasattr(tempo, "__len__") else tempo)
    except Exception:
        beat_times = _fallback_beats(start, duration)
        section_times = []
        tempo_value = None
    if len(beat_times) < 2:
        beat_times = _fallback_beats(start, duration)
    bars = estimate_bar_starts(beat_times, start, duration)
    progress_callback(45, t("rhythm_ready"))
    return {
        "stage": "edit",
        "platform": coverage.get("platform") or "youtube",
        "fingerprint": _beat_fingerprint(project, coverage),
        "window_start_sec": start,
        "window_duration_sec": duration,
        "tempo": tempo_value,
        "beats_sec": beat_times,
        "bars_sec": bars,
        "sections_sec": section_times,
    }


def _beat_fingerprint(project: Project, coverage: dict[str, Any]) -> str:
    return stable_fingerprint({"master": project.data.get("inputs", {}).get("master"), "window": coverage.get("window")})


def _camera_moves_fingerprint(project: Project) -> str:
    path = project.artifacts_dir / "camera_moves"
    if not path.exists():
        return ""
    entries = []
    for item in sorted(path.glob("*.json")):
        try:
            stat = item.stat()
        except OSError:
            continue
        entries.append({"name": item.name, "size": stat.st_size, "mtime": stat.st_mtime})
    return stable_fingerprint(entries)


def _fallback_beats(start: float, duration: float) -> list[float]:
    step = 0.5
    count = int(duration / step) + 1
    return [round(start + index * step, 3) for index in range(count + 1)]


def _with_director_quality(project: Project, coverage: dict[str, Any], progress_callback: ProgressCallback) -> dict[str, Any]:
    sources = coverage.get("sources") or []
    if not sources:
        return coverage
    progress_callback(50, "Scoring Sony director camera")
    scored = analyze_handheld_director_quality(project, sources)
    return {**coverage, "sources": scored}


def _reel_promo_plan(
    coverage: dict[str, Any],
    beats: dict[str, Any],
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an unsynchronised, beat-cut promo plan.

    The master timeline is used only for the music bed. Each video segment is
    selected independently from the available camera material, so a source's
    clip time is deliberately unrelated to ``master_start_sec``.
    """
    settings = settings or {}
    wizard = settings.get("wizard") if isinstance(settings.get("wizard"), dict) else settings
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    requested = max(20.0, min(60.0, float(wizard.get("reel_duration_sec") or 30.0)))
    duration = min(requested, float(window.get("duration_sec") or requested))
    end = start + duration
    sources = list(coverage.get("sources") or [])
    if not sources and coverage.get("segments"):
        sources = list(coverage["segments"])
    if not sources:
        raise ValueError("Reel has no usable video sources")

    # Prefer existing visual-analysis signals, but rotate through sources so a
    # single high-confidence camera cannot monopolise the promo.
    sources.sort(key=lambda item: (
        -float(item.get("shot_quality_score") or item.get("director_segment_score") or item.get("motion_score") or 0.0),
        str(item.get("path") or item.get("source_path") or ""),
    ))
    bars = sorted({float(value) for value in (beats.get("bars_sec") or beats.get("beats_sec") or []) if start < float(value) < end})
    boundaries = [start]
    cursor = start
    while cursor < end - 0.05:
        candidates = [value for value in bars if value > cursor + 0.7]
        next_cut = next((value for value in candidates if value - cursor >= 1.0), min(end, cursor + 2.0))
        next_cut = min(end, max(cursor + 0.8, next_cut))
        boundaries.append(next_cut)
        cursor = next_cut
    if boundaries[-1] < end:
        boundaries.append(end)

    landmarks = migrate_spherical_landmarks(settings.get("spherical_landmarks") or {})
    spherical_shots = _available_spherical_shots(landmarks, sweep_enabled=False)
    segments: list[dict[str, Any]] = []
    previous_source = None
    fixed_index = 0
    for index, (master_start, master_end) in enumerate(zip(boundaries, boundaries[1:])):
        seg_duration = max(0.1, master_end - master_start)
        ordered = sorted(sources, key=lambda item: (
            _source_id(item) == previous_source,
            index % max(1, len(sources)) != sources.index(item),
        ))
        source = ordered[0]
        previous_source = _source_id(source)
        source_duration = max(seg_duration, float(source.get("duration_sec") or seg_duration))
        # Deterministic spread over each source, independent of master time.
        clip_start = max(0.0, ((index * 1.37) % max(0.1, source_duration - seg_duration))) if source_duration > seg_duration else 0.0
        segment = {
            "title": window.get("title") or t("full_video"),
            "clip_path": source.get("path") or source.get("clip_path"),
            "source_path": source.get("source_path") or source.get("path") or source.get("clip_path"),
            "clip_start_sec": round(clip_start, 6),
            "master_start_sec": round(master_start, 6),
            "duration_sec": round(seg_duration, 6),
            "clip_offset_sec": 0.0,
            "filename": source.get("filename") or Path(str(source.get("path") or "video")).name,
            "projection": source.get("projection"),
            "reel_subject_center": {
                "x": float(source.get("subject_center_x") or source.get("face_center_x") or 0.5),
                "y": float(source.get("subject_center_y") or source.get("face_center_y") or 0.5),
            },
        }
        role = _source_role(source)
        if role == "360":
            shot = dict(spherical_shots[index % len(spherical_shots)]) if spherical_shots else {
                "type": "promo_360", "label": "360 promo", "yaw": 0.0, "pitch": 0.0, "fov": 95.0, "weight": 1.0,
            }
            segment["spherical_shot"] = _spherical_motion_profile(shot, index, enabled=False, hold_motion="none")
        elif role == "fixed_rear" and bool(wizard.get("fixed_rear_motion", True)):
            if fixed_index % 2 == 0:
                segment["motion"] = _ken_burns_motion(fixed_index)
            fixed_index += 1
        segments.append(segment)
    return {
        "stage": "edit",
        "platform": "reel",
        "reel_plan_version": REEL_PLAN_VERSION,
        "reel_duration_sec": round(duration, 6),
        "reel_aspect": str(wizard.get("reel_aspect") or "9:16"),
        "reel_text_overlays": list(wizard.get("reel_text_overlays") or []),
        "title": window.get("title") or t("full_video"),
        "real_edit_logic": "reel unsynchronised promo: independent dynamic source selection on master beats",
        "warnings": coverage.get("warnings") or [],
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "camera_usage": _camera_usage(segments),
        "cut_count": max(0, len(segments) - 1),
        "segments": segments,
    }


def _youtube_multicam_plan(
    coverage: dict[str, Any],
    beats: dict[str, Any],
    settings: dict[str, Any] | None = None,
    recorded_moves: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    end = start + max(1.0, float(window.get("duration_sec") or 1.0))
    bar_times = [float(value) for value in (beats.get("bars_sec") or []) if start <= float(value) <= end]
    if not bar_times or bar_times[0] > start:
        bar_times.insert(0, start)
    if bar_times[-1] < end:
        bar_times.append(end)
    section_times = [float(value) for value in (beats.get("sections_sec") or []) if start < float(value) < end]

    sources = coverage.get("sources") or []
    if not sources and coverage.get("segments"):
        sources = coverage["segments"]
    segments: list[dict[str, Any]] = []
    gaps: list[dict[str, float]] = []
    previous_source: str | None = None
    previous_framing: dict[str, Any] | None = None  # C: track framing for consecutive-duplicate detection
    previous_spherical_yaw: float | None = None
    previous_spherical_type: str | None = None
    recent_spherical_types: list[str] = []
    fixed_rear_motion_index = 0
    usage_counts: dict[str, int] = {}
    operator_samples_cache: dict[str, list[dict[str, Any]]] = {}
    selection_stats = _selection_stats_template(sources, start, end)
    project_settings = settings or {}
    edit_settings = project_settings.get("edit") if "edit" in project_settings else project_settings
    spherical_landmarks = migrate_spherical_landmarks(project_settings.get("spherical_landmarks") or {})
    spherical_mode = str(edit_settings.get("spherical_mode") or "automatic").lower()
    use_recorded_360 = spherical_mode == "directed"
    role_weights = _camera_role_weights(edit_settings)
    fixed_rear_motion = bool(edit_settings.get("fixed_rear_motion", True))
    # Static 360 holds are the safe shipped default. Motion remains an explicit
    # project opt-in until a filter path that does not reconfigure v360 per
    # frame is available.
    spherical_motion = bool(edit_settings.get("spherical_motion", False))
    hold_motion = str(edit_settings.get("spherical_hold_motion") or "none").lower()
    if hold_motion not in {"none", "subtle"}:
        hold_motion = "none"
    spherical_sweep = bool(edit_settings.get("spherical_sweep", False))
    sweep_speed = max(SPHERICAL_MIN_SWEEP_SPEED_DEG_PER_SEC, min(SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC, float(edit_settings.get("sweep_speed_deg_per_sec", SPHERICAL_SWEEP_SPEED_DEG_PER_SEC))))
    bar_index = 0
    segment_index = 0
    while bar_index < len(bar_times) - 1:
        bars_per_segment = _bars_for_segment(segment_index, bar_times, section_times, bar_index)
        max_bar_index = min(len(bar_times) - 1, bar_index + MAX_BARS_PER_SEGMENT)
        next_index = min(max_bar_index, bar_index + bars_per_segment)
        section_index = _reachable_section_index(bar_times, section_times, bar_index, next_index)
        if section_index is not None:
            next_index = min(section_index, max_bar_index)
        while next_index > bar_index + 1 and bar_times[next_index] - bar_times[bar_index] > MAX_SEGMENT_SEC:
            next_index -= 1
        while next_index < max_bar_index and bar_times[next_index] - bar_times[bar_index] < MIN_SEGMENT_SEC:
            next_index += 1
        segment_start = float(bar_times[bar_index])
        segment_end = min(end, float(bar_times[next_index]))
        if segment_end <= segment_start:
            break
        available = _quality_filtered_sources(_covering_sources(sources, segment_start, segment_end), segment_start, segment_end, selection_stats)
        if not available:
            gaps.append({"start_sec": round(segment_start, 3), "end_sec": round(segment_end, 3)})
            bar_index = next_index
            continue
        for source in available:
            stats = selection_stats.setdefault(_source_id(source), _selection_stats_for_source(source, start, end))
            stats["eligible_segments"] += 1
            stats["eligible_seconds"] += segment_end - segment_start
        # C: try ranked candidates until we find one whose framing differs from previous,
        # preventing consecutive identical shots from the same source.
        source = _choose_source_avoiding_identical_framing(
            available, previous_source, previous_framing, usage_counts, selection_stats, role_weights,
            segment_start, segment_end, segment_index, spherical_landmarks, use_recorded_360, recorded_moves or [],
        )
        is_automatic_360 = _source_role(source) == "360" and not (
            use_recorded_360 and recorded_move_covering(recorded_moves or [], segment_start, segment_end)
        )
        if is_automatic_360:
            next_index = _extend_360_hold_index(bar_times, next_index, segment_start, end, source)
            segment_end = min(end, float(bar_times[next_index]))
            # The source may not cover the longer phrase-aligned window. Keep
            # the original boundary in that case rather than inventing a gap.
        previous_source = _source_id(source)
        usage_counts[previous_source] = usage_counts.get(previous_source, 0) + 1
        chosen_stats = selection_stats.setdefault(previous_source, _selection_stats_for_source(source, start, end))
        chosen_stats["chosen_segments"] += 1
        chosen_stats["chosen_seconds"] += segment_end - segment_start
        segment = _segment_from_source(source, segment_start, segment_end, window.get("title") or t("full_video"))
        if _source_role(source) == "360":
            recorded = recorded_move_covering(recorded_moves or [], segment_start, segment_end) if use_recorded_360 else None
            if recorded:
                segment["spherical_shot"] = recorded_shot_for_segment(recorded, segment_start, segment_end)
            else:
                current_usage = _spherical_shot_usage(segments)
                available_shots = _available_spherical_shots(spherical_landmarks, spherical_sweep, sweep_speed)
                include_planet = current_usage.get("Planeta", 0) == 0 and sum(current_usage.values()) >= 5
                shot = _next_weighted_spherical_shot(
                    available_shots,
                    _spherical_type_usage(segments),
                    include_planet=include_planet,
                    previous_yaw=previous_spherical_yaw,
                    previous_type=previous_spherical_type,
                    recent_types=recent_spherical_types,
                )
                # Always attach a shot with motion, even with no configured landmarks
                # (shot=None) — a 360 segment must never fall back to a frozen,
                # motionless equirect passthrough.
                segment["spherical_shot"] = _spherical_motion_profile(shot or {}, segment_index, enabled=spherical_motion, hold_motion=hold_motion)
        elif fixed_rear_motion and _source_role(source) == "fixed_rear":
            # Count only fixed-camera cuts, not global timeline indices: this
            # keeps the intended roughly-half cadence even when other cameras
            # are inserted between iPhone shots.
            if fixed_rear_motion_index % 2 == 0:
                segment["motion"] = _ken_burns_motion(fixed_rear_motion_index)
            fixed_rear_motion_index += 1
        _apply_operator_avoidance(segment, source, operator_samples_cache)
        previous_framing = _framing_descriptor(source, segment)
        if _source_role(source) == "360" and segment.get("spherical_shot"):
            previous_spherical_yaw = float(segment["spherical_shot"].get("yaw") or 0.0) % 360.0
            previous_spherical_type = str(segment["spherical_shot"].get("type") or "")
            recent_spherical_types.append(previous_spherical_type)
            del recent_spherical_types[:-4]
        segments.append(segment)
        bar_index = next_index
        segment_index += 1

    warnings = list(coverage.get("warnings") or [])
    if gaps:
        warnings.extend([t("no_video_between", start=_fmt_time(gap["start_sec"]), end=_fmt_time(gap["end_sec"])) for gap in gaps])
    usage: dict[str, int] = {}
    for segment in segments:
        usage[Path(str(segment.get("clip_path"))).name] = usage.get(Path(str(segment.get("clip_path"))).name, 0) + 1
    return {
        "stage": "edit",
        "platform": coverage.get("platform") or "youtube",
        "title": window.get("title") or t("full_video"),
        "real_edit_logic": "youtube beat-aligned multicam v1" if (coverage.get("platform") or "youtube") == "youtube" else "reel beat-aligned multicam (vertical highlight)",
        "warnings": warnings,
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "selection_diagnostics": _finalize_selection_stats(selection_stats),
        "gaps": gaps,
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": usage,
        "spherical_shot_usage": _spherical_shot_usage(segments),
        "spherical_landmark_weights": {
            shot_type: float(data.get("weight") or 0.0)
            for shot_type, data in spherical_landmarks.items()
            if isinstance(data, dict)
        },
        "spherical_recording_usage": _spherical_recording_usage(segments, spherical_mode, recorded_moves),
        "segments": segments,
    }


def _simple_plan(
    coverage: dict[str, Any],
    project_settings: dict[str, Any] | None = None,
    recorded_moves: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    platform = coverage.get("platform") or "youtube"
    if platform == "360":
        # Passthrough mode: no camera selection, reprojection, motion, or any
        # other editing logic. The single segment cut.py already computed
        # (song window intersected with what the 360 camera covers) IS the
        # plan -- export.py trims/copies the original file directly.
        segments = coverage.get("segments") or []
        return {
            "stage": "edit",
            "platform": platform,
            "placeholder_logic": None,
            "real_edit_logic": "360 passthrough: original clip trimmed to the song range, untouched",
            "window": coverage.get("window") or {},
            "warnings": coverage.get("warnings") or [],
            "excluded_clips": coverage.get("excluded_clips") or [],
            "clip_diagnostics": coverage.get("clip_diagnostics") or [],
            "gaps": [],
            "cut_count": 0,
            "camera_usage": {},
            "spherical_shot_usage": {},
            "spherical_recording_usage": {},
            "segments": segments,
        }
    segments = _short_form_segments_from_best_coverage(coverage)
    edit_settings = ((project_settings or {}).get("edit") if "edit" in (project_settings or {}) else project_settings) or {}
    return {
        "stage": "edit",
        "platform": platform,
        "placeholder_logic": "short-form middle excerpt; multicam/highlight logic pending",
        "real_edit_logic": None,
        "warnings": coverage.get("warnings") or [],
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "gaps": [],
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": _camera_usage(segments),
        "spherical_shot_usage": {},
        "spherical_recording_usage": _spherical_recording_usage(segments, edit_settings.get("spherical_mode"), recorded_moves),
        "segments": segments,
    }


def build_spherical_shot_segments(
    base_segments: list[dict[str, Any]],
    landmarks: dict[str, Any] | None,
    recorded_moves: list[dict[str, Any]] | None = None,
    spherical_motion: bool = False,
    spherical_hold_motion: str | None = None,
) -> list[dict[str, Any]]:
    """Split 360 source coverage into named virtual-camera holds."""
    if not base_segments:
        return []
    shots = _available_spherical_shots(migrate_spherical_landmarks(landmarks or {}))
    has_recordings = bool(recorded_moves) and not FORCE_STATIC_360_ISOLATION
    if not shots and not has_recordings:
        return list(base_segments)
    total_duration = sum(max(0.0, float(segment.get("duration_sec") or 0.0)) for segment in base_segments)
    planet_budget = 1 if total_duration >= 18.0 else 0
    planet_after = total_duration * 0.58
    elapsed = 0.0
    usage: dict[str, int] = {}
    output: list[dict[str, Any]] = []
    for base in base_segments:
        remaining = max(0.0, float(base.get("duration_sec") or 0.0))
        local = 0.0
        while remaining > 0.001:
            hold = min(MAX_SEGMENT_SEC, remaining)
            if remaining - hold > 0.001 and remaining - hold < MIN_SEGMENT_SEC:
                hold = max(MIN_SEGMENT_SEC, remaining / 2.0)
            master_start = float(base.get("master_start_sec") or 0.0) + local
            master_end = master_start + hold
            recorded = None if FORCE_STATIC_360_ISOLATION else recorded_move_covering(recorded_moves or [], master_start, master_end)
            shot = _next_weighted_spherical_shot(shots, usage, include_planet=False)
            if planet_budget > 0 and elapsed <= planet_after < elapsed + hold and any(item.get("type") == "planet" for item in shots):
                shot = dict(next(item for item in shots if item.get("type") == "planet"))
                # No "yaw_end" hint is written here: nothing ever read it, and
                # it encoded a spin in absolute deg/s, which is exactly the
                # FOV-blind magnitude this motion rework removes. The planet's
                # spin now comes solely from spin_fov_fraction_per_sec, applied
                # (and clamped) at render time.
                planet_budget -= 1
            if not shot and not recorded:
                break
            if shot:
                usage[str(shot.get("type"))] = usage.get(str(shot.get("type")), 0) + 1
            segment = {
                **base,
                "clip_start_sec": round(float(base.get("clip_start_sec") or 0.0) + local, 6),
                "master_start_sec": round(master_start, 6),
                "duration_sec": round(hold, 6),
                "spherical_shot": recorded_shot_for_segment(recorded, master_start, master_end) if recorded else _spherical_motion_profile(shot or {}, len(output), enabled=spherical_motion, hold_motion=spherical_hold_motion),
            }
            output.append(segment)
            elapsed += hold
            local += hold
            remaining -= hold
    return output


def migrate_spherical_landmarks(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Normalize legacy yaw-only landmark settings to the current per-shot schema."""
    if not isinstance(raw, dict):
        return {}
    migrated: dict[str, dict[str, float]] = {}
    for shot_type, (legacy_key, _label, default_fov) in SPHERICAL_LANDMARKS.items():
        source = raw.get(shot_type)
        if source is None and legacy_key in raw:
            source = {"yaw": raw.get(legacy_key)}
        if not isinstance(source, dict):
            continue
        yaw = _landmark_yaw(source.get("yaw"), None)
        if yaw is None:
            continue
        migrated[shot_type] = {
            "yaw": yaw,
            "pitch": _landmark_weight(source, "pitch", 0.0),
            "fov": _landmark_weight(source, "fov", default_fov),
            "weight": max(0.0, _landmark_weight(source, "weight", 1.0)),
        }
    return migrated


def _available_spherical_shots(landmarks: dict[str, dict[str, float]], sweep_enabled: bool = False, sweep_speed: float = SPHERICAL_SWEEP_SPEED_DEG_PER_SEC) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for shot_type in SPHERICAL_SHOT_ORDER:
        key, label, default_fov = SPHERICAL_LANDMARKS[shot_type]
        data = landmarks.get(shot_type)
        if data is None and shot_type == "full_stage":
            data = {"yaw": 0.0, "pitch": 0.0, "fov": default_fov, "weight": 1.0}
        if not data:
            continue
        yaw = _landmark_yaw(data.get("yaw"), None)
        if yaw is None:
            continue
        weight = max(0.0, _landmark_weight(data, "weight", 1.0))
        if weight <= 0.0:
            continue
        shot = {
            "type": shot_type,
            "shot_id": shot_type,
            "label": label,
            "yaw": yaw,
            "pitch": _landmark_weight(data, "pitch", 0.0),
            "fov": _landmark_weight(data, "fov", default_fov),
            "weight": weight,
            "sweep_enabled": bool(sweep_enabled),
            "sweep_speed_deg_per_sec": max(SPHERICAL_MIN_SWEEP_SPEED_DEG_PER_SEC, min(SPHERICAL_MAX_SWEEP_SPEED_DEG_PER_SEC, float(sweep_speed))) if sweep_enabled else 0.0,
        }
        shot["fov"] = _plan_spherical_fov(shot)
        if shot_type == "planet":
            shot["spin_deg_per_sec"] = PLANET_SPIN_DEG_PER_SEC if sweep_enabled else 0.0
        shots.append(shot)
    return shots


def _spherical_motion_profile(shot: dict[str, Any], index: int, enabled: bool = False, hold_motion: str | None = None) -> dict[str, Any]:
    """Attach subtle, randomized-but-reproducible movement to a 360 landmark shot.

    Automatic motion is OPT-IN (``enabled``, default False). Unpredictable
    movement is worse than none: with the toggle off a landmark shot renders as
    a clean, completely still hold, and movement comes only from a recorded
    Director take or from deliberately switching this on.

    Normal landmark shots have optional near-static hold motion. ``none``
    locks the view; ``subtle`` adds at most 0.01 degrees/second of yaw drift.
    The project-level toggle remains backward-compatible.

    Magnitudes are stored as a FRACTION OF THE SHOT'S VISIBLE FIELD, not as
    absolute degrees. Absolute degrees were the bug: the same ±6° drift is a
    gentle nudge across a 140° wide shot but a big swing across a 73° one
    (the user's "singer"/"drummer" landmarks are both ~73°), because it
    covers nearly twice the fraction of what the viewer can actually see. The
    render side (``core.stages.export._v360_motion_at``) multiplies these
    fractions by the shot's real rendered h_fov and additionally clamps the
    result to a per-second ceiling, so motion reads as equally subtle at
    every zoom level and can never whip regardless of segment length. This is
    the same class of fix already applied to the Director's mouse
    sensitivity in 6fc6a61.
    """
    shot = dict(shot)
    shot_type = str(shot.get("type") or "")
    shot["fov"] = _plan_spherical_fov(shot)
    if shot_type == "planet":
        shot["pitch"] = -90.0
        shot["fov"] = max(240.0, float(shot.get("fov") or 240.0))
        shot["projection"] = "tiny_planet"
        shot["runtime_motion_enabled"] = False
        # The tiny-planet spin is a deliberate signature effect, but it is
        # still held to the same fraction-of-field budget so it reads as a
        # slow rotation rather than a carousel. With motion off it holds still
        # like every other shot.
        shot["spin_deg_per_sec"] = PLANET_SPIN_DEG_PER_SEC if enabled else 0.0
        shot["spin_fov_fraction_per_sec"] = 0.0
        shot["sweep_enabled"] = bool(shot.get("sweep_enabled", False)) and enabled
        shot["hold_motion"] = "none"
        shot["hold_motion_rate_deg_per_sec"] = 0.0
        shot["drift_yaw_fraction"] = 0.0
        shot["drift_pitch_fraction"] = 0.0
        shot["fov_delta_fraction"] = 0.0
        return shot
    # Explicit zeros prevent legacy render fallbacks from reviving wandering
    # in a cached or hand-edited plan.
    mode = str(hold_motion or ("subtle" if enabled else "none")).lower()
    if mode not in {"none", "subtle"}:
        mode = "subtle" if enabled else "none"
    shot["sweep_enabled"] = bool(shot.get("sweep_enabled", False)) and enabled
    # Runtime sendcmd animation is disabled globally until the safe
    # equirectangular reprojection path replaces FFmpeg's corrupting v360
    # reconfiguration. Keep the authored subtle rate in the plan for future
    # use, but make the shipped render pose static.
    shot["runtime_motion_enabled"] = False
    shot["hold_motion"] = mode
    shot["hold_motion_rate_deg_per_sec"] = SPHERICAL_HOLD_MOTION_DEG_PER_SEC if mode == "subtle" and enabled else 0.0
    shot["drift_yaw_fraction"] = 0.0
    shot["drift_pitch_fraction"] = 0.0
    shot["fov_delta_fraction"] = 0.0
    return shot


def _plan_spherical_fov(shot: dict[str, Any]) -> float:
    """Return the FOV written into a generated plan for a spherical shot."""
    shot_type = str(shot.get("type") or "")
    try:
        fov = float(shot.get("fov") or SPHERICAL_DEFAULT_FOV)
    except (TypeError, ValueError):
        fov = SPHERICAL_DEFAULT_FOV
    if shot_type == "planet":
        return max(220.0, min(300.0, fov))
    if shot_type == "recorded_move":
        return max(1.0, min(300.0, fov))
    return max(SPHERICAL_NORMAL_FOV_MIN, min(SPHERICAL_NORMAL_FOV_MAX, fov))


def _next_weighted_spherical_shot(
    shots: list[dict[str, Any]],
    usage: dict[str, int],
    include_planet: bool = False,
    previous_yaw: float | None = None,
    previous_type: str | None = None,
    recent_types: list[str] | None = None,
) -> dict[str, Any] | None:
    candidates = [
        shot
        for shot in shots
        if float(shot.get("weight") or 0.0) > 0.0 and (include_planet or shot.get("type") != "planet")
    ]
    if not candidates:
        return None
    recent_types = recent_types or []

    def distance(shot: dict[str, Any]) -> float:
        if previous_yaw is None:
            return 0.0
        return abs(((float(shot.get("yaw") or 0.0) - previous_yaw + 180.0) % 360.0) - 180.0)

    def score(shot: dict[str, Any]) -> tuple[float, float, float, str]:
        shot_type = str(shot.get("type") or "")
        weight = max(0.001, float(shot.get("weight") or 1.0))
        # Deficit from the configured weighted rotation is primary. A shot
        # below its target share beats a nearby shot that is already overused.
        weighted_deficit = usage.get(shot_type, 0) / weight
        if shot_type == previous_type:
            weighted_deficit += 100.0
        elif shot_type in recent_types[-3:]:
            # Recency breaks ties without overpowering the configured weight:
            # a 40-point landmark must still catch up after being underused.
            weighted_deficit += 0.01 * (4 - recent_types[-3:].index(shot_type))
        # Yaw is only a soft tiebreaker. It can make equal-priority choices
        # gentler, but can never starve a configured landmark.
        return (weighted_deficit, distance(shot), usage.get(shot_type, 0), shot_type)

    return dict(min(candidates, key=score))


def _extend_360_hold_index(
    bar_times: list[float],
    next_index: int,
    start: float,
    end: float,
    source: dict[str, Any],
) -> int:
    """Extend an automatic 360 hold to a phrase-aligned 6–12 second window."""
    index = next_index
    duration = min(end, float(bar_times[index])) - start
    while index + 1 < len(bar_times) and duration < SPHERICAL_TARGET_LANDMARK_HOLD_SEC:
        proposed_end = min(end, float(bar_times[index + 1]))
        if proposed_end - start > SPHERICAL_MAX_LANDMARK_HOLD_SEC:
            break
        if not _covering_sources([source], start, proposed_end):
            break
        index += 1
        duration = proposed_end - start
    if duration < SPHERICAL_MIN_LANDMARK_HOLD_SEC:
        while index + 1 < len(bar_times):
            proposed_end = min(end, float(bar_times[index + 1]))
            if proposed_end - start > SPHERICAL_MAX_LANDMARK_HOLD_SEC:
                break
            if not _covering_sources([source], start, proposed_end):
                break
            index += 1
            duration = proposed_end - start
            if duration >= SPHERICAL_MIN_LANDMARK_HOLD_SEC:
                break
    return index

def _landmark_yaw(value: Any, fallback: float | None) -> float | None:
    number = _parse_float(value)
    return number % 360.0 if number is not None else fallback


def _landmark_weight(data: dict[str, Any], key: str, fallback: float) -> float:
    number = _parse_float(data.get(key, fallback))
    return number if number is not None else fallback


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _spherical_shot_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        shot = segment.get("spherical_shot") or {}
        label = str(shot.get("label") or shot.get("type") or "").strip()
        if label:
            usage[label] = usage.get(label, 0) + 1
    return usage


def _spherical_type_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        shot = segment.get("spherical_shot") or {}
        shot_type = str(shot.get("type") or "").strip()
        if shot_type:
            usage[shot_type] = usage.get(shot_type, 0) + 1
    return usage


def _spherical_recording_usage(
    segments: list[dict[str, Any]],
    spherical_mode: str | None = None,
    recorded_moves: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    recorded = 0
    landmark = 0
    by_take: dict[str, int] = {}
    for segment in segments:
        if _source_role(segment) != "360":
            continue
        shot = segment.get("spherical_shot") or {}
        if shot.get("type") == "recorded_move":
            recorded += 1
            take = str(shot.get("recorded_take") or "Take")
            by_take[take] = by_take.get(take, 0) + 1
        elif shot:
            landmark += 1
    mode = str(spherical_mode or "automatic").lower()
    takes_available = len(recorded_moves or [])
    return {
        "mode": mode,
        "recorded_segments": recorded,
        "landmark_segments": landmark,
        "takes": by_take,
        "recorded_takes_available": takes_available,
        "recorded_takes_ignored": mode == "automatic" and takes_available > 0,
    }


def estimate_bar_starts(beat_times: list[float], start: float, duration: float) -> list[float]:
    """Estimate downbeats by grouping beats in fours."""
    end = start + duration
    beats = [float(value) for value in beat_times if start <= float(value) <= end]
    if not beats:
        return [start, end]
    bars = [start]
    best_phase = 0
    if len(beats) >= 8:
        gaps = [beats[index + 4] - beats[index] for index in range(len(beats) - 4)]
        best_phase = min(range(4), key=lambda phase: abs((gaps[phase] if phase < len(gaps) else 2.0) - np_median(gaps)))
    for index, beat in enumerate(beats):
        if index % 4 == best_phase and beat > start + 0.05:
            bars.append(round(beat, 3))
    if bars[-1] < end:
        bars.append(end)
    return sorted(set(bars))


def estimate_section_changes(y: Any, sr: int, beat_times: list[float]) -> list[float]:
    """Estimate section boundaries from spectral novelty peaks."""
    if not beat_times:
        return []
    try:
        import librosa
        import numpy as np

        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        novelty = np.linalg.norm(np.diff(chroma, axis=1), axis=0)
        if novelty.size == 0:
            return []
        threshold = float(np.percentile(novelty, 90))
        frame_times = librosa.frames_to_time(np.arange(novelty.size), sr=sr)
        candidates = [float(frame_times[index]) for index, value in enumerate(novelty) if float(value) >= threshold]
        sections = []
        for candidate in candidates:
            nearest = min(beat_times, key=lambda beat: abs(beat - candidate))
            if not sections or abs(nearest - sections[-1]) > 12:
                sections.append(round(nearest, 3))
        return sections
    except Exception:
        return []


def np_median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _reachable_section_index(bar_times: list[float], section_times: list[float], current_index: int, proposed_index: int) -> int | None:
    current = bar_times[current_index]
    proposed = bar_times[proposed_index]
    for section in section_times:
        if current + MIN_SEGMENT_SEC <= section <= min(proposed + 2.0, current + MAX_SEGMENT_SEC):
            nearest = min(range(current_index + 1, len(bar_times)), key=lambda index: abs(bar_times[index] - section))
            return nearest
    return None


def _bars_for_segment(segment_index: int, bar_times: list[float], section_times: list[float], bar_index: int) -> int:
    current = bar_times[bar_index]
    near_section = any(current < section <= current + MAX_SEGMENT_SEC for section in section_times)
    if near_section:
        return 1
    if segment_index % 5 in {1, 4}:
        return 1
    return MAX_BARS_PER_SEGMENT


def _short_form_segments_from_best_coverage(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    segments = coverage.get("segments") or []
    sources = coverage.get("sources") or []
    if not segments:
        return []
    platform = str(coverage.get("platform") or "")
    target = 45.0 if platform == "instagram" else 20.0 if platform == "tiktok" else float(segments[0].get("duration_sec") or 1.0)
    best = max(sources or segments, key=lambda source: float(source.get("confidence") or 0.0) * max(1.0, float(source.get("duration_sec") or 0.0)))
    duration = min(target, float(best.get("duration_sec") or target))
    clip_duration = float(best.get("duration_sec") or duration)
    clip_start = max(0.0, (clip_duration - duration) / 2)
    offset = float(best.get("offset_sec") or best.get("clip_offset_sec") or 0.0)
    return [
        {
            "title": (coverage.get("window") or {}).get("title") or best.get("title") or t("video_title"),
            "clip_path": best["path"] if "path" in best else best["clip_path"],
            "source_path": best.get("source_path") or best.get("path") or best.get("clip_path"),
            "clip_start_sec": clip_start,
            "master_start_sec": offset + clip_start,
            "duration_sec": duration,
            "clip_offset_sec": offset,
            "confidence": float(best.get("confidence") or 0.0),
            "filename": best.get("filename") or Path(str(best.get("path") or best.get("clip_path"))).name,
            "projection": best.get("projection"),
        }
    ]


def _covering_sources(sources: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    available = []
    for source in sources:
        offset = float(source.get("offset_sec") or 0.0)
        duration = float(source.get("duration_sec") or 0.0)
        if offset <= start and offset + duration >= end:
            available.append(source)
    return available


def _quality_filtered_sources(
    sources: list[dict[str, Any]],
    start: float,
    end: float,
    selection_stats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    filtered = []
    rejected_handheld: list[tuple[float, dict[str, Any]]] = []
    for source in sources:
        if _source_role(source) != "handheld":
            filtered.append(source)
            continue
        quality = director_quality_for_segment(source, start, end)
        stats = selection_stats.setdefault(_source_id(source), _selection_stats_for_source(source, start, end))
        stats.setdefault("director_quality", (source.get("director_quality") or {}).get("summary") or {})
        if quality.get("eligible", True):
            stats["director_score_sum"] = float(stats.get("director_score_sum") or 0.0) + float(quality.get("score") or 0.0)
            stats["director_score_windows"] = int(stats.get("director_score_windows") or 0) + 1
            filtered.append({**source, "director_segment_score": quality.get("score")})
        else:
            stats["director_rejected_segments"] = int(stats.get("director_rejected_segments") or 0) + 1
            reasons = stats.setdefault("director_reject_reasons", {})
            for reason in quality.get("reasons") or ["low director score"]:
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
            rejected_handheld.append((float(quality.get("score") or 0.0), {**source, "director_segment_score": quality.get("score")}))
    if not filtered and rejected_handheld:
        return [sorted(rejected_handheld, key=lambda item: item[0], reverse=True)[0][1]]
    return filtered


def _choose_source(
    sources: list[dict[str, Any]],
    previous_source: str | None,
    usage_counts: dict[str, int] | None = None,
    selection_stats: dict[str, dict[str, Any]] | None = None,
    role_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    usage_counts = usage_counts or {}
    role_weights = role_weights or DEFAULT_CAMERA_ROLE_WEIGHTS
    usable_sources = [source for source in sources if float(role_weights.get(_source_role(source), role_weights.get("handheld", 0.3))) > 0.0]
    if not usable_sources:
        usable_sources = sources
    candidates = [source for source in usable_sources if _source_id(source) != previous_source] if len(usable_sources) > 1 else usable_sources
    if not candidates:
        candidates = usable_sources
    selection_stats = selection_stats or {}

    def score(source: dict[str, Any]) -> tuple[float, int, float, float, str]:
        role = _source_role(source)
        target_share = max(0.001, float(role_weights.get(role, role_weights.get("handheld", 0.3))))
        chosen_seconds = float((selection_stats.get(_source_id(source)) or {}).get("chosen_seconds") or 0.0)
        director_bonus = float(source.get("director_segment_score") or 1.0) if role == "handheld" else 1.0
        return (chosen_seconds / target_share, usage_counts.get(_source_id(source), 0), -director_bonus, -float(source.get("confidence") or 0.0), _source_id(source))

    return sorted(candidates, key=score)[0]


def _choose_source_avoiding_identical_framing(
    sources: list[dict[str, Any]],
    previous_source: str | None,
    previous_framing: dict[str, Any] | None,
    usage_counts: dict[str, int],
    selection_stats: dict[str, dict[str, Any]],
    role_weights: dict[str, float],
    segment_start: float,
    segment_end: float,
    segment_index: int,
    spherical_landmarks: dict[str, dict[str, float]],
    use_recorded_360: bool,
    recorded_moves: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pick the best source while avoiding consecutive near-identical framing (Issue C).

    Consecutive segments from the same source are only acceptable when their framing
    differs meaningfully: for 360 sources the shot type must differ; for non-360 sources
    the same source is already barred by _choose_source.  When the best candidate would
    produce near-identical framing to the previous segment, the next-best candidate that
    does not is preferred.  If no alternative exists the best candidate is kept.
    """
    # Build a ranked list of all candidates.
    usage_counts_copy = dict(usage_counts)
    role_weights_copy = dict(role_weights)
    ranked: list[dict[str, Any]] = []
    # Produce a sorted list by calling _choose_source iteratively isn't clean; instead
    # replicate the sort key inline.
    def score(src: dict[str, Any]) -> tuple:
        role = _source_role(src)
        target_share = max(0.001, float(role_weights_copy.get(role, role_weights_copy.get("handheld", 0.3))))
        chosen_seconds = float((selection_stats.get(_source_id(src)) or {}).get("chosen_seconds") or 0.0)
        director_bonus = float(src.get("director_segment_score") or 1.0) if role == "handheld" else 1.0
        # Penalise repeating the previous source (same as _choose_source does via candidate filter)
        same_as_prev = 1 if _source_id(src) == previous_source else 0
        return (same_as_prev, chosen_seconds / target_share, usage_counts_copy.get(_source_id(src), 0), -director_bonus, -float(src.get("confidence") or 0.0), _source_id(src))

    usable = [src for src in sources if float(role_weights_copy.get(_source_role(src), role_weights_copy.get("handheld", 0.3))) > 0.0] or list(sources)
    ranked = sorted(usable, key=score)

    if not ranked:
        return _choose_source(sources, previous_source, usage_counts, selection_stats, role_weights)

    for candidate in ranked:
        framing = _predict_framing(candidate, segment_start, segment_end, segment_index, spherical_landmarks, use_recorded_360, recorded_moves)
        if previous_framing is None or not _framing_nearly_identical(framing, previous_framing):
            return candidate

    # All candidates produce identical framing — fall back to the best-ranked one.
    return ranked[0]


def _predict_framing(
    source: dict[str, Any],
    segment_start: float,
    segment_end: float,
    segment_index: int,
    spherical_landmarks: dict[str, dict[str, float]],
    use_recorded_360: bool,
    recorded_moves: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return a framing descriptor for what this source would look like as the next segment."""
    role = _source_role(source)
    if role == "360":
        if use_recorded_360:
            recorded = recorded_move_covering(recorded_moves, segment_start, segment_end)
            if recorded:
                return {"source": _source_id(source), "type": "recorded_move", "take": recorded.get("name") or ""}
        shots = _available_spherical_shots(spherical_landmarks)
        # We cannot predict usage at this point without side-effects; use the shot order
        # position as a proxy (it will be weighted the same way).
        shot_type = str(shots[0].get("type") or "") if shots else "unknown"
        return {"source": _source_id(source), "type": "spherical", "shot": shot_type}
    # For non-360 sources, the source identity alone determines "sameness" since
    # ken_burns seeds differ by index and iPhone/Sony framing changes continuously.
    return {"source": _source_id(source), "type": role}


def _framing_descriptor(source: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    """Return the actual framing descriptor for a segment that has been fully built."""
    role = _source_role(source)
    shot = segment.get("spherical_shot")
    if shot and role == "360":
        shot_type = str(shot.get("type") or "")
        if shot_type == "recorded_move":
            return {"source": _source_id(source), "type": "recorded_move", "take": shot.get("recorded_take") or ""}
        return {"source": _source_id(source), "type": "spherical", "shot": shot_type,
                "yaw": float(shot.get("yaw") or 0.0), "fov": float(shot.get("fov") or 100.0)}
    return {"source": _source_id(source), "type": role}


def _framing_nearly_identical(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Return True when two framing descriptors represent consecutively identical shots.

    Rules:
    - Different source → never identical.
    - Same source, non-360 role → identical (same camera, same framing, no motion).
    - Same source, 360 recorded_move → never identical (curves are always different).
    - Same source, 360 spherical: identical when same shot type AND yaw within 5° AND fov within 10%.
    """
    if a.get("source") != b.get("source"):
        return False
    role_a = a.get("type")
    role_b = b.get("type")
    # recorded_move takes always differ (continuous curve changes)
    if role_a == "recorded_move" or role_b == "recorded_move":
        return False
    if role_a == "spherical" and role_b == "spherical":
        if a.get("shot") != b.get("shot"):
            return False
        yaw_a = float(a.get("yaw") or 0.0)
        yaw_b = float(b.get("yaw") or 0.0)
        yaw_delta = abs(((yaw_a - yaw_b + 180.0) % 360.0) - 180.0)
        fov_a = float(a.get("fov") or 100.0)
        fov_b = float(b.get("fov") or 100.0)
        fov_ratio = abs(fov_a - fov_b) / max(fov_a, fov_b, 1.0)
        return yaw_delta < 5.0 and fov_ratio < 0.10
    # Same non-360 source back-to-back (the _choose_source candidate filter already
    # mostly prevents this; this catches edge cases where only one source is available).
    return role_a == role_b


def _segment_from_source(source: dict[str, Any], start: float, end: float, title: str) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    start = _round_to_frame(start)
    end = max(start + 1.0 / EDIT_FPS, _round_to_frame(end))
    return {
        "title": title,
        "clip_path": source["path"],
        "source_path": source.get("source_path") or source["path"],
        "clip_start_sec": max(0.0, start - offset),
        "master_start_sec": start,
        "duration_sec": max(1.0 / EDIT_FPS, end - start),
        "clip_offset_sec": offset,
        "confidence": float(source.get("confidence") or 0.0),
        "filename": source.get("filename") or Path(str(source["path"])).name,
        "projection": source.get("projection"),
    }


def _camera_role_weights(settings: dict[str, Any] | None) -> dict[str, float]:
    raw = (settings or {}).get("camera_role_weights") or {}
    weights = {**DEFAULT_CAMERA_ROLE_WEIGHTS}
    for key in weights:
        try:
            weights[key] = max(0.0, float(raw.get(key, weights[key])))
        except (TypeError, ValueError):
            pass
    total = sum(weights.values()) or 1.0
    return {key: value / total for key, value in weights.items()}


def _source_role(source: dict[str, Any]) -> str:
    projection = str(source.get("projection") or "").lower()
    filename = str(source.get("filename") or source.get("path") or source.get("source_path") or "").lower()
    if projection in {"equirect", "raw_insv"} or filename.endswith(".insv") or "360" in filename:
        return "360"
    if "iphone" in filename or filename.endswith(".mov"):
        return "fixed_rear"
    return "handheld"


def _apply_operator_avoidance(segment: dict[str, Any], source: dict[str, Any], samples_cache: dict[str, list[dict[str, Any]]]) -> None:
    """Nudge a segment's framing away from a detected camera operator, if any.

    Reads cached per-clip detections only (never runs detection here — that
    happens once during ingest); Sony (handheld) is skipped entirely.
    """
    role = _source_role(source)
    if role not in {"360", "fixed_rear"}:
        return
    # A landmark preview has no segment timestamp, so it cannot reproduce a
    # time-varying operator-avoidance yaw shift. Keep 360 landmark coordinates
    # identical in the plan, preview, and renderer; fixed-rear framing keeps
    # the avoidance adjustment.
    if role == "360":
        return
    analysis_path = str(source.get("path") or "")
    if not analysis_path:
        return
    if analysis_path not in samples_cache:
        samples_cache[analysis_path] = load_cached_operator_presence(analysis_path)
    samples = samples_cache[analysis_path]
    if not samples:
        return
    current_yaw = float((segment.get("spherical_shot") or {}).get("yaw") or 0.0)
    adjustment = avoidance_for_segment(role, samples, float(segment["clip_start_sec"]), float(segment["duration_sec"]), current_yaw)
    if not adjustment:
        return
    segment["operator_avoidance"] = adjustment
    if adjustment["type"] == "yaw_shift":
        shot = segment.get("spherical_shot")
        if not shot:
            return
        shift = float(adjustment["yaw_deg"])
        shot["yaw"] = (float(shot.get("yaw") or 0.0) + shift) % 360.0
        for sample in shot.get("curve") or []:
            sample["yaw"] = (float(sample.get("yaw") or 0.0) + shift) % 360.0
    elif adjustment["type"] == "zoom_crop":
        segment["motion"] = {"type": "zoom_crop", "zoom": adjustment["zoom"], "cx": adjustment["cx"], "cy": adjustment["cy"]}


def _ken_burns_motion(index: int) -> dict[str, Any]:
    rng = random.Random(stable_fingerprint({"fixed_camera_motion": index}))
    targets = [
        (0.5, 0.5, 5),
        (0.44, 0.5, 1),
        (0.56, 0.5, 1),
        (0.5, 0.42, 1),
        (0.5, 0.58, 1),
    ]
    total = sum(weight for _x, _y, weight in targets)
    pick = rng.uniform(0.0, total)
    pan_x, pan_y = 0.5, 0.5
    cursor = 0.0
    for x, y, weight in targets:
        cursor += weight
        if pick <= cursor:
            pan_x, pan_y = x, y
            break
    zoom_delta = rng.uniform(0.05, 0.11)
    zoom_in = rng.random() < 0.58
    return {
        "type": "ken_burns",
        "zoom_start": round(1.0 if zoom_in else 1.0 + zoom_delta, 3),
        "zoom_end": round(1.0 + zoom_delta if zoom_in else 1.0, 3),
        "pan_x": round(pan_x, 3),
        "pan_y": round(pan_y, 3),
    }


def _round_to_frame(seconds: float, fps: float = EDIT_FPS) -> float:
    return round(round(float(seconds) * fps) / fps, 6)


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("source_path") or source.get("path") or source.get("filename"))


def _selection_stats_template(sources: list[dict[str, Any]], window_start: float, window_end: float) -> dict[str, dict[str, Any]]:
    return {_source_id(source): _selection_stats_for_source(source, window_start, window_end) for source in sources}


def _selection_stats_for_source(source: dict[str, Any], window_start: float, window_end: float) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    duration = float(source.get("duration_sec") or 0.0)
    covered_seconds = max(0.0, min(window_end, offset + duration) - max(window_start, offset))
    return {
        "filename": source.get("filename") or Path(str(source.get("path") or source.get("source_path") or "")).name,
        "path": source.get("path"),
        "source_path": source.get("source_path"),
        "confidence": float(source.get("confidence") or 0.0),
        "offset_sec": offset,
        "duration_sec": duration,
        "covered_seconds": covered_seconds,
        "eligible_segments": 0,
        "eligible_seconds": 0.0,
        "chosen_segments": 0,
        "chosen_seconds": 0.0,
        "director_rejected_segments": 0,
        "director_reject_reasons": {},
        "director_score_sum": 0.0,
        "director_score_windows": 0,
    }


def _finalize_selection_stats(stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for item in stats.values():
        entry = dict(item)
        entry["covered_seconds"] = round(float(entry.get("covered_seconds") or 0.0), 3)
        entry["eligible_seconds"] = round(float(entry.get("eligible_seconds") or 0.0), 3)
        entry["chosen_seconds"] = round(float(entry.get("chosen_seconds") or 0.0), 3)
        score_windows = int(entry.pop("director_score_windows", 0) or 0)
        score_sum = float(entry.pop("director_score_sum", 0.0) or 0.0)
        if score_windows:
            entry["director_average_score"] = round(score_sum / score_windows, 3)
        entry["director_rejected_segments"] = int(entry.get("director_rejected_segments") or 0)
        if entry["eligible_segments"] and not entry["chosen_segments"]:
            entry["selection_reason"] = "eligible but not selected by camera rotation"
        elif not entry["eligible_segments"]:
            entry["selection_reason"] = "not covering selected edit intervals"
        else:
            entry["selection_reason"] = f"chosen {entry['chosen_segments']} of {entry['eligible_segments']} eligible segments"
        if entry["director_rejected_segments"]:
            reasons = entry.get("director_reject_reasons") or {}
            reason_text = " / ".join(sorted(reasons, key=lambda key: (-reasons[key], key))[:3])
            entry["selection_reason"] += f"; Sony quality rejected {entry['director_rejected_segments']} windows"
            if reason_text:
                entry["selection_reason"] += f": {reason_text}"
        finalized.append(entry)
    return sorted(finalized, key=lambda item: str(item.get("filename") or ""))


def _camera_usage(segments: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for segment in segments:
        name = Path(str(segment.get("clip_path"))).name
        usage[name] = usage.get(name, 0) + 1
    return usage


def _fmt_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
