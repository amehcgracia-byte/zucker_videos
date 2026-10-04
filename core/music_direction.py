"""Music-aware pacing and deterministic creative variation for long edits."""
from __future__ import annotations

import bisect
import math
import random
from typing import Any

from core.stages.base import stable_fingerprint

VERSION = 1


def energy_timeline(beats: dict[str, Any]) -> tuple[list[float], list[float]]:
    """Keep the original bar mapping independent of inserted cut boundaries."""
    times = list(beats.get("bars_sec") or [])
    raw = list(beats.get("energy_by_bar") or [])
    values = []
    for value in raw:
        try:
            value = float(value)
            values.append(min(1., max(0., value)) if math.isfinite(value) else .5)
        except (TypeError, ValueError):
            values.append(.5)
    # A local average removes one-bar spikes; a clear peak retains its accent.
    smooth = [max(sum(values[max(0, i-1):i+2])/len(values[max(0, i-1):i+2]), v*.92)
              for i, v in enumerate(values)]
    return times, smooth


def direction_at(timeline: tuple[list[float], list[float]], seconds: float,
                 seed: str, index: int, previous: str = "tranquilo") -> dict[str, Any]:
    times, values = timeline
    i = max(0, bisect.bisect_right(times, seconds)-1)
    energy = values[min(i, len(values)-1)] if values else .5
    # Hysteresis prevents alternating styles at a threshold.
    frantic = energy >= (.79 if previous == "frenetico" else .85)
    animated = energy >= (.52 if previous == "animado" else .60)
    group = "frenetico" if frantic else "animado" if animated else "tranquilo"
    low, high = {"tranquilo": (5., 7.), "animado": (2.5, 4.), "frenetico": (1., 2.)}[group]
    rng = random.Random(stable_fingerprint({"seed": seed, "shot": index, "direction": VERSION}))
    target = rng.uniform(low, high) if seed else (low+high)/2
    return {"group": group, "energy": round(energy, 4), "min_sec": low, "max_sec": high,
            "target_sec": round(target, 4), "reason": "smoothed_bar_energy" if values else "energy_unavailable_conservative"}
