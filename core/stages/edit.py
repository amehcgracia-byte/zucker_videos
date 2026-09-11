"""Beat-aligned edit decision generation."""

from __future__ import annotations

import json
import logging
import math
import random
from pathlib import Path
from typing import Any

from core.camera_moves import load_camera_moves, recorded_move_covering, recorded_shot_for_segment
from core.messages import t
from core.operator_avoidance import OPERATOR_AVOIDANCE_VERSION, avoidance_for_segment, load_cached_operator_presence
from core.reel_framing import REEL_FRAMING_VERSION, subject_box_for_window
from core.non_music import NON_MUSIC_VERSION, analyze_non_music_sources
from core.project import Project
from core.shot_quality import DIRECTOR_SCORE_THRESHOLD, SHOT_QUALITY_VERSION, analyze_handheld_director_quality, director_quality_for_segment
from core.stages.base import ProgressCallback, Stage, artifact_path, stable_fingerprint, write_artifact_json
from core.stages.cut import _clip_master_ranges, load_coverage


LOGGER = logging.getLogger(__name__)

MIN_SEGMENT_SEC = 2.0
MAX_SEGMENT_SEC = 6.0
MAX_BARS_PER_SEGMENT = 2
EDIT_FPS = 30.0
DEFAULT_CAMERA_ROLE_WEIGHTS = {"360": 50.0, "handheld": 30.0, "fixed_rear": 20.0}
SPHERICAL_PAN_SEC = 0.45  # legacy plan field; sweep timing is angular-speed based
SPHERICAL_MOTION_PLAN_VERSION = 10
# Reel has its own pacing contract.  Keep this independent from the
# YouTube/360 segment limits so a Reel change cannot invalidate or alter their
# edit cadence accidentally.
# Reel pacing is intentionally independent from the long-form edit cadence.
# The slot count is also source-aware: a project with more usable sources must
# get more visual decisions, while still allowing very short slots when the
# user asks for a short Reel containing many sources.
REEL_MIN_CUT_SEC = 0.5
REEL_MAX_CUT_SEC = 2.0
REEL_DEFAULT_CUT_TARGET_SEC = 1.75
REEL_DEFAULT_CUTS_PER_SOURCE = 1.0
REEL_SINGLE_SOURCE_MIX_INTERVAL_SEC = 5.0
REEL_PLAN_VERSION = 5
MOTION_CATALOG = (
    "full_static", "full_zoom_in", "zoom_in_center", "zoom_out_center", "zoom_in", "zoom_out",
    "pan_right_center", "pan_left_center", "pan_down_center", "pan_up_center",
)
# Selection is weighted per cut, rather than merely exposing every option in
# the catalogue.  In particular, moves toward the subject are preferred, and
# the two explicit centred recipes have enough weight to be visible in real
# plans instead of only in unit-test sequences.
MOTION_WEIGHTS = {
    # Kept in the catalogue for backwards-compatible recipe diagnostics;
    # production fixed-camera calls pass allow_static=False.
    "full_static": 1.5,
    "full_zoom_in": 2.0,
    "zoom_in_center": 4.0,
    "zoom_in": 4.0,
    "zoom_out_center": 1.5,
    "zoom_out": 1.5,
    "pan_right_center": 1.0,
    "pan_left_center": 1.0,
    "pan_down_center": 1.0,
    "pan_up_center": 1.0,
}
# The previous 1.32x fast setting was too abrupt.  Normal is now the fastest
# authored speed, and a new very-slow option keeps close-ups composed.
MOTION_SPEEDS = (("very_slow", 0.50), ("slow", 0.72), ("fast", 1.0))
IPHONE_CROP_TOP_LIMIT = 0.80
# Bump this whenever the YouTube camera-choice invariant changes so an older
# cached edit plan cannot keep producing the previous camera runs.
YOUTUBE_CAMERA_SELECTION_VERSION = 15
# Legacy diagnostic threshold retained in project settings/manifests. The
# production policy now stops close-up filler as soon as one alternative
# physical camera covers the same synced window.
DEFAULT_FIXED_CAMERA_ZOOM_COVERAGE_THRESHOLD = 2
# A fixed camera is a gap filler only when it is the sole usable source for
# the interval.  With any alternative available its movement is deliberately
# tiny: the full frame is the editorial baseline, not a 3x crop.
FIXED_CAMERA_GENTLE_ZOOM_FRACTION = 0.15
FIXED_CAMERA_GENTLE_ZOOM_MIN = 1.10
FIXED_CAMERA_GENTLE_ZOOM_MAX = 1.20
MAX_CONSECUTIVE_CAMERA_SEGMENTS = 2
# Retained as a versioned emergency switch for diagnostics; normal builds use
# the shared gentle hold/sweep motion below.
FORCE_STATIC_360_ISOLATION = True
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
SPHERICAL_NORMAL_FOV_MAX = 300.0
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
EDIT_PLAN_ALGORITHM_VERSION = 16
# Editorial targets for the measured 360 landmarks.  The remaining 20% is
# assigned to every other available landmark in equal relative shares.
DEFAULT_SPHERICAL_TARGET_WEIGHTS = {
    "singer": 0.60,
    "full_stage": 0.10,
    "audience": 0.10,
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
        coverage_payload: dict[str, Any] | None = None
        try:
            coverage_payload = load_coverage(project)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        platform = str(
            (coverage_payload or {}).get("platform")
            or project.data.get("settings", {}).get("wizard", {}).get("platform")
            or "youtube"
        ).lower()
        return stable_fingerprint(
            {
                # Follow coverage.json itself so Edit cannot reuse a plan
                # built from a previous sync/cut pass.
                "cut": coverage_payload,
                "settings": project.data["settings"].get(self.name, {}),
                "spherical_landmarks": project.data["settings"].get("spherical_landmarks", {}),
                "camera_moves": _camera_moves_fingerprint(project),
                "shot_quality_version": SHOT_QUALITY_VERSION,
                "director_score_threshold": DIRECTOR_SCORE_THRESHOLD,
                "operator_avoidance_version": OPERATOR_AVOIDANCE_VERSION,
                # Mode-specific recipes must not invalidate unrelated edit
                # plans.  A Reel pacing change is not a YouTube camera-choice
                # change, and a 360 spherical recipe change is not a Reel one.
                "mode_algorithm_versions": {
                    "reel": {
                        "plan": REEL_PLAN_VERSION,
                        "framing": REEL_FRAMING_VERSION,
                    } if platform == "reel" else None,
                    "youtube": YOUTUBE_CAMERA_SELECTION_VERSION if platform == "youtube" else None,
                    "360": SPHERICAL_MOTION_PLAN_VERSION if platform == "360" else None,
                },
                "non_music_version": NON_MUSIC_VERSION,
                "algorithm_version": EDIT_PLAN_ALGORITHM_VERSION,
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
        # 360 direction is fixed automatic configuration. Director takes are
        # no longer part of the product and must never affect a new plan.
        recorded_moves: list[dict[str, Any]] = []
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
            coverage = {**coverage, "singing_segments": _detect_singing_segments(project, coverage)}
            plan = _youtube_multicam_plan(coverage, beats, project.data.get("settings", {}), recorded_moves=recorded_moves)
        write_artifact_json(artifact_path(project, "beats.json"), beats)
        plan["edit_plan_algorithm_version"] = EDIT_PLAN_ALGORITHM_VERSION
        validate_plan_camera_source_consistency(plan)
        write_artifact_json(artifact_path(project, "edit_plan.json"), plan)
        progress_callback(100, t("edit_plan_ready"))
        return self.outputs(project)


def load_edit_plan(project: Project) -> dict[str, Any]:
    """Load edit_plan.json."""
    with artifact_path(project, "edit_plan.json").open("r", encoding="utf-8") as fh:
        plan = json.load(fh)
    validate_plan_camera_source_consistency(plan)
    return plan


def _load_or_analyze_beats(project: Project, coverage: dict[str, Any], progress_callback: ProgressCallback) -> dict[str, Any]:
    path = artifact_path(project, "beats.json")
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if cached.get("fingerprint") == _beat_fingerprint(project, coverage):
            progress_callback(45, t("rhythm_cached"))
            return cached
    master = project.data.get("inputs", {}).get("master")
    audio_path = str((master or {}).get("path") or "")
    if not audio_path:
        videos = project.data.get("inputs", {}).get("videos") or []
        if coverage.get("platform") == "reel" and len(videos) == 1:
            source = videos[0]
            if (source.get("probe") or {}).get("audio_codec"):
                audio_path = str(source.get("path") or "")
    if not audio_path:
        raise ValueError(t("missing_master_for_edit"))
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    duration = max(1.0, float(window.get("duration_sec") or 1.0))
    try:
        import librosa

        y, sr = librosa.load(audio_path, sr=22050, mono=True, offset=start, duration=duration)
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


def _detect_singing_segments(project: Project, coverage: dict[str, Any]) -> list[dict[str, Any]]:
    """Return conservative sung windows using the existing master analysis."""
    master = project.data.get("inputs", {}).get("master") or {}
    path = str(master.get("path") or "")
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    duration = max(0.0, float(window.get("duration_sec") or 0.0))
    if not path or not Path(path).exists() or duration <= 0.0:
        return []
    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(path, sr=11025, mono=True, offset=start, duration=duration)
        if len(y) < sr:
            return []
        harmonic, _percussive = librosa.effects.hpss(y)
        hop = 512
        rms = librosa.feature.rms(y=harmonic, hop_length=hop)[0]
        threshold = max(float(np.percentile(rms, 55)), 0.015)
        active = rms >= threshold
        raw: list[tuple[float, float]] = []
        for index, value in enumerate(active):
            if not value:
                continue
            local_start = index * hop / sr
            local_end = min(duration, (index + 1) * hop / sr)
            if raw and local_start <= raw[-1][1] + 0.35:
                raw[-1] = (raw[-1][0], local_end)
            else:
                raw.append((local_start, local_end))
        return [
            {"start_sec": round(start + begin, 3), "end_sec": round(start + end, 3), "classification": "singing"}
            for begin, end in raw
            if end - begin >= 1.0
        ]
    except Exception:
        LOGGER.warning("Singing analysis unavailable; continuing without singer preference", exc_info=True)
        return []


def _beat_fingerprint(project: Project, coverage: dict[str, Any]) -> str:
    inputs = project.data.get("inputs", {})
    master = inputs.get("master")
    audio = master
    if not audio and coverage.get("platform") == "reel" and len(inputs.get("videos") or []) == 1:
        source = inputs["videos"][0]
        if (source.get("probe") or {}).get("audio_codec"):
            audio = {"path": source.get("path"), "probe": source.get("probe")}
    return stable_fingerprint({"audio": audio, "window": coverage.get("window")})


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
    scored = analyze_non_music_sources(project, scored)
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

    # One video is a continuous Reel source, not a one-camera multicam edit.
    # Keep the take intact and align its source position with the selected
    # audio window. Aspect ratio is applied later by the export renderer.
    if len(sources) == 1 and coverage.get("single_source_reel") is True:
        source = sources[0]
        source_duration = max(0.0, float(source.get("duration_sec") or 0.0))
        warnings = list(coverage.get("warnings") or [])
        clip_start = start if source_duration >= start + duration else 0.0
        if clip_start == 0.0 and start > 0.0:
            warnings.append(
                f"The video is shorter than the selected audio offset ({start:.1f}s); "
                "the video starts at 0s while the audio keeps the requested offset."
            )
        if source_duration > 0 and clip_start + duration > source_duration:
            duration = max(0.1, source_duration - clip_start)
            warnings.append(
                f"The video covers only {duration:.1f}s of the requested Reel window; "
                "the export was trimmed to the available video duration."
            )
        # Mix is still one continuous cut: split only the renderer's work into
        # adjacent, treatment-only intervals so the framing can alternate
        # inside the cut. The source and audio timeline remain contiguous.
        interval_count = 1
        if str(wizard.get("reel_aspect") or "9:16") == "mix_vertical_horizontal":
            interval_count = max(3, math.ceil(duration / REEL_SINGLE_SOURCE_MIX_INTERVAL_SEC))
        interval_duration = duration / interval_count
        segments = []
        for index in range(interval_count):
            segment_duration = interval_duration if index < interval_count - 1 else duration - interval_duration * index
            segments.append({
                "title": window.get("title") or t("full_video"),
                "clip_path": source.get("path") or source.get("clip_path"),
                "source_path": source.get("source_path") or source.get("path") or source.get("clip_path"),
                "clip_start_sec": round(clip_start + interval_duration * index, 6),
                "master_start_sec": round(start + interval_duration * index, 6),
                "duration_sec": round(segment_duration, 6),
                "clip_offset_sec": 0.0,
                "filename": source.get("filename") or Path(str(source.get("path") or "video")).name,
                "projection": source.get("projection"),
                "camera_id": _camera_id(source),
                "single_source_continuous": True,
                "single_source_continuous_group": "single-source-reel",
            })
        _assign_reel_mix_treatments(segments, [source], wizard)
        return {
            "stage": "edit",
            "platform": "reel",
            "reel_plan_version": REEL_PLAN_VERSION,
            "reel_duration_sec": round(duration, 6),
            "reel_aspect": str(wizard.get("reel_aspect") or "9:16"),
            "reel_mix_vertical_ratio": wizard.get("reel_mix_vertical_ratio", "auto"),
            "reel_text_overlays": list(wizard.get("reel_text_overlays") or []),
            "reel_image_overlays": list(wizard.get("reel_image_overlays") or []),
            "title": window.get("title") or t("full_video"),
            "real_edit_logic": "single-source Reel: continuous video aligned to the selected audio offset",
            "warnings": warnings,
            "excluded_clips": coverage.get("excluded_clips") or [],
            "clip_diagnostics": coverage.get("clip_diagnostics") or [],
            "camera_usage": _camera_usage(segments),
            "cut_count": 0,
            "segments": segments,
        }

    # Preserve ingest order.  Reel is deliberately unsynchronised and its
    # visual rhythm comes from cycling through every available Drop box
    # source, rather than allowing quality sorting to starve quieter cameras.
    sources = [source for source in sources if source.get("path") or source.get("source_path")]
    # Use evenly sized slots.  The target is configurable, and the source
    # relation is a hard lower bound so every usable source contributes at
    # least one cut.  This deliberately permits sub-second cuts when a short
    # Reel contains more sources than can fit at the normal pacing target.
    try:
        target_cut_sec = float(wizard.get("reel_cut_target_sec") or REEL_DEFAULT_CUT_TARGET_SEC)
    except (TypeError, ValueError):
        target_cut_sec = REEL_DEFAULT_CUT_TARGET_SEC
    target_cut_sec = max(REEL_MIN_CUT_SEC, min(REEL_MAX_CUT_SEC, target_cut_sec))
    try:
        cuts_per_source = float(wizard.get("reel_cuts_per_source") or REEL_DEFAULT_CUTS_PER_SOURCE)
    except (TypeError, ValueError):
        cuts_per_source = REEL_DEFAULT_CUTS_PER_SOURCE
    cuts_per_source = max(1.0, min(5.0, cuts_per_source))
    duration_slots = max(
        1,
        math.ceil((end - start) / target_cut_sec),
        math.ceil(len(sources) * cuts_per_source),
        len(sources),
    )
    slot_duration = (end - start) / duration_slots
    boundaries = [start + slot_duration * index for index in range(duration_slots + 1)]

    landmarks = migrate_spherical_landmarks(settings.get("spherical_landmarks") or {})
    spherical_shots = _available_spherical_shots(landmarks, sweep_enabled=False)
    segments: list[dict[str, Any]] = []
    previous_source = None
    fixed_index = 0
    for index, (master_start, master_end) in enumerate(zip(boundaries, boundaries[1:])):
        seg_duration = max(0.1, master_end - master_start)
        source = sources[index % len(sources)]
        # With more than one source, the round-robin index guarantees that a
        # source is not repeated until all other sources have had a turn.
        if len(sources) > 1 and _source_id(source) == previous_source:
            source = sources[(index + 1) % len(sources)]
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
            "camera_id": _camera_id(source),
        }
        role = _source_role(source)
        if role == "360":
            shot = dict(spherical_shots[index % len(spherical_shots)]) if spherical_shots else {
                "type": "promo_360", "label": "360 promo", "yaw": 0.0, "pitch": 0.0, "fov": 95.0, "weight": 1.0,
            }
            segment["spherical_shot"] = _spherical_motion_profile(shot, index, enabled=False, hold_motion="none")
        elif role == "fixed_rear" and bool(wizard.get("fixed_rear_motion", True)):
            target_x, target_y = _reel_target_for_source(source, clip_start, seg_duration)
            segment["motion"] = _ken_burns_motion(
                fixed_index,
                target_x,
                target_y,
                allow_static=False,
                force_full_zoom=(fixed_index % 2 == 0),
                force_close=(fixed_index % 2 == 1),
            )
            fixed_index += 1
        elif role == "handheld":
            target_x, target_y = _reel_target_for_source(source, clip_start, seg_duration)
            segment["reel_subject_center"] = {"x": target_x, "y": target_y}
        if role == "fixed_rear" and not segment.get("motion"):
            target_x, target_y = _reel_target_for_source(source, clip_start, seg_duration)
            segment["reel_subject_center"] = {"x": target_x, "y": target_y}
        segments.append(segment)
    _assign_reel_mix_treatments(segments, sources, wizard)
    _validate_motion_segments(segments)
    return {
        "stage": "edit",
        "platform": "reel",
        "reel_plan_version": REEL_PLAN_VERSION,
        "reel_duration_sec": round(duration, 6),
        "reel_aspect": str(wizard.get("reel_aspect") or "9:16"),
        "reel_mix_vertical_ratio": wizard.get("reel_mix_vertical_ratio", "auto"),
        "reel_text_overlays": list(wizard.get("reel_text_overlays") or []),
        "reel_image_overlays": list(wizard.get("reel_image_overlays") or []),
        "title": window.get("title") or t("full_video"),
        # Keep the user's master selection as an explicit timeline anchor;
        # export must not infer it from whichever segment happens to sort first.
        "master_window_start_sec": round(start, 6),
        "master_window_end_sec": round(end, 6),
        "real_edit_logic": "reel unsynchronised promo: independent dynamic source selection on master beats",
        "warnings": coverage.get("warnings") or [],
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "camera_usage": _camera_usage(segments),
        "cut_count": max(0, len(segments) - 1),
        "segments": segments,
    }


def _reel_framing_confidence(source: dict[str, Any], clip_start_sec: float, duration_sec: float) -> float:
    """Score whether the existing Reel framing analysis can hold a subject.

    This is deliberately a read-only consumer of the cached detector output.
    It never runs detection during edit or export.  A score of zero means the
    source has no usable subject evidence for this cut.
    """
    profile = source.get("reel_framing") or {}
    if not isinstance(profile, dict):
        return 0.0
    end = float(clip_start_sec) + max(0.0, float(duration_sec))
    samples = [
        sample for sample in profile.get("samples") or []
        if float(sample.get("t") or 0.0) >= float(clip_start_sec) - 0.35
        and float(sample.get("t") or 0.0) <= end + 0.35
    ]
    confidences = [
        float(box.get("confidence") or 0.0)
        for sample in samples
        for box in sample.get("boxes") or []
        if float(box.get("confidence") or 0.0) >= 0.20
    ]
    if not confidences:
        return 0.0
    # Two or more observations cover a normal 1.5–2s cut.  Sparse evidence is
    # still useful, but is weighted down so a wide/general shot prefers blur.
    coverage = min(1.0, len(samples) / 2.0)
    return round(sum(confidences) / len(confidences) * coverage, 4)


def _assign_reel_mix_treatments(
    segments: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    wizard: dict[str, Any],
) -> None:
    """Assign the new editorial vertical/horizontal treatment per cut.

    ``mix`` remains the native-geometry mode and is intentionally untouched.
    The new mode uses the existing subject framing confidence by default, with
    a small deterministic alternation guard so a run of otherwise good
    detections does not become visually monotonous.
    """
    if str(wizard.get("reel_aspect") or "9:16") != "mix_vertical_horizontal":
        return
    source_by_path = {str(source.get("path") or source.get("source_path") or source.get("clip_path") or ""): source for source in sources}
    scored: list[float] = []
    for segment in segments:
        key = str(segment.get("clip_path") or segment.get("source_path") or "")
        source = source_by_path.get(key) or next(
            (item for item in sources if str(item.get("path") or item.get("source_path") or item.get("clip_path") or "") == key),
            {},
        )
        score = _reel_framing_confidence(source, float(segment.get("clip_start_sec") or 0.0), float(segment.get("duration_sec") or 0.0))
        scored.append(score)
        segment["reel_mix_confidence"] = score

    ratio = wizard.get("reel_mix_vertical_ratio", "auto")
    selected: set[int] = set()
    if str(ratio).lower() != "auto":
        try:
            target_count = max(0, min(len(segments), round(len(segments) * float(ratio))))
        except (TypeError, ValueError):
            target_count = round(len(segments) * 0.5)
        selected = {index for index, _ in sorted(enumerate(scored), key=lambda item: (-item[1], item[0]))[:target_count]}
    else:
        selected = {index for index, score in enumerate(scored) if score >= 0.45}
        # Confidence remains the first choice.  If all cuts have the same
        # confidence class, introduce a deterministic rhythmic change; this
        # is an editorial treatment, not a claim that the source geometry
        # changed.
        if len(segments) >= 3:
            if not selected:
                selected.update(index for index in range(0, len(segments), 2))
            elif len(selected) == len(segments):
                selected.difference_update(index for index in range(1, len(segments), 2))
            for index in range(2, len(segments)):
                previous = index - 1 in selected
                previous_previous = index - 2 in selected
                if previous and previous_previous:
                    selected.discard(index)
                elif not previous and not previous_previous:
                    selected.add(index)

    for index, segment in enumerate(segments):
        segment["reel_mix_treatment"] = "vertical" if index in selected else "horizontal"


def _youtube_multicam_plan(
    coverage: dict[str, Any],
    beats: dict[str, Any],
    settings: dict[str, Any] | None = None,
    recorded_moves: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    platform = str(coverage.get("platform") or "youtube")
    window = coverage.get("window") or {}
    start = float(window.get("start_sec") or 0.0)
    end = start + max(1.0, float(window.get("duration_sec") or 1.0))
    # The effective cut window is authoritative.  Keep a second explicit cap
    # here so a stale/overlong beat list can never select material after the
    # real song/trim end.
    trim_end = window.get("trim_end_sec")
    if trim_end is not None:
        try:
            end = min(end, float(trim_end))
        except (TypeError, ValueError):
            pass
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
    non_music_fill_history: list[dict[str, Any]] = []
    gaps: list[dict[str, float]] = []
    previous_source: str | None = None
    previous_camera: str | None = None
    consecutive_camera_segments = 0
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
    camera_target_weights = _camera_target_weights(sources, role_weights, edit_settings)
    spherical_target_weights = _spherical_target_weights(edit_settings)
    fixed_rear_motion = bool(edit_settings.get("fixed_rear_motion", True))
    try:
        fixed_zoom_coverage_threshold = max(
            1,
            int(edit_settings.get(
                "fixed_camera_zoom_coverage_threshold",
                DEFAULT_FIXED_CAMERA_ZOOM_COVERAGE_THRESHOLD,
            )),
        )
    except (TypeError, ValueError):
        fixed_zoom_coverage_threshold = DEFAULT_FIXED_CAMERA_ZOOM_COVERAGE_THRESHOLD
    # Static 360 holds are the safe shipped default. Motion remains an explicit
    # project opt-in until a filter path that does not reconfigure v360 per
    # frame is available.
    spherical_motion = False
    hold_motion = str(edit_settings.get("spherical_hold_motion") or "none").lower()
    if hold_motion not in {"none", "subtle"}:
        hold_motion = "none"
    spherical_sweep = False
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
        available = _quality_filtered_sources(_covering_sources(sources, segment_start, segment_end, platform=platform), segment_start, segment_end, selection_stats)
        if not available:
            gaps.append({"start_sec": round(segment_start, 3), "end_sec": round(segment_end, 3)})
            bar_index = next_index
            continue
        for source in available:
            stats = selection_stats.setdefault(_source_id(source), _selection_stats_for_source(source, start, end))
            stats["eligible_segments"] += 1
            stats["eligible_seconds"] += segment_end - segment_start
        available_camera_ids = {_camera_id(source) for source in available}
        forced_alternatives: set[str] = set()
        if (
            previous_camera is not None
            and consecutive_camera_segments >= MAX_CONSECUTIVE_CAMERA_SEGMENTS
            and len(available_camera_ids) > 1
        ):
            forced_alternatives = {camera_id for camera_id in available_camera_ids if camera_id != previous_camera}
        # C: try ranked candidates until we find one whose framing differs from previous,
        # preventing consecutive identical shots from the same source.
        source = _choose_source_avoiding_identical_framing(
            available, previous_source, previous_framing, usage_counts, selection_stats, role_weights,
            segment_start, segment_end, segment_index, spherical_landmarks, use_recorded_360, recorded_moves or [],
            forced_alternatives=forced_alternatives,
            camera_target_weights=camera_target_weights,
            prefer_battery_camera=segment_end >= end - 0.001,
            preferred_camera_ids=_preferred_singing_camera_ids(
                available, _is_singing_window(coverage, segment_start, segment_end)
            ),
        )
        is_automatic_360 = _source_role(source) == "360" and not (
            use_recorded_360 and recorded_move_covering(recorded_moves or [], segment_start, segment_end)
        )
        if is_automatic_360 and len(available_camera_ids) == 1:
            next_index = _extend_360_hold_index(bar_times, next_index, segment_start, end, source)
            segment_end = min(end, float(bar_times[next_index]))
            # The source may not cover the longer phrase-aligned window. Keep
            # the original boundary in that case rather than inventing a gap.
        previous_source = _source_id(source)
        selected_camera = _camera_id(source)
        if selected_camera == previous_camera:
            consecutive_camera_segments += 1
        else:
            previous_camera = selected_camera
            consecutive_camera_segments = 1
        usage_counts[previous_source] = usage_counts.get(previous_source, 0) + 1
        chosen_stats = selection_stats.setdefault(previous_source, _selection_stats_for_source(source, start, end))
        chosen_stats["chosen_segments"] += 1
        chosen_stats["chosen_seconds"] += segment_end - segment_start
        segment = _segment_from_source(source, segment_start, segment_end, window.get("title") or t("full_video"), platform=platform)
        segment["camera_id"] = selected_camera
        segment["singing_detected"] = _is_singing_window(coverage, segment_start, segment_end)
        segment["available_camera_ids"] = sorted(available_camera_ids)
        alternative_camera_ids = sorted(camera_id for camera_id in available_camera_ids if camera_id != selected_camera)
        segment["camera_alternative_available"] = bool(alternative_camera_ids)
        segment["fixed_camera_alternative_count"] = len(alternative_camera_ids)
        segment["fixed_camera_zoom_coverage_threshold"] = fixed_zoom_coverage_threshold
        _apply_non_music_insert(segment, source, segment_index)
        if (
            _source_role(source) == "fixed_rear"
            and available_camera_ids <= {_camera_id(source)}
            and not any(_source_role(item) == "handheld" for item in available)
        ):
            filler = _sony_non_music_filler(sources, segment, segment_index, non_music_fill_history)
            if filler is not None:
                filler["available_camera_ids"] = list(segment.get("available_camera_ids") or [])
                filler["camera_alternative_available"] = False
                filler["iphone_gap_original_camera"] = selected_camera
                segment = filler
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
                    target_weights=spherical_target_weights,
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
            # Every fixed-camera cut receives a crop motion. The source role,
            # not the filename, is authoritative: files such as ZZ24.7 .mov
            # are fixed cameras even when they are not named "iPhone".
            target_x, target_y = _visible_iphone_target(source, float(segment.get("clip_start_sec") or 0.0), segment_end - segment_start, operator_samples_cache)
            if alternative_camera_ids:
                # One valid alternative is enough to stop treating the fixed
                # camera as a gap filler. The old threshold compared against
                # two, so a fixed camera plus one other source still got the
                # aggressive 3x recipe.
                segment["motion"] = _gentle_fixed_camera_motion(fixed_rear_motion_index)
                segment["fixed_camera_zoom_policy"] = "gentle_center_motion_sufficient_coverage"
            else:
                segment["motion"] = _ken_burns_motion(
                    fixed_rear_motion_index,
                    target_x,
                    target_y,
                    allow_static=False,
                    force_full_zoom=(fixed_rear_motion_index % 2 == 0),
                    force_close=(fixed_rear_motion_index % 2 == 1),
                )
                segment["fixed_camera_zoom_policy"] = "close_up_motion_low_coverage"
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

    _validate_motion_segments(segments)
    warnings = list(coverage.get("warnings") or [])
    if gaps:
        warnings.extend([t("no_video_between", start=_fmt_time(gap["start_sec"]), end=_fmt_time(gap["end_sec"])) for gap in gaps])
    usage: dict[str, int] = {}
    for segment in segments:
        usage[Path(str(segment.get("clip_path"))).name] = usage.get(Path(str(segment.get("clip_path"))).name, 0) + 1
    finalized_stats = _finalize_selection_stats(selection_stats, role_weights)
    for role, weight in role_weights.items():
        role_sources = [item for item in finalized_stats if item.get("role") == role]
        if weight > 0 and role_sources and not any(item.get("chosen_segments") for item in role_sources):
            warnings.append(
                f"Camera role {role} was requested at {weight * 100:.0f}% but contributed no segments: "
                + "; ".join(str(item.get("selection_reason") or "unusable") for item in role_sources)
            )
    return {
        "stage": "edit",
        "platform": coverage.get("platform") or "youtube",
        "singing_segments": coverage.get("singing_segments") or [],
        "title": window.get("title") or t("full_video"),
        "master_window_start_sec": round(start, 6),
        "master_window_end_sec": round(end, 6),
        "real_edit_logic": "youtube beat-aligned multicam v1" if (coverage.get("platform") or "youtube") == "youtube" else "reel beat-aligned multicam (vertical highlight)",
        "warnings": warnings,
        "excluded_clips": coverage.get("excluded_clips") or [],
        "clip_diagnostics": coverage.get("clip_diagnostics") or [],
        "selection_diagnostics": finalized_stats,
        "camera_distribution": _camera_distribution(finalized_stats, camera_target_weights),
        "camera_target_weights": camera_target_weights,
        "singing_camera_assignments": _singing_camera_assignments(segments),
        "non_music_bank": {
            str(source.get("filename") or source.get("path") or "source"): source.get("non_music_windows") or []
            for source in sources
            if source.get("non_music_windows")
        },
        "non_music_audio_rejections": {
            str(source.get("filename") or source.get("path") or "source"): int(source.get("non_music_audio_rejected") or 0)
            for source in sources
            if int(source.get("non_music_audio_rejected") or 0) > 0
        },
        "gaps": gaps,
        "cut_count": max(0, len(segments) - 1),
        "camera_usage": usage,
        "spherical_shot_usage": _spherical_shot_usage(segments),
        "spherical_shot_distribution": _spherical_shot_distribution(segments, spherical_target_weights),
        "spherical_target_weights": spherical_target_weights,
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
    target_weights: dict[str, float] | None = None,
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
        LOGGER.info(
            "Sony gap filler unavailable master_start=%.3f duration=%.3f history=%s",
            master_start,
            duration,
            len(history),
        )
        return None
    recent_types = recent_types or []

    def distance(shot: dict[str, Any]) -> float:
        if previous_yaw is None:
            return 0.0
        return abs(((float(shot.get("yaw") or 0.0) - previous_yaw + 180.0) % 360.0) - 180.0)

    target_weights = target_weights or {str(shot.get("type")): float(shot.get("weight") or 1.0) for shot in candidates}
    total_target = sum(max(0.0, float(target_weights.get(str(shot.get("type")), 0.0))) for shot in candidates) or 1.0

    def score(shot: dict[str, Any]) -> tuple[float, float, float, str]:
        shot_type = str(shot.get("type") or "")
        weight = max(0.001, float(target_weights.get(shot_type, 0.0)) / total_target)
        # Deficit from the configured weighted rotation is primary. A shot
        # below its target share beats a nearby shot that is already overused.
        # Start every landmark with one virtual slot. Without this prior all
        # counts are zero and the alphabetical tiebreaker starves the 60%
        # singer target before the weighted rotation has any evidence.
        weighted_deficit = (usage.get(shot_type, 0) + 1.0) / weight
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


def _spherical_target_weights(settings: dict[str, Any] | None) -> dict[str, float]:
    """Return normalized 360 shot targets, preserving the 60/10/10/20 rule.

    ``spherical_shot_target_weights`` is intentionally separate from the
    landmark ``weight`` field: the latter is a legacy per-landmark preference
    and cannot express the requested hierarchy reliably.
    """
    raw = (settings or {}).get("spherical_shot_target_weights") or {}
    targets = dict(DEFAULT_SPHERICAL_TARGET_WEIGHTS)
    for key in SPHERICAL_SHOT_ORDER:
        if key in raw:
            try:
                value = float(raw[key])
                targets[key] = max(0.0, value / 100.0 if value > 1.0 else value)
            except (TypeError, ValueError):
                continue
    explicit = sum(targets.get(key, 0.0) for key in ("singer", "full_stage", "audience"))
    remaining = max(0.0, 1.0 - explicit)
    others = [key for key in SPHERICAL_SHOT_ORDER if key not in {"singer", "full_stage", "audience", "planet"}]
    configured_other = {key for key in others if key in raw}
    if configured_other:
        configured_total = sum(targets.get(key, 0.0) for key in configured_other) or 1.0
        for key in others:
            targets[key] = remaining * targets.get(key, 0.0) / configured_total if key in configured_other else 0.0
    else:
        share = remaining / max(1, len(others))
        for key in others:
            targets[key] = share
    targets["planet"] = 0.0
    total = sum(targets.values()) or 1.0
    return {key: round(value / total, 6) for key, value in targets.items() if value > 0.0}


def _spherical_shot_distribution(segments: list[dict[str, Any]], targets: dict[str, float]) -> list[dict[str, Any]]:
    usage = _spherical_type_usage(segments)
    total = sum(usage.values()) or 1
    return [
        {
            "shot_type": shot_type,
            "cuts": int(usage.get(shot_type, 0)),
            "actual_percent": round(usage.get(shot_type, 0) / total * 100.0, 2),
            "target_percent": round(float(targets.get(shot_type, 0.0)) * 100.0, 2),
        }
        for shot_type in sorted(set(targets) | set(usage))
    ]


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


def _covering_sources(
    sources: list[dict[str, Any]], start: float, end: float, *, platform: str = "youtube"
) -> list[dict[str, Any]]:
    available = []
    for source in sources:
        if any(offset_start <= start and offset_end >= end for offset_start, offset_end, _offset in _clip_master_ranges(source, platform)):
            available.append(source)
    return available


def _quality_filtered_sources(
    sources: list[dict[str, Any]],
    start: float,
    end: float,
    selection_stats: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    filtered = []
    for source in sources:
        if _source_role(source) != "handheld":
            filtered.append(source)
            continue
        source_start = start - float(source.get("offset_sec") or 0.0)
        source_end = source_start + max(0.0, end - start)
        if any(
            window.get("dominant_person")
            and float(window.get("start_sec") or 0.0) < source_end
            and float(window.get("end_sec") or 0.0) > source_start
            for window in (source.get("non_music_windows") or [])
        ):
            stats = selection_stats.setdefault(_source_id(source), _selection_stats_for_source(source, start, end))
            stats["audio_visual_rejected"] = int(stats.get("audio_visual_rejected") or 0) + 1
            continue
        quality = director_quality_for_segment(source, start, end)
        stats = selection_stats.setdefault(_source_id(source), _selection_stats_for_source(source, start, end))
        stats.setdefault("director_quality", (source.get("director_quality") or {}).get("summary") or {})
        stats["director_score_sum"] = float(stats.get("director_score_sum") or 0.0) + float(quality.get("score") or 0.0)
        stats["director_score_windows"] = int(stats.get("director_score_windows") or 0) + 1
        if not quality.get("eligible", True):
            stats["director_rejected_segments"] = int(stats.get("director_rejected_segments") or 0) + 1
            reasons = stats.setdefault("director_reject_reasons", {})
            for reason in quality.get("reasons") or ["low director score"]:
                reasons[str(reason)] = reasons.get(str(reason), 0) + 1
        # Quality is a soft preference, not a camera-wide veto. A dominant
        # camera must still participate when it has usable synced coverage;
        # its score and rejected-window reasons remain in diagnostics.
        filtered.append({**source, "director_segment_score": quality.get("score")})
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
    role_source_ids = {_source_id(source): _source_role(source) for source in usable_sources}

    def score(source: dict[str, Any]) -> tuple[float, float, int, float, float, str]:
        role = _source_role(source)
        target_share = max(0.001, float(role_weights.get(role, role_weights.get("handheld", 0.3))))
        chosen_seconds = float((selection_stats.get(_source_id(source)) or {}).get("chosen_seconds") or 0.0)
        role_chosen_seconds = sum(
            float((selection_stats.get(source_id) or {}).get("chosen_seconds") or 0.0)
            for source_id, source_role in role_source_ids.items() if source_role == role
        )
        covered_seconds = max(1.0, float((selection_stats.get(_source_id(source)) or {}).get("covered_seconds") or 0.0))
        director_bonus = float(source.get("director_segment_score") or 1.0) if role == "handheld" else 1.0
        return (role_chosen_seconds / target_share, chosen_seconds / covered_seconds, usage_counts.get(_source_id(source), 0), -director_bonus, -float(source.get("confidence") or 0.0), _source_id(source))

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
    forced_alternatives: set[str] | None = None,
    prefer_battery_camera: bool = False,
    preferred_camera_ids: set[str] | None = None,
    camera_target_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Pick the best source while avoiding consecutive near-identical framing (Issue C).

    Consecutive segments from the same source are only acceptable when their framing
    differs meaningfully: for 360 sources the shot type must differ; for non-360 sources
    the same source is already barred by _choose_source.  When the best candidate would
    produce near-identical framing to the previous segment, the next-best candidate that
    does not is preferred.  If no alternative exists the best candidate is kept.
    """
    # Build a ranked list of all candidates. The YouTube rule is strict:
    # whenever another covered camera exists, the immediately previous camera
    # is not eligible. Repeating it is allowed only when it is the sole
    # covered source for this interval. This applies equally to 360 sources;
    # passthrough and Reel use separate paths and are untouched.
    usage_counts_copy = dict(usage_counts)
    role_weights_copy = dict(role_weights)
    preferred_camera_ids = preferred_camera_ids or set()
    camera_target_weights = camera_target_weights or {}
    role_source_ids = {_source_id(source): _source_role(source) for source in sources}
    ranked: list[dict[str, Any]] = []
    # Produce a sorted list by calling _choose_source iteratively isn't clean; instead
    # replicate the sort key inline.
    def score(src: dict[str, Any]) -> tuple:
        role = _source_role(src)
        target_share = max(0.001, float(camera_target_weights.get(_camera_id(src), role_weights_copy.get(role, role_weights_copy.get("handheld", 0.3)))))
        chosen_seconds = float((selection_stats.get(_source_id(src)) or {}).get("chosen_seconds") or 0.0)
        role_chosen_seconds = sum(
            float((selection_stats.get(source_id) or {}).get("chosen_seconds") or 0.0)
            for source_id, source_role in role_source_ids.items() if source_role == role
        )
        camera_chosen_seconds = sum(
            float((selection_stats.get(source_id) or {}).get("chosen_seconds") or 0.0)
            for source_id in role_source_ids
            if _camera_id(next(item for item in sources if _source_id(item) == source_id)) == _camera_id(src)
        )
        covered_seconds = max(1.0, float((selection_stats.get(_source_id(src)) or {}).get("covered_seconds") or 0.0))
        director_bonus = float(src.get("director_segment_score") or 1.0) if role == "handheld" else 1.0
        battery_bonus = 1 if prefer_battery_camera and _is_battery_camera(src) else 0
        singing_bonus = 1 if _camera_id(src) in preferred_camera_ids else 0
        return (-singing_bonus, camera_chosen_seconds / target_share, role_chosen_seconds / max(0.001, float(role_weights_copy.get(role, 0.3))), -battery_bonus, chosen_seconds / covered_seconds, usage_counts_copy.get(_source_id(src), 0), -director_bonus, -float(src.get("confidence") or 0.0), _source_id(src))

    usable = [src for src in sources if float(role_weights_copy.get(_source_role(src), role_weights_copy.get("handheld", 0.3))) > 0.0] or list(sources)
    if forced_alternatives:
        hard_alternatives = [src for src in usable if _camera_id(src) in forced_alternatives]
    else:
        hard_alternatives = usable
    alternatives = [src for src in hard_alternatives if _source_id(src) != previous_source]
    ranked = sorted(alternatives or hard_alternatives or usable, key=score)

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


def _segment_from_source(
    source: dict[str, Any], start: float, end: float, title: str, *, platform: str = "youtube"
) -> dict[str, Any]:
    offset = float(source.get("offset_sec") or 0.0)
    if platform == "youtube" and source.get("offset_ranges"):
        matches = [
            item[2]
            for item in _clip_master_ranges(source, platform)
            if item[0] <= start and item[1] >= min(end, start + 1.0 / EDIT_FPS)
        ]
        if matches:
            offset = matches[0]
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
        # Store the identity next to the exact render path.  This prevents a
        # later plan transformation from carrying a camera label over to a
        # different source.
        "camera_id": _camera_id(source),
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


def _camera_target_weights(sources: list[dict[str, Any]], role_weights: dict[str, float], settings: dict[str, Any] | None = None) -> dict[str, float]:
    """Expand configured role targets into physical-camera targets.

    The UI historically stores role weights, while a role can contain several
    physical cameras. Splitting a role target across its cameras prevents one
    camera from consuming the whole role allocation and makes the configured
    percentage auditable in the final manifest.
    """
    settings = settings or {}
    explicit = settings.get("camera_weights") or {}
    cameras: dict[str, str] = {}
    for source in sources:
        cameras[_camera_id(source)] = _source_role(source)
    if not cameras:
        return {}
    targets: dict[str, float] = {}
    for camera_id, role in cameras.items():
        raw = explicit.get(camera_id, explicit.get(str(camera_id).lower())) if isinstance(explicit, dict) else None
        try:
            if raw is not None:
                value = float(raw)
                targets[camera_id] = value / 100.0 if value > 1.0 else max(0.0, value)
                continue
        except (TypeError, ValueError):
            pass
        same_role = sum(1 for item in cameras.values() if item == role)
        targets[camera_id] = float(role_weights.get(role, 0.0)) / max(1, same_role)
    total = sum(targets.values()) or 1.0
    return {camera_id: value / total for camera_id, value in targets.items()}


def _source_role(source: dict[str, Any]) -> str:
    projection = str(source.get("projection") or "").lower()
    if projection in {"equirect", "raw_insv"} or source.get("raw_360") is True:
        return "360"
    explicit_role = str(source.get("camera_role") or "").strip().lower()
    if explicit_role in {"360", "fixed_rear", "handheld"}:
        return explicit_role
    if source.get("is_static_camera") is True or source.get("static_camera") is True:
        return "fixed_rear"
    camera_type = str(source.get("camera_type") or source.get("device_type") or "").lower()
    if camera_type in {"iphone", "phone", "mobile", "smartphone", "static"}:
        return "fixed_rear"
    return "handheld"


def _camera_distribution(selection_stats: list[dict[str, Any]], target_weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    total = sum(float(item.get("chosen_seconds") or 0.0) for item in selection_stats) or 1.0
    grouped: dict[str, dict[str, Any]] = {}
    for item in selection_stats:
        camera_id = _camera_id(item)
        entry = grouped.setdefault(camera_id, {"camera_id": camera_id, "filename": item.get("filename"), "role": item.get("role"), "chosen_seconds": 0.0})
        entry["chosen_seconds"] += float(item.get("chosen_seconds") or 0.0)
    return [
        {
            **entry,
            "configured_percent": round(float((target_weights or {}).get(camera_id, entry.get("configured_weight", 0.0))) * 100.0, 2),
            "chosen_seconds": round(float(entry["chosen_seconds"]), 3),
            "actual_percent": round(float(entry["chosen_seconds"]) / total * 100.0, 2),
        }
        for camera_id, entry in grouped.items()
    ]


def _singing_camera_assignments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "start_sec": round(float(segment.get("master_start_sec") or 0.0), 3),
            "end_sec": round(float(segment.get("master_start_sec") or 0.0) + float(segment.get("duration_sec") or 0.0), 3),
            "camera_id": segment.get("camera_id"),
            "source": segment.get("filename") or Path(str(segment.get("clip_path") or "")).name,
            "sung": bool(segment.get("singing_detected")),
        }
        for segment in segments
        if segment.get("singing_detected")
    ]


def _is_singing_window(coverage: dict[str, Any], start: float, end: float) -> bool:
    return any(
        float(item.get("start_sec") or 0.0) < end and float(item.get("end_sec") or 0.0) > start
        for item in (coverage.get("singing_segments") or [])
    )


def _preferred_singing_camera_ids(sources: list[dict[str, Any]], singing: bool) -> set[str]:
    if not singing:
        return set()
    sony = {_camera_id(source) for source in sources if _source_role(source) == "handheld" and "sony" in str(source.get("filename") or source.get("path") or "").lower()}
    if sony:
        return sony
    return {_camera_id(source) for source in sources if _source_role(source) == "360"}


def _reel_target_for_source(source: dict[str, Any], clip_start_sec: float, duration_sec: float) -> tuple[float, float]:
    """Return a Reel-only safe target for the current source/window.

    Fixed/iPhone recipes consume source-space subject coordinates because the
    Ken Burns solver locks the subject while zooming. Sony receives crop-space
    offsets for the 9:16 renderer, computed from the detected union box.
    """
    profile = source.get("reel_framing") or {}
    box = subject_box_for_window(profile, clip_start_sec, duration_sec)
    if not box:
        # No reliable detection: a centered full-frame fallback is safer than
        # inheriting a stale target from another source or segment.
        return 0.5, 0.5
    center_x = (float(box["x1"]) + float(box["x2"])) / 2.0
    center_y = (float(box["y1"]) + float(box["y2"])) / 2.0
    if _source_role(source) == "fixed_rear":
        return max(0.15, min(0.85, center_x)), max(0.25, min(0.75, center_y))
    # The Reel renderer is 9:16. Compute the normalized crop window in source
    # coordinates, then select the smallest offset that contains the subject
    # plus breathing room. This prevents a subject box at the edge being
    # clipped merely because its centre looks acceptable.
    probe = source.get("probe") or {}
    try:
        aspect = float(probe.get("width") or 16) / max(1.0, float(probe.get("height") or 9))
    except (TypeError, ValueError):
        aspect = 16.0 / 9.0
    output_aspect = 9.0 / 16.0
    crop_w = min(1.0, output_aspect / max(0.01, aspect))
    crop_h = min(1.0, aspect / output_aspect)
    margin_x = min(0.08, crop_w * 0.18)
    margin_y = min(0.08, crop_h * 0.12)
    left_min = max(0.0, float(box["x2"]) + margin_x - crop_w)
    left_max = min(1.0 - crop_w, float(box["x1"]) - margin_x)
    top_min = max(0.0, float(box["y2"]) + margin_y - crop_h)
    top_max = min(1.0 - crop_h, float(box["y1"]) - margin_y)
    left = (left_min + left_max) / 2.0 if left_min <= left_max else max(0.0, min(1.0 - crop_w, center_x - crop_w / 2.0))
    top = (top_min + top_max) / 2.0 if top_min <= top_max else max(0.0, min(1.0 - crop_h, center_y - crop_h / 2.0))
    return round(left / max(0.0001, 1.0 - crop_w), 4), round(top / max(0.0001, 1.0 - crop_h), 4)


def _is_iphone_source(source: dict[str, Any]) -> bool:
    """Return whether a source is the wide iPhone camera used for crop shots."""
    value = str(source.get("filename") or source.get("path") or source.get("source_path") or "").lower()
    return "iphone" in value or "img_" in value


def _is_battery_camera(source: dict[str, Any]) -> bool:
    """Identify the portable/battery camera used for preferred ending shots."""
    return _source_role(source) == "fixed_rear" and _is_iphone_source(source)


def _visible_iphone_target(
    source: dict[str, Any],
    clip_start_sec: float,
    duration_sec: float,
    samples_cache: dict[str, list[dict[str, Any]]],
) -> tuple[float, float]:
    """Choose a visible non-operator subject anchor for an iPhone crop.

    The ingest detector's secondary subject is preferred over its dominant
    figure (the latter is often a back-facing operator).  Among candidates it
    slightly prefers the right side, while still accepting singer/drummer/
    bassist positions elsewhere in the frame.
    """
    path = str(source.get("path") or source.get("source_path") or "")
    if path not in samples_cache:
        samples_cache[path] = load_cached_operator_presence(path) if path else []
    def safe(x: float, y: float) -> tuple[float, float]:
        # Keep enough breathing room that the subject cannot be clipped when
        # the close-up reaches its endpoint.
        # Keep automatic fixed-camera framing out of the ceiling/floor bands.
        # A real detected subject is still selected below, but its target is
        # kept inside the useful central composition envelope so the crop
        # cannot spend a shot on lamps or the poorly placed lower edge.
        return max(0.25, min(0.75, float(x))), max(0.35, min(0.65, float(y)))

    window = [
        sample for sample in samples_cache[path]
        if clip_start_sec - 0.5 <= float(sample.get("t") or 0.0) <= clip_start_sec + duration_sec + 0.5
    ]
    candidates = [
        sample for sample in window
        if sample.get("subject_cx") is not None and sample.get("subject_cy") is not None
    ]
    operator_samples = [
        sample for sample in window
        if float(sample.get("area_fraction") or 0.0) >= 0.08
    ]
    operator = max(operator_samples, key=lambda item: float(item.get("area_fraction") or 0.0), default=None)
    if candidates:
        if operator is not None:
            # Do not accept a secondary box that is still close to the
            # operator; this is the duplicate-detection failure seen in
            # Berlin's lower-left back-facing operator.
            candidates = [
                item for item in candidates
                if abs(float(item.get("subject_cx") or 0.5) - float(operator.get("cx") or 0.5)) >= 0.18
                or abs(float(item.get("subject_cy") or 0.5) - float(operator.get("cy") or 0.5)) >= 0.18
            ]
    if candidates:
        selected = max(
            candidates,
            key=lambda item: float(item.get("subject_area_fraction") or 0.0) + (0.08 if float(item.get("subject_cx") or 0.5) >= 0.5 else 0.0),
        )
        return safe(float(selected.get("subject_cx") or 0.5), float(selected.get("subject_cy") or 0.5))
    # A short/ dark window can have no positive detector result even though
    # neighbouring frames do. Reuse the most recent reliable subject anchor
    # before the window rather than jumping to an inherited top-heavy target.
    prior = [
        sample for sample in samples_cache[path]
        if float(sample.get("t") or 0.0) < clip_start_sec
        and sample.get("subject_cx") is not None
        and sample.get("subject_cy") is not None
        and float(sample.get("subject_area_fraction") or 0.0) >= 0.01
    ]
    if prior:
        selected = max(prior, key=lambda item: float(item.get("t") or 0.0))
        return safe(float(selected.get("subject_cx") or 0.5), float(selected.get("subject_cy") or 0.5))
    for x_key, y_key in (("subject_center_x", "subject_center_y"), ("face_center_x", "face_center_y")):
        if source.get(x_key) is not None and source.get(y_key) is not None:
            return safe(float(source[x_key]), float(source[y_key]))
    if operator is not None:
        # When no independent subject is visible, aim away from the detected
        # operator rather than falling back to the centre (which was the
        # recurrent Berlin failure). Keep the crop vertically composed.
        operator_x = float(operator.get("cx") or 0.5)
        operator_y = float(operator.get("cy") or 0.5)
        # Use a modest opposite-side bias, not an extreme corner: the Berlin
        # operator occupies the lower-left while the stage centre remains the
        # useful subject area. A corner target removed the operator but also
        # threw away the musicians.
        return safe(0.58 if operator_x < 0.5 else 0.42, 0.40 if operator_y >= 0.55 else 0.60)
    return safe(0.5, 0.5)


def _apply_non_music_insert(
    segment: dict[str, Any],
    source: dict[str, Any],
    segment_index: int,
    *,
    force: bool = False,
    preferred_window: dict[str, Any] | None = None,
) -> None:
    """Use a vetted Sony cutaway at occasional internal cut boundaries."""
    if _source_role(source) != "handheld":
        return
    windows = source.get("non_music_windows") or []
    if not windows or (not force and segment_index % 8 != 0):
        return
    duration = float(segment.get("duration_sec") or 0.0)
    eligible = [window for window in windows if float(window.get("end_sec") or 0.0) - float(window.get("start_sec") or 0.0) >= duration]
    # A cutaway containing a single visible musician is unsafe while the
    # master is musically active unless we have evidence that the pictured
    # instrument is actually being played.  The current visual detector does
    # not claim instrument-use certainty, so it rejects such candidates
    # conservatively; audience/ambient/hoguera candidates remain eligible.
    master_path = source.get("non_music_master_path")
    audio_active = False
    # A single dominant musician is never a validated cutaway. This closes
    # the alternate path where a singer close-up was selected by the normal
    # Sony source rotation instead of the periodic bank insertion.
    eligible = [window for window in eligible if not window.get("dominant_person") or window.get("instrument_in_use") is True]
    if master_path:
        from core.non_music import master_audio_is_active
        audio_active = master_audio_is_active(str(master_path), float(segment.get("master_start_sec") or 0.0), duration)
        if audio_active:
            safe_eligible = [window for window in eligible if not window.get("dominant_person") or window.get("instrument_in_use") is True]
            source["non_music_audio_rejected"] = int(source.get("non_music_audio_rejected") or 0) + (len(eligible) - len(safe_eligible))
            eligible = safe_eligible
    if not eligible:
        return
    if preferred_window is not None:
        preferred_start = float(preferred_window.get("start_sec") or 0.0)
        window = next((item for item in eligible if abs(float(item.get("start_sec") or 0.0) - preferred_start) < 0.001), None)
        if window is None:
            return
    else:
        window = eligible[(segment_index // 8) % len(eligible)]
    segment["clip_start_sec"] = round(float(window["start_sec"]) + 0.25, 6)
    segment["non_music_insert"] = True
    segment["non_music_reason"] = window.get("reason") or "non-musical visual candidate"
    segment["non_music_score"] = float(window.get("score") or 0.0)
    segment["non_music_audio_checked"] = bool(master_path)
    segment["non_music_audio_active"] = audio_active
    segment["non_music_visual_audio_safe"] = not bool(audio_active and window.get("dominant_person") and window.get("instrument_in_use") is not True)


def _sony_non_music_filler(
    sources: list[dict[str, Any]],
    segment: dict[str, Any],
    segment_index: int,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Use a varied Sony ambient candidate with a real 180-second cooldown."""
    history = history if history is not None else []
    duration = float(segment.get("duration_sec") or 0.0)
    master_start = float(segment.get("master_start_sec") or 0.0)
    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for source in sources:
        if _source_role(source) != "handheld":
            continue
        for window in source.get("non_music_windows") or []:
            if float(window.get("end_sec") or 0.0) - float(window.get("start_sec") or 0.0) < duration:
                continue
            frame_key = f"{_source_id(source)}|{float(window.get('start_sec') or 0.0):.3f}"
            if any(
                item.get("frame_key") == frame_key
                and (
                    abs(master_start - float(item.get("master_start_sec") or 0.0)) < 180.0
                    or item is history[-1]
                )
                for item in history
            ):
                continue
            candidates.append((frame_key, source, window))
    if not candidates:
        return None
    # Stable pseudo-random ordering gives variety while keeping an export
    # reproducible.  The history filter above enforces both the no-consecutive
    # repeat rule and the three-minute real-time cooldown.
    ordered = sorted(
        candidates,
        key=lambda item: stable_fingerprint({"segment": segment_index, "frame": item[0]}),
    )
    frame_key, source, window = ordered[0]
    replacement = _segment_from_source(
        source,
        float(segment.get("master_start_sec") or 0.0),
        float(segment.get("master_start_sec") or 0.0) + float(segment.get("duration_sec") or 0.0),
        str(segment.get("title") or t("full_video")),
        platform="youtube",
    )
    _apply_non_music_insert(replacement, source, segment_index, force=True, preferred_window=window)
    if not replacement.get("non_music_insert"):
        return None
    history.append({"frame_key": frame_key, "master_start_sec": master_start})
    LOGGER.info(
        "Sony gap filler selected master_start=%.3f frame=%s source_start=%.3f history=%s",
        master_start,
        frame_key,
        float(window.get("start_sec") or 0.0),
        len(history),
    )
    replacement["sony_gap_fill"] = True
    replacement["sony_gap_fill_from"] = "iphone_only_coverage"
    replacement["non_music_frame_key"] = frame_key
    replacement["non_music_source_start_sec"] = float(window.get("start_sec") or 0.0)
    return replacement


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
        # Do not replace an animated iPhone recipe with a static crop: that
        # was the regression that made recent previews lose their zoom-out.
        # The dominant detection is the likely back-facing operator; when an
        # alternate subject exists, keep that fact for downstream diagnostics
        # and retain the animated target recipe.
        segment["operator_avoidance"] = adjustment
        if not (segment.get("motion") or {}).get("type") == "ken_burns":
            segment["motion"] = {"type": "zoom_crop", "zoom": adjustment["zoom"], "cx": adjustment["cx"], "cy": adjustment["cy"]}


def _pan_for_target(zoom: float, target: float) -> float:
    if zoom <= 1.001:
        return 0.5
    return max(0.0, min(1.0, (zoom * target - 0.5) / (zoom - 1.0)))


def _minimum_pan_y_for_top_edge(zoom: float, top_limit: float = IPHONE_CROP_TOP_LIMIT) -> float:
    """Minimum crop-space Y needed to keep the crop's top edge below a limit."""
    zoom = max(1.0, float(zoom))
    return max(0.0, min(1.0, float(top_limit) + 0.5 / zoom))


def _clamp_pan_y_for_top_edge(pan_y: float, zoom: float, top_limit: float = IPHONE_CROP_TOP_LIMIT) -> float:
    return max(_minimum_pan_y_for_top_edge(zoom, top_limit), min(1.0, float(pan_y)))


def _minimum_zoom_for_target(target: float) -> float:
    edge = min(max(0.01, float(target)), 1.0 - max(0.01, float(target)))
    return max(1.0, 0.5 / edge)


def _full_frame_static_motion() -> dict[str, Any]:
    """Return the low-aggression fixed-camera recipe used with good coverage."""
    return {
        "type": "ken_burns",
        "movement": "full_static",
        "speed": "static",
        "speed_factor": 1.0,
        "lock_target": False,
        "target_x": 0.5,
        "target_y": 0.5,
        "zoom_start": 1.0,
        "zoom_end": 1.0,
        "pan_x_start": 0.5,
        "pan_x_end": 0.5,
        "pan_y_start": 0.5,
        "pan_y_end": 0.5,
        "pan_x": 0.5,
        "pan_y": 0.5,
        "top_edge_limit": IPHONE_CROP_TOP_LIMIT,
        "vertical_motion": "static",
    }


def _gentle_fixed_camera_motion(index: int) -> dict[str, Any]:
    """One centred, barely perceptible move from/to the full frame.

    This is deliberately not built from ``_ken_burns_motion``. That helper's
    historical ``force_full_zoom`` recipe starts around 3x, which was the
    source of the apparent double/aggressive zoom in otherwise well-covered
    YouTube cuts.
    """
    zoom = round(1.0 + FIXED_CAMERA_GENTLE_ZOOM_FRACTION, 3)
    zoom_in = index % 2 == 0
    return {
        "type": "ken_burns",
        "movement": "zoom_in_center" if zoom_in else "zoom_out_center",
        "speed": "very_slow",
        "speed_factor": MOTION_SPEEDS[0][1],
        "lock_target": False,
        "centered": True,
        "target_x": 0.5,
        "target_y": 0.5,
        "zoom_start": 1.0 if zoom_in else zoom,
        "zoom_end": zoom if zoom_in else 1.0,
        "pan_x_start": 0.5,
        "pan_x_end": 0.5,
        "pan_y_start": 0.5,
        "pan_y_end": 0.5,
        "pan_x": 0.5,
        "pan_y": 0.5,
        "top_edge_limit": IPHONE_CROP_TOP_LIMIT,
        "vertical_motion": "static",
        "zoom_path_fraction": FIXED_CAMERA_GENTLE_ZOOM_FRACTION,
    }


def _ken_burns_motion(
    index: int,
    target_x: float = 0.5,
    target_y: float = 0.5,
    *,
    allow_static: bool = True,
    force_full_zoom: bool = False,
    force_close: bool = False,
) -> dict[str, Any]:
    rng = random.Random(stable_fingerprint({"fixed_camera_motion": index}))
    # Close-up range for the wide iPhone stage.  The catalog is explicit: a
    # segment gets one movement recipe, never an accidental combination.
    kind = "full_zoom_in" if force_full_zoom else rng.choices(MOTION_CATALOG, weights=[MOTION_WEIGHTS[kind] for kind in MOTION_CATALOG], k=1)[0]
    if force_close and kind in {"full_static", "full_zoom_in"}:
        kind = "zoom_in_center"
    if kind == "full_static" and not allow_static:
        # Defensive guard for old/randomized recipes: static fixed-camera
        # framing is no longer an allowed output.
        kind = "full_zoom_in"
    speed_name, speed_factor = MOTION_SPEEDS[index % len(MOTION_SPEEDS)]
    if force_full_zoom:
        speed_name, speed_factor = "very_slow", MOTION_SPEEDS[0][1]
    target_x = max(0.05, min(0.95, float(target_x)))
    target_y = max(0.05, min(0.95, float(target_y)))
    close_zoom = rng.uniform(3.2, 3.6)
    tight_zoom = rng.uniform(3.0, 3.2)
    if kind == "full_static":
        zoom_start = zoom_end = 3.0
        pan_x_start = pan_x_end = 0.5
        pan_y_start = pan_y_end = _minimum_pan_y_for_top_edge(zoom_start)
    elif kind == "full_zoom_in" and force_full_zoom:
        # “Full” means the widest permitted action frame. A literal 1.0x
        # frame necessarily includes the ceiling, so start at a lower crop
        # that respects the hard top-edge boundary.
        target_x = target_y = 0.65
        zoom_start, zoom_end = 3.0, 3.4
        pan_x_start = pan_x_end = 0.5
        pan_y_start = _minimum_pan_y_for_top_edge(zoom_start)
        pan_y_end = _minimum_pan_y_for_top_edge(zoom_end)
    elif kind in {"zoom_in", "zoom_in_center", "full_zoom_in"} and not force_full_zoom:
        min_zoom = max(_minimum_zoom_for_target(target_x), _minimum_zoom_for_target(target_y))
        tight_zoom = max(tight_zoom, min_zoom)
        zoom_start, zoom_end = tight_zoom, close_zoom
        pan_x_start, pan_x_end = _pan_for_target(zoom_start, target_x), _pan_for_target(zoom_end, target_x)
        pan_y_start, pan_y_end = _pan_for_target(zoom_start, target_y), _pan_for_target(zoom_end, target_y)
    elif kind in {"zoom_out", "zoom_out_center"}:
        min_zoom = max(_minimum_zoom_for_target(target_x), _minimum_zoom_for_target(target_y))
        tight_zoom = max(tight_zoom, min_zoom)
        zoom_start, zoom_end = close_zoom, tight_zoom
        pan_x_start, pan_x_end = _pan_for_target(zoom_start, target_x), _pan_for_target(zoom_end, target_x)
        pan_y_start, pan_y_end = _pan_for_target(zoom_start, target_y), _pan_for_target(zoom_end, target_y)
    elif kind.startswith("pan_"):
        zoom_start = zoom_end = close_zoom
        if kind in {"pan_down_center", "pan_up_center"}:
            pan_x_start = pan_x_end = 0.5
            pan_y_start, pan_y_end = ((0.22, 0.78) if kind == "pan_down_center" else (0.78, 0.22))
        else:
            pan_x_start, pan_x_end = (0.22, 0.78) if "right" in kind else (0.78, 0.22)
            pan_y_start = pan_y_end = 0.5
    else:
        zoom_start = zoom_end = close_zoom
        pan_x_start = pan_y_start = 0.5
        pan_x_end = 0.82 if "right" in kind else 0.18
        pan_y_end = 0.78 if "bottom" in kind else 0.22
    pan_y_start = _clamp_pan_y_for_top_edge(pan_y_start, zoom_start)
    pan_y_end = _clamp_pan_y_for_top_edge(pan_y_end, zoom_end)
    # A hard top-edge cap can collapse the old 0.22/0.78 pan endpoints into
    # the same legal value. Preserve the authored direction with a shorter
    # legal travel instead of silently turning a pan into a static shot.
    if kind == "pan_down_center" and pan_y_end <= pan_y_start:
        pan_y_start = _minimum_pan_y_for_top_edge(zoom_start)
        pan_y_end = min(1.0, pan_y_start + 0.12)
    elif kind == "pan_up_center" and pan_y_start <= pan_y_end:
        pan_y_start = min(1.0, _minimum_pan_y_for_top_edge(zoom_start) + 0.12)
        pan_y_end = _minimum_pan_y_for_top_edge(zoom_end)
    elif kind.startswith("pan_") and kind not in {"pan_down_center", "pan_up_center"}:
        if "bottom" in kind and pan_y_end <= pan_y_start:
            pan_y_start = _minimum_pan_y_for_top_edge(zoom_start)
            pan_y_end = min(1.0, pan_y_start + 0.12)
        elif "top" in kind and pan_y_start <= pan_y_end:
            pan_y_start = min(1.0, _minimum_pan_y_for_top_edge(zoom_start) + 0.12)
            pan_y_end = _minimum_pan_y_for_top_edge(zoom_end)
    # Vertical direction is an authored invariant, not an accidental result
    # of target-lock math. Production defaults travel down; the only upward
    # recipes start at the bottom edge and travel back up.
    vertical_motion = "up" if kind == "pan_up_center" or (kind.startswith("pan_") and "top" in kind) else "down"
    if kind not in {"full_static"} and vertical_motion == "down" and pan_y_end < pan_y_start:
        pan_y_end = min(1.0, pan_y_start + 0.12)
    if vertical_motion == "up":
        pan_y_start = max(pan_y_start, min(1.0, _minimum_pan_y_for_top_edge(zoom_start) + 0.12))
        pan_y_end = min(pan_y_end, _minimum_pan_y_for_top_edge(zoom_end))
    return {
        "type": "ken_burns",
        "movement": kind,
        "speed": speed_name,
        "speed_factor": speed_factor,
        "lock_target": kind in {"zoom_in", "zoom_out", "zoom_in_center", "zoom_out_center", "full_zoom_in"},
        "target_x": round(target_x, 4),
        "target_y": round(target_y, 4),
        "zoom_start": round(zoom_start, 3),
        "zoom_end": round(zoom_end, 3),
        "pan_x_start": round(pan_x_start, 3),
        "pan_x_end": round(pan_x_end, 3),
        "pan_y_start": round(pan_y_start, 3),
        "pan_y_end": round(pan_y_end, 3),
        "pan_x": round(pan_x_start, 3),
        "pan_y": round(pan_y_start, 3),
        "top_edge_limit": IPHONE_CROP_TOP_LIMIT,
        "vertical_motion": vertical_motion,
    }


def _motion_active_axes(motion: dict[str, Any]) -> list[str]:
    """Return the moving axes in a ken-burns recipe, for audit/tests."""
    axes: list[str] = []
    if float(motion.get("zoom_start", 1.0)) != float(motion.get("zoom_end", 1.0)):
        axes.append("zoom")
    # These pan values are derived crop coordinates that keep the selected
    # subject locked while zooming; they are not a second authored movement.
    if motion.get("lock_target") and "zoom" in axes:
        return axes
    if float(motion.get("pan_x_start", motion.get("pan_x", 0.5))) != float(motion.get("pan_x_end", motion.get("pan_x", 0.5))):
        axes.append("pan_x")
    if float(motion.get("pan_y_start", motion.get("pan_y", 0.5))) != float(motion.get("pan_y_end", motion.get("pan_y", 0.5))):
        axes.append("pan_y")
    return axes


def _valid_motion_recipe(motion: dict[str, Any]) -> bool:
    kind = str(motion.get("movement") or "")
    if kind not in MOTION_CATALOG:
        return False
    axes = _motion_active_axes(motion)
    if kind == "full_static":
        return axes == []
    if kind in {"zoom_in", "zoom_out", "zoom_in_center", "zoom_out_center", "full_zoom_in"}:
        return axes == ["zoom"]
    if kind in {"pan_right_center", "pan_left_center"}:
        return axes == ["pan_x"]
    if kind in {"pan_down_center", "pan_up_center"}:
        return axes == ["pan_y"]
    return axes == ["pan_x", "pan_y"]


def _validate_motion_segments(segments: list[dict[str, Any]]) -> None:
    """Fail edit-plan creation rather than export a mixed-motion segment."""
    for index, segment in enumerate(segments):
        motion = segment.get("motion") or {}
        if motion.get("type") == "ken_burns" and not _valid_motion_recipe(motion):
            raise ValueError(f"Invalid combined camera motion in segment {index}")


def _camera_id_from_render_path(segment: dict[str, Any]) -> str:
    """Derive the camera identity from the source that will actually render.

    This deliberately ignores ``segment['camera_id']``.  The old plan writer
    could preserve an iPhone label while replacing the path with the 360
    proxy, making all downstream camera statistics false.
    """
    return _camera_id({
        "source_path": segment.get("source_path") or segment.get("clip_path"),
        "filename": segment.get("filename"),
        "projection": segment.get("projection"),
    })


def validate_plan_camera_source_consistency(plan: dict[str, Any]) -> None:
    """Fail fast when a plan label and its render source identify cameras differently."""
    for index, segment in enumerate(plan.get("segments") or []):
        camera_id = str(segment.get("camera_id") or "").strip().lower()
        # Hand-authored/export fixture plans from older projects may omit the
        # optional label entirely.  There is nothing to compare in that case;
        # the invariant applies whenever a camera_id is present.
        if not camera_id:
            continue
        render_camera = _camera_id_from_render_path(segment)
        if camera_id != render_camera:
            raise ValueError(
                f"Edit plan camera/source mismatch at segment {index}: "
                f"camera_id={camera_id!r}, render_source_camera={render_camera!r}, "
                f"source_path={segment.get('source_path') or segment.get('clip_path')!r}"
            )


def _round_to_frame(seconds: float, fps: float = EDIT_FPS) -> float:
    return round(round(float(seconds) * fps) / fps, 6)


def _source_id(source: dict[str, Any]) -> str:
    return str(source.get("source_path") or source.get("path") or source.get("filename"))


def _camera_id(source: dict[str, Any]) -> str:
    """Return a stable physical-camera identity for a synced source.

    A camera can produce several files (for example Sony C0064 and C0065), so
    source_path is deliberately not sufficient for the YouTube run limit.
    Ingest metadata wins; otherwise use the meaningful source directory and
    finally the filename stem for synthetic/test paths.
    """
    for key in ("camera_id", "camera_name", "camera", "camera_label"):
        value = source.get(key)
        if value:
            return str(value).strip().lower()
    path_value = source.get("source_path") or source.get("original_path") or source.get("path") or source.get("filename")
    path = Path(str(path_value))
    generic = {"", "tmp", "cache", "proxies", "uploads", "wizarduploads", "video", "videos", "raw"}
    parent = path.parent.name.strip().lower()
    if parent not in generic:
        return parent
    stem = path.stem.lower()
    for marker in ("insta360", "360", "iphone", "sony", "gopro"):
        if marker in stem:
            return marker
    return stem or _source_id(source).lower()


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
        # Keep ingest's property-based classification attached to the
        # statistics record.  Reconstructing a source from path/filename here
        # used to turn a real equirectangular camera back into a flat
        # handheld, which made the audit and target weights lie even when the
        # segment itself was spherical.
        "projection": source.get("projection"),
        "raw_360": source.get("raw_360") is True,
        "camera_role": source.get("camera_role"),
        "camera_id": source.get("camera_id"),
        "camera_name": source.get("camera_name"),
        "camera_type": source.get("camera_type"),
        "device_type": source.get("device_type"),
        "is_static_camera": source.get("is_static_camera"),
        "static_camera": source.get("static_camera"),
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


def _finalize_selection_stats(stats: dict[str, dict[str, Any]], role_weights: dict[str, float] | None = None) -> list[dict[str, Any]]:
    finalized = []
    for item in stats.values():
        entry = dict(item)
        entry["role"] = _source_role(entry)
        entry["configured_weight"] = float((role_weights or DEFAULT_CAMERA_ROLE_WEIGHTS).get(entry["role"], 0.0))
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
