import pytest
from core.stages.export import _segment_detail_fraction

@pytest.mark.parametrize("message,expected", [
    ("360 pan_right — 37/120 frames", 37/120),
    ("360 pan_left — 60/120 frames", .5),
    ("360 close_hold — 120/120 frames", 1),
    ("Encoding — 48%", .48),
    ("complete", 1),
    ("Waiting for source", 0),
])
def test_native_frame_counts_and_encoder_percentages_move_the_bar(message, expected):
    assert _segment_detail_fraction(message) == pytest.approx(expected)
