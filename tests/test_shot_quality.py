from __future__ import annotations

from core.shot_quality import director_quality_for_segment, score_director_window
from core.stages.edit import _quality_filtered_sources, _selection_stats_template


def test_director_score_gates_no_face_and_high_motion_windows():
    good_score, good_reasons = score_director_window({"face_score": 0.8, "motion": 0.08, "sharpness": 0.8, "exposure": 0.8})
    bad_score, bad_reasons = score_director_window({"face_score": 0.0, "motion": 0.9, "sharpness": 0.7, "exposure": 0.8})

    assert good_score > bad_score
    assert good_reasons == []
    assert "no face" in bad_reasons
    assert "camera moving" in bad_reasons


def test_quality_filtered_sources_keeps_bad_sony_window_as_soft_diagnostic():
    sony = {
        "path": "/tmp/sony.mp4",
        "filename": "sony.mp4",
        "offset_sec": 0,
        "duration_sec": 4,
        "director_quality": {
            "windows": [
                {"master_start_sec": 0, "master_end_sec": 2, "score": 0.2, "eligible": False, "reasons": ["no face"]},
                {"master_start_sec": 2, "master_end_sec": 4, "score": 0.8, "eligible": True, "reasons": []},
            ]
        },
    }
    iphone = {"path": "/tmp/iphone.mov", "filename": "iphone.mov", "offset_sec": 0, "duration_sec": 4}
    stats = _selection_stats_template([sony, iphone], 0, 4)

    first = _quality_filtered_sources([sony, iphone], 0, 2, stats)
    second = _quality_filtered_sources([sony, iphone], 2, 4, stats)

    assert {source["filename"] for source in first} == {"sony.mp4", "iphone.mov"}
    assert {source["filename"] for source in second} == {"sony.mp4", "iphone.mov"}
    assert stats["/tmp/sony.mp4"]["director_rejected_segments"] == 1
    assert stats["/tmp/sony.mp4"]["director_reject_reasons"] == {"no face": 1}


def test_director_quality_for_segment_averages_overlapping_windows():
    source = {
        "director_quality": {
            "windows": [
                {"master_start_sec": 0, "master_end_sec": 2, "score": 0.8, "eligible": True},
                {"master_start_sec": 2, "master_end_sec": 4, "score": 0.6, "eligible": True},
            ]
        }
    }

    quality = director_quality_for_segment(source, 1, 3)

    assert quality["eligible"] is True
    assert quality["score"] == 0.7
