"""Runs the 360 motion audit (tools/audit_360.py) as a slow test.

The audit renders a real export and measures the delivered pixels, so this is
the only check that would catch automatic 360 motion shipping wild again --
every earlier fix looked correct in the sendcmd instrumentation and still went
out wrong. It is marked slow because it encodes and decodes real video.

Always synthetic here: a test must not depend on whatever 360 clip happens to
be in the user's last project.
"""

from __future__ import annotations

import shutil

import pytest

from tools.audit_360 import (
    MAX_CONTROL_PERCENT_PER_SEC,
    MIN_POSITIVE_PERCENT_PER_SEC,
    format_report,
    run_audit,
)


@pytest.fixture(scope="module")
def audit_report():
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not available")
    pytest.importorskip("cv2")
    # One render shared by every assertion below: it is by far the slowest part.
    return run_audit(synthetic=True)


@pytest.mark.slow
def test_audit_measurement_is_calibrated_in_both_directions(audit_report):
    # Without this, nothing else in the audit means anything: a measurement that
    # invents motion fails honest holds, and one that is blind to motion passes
    # everything -- including a render with no 360 framing in it at all.
    control = audit_report.control
    positive = audit_report.positive
    assert control is not None and positive is not None, format_report(audit_report)
    assert control.percent_per_sec <= MAX_CONTROL_PERCENT_PER_SEC, format_report(audit_report)
    assert positive.percent_per_sec >= MIN_POSITIVE_PERCENT_PER_SEC, format_report(audit_report)


@pytest.mark.slow
def test_audit_confirms_the_360_reframing_actually_happened(audit_report):
    # Different landmarks must render as different framings. If they are all the
    # same image the v360 reframing silently did not run, everything measures as
    # perfectly still, and every threshold below passes over a flat passthrough.
    assert audit_report.reframed, format_report(audit_report)


@pytest.mark.slow
def test_every_hold_segment_is_measured(audit_report):
    """Holds are measured, but their magnitude is NOT asserted here.

    This test runs synthetically (it must not depend on the user's media), and on
    a synthetic source the hold reading is dominated by the texture's spatial
    frequency rather than by the motion -- the same render measures 11.2%/s or
    0.017%/s depending on how coarse the noise is. Asserting a threshold on that
    number would be asserting the texture. See the tools/audit_360.py docstring.

    The authored motion budget is instead guaranteed exactly, and cheaply, by
    test_edit.py::test_automatic_360_motion_never_exceeds_the_fov_fraction_budget,
    which checks the sendcmd stream ffmpeg is actually handed. Run
    `python -m tools.audit_360` against a real 360 clip to judge hold magnitudes
    on real footage.
    """
    holds = [segment for segment in audit_report.segments if segment.kind == "hold"]
    assert holds, format_report(audit_report)
    assert all(hold.samples > 0 for hold in holds), format_report(audit_report)
    # Excluded from the verdict on a synthetic run, so a hold cannot fail the
    # audit for a reason that is really about the test texture.
    assert audit_report.synthetic_source
    assert audit_report.passed, format_report(audit_report)


@pytest.mark.slow
def test_360_shot_changes_are_cuts_or_unhurried_reframes(audit_report):
    # Panning between landmarks spread around the sphere was the dominant source
    # of the "swings wildly" report; a change of landmark should cut.
    for boundary in audit_report.boundaries:
        assert boundary.passed, format_report(audit_report)
