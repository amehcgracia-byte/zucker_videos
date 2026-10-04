from __future__ import annotations

import json
from pathlib import Path

from core.engine import PipelineEngine
from core.project import create_project, file_record
from core.stages.backstage import (
    BACKSTAGE_MUSIC_DUCK_RATIO,
    BACKSTAGE_MUSIC_DUCK_THRESHOLD,
    BACKSTAGE_MUSIC_VOLUME,
    BackstageAnalysisStage,
    BackstageEditStage,
    BackstageExportStage,
    _backstage_music_filter,
    _backstage_segment_offsets,
    _backstage_subtitle_entries,
    _backstage_final_subtitle_entries,
    _background_music_runs,
    _music_seek_offset,
    _parchment_intervals,
    _snap_backstage_interval_to_words,
    _flatten_moments,
    _backstage_single_pass_filtergraph,
    update_backstage_cue_text,
)
from core.backstage_transcription import extract_story_bites


def test_backstage_pipeline_has_no_sync_or_master_dependency(tmp_path):
    project = create_project("backstage", str(tmp_path / "backstage.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "backstage"}
    project.data["inputs"]["videos"] = []
    engine = PipelineEngine()
    try:
        assert engine._plan("export", project) == ["ingest", "cut", "edit", "export"]
        assert isinstance(engine._stage_for_project("cut", project), BackstageAnalysisStage)
        assert isinstance(engine._stage_for_project("edit", project), BackstageEditStage)
        assert isinstance(engine._stage_for_project("export", project), BackstageExportStage)
    finally:
        engine.shutdown()


def test_backstage_edit_artifact_is_video_led_and_does_not_use_music_plan(tmp_path):
    project = create_project("backstage", str(tmp_path / "backstage.zuckervid"))
    project.data["settings"]["wizard"] = {"platform": "backstage"}
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"placeholder")
    project.data["inputs"]["videos"] = [file_record(str(source))]
    project.save()
    artifact = {
        "platform": "backstage",
        "version": 1,
        "sources": [{
            "path": str(source), "filename": "clip.mp4", "duration_sec": 8.0, "mtime": 10.0,
            "moments": [{"clip_start_sec": 0.0, "duration_sec": 6.0, "kind": "moment", "interest_score": 0.8}],
        }],
    }
    (project.artifacts_dir / "backstage_analysis.json").write_text(json.dumps(artifact), encoding="utf-8")
    BackstageEditStage().run(project, lambda *_: None)
    plan = json.loads((project.artifacts_dir / "backstage_edit.json").read_text(encoding="utf-8"))
    assert plan["platform"] == "backstage"
    assert plan["audio_mode"] == "original_per_clip"
    assert plan["chronological"] is True
    assert plan["segments"][0]["audio_original"] is True
    assert "sync_map" not in plan
    assert "master_start_sec" not in plan["segments"][0]


def test_backstage_music_filter_keeps_music_audible_and_ducks_camera_audio():
    graph = _backstage_music_filter(120.0)
    assert f"volume=eval=frame:volume='{BACKSTAGE_MUSIC_VOLUME:.3f}" in graph
    assert "if(lt(" not in graph
    assert "volume=0:enable='between(t," not in graph
    assert f"threshold={BACKSTAGE_MUSIC_DUCK_THRESHOLD:.3f}" in graph
    assert f"ratio={BACKSTAGE_MUSIC_DUCK_RATIO}" in graph
    assert "amix=inputs=2:duration=longest:dropout_transition=2:normalize=0" in graph
    assert "loudnorm=I=-16" in graph


def test_backstage_music_filter_scales_without_nested_expressions():
    ranges = [(float(index), float(index) + 1.0) for index in range(20, 180, 20)]
    graph = _backstage_music_filter(180.0, ranges)
    assert "if(lt(" not in graph
    assert "volume=eval=frame" in graph
    assert "between(t,4.000,20.000)" in graph


def test_backstage_music_runs_drop_short_gaps():
    runs = _background_music_runs(60.0, [(20.0, 25.0), (42.0, 60.0)])
    assert runs == [(4.0, 20.0), (25.0, 42.0)]


def test_backstage_music_uses_middle_of_source_and_interstitial_cards():
    assert _parchment_intervals(120.0, ["A", "B", "C"])[0][0] > 0
    assert _music_seek_offset("/does/not/exist", 30.0) == 0.0


def test_backstage_final_subtitles_use_mounted_timeline_and_manual_override():
    transcription = {"sources": [{"segments": [
        {"start_sec": 1.0, "end_sec": 2.0, "text": "automatic"},
        {"start_sec": 8.0, "end_sec": 9.0, "text": "later"},
    ]}]}
    segments = [{"duration_sec": 5.0, "subtitle_text": "Job Center"}, {"duration_sec": 5.0, "subtitle_text": ""}]
    assert _backstage_final_subtitle_entries(transcription, segments, 9.8) == [(0.0, 5.0, "Job Center"), (8.0, 9.0, "later")]


def test_story_bites_are_phrase_sized_and_feed_moment_selection():
    sources = [{"path": "/tmp/a.mp4", "filename": "a.mp4", "segments": [
        {"start_sec": 1.0, "end_sec": 3.0, "text": "We stopped the van and took the piano inside."},
    ]}]
    bites = extract_story_bites(sources)
    assert bites[0]["speaker"] == "unknown"
    assert bites[0]["start_sec"] == 1.0
    analysis = {"story_bites": bites, "sources": [{
        "path": "/tmp/a.mp4", "filename": "a.mp4", "moments": [{
            "clip_start_sec": 0.0, "duration_sec": 6.0, "interest_score": 0.2,
            "music": {"music_present": True, "music_score": 0.8},
        }],
    }]}
    moment = _flatten_moments(analysis)[0]
    assert moment["story_bites"]
    assert moment["selection_score"] < moment["interest_score"]


def test_backstage_subtitles_remap_source_time_to_export_time_after_crossfade():
    segments = [
        {"clip_start_sec": 10.0, "duration_sec": 5.0, "story_sequences": []},
        {"clip_start_sec": 40.0, "duration_sec": 8.0, "story_sequences": [{
            "start_sec": 42.0, "end_sec": 45.0, "english_text": "The story starts here.",
        }]},
    ]
    offsets = _backstage_segment_offsets(segments)
    entries = _backstage_subtitle_entries(segments, offsets)
    assert offsets[1] == 4.82
    assert entries == [(6.82, 9.82, "The story starts here.")]


def test_backstage_subtitle_cues_are_clipped_to_selected_cut():
    segments = [{"clip_start_sec": 10.0, "duration_sec": 5.0, "story_sequences": [], "transcription_segments": [
        {"start_sec": 8.0, "end_sec": 12.0, "text": "before and inside"},
        {"start_sec": 14.0, "end_sec": 18.0, "text": "inside and after"},
    ]}]
    entries = _backstage_subtitle_entries(segments, [20.0])
    assert entries == [(20.0, 22.0, "before and inside"), (24.0, 25.0, "inside and after")]


def test_backstage_word_boundaries_never_cut_inside_a_word():
    words = [{"start_sec": 10.0, "end_sec": 10.7, "word": "hello"}, {"start_sec": 10.8, "end_sec": 11.4, "word": "there"}]
    assert _snap_backstage_interval_to_words(10.2, 11.1, [{"words": words}]) == (10.0, 11.4)


def test_manual_cue_edit_mutates_text_only_and_preserves_timestamps():
    payload = {"cues": [{"id": "cue-1", "text": "old", "start_sec": 1.25, "end_sec": 3.5, "duration_sec": 2.25}]}
    before = json.loads(json.dumps(payload))
    update_backstage_cue_text(payload, "cue-1", "new")
    assert payload["cues"][0]["text"] == "new"
    for key in ("start_sec", "end_sec", "duration_sec"):
        assert payload["cues"][0][key] == before["cues"][0][key]


def test_backstage_export_graph_repairs_each_clip_before_concat():
    segments = [{"source_path": "/tmp/a.mp4", "clip_start_sec": 2, "duration_sec": 5, "background_music_policy": "background_music_only"}, {"source_path": "/tmp/b.mp4", "clip_start_sec": 4, "duration_sec": 7, "background_music_policy": "clip_audio_only"}]
    _inputs, graph, _label, duration, _muted = _backstage_single_pass_filtergraph(segments)
    assert duration == 12
    assert graph.count("aresample=48000:async=1:first_pts=0") == 2
    assert graph.count("apad") == 2
    assert graph.count("atrim=duration=") == 2
    assert "concat=n=2:v=1:a=1" in graph
