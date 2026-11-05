from __future__ import annotations

from core.shot_quality import director_quality_for_segment, score_director_window
from core.stages.edit import _quality_filtered_sources, _selection_stats_template


def test_smiles_prefer_usable_shots_without_rescuing_blur_or_movement():
    from core.shot_quality import smile_for_segment
    from core.stages.edit import _choose_source
    good={'face_score':.8,'motion':.05,'sharpness':.8,'exposure':.8}
    assert score_director_window({**good,'smile_score':1})[0]>score_director_window(good)[0]
    bad={**good,'motion':.9}
    assert score_director_window({**bad,'smile_score':1})==score_director_window(bad)
    def source(name,smile):
        return {'path':f'/tmp/{name}.mp4','filename':f'{name}.mp4','camera_role':'fixed_rear',
                'director_quality':{'windows':[{'master_start_sec':0,'master_end_sec':2,
                'signals':{**good,'smile_score':smile}}]}}
    neutral,happy=source('a',0),source('z',1)
    scored=_quality_filtered_sources([neutral,happy],0,2,{})
    assert _choose_source(scored,None)['path']==happy['path']
    assert _choose_source(scored,happy['path'])['path']==neutral['path']  # no repeated camera
    assert smile_for_segment(happy,3,4)==0
    assert smile_for_segment(source('fleeting',.25),0,2)==0


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
