from __future__ import annotations

from core.shot_quality import director_quality_for_segment, score_director_window
from core.stages.edit import _quality_filtered_sources, _selection_stats_template


def test_real_bundled_cascade_finds_smile_but_not_empty_frame():
    from pathlib import Path
    import numpy as np
    from PIL import Image
    from core.shot_quality import _smile_score
    image=np.asarray(Image.open(Path(__file__).parent/'fixtures/smile/smile.jpg').convert('RGB'))
    assert _smile_score(image)==1
    assert _smile_score(np.zeros_like(image))==0


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
    assert smile_for_segment(source('one_sample',.5),0,2)==0
    happy['director_quality']['windows'].append({'master_start_sec':2,'master_end_sec':8,
                                                'signals':{**good,'smile_score':0}})
    assert smile_for_segment(happy,0,8)==1  # a short smile inside a long shot still counts
    assert 0 < smile_for_segment(happy,1.9,8)<.11


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


def test_quality_extraction_limits_ffmpeg_window_only_when_duration_known(monkeypatch, tmp_path):
    """Issue Montara #56: -t controls output decoding, not only progress."""
    import io
    from unittest.mock import MagicMock
    import core.shot_quality as quality

    commands = []

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        process = MagicMock()
        process.stdout = io.BytesIO(b"")
        process.stderr = io.BytesIO(b"")
        process.wait.return_value = 0
        return process

    monkeypatch.setattr(quality, "_keyframe_interval", lambda _: None)
    monkeypatch.setattr(quality.subprocess, "Popen", fake_popen)
    sample = tmp_path / "sample.mp4"
    for duration in (3.25, 0, float("inf")):
        frames, error = quality._extract_quality_frames("ffmpeg", sample, duration, None)
        assert frames == [] and error is None

    limited, unknown, infinite = commands
    assert limited[limited.index("-i") + 1] == str(sample)
    assert limited[limited.index("-t") + 1] == "3.250000"
    assert limited.index("-i") < limited.index("-t") < limited.index("-vf")
    assert "-t" not in unknown
    assert "-t" not in infinite
