from __future__ import annotations

from core.throughput import (
    ROLLING_SAMPLE_LIMIT,
    estimated_export_seconds,
    record_export_throughput,
)


def test_no_estimate_until_this_machine_has_actually_finished_an_export():
    """With no history the estimate must be None, never an optimistic guess.

    The UI turns None into "estimating..." -- the whole point of the change is
    that we stop promising "1-3 minutes" for a job we have no basis to size.
    """
    assert estimated_export_seconds({}, "youtube", 300.0, 40) is None
    assert estimated_export_seconds({"export_throughput": {}}, "youtube", 300.0, 40) is None


def test_estimate_reflects_measured_throughput():
    config: dict = {}
    # A 200s export took 400s -> 2x realtime on this machine.
    record_export_throughput(config, "youtube", 200.0, 400.0, 20)
    estimate = estimated_export_seconds(config, "youtube", 200.0, 20)
    assert estimate is not None
    assert estimate == 400.0
    # Twice the output should predict roughly twice the wait.
    assert estimated_export_seconds(config, "youtube", 400.0, 40) > 700.0


def test_throughput_is_tracked_per_platform():
    """A 360 passthrough and a re-encoded multicam run at very different speeds."""
    config: dict = {}
    record_export_throughput(config, "youtube", 100.0, 500.0, 30)
    record_export_throughput(config, "360", 100.0, 50.0, 1)
    youtube = estimated_export_seconds(config, "youtube", 100.0, 30)
    spherical = estimated_export_seconds(config, "360", 100.0, 1)
    assert youtube > spherical * 5
    # An unseen platform has no history of its own.
    assert estimated_export_seconds(config, "reel", 100.0, 10) is None


def test_rolling_average_forgets_old_runs():
    config: dict = {}
    for _ in range(ROLLING_SAMPLE_LIMIT + 5):
        record_export_throughput(config, "youtube", 100.0, 100.0, 10)
    samples = config["export_throughput"]["youtube"]["samples"]
    assert len(samples) == ROLLING_SAMPLE_LIMIT
    # Machine suddenly gets much slower: the average must move toward it.
    before = estimated_export_seconds(config, "youtube", 100.0, 10)
    for _ in range(ROLLING_SAMPLE_LIMIT):
        record_export_throughput(config, "youtube", 100.0, 400.0, 10)
    after = estimated_export_seconds(config, "youtube", 100.0, 10)
    assert after > before * 2


def test_degenerate_runs_are_ignored():
    config: dict = {}
    record_export_throughput(config, "youtube", 0.0, 100.0, 10)
    record_export_throughput(config, "youtube", 100.0, 0.0, 10)
    assert estimated_export_seconds(config, "youtube", 100.0, 10) is None
