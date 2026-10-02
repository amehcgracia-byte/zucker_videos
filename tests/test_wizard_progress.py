from __future__ import annotations

from server.wizard import _aggregate_segment_progress


def test_parallel_segment_progress_uses_all_known_worker_progress():
    percent, completed = _aggregate_segment_progress(
        {1: 1.0, 2: 1.0, 3: 0.5, 4: 0.0},
        4,
    )
    assert percent == 62
    assert completed == 2


def test_parallel_segment_progress_is_bounded_and_empty_safe():
    assert _aggregate_segment_progress({}, 0) == (0, 0)
    assert _aggregate_segment_progress({1: 1.5, 2: -1.0}, 2) == (50, 1)
