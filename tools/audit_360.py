"""Objective self-test for automatic 360 motion, measured on the DELIVERED MP4.

Why this exists
---------------
Automatic 360 motion has been "fixed" several times. Every fix looked correct
in instrumentation -- the sendcmd stream ffmpeg is handed -- and still shipped
motion the user described as wild. The verification gap was always the same:
nobody measured the file that actually comes out.

So this audit renders a real export and then measures the *pixels*. Apparent
motion is measured with Farneback dense optical flow, which tracks how the
image actually moves. That matters because v360's ``flat`` output is a
RECTILINEAR projection: a yaw change re-projects the image non-uniformly
(edges move further than the centre) rather than translating it. A previous
attempt used naive cross-correlation, which assumes a uniform shift, produced
nonsense, and was abandoned. Optical flow handles the non-uniform case, and
the median of the per-pixel flow field is robust to the watermark, the frame
edges, and compression noise.

What it reports, per segment of the finished video:
  shot type, the FOV it was rendered at, hold vs transition, and measured
  apparent motion as a percentage of frame width per second.

Thresholds (see MAX_HOLD_PERCENT_PER_SEC / MAX_TRANSITION_PERCENT_PER_SEC):
  * a hold must stay under ~2% of frame width per second;
  * a shot change must either be a hard cut, or pan under ~10%/s.

Two controls bracket the measurement, because dense flow can fail in BOTH
directions and either failure makes the report worthless:

  * a static camera, which must read ~0%/s -- otherwise the measurement is
    inventing motion and honest holds would be failed;
  * a deliberate 80-degree pan, which must read high -- otherwise the
    measurement is blind and a hold reading ~0 proves nothing.

The audit fails loudly rather than reporting numbers it cannot trust. Both
controls are load-bearing: flow returns exactly zero wherever the image has no
local gradient, and it saturates when content moves further between samples than
it can track. Each of those made this audit report a perfect pass over nothing
at all (see _FLOW_MEASURABLE_TEXTURE and FLOW_SAMPLE_FPS).

RUN THIS ON REAL FOOTAGE. Hold magnitudes are only asserted for real media.
--------------------------------------------------------------------------
On a synthetic source the hold NUMBERS are indicative, not verdicts, and are
excluded from the pass/fail result. Measured against ground truth (a hold whose
sendcmd moves the camera a known 1.67% of field per second), the same render
measures 11.2, 1.07, 0.017 and 0.002 %/s as the synthetic texture is coarsened
from 8 px features to 64 px. The reading is dominated by the texture's spatial
frequency rather than by the motion: fine texture makes sub-pixel resampling
shimmer read as motion, and coarse texture leaves flow nothing to track. No
single frequency measures both a hold and the pan control correctly -- the
frequency that keeps the pan control honest over-reads holds by ~7x.

Real concert footage has broadband spatial detail and does not have that
degenerate behaviour, so hold thresholds are enforced only when a real 360 clip
was used. What the synthetic run still verifies is structural and robust: that
the reframing happened, that landmarks changed by cutting rather than sweeping,
and that the measurement is calibrated in both directions.

The authored motion budget itself is separately guaranteed by a fast unit test
against the sendcmd stream (see
tests/test_edit.py::test_automatic_360_motion_never_exceeds_the_fov_fraction_budget),
which is exact, so a synthetic run losing the hold verdict costs little.

Usage
-----
    python -m tools.audit_360                 # uses the last project's 360 clip
    python -m tools.audit_360 --project PATH  # a specific .zuckervid folder
    python -m tools.audit_360 --synthetic     # never touch real media
    python -m tools.audit_360 --keep          # leave the rendered MP4 in place

Also runs under pytest as a slow test (tests/test_audit_360.py).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Holds should be barely perceptible; a shot change is allowed to be a cut, or
# a deliberate but unhurried reframe.
MAX_HOLD_PERCENT_PER_SEC = 2.0
MAX_TRANSITION_PERCENT_PER_SEC = 10.0
# The control is a static camera with motion off: anything above this means the
# measurement itself is miscalibrated.
MAX_CONTROL_PERCENT_PER_SEC = 0.5
# The positive control is a deliberate 80°-over-4s pan. If the measurement reads
# LESS than this, optical flow is failing to see real motion, so a hold reading
# ~0 proves nothing. Comfortably below the ~5%/s a 20°/s pan of a 100° field
# actually produces, with headroom for compression and the central crop.
MIN_POSITIVE_PERCENT_PER_SEC = 2.5
# Two frames either side of a boundary this different cannot be a pan; it is a
# cut. Mean absolute 0-255 luma difference.
CUT_MEAN_ABS_DIFF = 12.0

AUDIT_SEGMENT_SEC = 4.0
AUDIT_SEGMENT_COUNT = 5
# Dense optical flow only tracks displacements up to roughly its window size, so
# the SAMPLE RATE sets the fastest motion the audit can see. At 6 fps a genuine
# 20%-of-field-per-second pan moves ~64 px between samples, which saturates
# Farneback: it under-reported that pan as 2.2%/s and the positive control failed
# even though the render was correct. At 15 fps the same pan is ~26 px per sample
# and measures 19.98%/s, while a 2%/s hold (~2.6 px) still measures 1.99%/s --
# accurate across the whole range the audit asserts on.
FLOW_SAMPLE_FPS = 15.0
# Skip this much at each end of a segment so cross-fades and the neighbouring
# shot never leak into a hold measurement.
SEGMENT_EDGE_PAD_SEC = 0.7


@dataclass
class SegmentMeasurement:
    index: int
    label: str
    shot_type: str
    fov: float
    kind: str  # "hold" | "control" | "positive"
    percent_per_sec: float
    samples: int
    threshold: float

    @property
    def passed(self) -> bool:
        # The positive control must read AT LEAST its threshold (it is deliberate
        # motion the flow must detect); every other kind must stay UNDER it.
        if self.kind == "positive":
            return self.percent_per_sec >= self.threshold
        return self.percent_per_sec <= self.threshold


@dataclass
class BoundaryMeasurement:
    index: int
    label: str
    is_cut: bool
    percent_per_sec: float
    threshold: float = MAX_TRANSITION_PERCENT_PER_SEC

    @property
    def passed(self) -> bool:
        # A hard cut is an allowed way to change framing; a continuous reframe
        # has to stay within the transition budget.
        return True if self.is_cut else self.percent_per_sec <= self.threshold


@dataclass
class AuditReport:
    output_path: str
    segments: list[SegmentMeasurement] = field(default_factory=list)
    boundaries: list[BoundaryMeasurement] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    reframed: bool = True
    reframe_detail: str = ""
    # True when no real 360 clip was used. Hold magnitudes measured on the
    # synthetic texture are indicative only (see the module docstring), so they
    # are reported but excluded from the verdict.
    synthetic_source: bool = False

    @property
    def control(self) -> SegmentMeasurement | None:
        return next((s for s in self.segments if s.kind == "control"), None)

    @property
    def positive(self) -> SegmentMeasurement | None:
        return next((s for s in self.segments if s.kind == "positive"), None)

    @property
    def calibrated(self) -> bool:
        # Calibrated in BOTH directions: the static control must read ~0 (no
        # invented motion) AND the positive control must read high (real motion
        # is detected). Either failing means the numbers cannot be trusted.
        control = self.control
        positive = self.positive
        static_ok = control is not None and control.percent_per_sec <= MAX_CONTROL_PERCENT_PER_SEC
        positive_ok = positive is not None and positive.percent_per_sec >= MIN_POSITIVE_PERCENT_PER_SEC
        return static_ok and positive_ok

    @property
    def passed(self) -> bool:
        # `reframed` first: a file where the 360 reframing never happened
        # measures 0%/s everywhere and would otherwise sail through as a
        # perfect pass. That is exactly the silent failure this audit exists to
        # catch, so it is a hard fail, not a note.
        if not self.reframed or not self.calibrated:
            return False
        judged = [s for s in self.segments if s.kind != "hold" or not self.synthetic_source]
        return all(s.passed for s in judged) and all(b.passed for b in self.boundaries)


def verify_shots_are_distinct(video: Path, spans: list[tuple[float, float]], work_dir: Path) -> tuple[bool, str]:
    """Check the 360 landmarks actually rendered as different framings.

    Different landmarks point at different parts of the sphere, so their
    rendered frames must look substantially different. If they are all the same
    image, the v360 reframing silently did not happen (for example the segment's
    source path failed to match its input record, so the export found no probe
    and fell back to a flat letterboxed passthrough). Everything then measures
    as perfectly still, and every threshold passes over a video with no 360
    framing in it at all.
    """
    import cv2
    import numpy as np

    work_dir.mkdir(parents=True, exist_ok=True)
    stills: list[Any] = []
    for index, (start, end) in enumerate(spans):
        middle = (start + end) / 2.0
        frames = _extract_frames(video, middle, middle + 0.2, work_dir / f"d{index}", 5.0)
        if not frames:
            continue
        image = cv2.imread(str(frames[0]), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            stills.append(image.astype("float32"))
    if len(stills) < 2:
        return False, "could not sample enough 360 segments to compare framings"
    diffs = [float(np.mean(np.abs(stills[i] - stills[i + 1]))) for i in range(len(stills) - 1)]
    worst = max(diffs)
    if worst < CUT_MEAN_ABS_DIFF:
        return False, (
            f"every 360 segment rendered the SAME image (largest difference between "
            f"consecutive landmarks was {worst:.2f}, needs >= {CUT_MEAN_ABS_DIFF}). "
            "The v360 reframing did not happen at all -- the motion numbers below are "
            "measuring a flat passthrough, not a 360 render."
        )
    return True, f"landmark framings differ as expected (largest consecutive difference {worst:.1f})"


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------


def _extract_frames(video: Path, start: float, end: float, out_dir: Path, fps: float) -> list[Path]:
    """Decode [start, end) to PNGs. Output-side -ss: input-side seeking snaps to
    the preceding keyframe and silently returns the SAME frame for different
    timestamps, which reads as zero motion and would quietly pass this audit.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = max(0.05, end - start)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(video),
            "-ss", f"{start:.3f}",
            "-t", f"{duration:.3f}",
            "-vf", f"fps={fps:.3f}",
            "-fps_mode", "passthrough",
            str(out_dir / "f%04d.png"),
        ],
        check=True,
        capture_output=True,
    )
    return sorted(out_dir.glob("*.png"))


def _flow_percent_per_sec(frames: list[Path], fps: float) -> tuple[float, int]:
    """Median apparent motion between consecutive frames, as % of frame width/s.

    Uses the median of the dense flow magnitude over a central crop: the median
    ignores the watermark and any small moving detail, and the crop keeps the
    frame edges (where rectilinear re-projection exaggerates movement, and
    where padding bars can sit) out of the statistic.
    """
    import cv2
    import numpy as np

    if len(frames) < 2:
        return 0.0, 0
    rates: list[float] = []
    previous = None
    width = None
    for path in frames:
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        height, width = image.shape[:2]
        crop = image[int(height * 0.2) : int(height * 0.8), int(width * 0.2) : int(width * 0.8)]
        # Equalise brightness so the intro/outro cross-fades cannot masquerade
        # as motion, then keep the image as 8-bit. cv2.calcOpticalFlowFarneback
        # requires a CV_8U single-channel image: handed float32 it silently
        # returns a near-zero flow field, which reads as "no motion" for even a
        # deliberate 80° pan -- the exact blindness the positive control caught.
        crop = cv2.normalize(crop, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        if previous is not None:
            flow = cv2.calcOpticalFlowFarneback(previous, crop, None, 0.5, 3, 21, 3, 5, 1.2, 0)
            magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
            median_px = float(np.median(magnitude))
            rates.append(median_px * fps / float(width) * 100.0)
        previous = crop
    if not rates:
        return 0.0, 0

    # Median across the segment: one odd frame pair (a decode hiccup, a
    # compression artefact) must not decide a pass or fail.
    return float(np.median(rates)), len(rates)


def _mean_abs_diff(a: Path, b: Path) -> float:
    import cv2
    import numpy as np

    first = cv2.imread(str(a), cv2.IMREAD_GRAYSCALE)
    second = cv2.imread(str(b), cv2.IMREAD_GRAYSCALE)
    if first is None or second is None or first.shape != second.shape:
        return 255.0
    return float(np.mean(np.abs(first.astype("float32") - second.astype("float32"))))


def measure_segment(video: Path, start: float, end: float, work_dir: Path) -> tuple[float, int]:
    frames = _extract_frames(
        video,
        start + SEGMENT_EDGE_PAD_SEC,
        max(start + SEGMENT_EDGE_PAD_SEC + 0.2, end - SEGMENT_EDGE_PAD_SEC),
        work_dir,
        FLOW_SAMPLE_FPS,
    )
    return _flow_percent_per_sec(frames, FLOW_SAMPLE_FPS)


def measure_boundary(video: Path, boundary: float, work_dir: Path) -> tuple[bool, float]:
    """Classify a shot change as a hard cut, or measure how fast it pans."""
    frames = _extract_frames(video, boundary - 0.25, boundary + 0.25, work_dir, 30.0)
    if len(frames) < 2:
        return False, 0.0
    middle = len(frames) // 2
    before, after = frames[max(0, middle - 1)], frames[min(len(frames) - 1, middle)]
    if _mean_abs_diff(before, after) >= CUT_MEAN_ABS_DIFF:
        return True, 0.0
    rate, _ = _flow_percent_per_sec(frames, 30.0)
    return False, rate


# --------------------------------------------------------------------------
# Building the export under test
# --------------------------------------------------------------------------


def _find_real_360_source(project_path: str | None) -> tuple[str | None, dict[str, Any] | None]:
    """Return (360 clip path, spherical landmarks) from the user's real setup."""
    from server.inbox import load_global_config

    config = load_global_config()
    landmarks = config.get("spherical_landmarks") or None
    folder = project_path or config.get("last_project_path")
    if not folder or not Path(folder).exists():
        return None, landmarks
    try:
        project_file = Path(folder) / "project.json"
        data = json.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, landmarks
    for record in (data.get("inputs") or {}).get("videos") or []:
        probe = record.get("probe") or {}
        if probe.get("projection") == "equirect" or record.get("projection") == "equirect":
            path = str(record.get("path") or "")
            if path and Path(path).exists():
                return path, landmarks
    return None, landmarks


# Low-resolution uniform noise scaled up smoothly: a texture with a usable local
# gradient at EVERY pixel and no aliasing.
#
# This is a measurement requirement, not an aesthetic one. Dense optical flow
# cannot estimate motion where there is no local gradient, and silently returns
# exactly zero there -- so the median of the flow field over an image with large
# flat areas is zero no matter how much the camera moved. `testsrc2`, used here
# originally, is ~93% flat by that measure (mandelbrot ~42%), which made every
# synthetic audit read 0%/s: the holds passed and the deliberate-pan positive
# control failed, for the same reason. This texture is ~7% flat and recovers a
# known shift exactly, from sub-pixel up.
_FLOW_MEASURABLE_TEXTURE = "nullsrc=size=240x120,format=gray,geq=random(1)*255,scale={width}:{height}:flags=bicubic,format=gray"


def _synthetic_equirect(path: Path, seconds: float) -> None:
    """A static equirect clip that optical flow can actually measure.

    Static content is the point: with the sphere itself frozen, any measured flow
    is virtual-camera motion and nothing else.
    """
    still = path.with_suffix(".png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", _FLOW_MEASURABLE_TEXTURE.format(width=1920, height=960), "-frames:v", "1", str(still)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(still), "-t", f"{seconds:.2f}",
         "-r", "30", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-g", "30", str(path)],
        check=True, capture_output=True,
    )


def _synthetic_flat(path: Path, seconds: float) -> None:
    """Static flat (non-360) clip used as the measurement control.

    Uses the same measurable texture as the equirect source: the control has to
    prove the measurement reads ~0 on a static camera over content it CAN see.
    Over a flat-coloured clip it would read 0 because there is nothing to
    measure, which proves nothing at all.
    """
    still = path.with_suffix(".png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", _FLOW_MEASURABLE_TEXTURE.format(width=1920, height=1080), "-frames:v", "1", str(still)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(still), "-t", f"{seconds:.2f}",
         "-r", "30", "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-g", "30", str(path)],
        check=True, capture_output=True,
    )


def build_and_render(work: Path, project_path: str | None, synthetic: bool) -> tuple[Path, list[dict[str, Any]], list[str], bool]:
    """Render a short automatic-mode export.

    Returns (mp4, segment metadata, notes, used_synthetic_source). The last flag
    matters for the verdict: hold magnitudes are only trustworthy on real
    footage (see the module docstring).
    """
    from core.project import create_project, file_record
    from core.stages.base import write_artifact_json
    from core.stages.edit import (
        _available_spherical_shots,
        _next_weighted_spherical_shot,
        _spherical_motion_profile,
        _spherical_type_usage,
    )
    from core.stages.export import ExportStage

    notes: list[str] = []
    os.environ["HOME"] = str(work / "home")

    total_sec = AUDIT_SEGMENT_SEC * (AUDIT_SEGMENT_COUNT + 1) + 8
    real_source, landmarks = (None, None) if synthetic else _find_real_360_source(project_path)
    if real_source:
        source = real_source
        notes.append(f"360 source: real project clip {Path(source).name}")
    else:
        source = str(work / "equirect.mp4")
        _synthetic_equirect(Path(source), total_sec)
        notes.append("360 source: synthetic equirect (no real 360 clip found) — hold magnitudes are INDICATIVE ONLY")
    # Resolve every path the same way file_record() does. On macOS /tmp and
    # /var are symlinks into /private, so an unresolved segment path silently
    # fails to match its input record -- the export then finds no probe, quietly
    # drops the v360 reframing, and renders a flat letterboxed passthrough with
    # no warning at all. That produced a fully green audit over a file with no
    # 360 framing in it whatsoever.
    source = str(Path(source).resolve())
    if not landmarks:
        landmarks = {
            "full_stage": {"yaw": 355.0, "pitch": -26.3, "fov": 114.8, "weight": 15.0},
            "singer": {"yaw": 21.0, "pitch": -23.4, "fov": 73.9, "weight": 5.0},
            "left": {"yaw": 308.0, "pitch": -15.9, "fov": 100.0, "weight": 40.0},
            "audience": {"yaw": 175.0, "pitch": -12.9, "fov": 111.4, "weight": 15.0},
            "audience_stage_wide": {"yaw": 72.0, "pitch": -14.3, "fov": 138.8, "weight": 15.0},
        }
        notes.append("landmarks: built-in venue-like defaults")
    else:
        notes.append(f"landmarks: the user's own saved setup ({len(landmarks)} shots)")

    control_source = work / "flat.mp4"
    _synthetic_flat(control_source, total_sec)
    control_source = control_source.resolve()
    master = (work / "master.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={total_sec:.2f}", str(master)],
        check=True, capture_output=True,
    )
    master = master.resolve()

    project = create_project("Audit360", str(work / "Audit360.zuckervid"))
    spherical_record = file_record(source)
    spherical_record.update(
        {"projection": "equirect",
         "probe": {"valid_video": True, "projection": "equirect", "duration": total_sec,
                   "width": 1920, "height": 960, "fps": 30.0, "video_codec": "h264"}}
    )
    control_record = file_record(str(control_source))
    control_record.update(
        {"probe": {"valid_video": True, "duration": total_sec, "width": 1920, "height": 1080,
                   "fps": 30.0, "video_codec": "h264"}}
    )
    project.data["inputs"]["master"] = file_record(str(master))
    project.data["inputs"]["videos"] = [spherical_record, control_record]
    project.data["settings"]["wizard"] = {"platform": "youtube"}
    # Motion is opt-in in the app; the audit exists to measure it, so force it on.
    project.data["settings"]["edit"] = {"spherical_landmarks": landmarks, "spherical_motion": True}

    shots = _available_spherical_shots(landmarks)
    segments: list[dict[str, Any]] = []
    meta: list[dict[str, Any]] = []
    built: list[dict[str, Any]] = []
    clock = 0.0
    for index in range(AUDIT_SEGMENT_COUNT):
        shot = _next_weighted_spherical_shot(shots, _spherical_type_usage(built))
        profile = _spherical_motion_profile(shot or {}, index, enabled=True)
        segment = {
            "title": "Audit", "filename": Path(source).name,
            "clip_path": source, "source_path": source,
            "clip_start_sec": clock, "master_start_sec": clock,
            "duration_sec": AUDIT_SEGMENT_SEC, "projection": "equirect",
            "spherical_shot": profile,
        }
        segments.append(segment)
        built.append(segment)
        meta.append({"kind": "hold", "shot_type": str(profile.get("type") or "(none)"),
                     "fov": float(profile.get("fov") or 0.0), "duration": AUDIT_SEGMENT_SEC})
        clock += AUDIT_SEGMENT_SEC
    # Positive control: a recorded-move curve with a big deliberate pan.
    # Recorded moves are user-authored and bypass the automatic-motion clamp, so
    # this genuinely sweeps the view. It proves the optical-flow measurement can
    # SEE real 360 motion -- without it, a hold reading ~0 is indistinguishable
    # from a measurement that simply never detects anything.
    pan_curve = [
        {"t": round(AUDIT_SEGMENT_SEC * frac, 3), "yaw": round(-40.0 + 80.0 * frac, 3), "pitch": 0.0, "fov": 100.0}
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    segments.append({
        "title": "Pan control", "filename": Path(source).name,
        "clip_path": source, "source_path": source,
        "clip_start_sec": clock, "master_start_sec": clock,
        "duration_sec": AUDIT_SEGMENT_SEC, "projection": "equirect",
        "spherical_shot": {"type": "recorded_move", "yaw": -40.0, "pitch": 0.0, "fov": 100.0, "curve": pan_curve},
    })
    meta.append({"kind": "positive", "shot_type": "deliberate 80° pan (recorded)", "fov": 100.0,
                 "duration": AUDIT_SEGMENT_SEC})
    clock += AUDIT_SEGMENT_SEC
    # Control: static flat camera, no motion of any kind.
    segments.append({
        "title": "Control", "filename": control_source.name,
        "clip_path": str(control_source), "source_path": str(control_source),
        "clip_start_sec": clock, "master_start_sec": clock,
        "duration_sec": AUDIT_SEGMENT_SEC,
    })
    meta.append({"kind": "control", "shot_type": "static camera (no motion)", "fov": 0.0,
                 "duration": AUDIT_SEGMENT_SEC})

    write_artifact_json(project.artifacts_dir / "edit_plan.json", {"platform": "youtube", "segments": segments})
    ExportStage().run(project, lambda percent, message: None)
    manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
    return Path(manifest["exports"][0]["path"]), meta, notes, real_source is None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_audit(project_path: str | None = None, synthetic: bool = False, keep: bool = False) -> AuditReport:
    from core.stages.export import INTRO_DURATION

    work = Path(tempfile.mkdtemp(prefix="audit360-"))
    try:
        output, meta, notes, used_synthetic = build_and_render(work, project_path, synthetic)
        report = AuditReport(output_path=str(output), notes=notes, synthetic_source=used_synthetic)
        frames_dir = work / "frames"
        clock = INTRO_DURATION
        for index, item in enumerate(meta):
            start, end = clock, clock + float(item["duration"])
            rate, samples = measure_segment(output, start, end, frames_dir / f"seg{index}")
            threshold = {
                "control": MAX_CONTROL_PERCENT_PER_SEC,
                "positive": MIN_POSITIVE_PERCENT_PER_SEC,
            }.get(str(item["kind"]), MAX_HOLD_PERCENT_PER_SEC)
            report.segments.append(
                SegmentMeasurement(
                    index=index, label=f"segment {index + 1}", shot_type=str(item["shot_type"]),
                    fov=float(item["fov"]), kind=str(item["kind"]),
                    percent_per_sec=round(rate, 3), samples=samples, threshold=threshold,
                )
            )
            if index > 0:
                is_cut, pan_rate = measure_boundary(output, clock, frames_dir / f"bnd{index}")
                report.boundaries.append(
                    BoundaryMeasurement(index=index, label=f"{index} -> {index + 1}",
                                        is_cut=is_cut, percent_per_sec=round(pan_rate, 3))
                )
            clock = end
        spherical_spans = [
            (INTRO_DURATION + sum(float(m["duration"]) for m in meta[:i]),
             INTRO_DURATION + sum(float(m["duration"]) for m in meta[: i + 1]))
            for i, item in enumerate(meta)
            if item["kind"] == "hold"
        ]
        report.reframed, report.reframe_detail = verify_shots_are_distinct(
            output, spherical_spans, frames_dir / "distinct"
        )
        if keep:
            kept = Path.cwd() / output.name
            shutil.copy2(output, kept)
            report.output_path = str(kept)
        return report
    finally:
        shutil.rmtree(work, ignore_errors=True)


def format_report(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append("360 motion audit — measured from the delivered MP4 (dense optical flow)")
    lines.append("=" * 78)
    for note in report.notes:
        lines.append(f"  {note}")
    lines.append("")
    lines.append(f"{'#':>2}  {'shot':26s} {'fov':>6} {'kind':8s} {'%width/s':>9} {'limit':>6}  result")
    lines.append("-" * 78)
    for segment in report.segments:
        fov = f"{segment.fov:6.1f}" if segment.fov else "     -"
        # The positive control passes by reading ABOVE its floor; show the
        # direction so the limit column isn't misread as a ceiling it broke.
        limit = f"≥{segment.threshold:4.1f}" if segment.kind == "positive" else f"{segment.threshold:5.1f}"
        advisory = report.synthetic_source and segment.kind == "hold"
        result = "(info)" if advisory else ("PASS" if segment.passed else "FAIL")
        lines.append(
            f"{segment.index + 1:2d}  {segment.shot_type[:26]:26s} {fov} {segment.kind:8s} "
            f"{segment.percent_per_sec:9.3f} {limit:>6}  {result}"
        )
    if report.synthetic_source:
        lines.append("")
        lines.append("  Hold rows read (info), not PASS/FAIL: on a synthetic source the number is")
        lines.append("  dominated by the texture's spatial frequency rather than by the motion, so")
        lines.append("  it is not a verdict. Re-run against a real 360 clip to judge holds.")
    if report.boundaries:
        lines.append("")
        lines.append("shot changes:")
        for boundary in report.boundaries:
            how = "hard cut" if boundary.is_cut else f"pan {boundary.percent_per_sec:.2f}%/s"
            lines.append(f"  {boundary.label:10s} {how:24s} {'PASS' if boundary.passed else 'FAIL'}")
    lines.append("")
    if report.reframed:
        lines.append(f"reframing OK: {report.reframe_detail}")
    else:
        lines.append(f"REFRAMING FAILED: {report.reframe_detail}")
    control = report.control
    positive = report.positive
    if control is None or positive is None:
        lines.append("CONTROLS MISSING — measurement is unverified.")
    elif not report.calibrated:
        if control.percent_per_sec > MAX_CONTROL_PERCENT_PER_SEC:
            lines.append(
                f"CALIBRATION FAILED: a static camera measured {control.percent_per_sec:.3f}%/s "
                f"(limit {MAX_CONTROL_PERCENT_PER_SEC}) — the measurement is inventing motion."
            )
        if positive.percent_per_sec < MIN_POSITIVE_PERCENT_PER_SEC:
            lines.append(
                f"CALIBRATION FAILED: a deliberate 80° pan measured only {positive.percent_per_sec:.3f}%/s "
                f"(needs ≥ {MIN_POSITIVE_PERCENT_PER_SEC}) — the measurement is BLIND to real motion, "
                "so a hold reading ~0 proves nothing."
            )
        lines.append("No number below can be trusted until the controls pass.")
    else:
        lines.append(
            f"calibrated in both directions: static camera {control.percent_per_sec:.3f}%/s (≤{MAX_CONTROL_PERCENT_PER_SEC}), "
            f"deliberate pan {positive.percent_per_sec:.3f}%/s (≥{MIN_POSITIVE_PERCENT_PER_SEC})."
        )
    lines.append("")
    lines.append(f"RESULT: {'PASS' if report.passed else 'FAIL'}")
    lines.append(f"rendered file: {report.output_path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit automatic 360 motion in a real export.")
    parser.add_argument("--project", help="A .zuckervid folder to take the 360 clip from")
    parser.add_argument("--synthetic", action="store_true", help="Never use real media")
    parser.add_argument("--keep", action="store_true", help="Keep the rendered MP4 in the working directory")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args(argv)

    report = run_audit(project_path=args.project, synthetic=args.synthetic, keep=args.keep)
    if args.json:
        print(json.dumps({
            "passed": report.passed,
            "calibrated": report.calibrated,
            # Hold magnitudes are advisory when this is true; see the docstring.
            "synthetic_source": report.synthetic_source,
            "reframed": report.reframed,
            "reframe_detail": report.reframe_detail,
            "output": report.output_path,
            "segments": [vars(s) | {"passed": s.passed} for s in report.segments],
            "boundaries": [vars(b) | {"passed": b.passed} for b in report.boundaries],
        }, indent=2))
    else:
        print(format_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
