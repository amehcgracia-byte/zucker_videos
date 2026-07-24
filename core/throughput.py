"""Persisted export-throughput model, used to give an honest ETA up front.

The progress bar used to have nothing to go on until a run was well underway,
so it showed an optimistic guess that was routinely far short of the real
total. This module keeps a rolling average of how fast THIS machine actually
renders, measured in wall-clock seconds per second of finished video, so the
very first status poll of a new run can quote a number grounded in real
measurements rather than a hopeful placeholder.

Samples are kept per platform (a 360 passthrough and a re-encoded YouTube
multicam run at very different speeds) and stored in the app's global config
so the estimate survives restarts.
"""

from __future__ import annotations

from typing import Any

# Keep the average responsive to a machine getting faster/slower (or the user
# switching to a different source format) without letting one odd run dominate.
ROLLING_SAMPLE_LIMIT = 8
CONFIG_KEY = "export_throughput"


def record_export_throughput(
    config: dict[str, Any],
    platform: str,
    output_duration_sec: float,
    elapsed_sec: float,
    segment_count: int,
) -> dict[str, Any]:
    """Fold one finished export into the rolling average; returns the config.

    The caller owns loading and saving the config, so this stays a pure
    dict transformation and is trivial to test.
    """
    if output_duration_sec <= 0 or elapsed_sec <= 0:
        return config
    store = dict(config.get(CONFIG_KEY) or {})
    key = str(platform or "youtube")
    entry = dict(store.get(key) or {})
    samples = list(entry.get("samples") or [])
    samples.append(
        {
            "seconds_per_output_second": round(elapsed_sec / output_duration_sec, 4),
            "seconds_per_segment": round(elapsed_sec / max(1, segment_count), 4),
        }
    )
    entry["samples"] = samples[-ROLLING_SAMPLE_LIMIT:]
    store[key] = entry
    config[CONFIG_KEY] = store
    return config


def estimated_export_seconds(
    config: dict[str, Any],
    platform: str,
    output_duration_sec: float,
    segment_count: int,
) -> float | None:
    """Predicted wall-clock seconds for a run, or None when nothing is known.

    Returning None is deliberate and important: the UI must say "estimating..."
    rather than invent an optimistic number. An estimate is only produced once
    this machine has actually finished at least one comparable export.
    """
    store = (config or {}).get(CONFIG_KEY) or {}
    entry = store.get(str(platform or "youtube")) or {}
    samples = entry.get("samples") or []
    if not samples or output_duration_sec <= 0:
        return None
    per_second = [float(s.get("seconds_per_output_second") or 0.0) for s in samples]
    per_second = [value for value in per_second if value > 0]
    if not per_second:
        return None
    average = sum(per_second) / len(per_second)
    estimate = average * float(output_duration_sec)
    # Longer edits carry more per-segment fixed cost (probe, seek, cache
    # lookup), which the per-second figure alone under-counts on very cut-heavy
    # plans; blend in the per-segment observation when we have one.
    per_segment = [float(s.get("seconds_per_segment") or 0.0) for s in samples]
    per_segment = [value for value in per_segment if value > 0]
    if per_segment and segment_count > 0:
        segment_estimate = (sum(per_segment) / len(per_segment)) * segment_count
        estimate = max(estimate, 0.5 * (estimate + segment_estimate))
    return round(estimate, 1)
