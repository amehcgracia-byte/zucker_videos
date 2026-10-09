from pathlib import Path

from core import fillers, scene_tags
from core.stages.edit import _youtube_multicam_plan


def _sample(shot="audience", time="night", rain=0.01, fire=0.01, brightness=60.0, sharpness=300.0):
    return {"shot": {shot: 0.9, "performance": 0.1}, "time": {time: 0.9}, "place": {"indoor": 0.8},
            "rain": rain, "fire": fire, "brightness": brightness, "sharpness": sharpness, "model": True}


def _window(start, end, shot="audience", time="night", rain="no", rain_p=0.01, fire="no", fire_p=0.01, path="/tmp/filler.mp4"):
    return {"start_sec": start, "end_sec": end, "shot": shot, "shot_confidence": 0.9, "sharpness": 300.0,
            "conditions": {"time": time, "place": "indoor", "rain": rain, "rain_probability": rain_p,
                           "fire": fire, "fire_probability": fire_p},
            "source_path": path, "clip_path": path, "filename": Path(path).name, "score": 0.9}


def test_windows_group_one_shot_type_and_skip_performance_and_darkness():
    samples = [_sample()] * 11 + [_sample(shot="performance")] * 3 + [_sample(brightness=2.0)] * 3 + [_sample(shot="face")] * 3
    windows = fillers.build_windows(samples, 20.0)
    assert [(w["shot"], w["start_sec"], w["end_sec"]) for w in windows] == [
        ("audience", 0.0, 8.0), ("audience", 8.0, 11.0), ("face", 17.0, 20.0)]
    assert all(fillers.MIN_WINDOW_SEC <= w["end_sec"] - w["start_sec"] <= fillers.MAX_WINDOW_SEC for w in windows)


def test_conditions_flag_possible_rain_as_unknown_not_dry():
    summary = fillers.summarize_conditions([_sample(rain=0.02)] * 8 + [_sample(rain=0.6)] * 2)
    assert summary["time"] == "night"
    assert summary["rain"] == "unknown" and summary["fire"] == "no"


def test_rain_and_fire_are_never_invented():
    dry_night = {"time": "night", "rain": "no", "fire": "no"}
    assert not fillers.compatibility(_window(0, 4, rain="yes", rain_p=0.8), dry_night)[0]
    # Unconfirmed project weather counts as dry; a doubtful window is rejected too.
    assert not fillers.compatibility(_window(0, 4, rain="unknown", rain_p=0.4), {"time": "night", "rain": "unknown"})[0]
    assert not fillers.compatibility(_window(0, 4, fire="yes", fire_p=0.9), dry_night)[0]
    assert fillers.compatibility(_window(0, 4, rain="yes", rain_p=0.8), {"time": "night", "rain": "yes", "fire": "no"})[0]


def test_day_and_night_never_mix_but_unknown_and_sunset_may():
    assert not fillers.compatibility(_window(0, 4, time="day"), {"time": "night"})[0]
    assert fillers.compatibility(_window(0, 4, time="sunset"), {"time": "night"})[0]
    assert fillers.compatibility(_window(0, 4, time="unknown"), {"time": "night"})[0]
    assert not fillers.compatibility(_window(0, 4, shot="performance"), {"time": "night"})[0]


def test_choice_rotates_and_respects_the_three_minute_cooldown():
    bank = {"windows": [_window(0, 6), _window(10, 16)]}
    history: list[dict] = []
    first = fillers.choose_filler(bank, 30.0, 5.0, history, "seed", 1)
    second = fillers.choose_filler(bank, 36.0, 5.0, history, "seed", 2)
    assert first["key"] != second["key"]
    assert fillers.choose_filler(bank, 42.0, 5.0, history, "seed", 3) is None
    assert fillers.choose_filler(bank, 30.0 + fillers.REUSE_COOLDOWN_SEC + 1, 5.0, history, "seed", 4) is not None


def test_overrides_only_accept_known_values(tmp_path):
    class Project:
        data = {"settings": {"edit": {"scene_conditions": {"time": "night", "rain": "maybe", "fire": "no"}}}}
    assert fillers.condition_overrides(Project()) == {"time": "night", "fire": "no"}


def test_plan_fills_a_camera_gap_with_a_compatible_filler():
    coverage = {
        "platform": "youtube",
        "window": {"title": "Song", "start_sec": 0.0, "duration_sec": 24.0},
        "sources": [
            {"path": "/tmp/a.mp4", "filename": "a.mp4", "offset_sec": 0.0, "duration_sec": 8.0, "confidence": 9.0},
            {"path": "/tmp/b.mp4", "filename": "b.mp4", "offset_sec": 16.0, "duration_sec": 8.0, "confidence": 9.0},
        ],
    }
    beats = {"bars_sec": [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0, 24.0], "sections_sec": []}
    bank = {"enabled": True, "model": True, "conditions": {"time": "night", "rain": "no", "fire": "no"},
            "windows": [_window(0, 8), _window(20, 28, shot="face")], "rejected": {"rain in filler but not in the video": 1}}

    without = _youtube_multicam_plan(coverage, beats)
    plan = _youtube_multicam_plan(coverage, beats, filler_bank=bank)

    assert without["gaps"] and not plan["gaps"]
    filler_segments = [segment for segment in plan["segments"] if segment.get("filler")]
    assert filler_segments
    gap_stretches = {(gap["start_sec"], gap["end_sec"]) for gap in without["gaps"]}
    gap_start, gap_end = min(start for start, _ in gap_stretches), max(end for _, end in gap_stretches)
    assert all(gap_start - 1e-6 <= segment["master_start_sec"] and
               segment["master_start_sec"] + segment["duration_sec"] <= 16.0 + 1e-6 for segment in filler_segments)
    # Camera b takes over again exactly where its coverage starts.
    assert any(segment["filename"] == "b.mp4" and abs(segment["master_start_sec"] - 16.0) < 1e-6 for segment in plan["segments"])
    assert gap_end >= 16.0
    assert all(segment["filler_conditions"]["rain"] == "no" for segment in filler_segments)
    assert plan["fillers"]["used_segments"] == len(filler_segments)
    starts = [segment["master_start_sec"] for segment in plan["segments"]]
    assert starts == sorted(starts)


def test_tagging_degrades_to_brightness_without_the_model(monkeypatch):
    import numpy as np
    monkeypatch.setattr(scene_tags, "_labels", lambda: None)
    dark = np.zeros((64, 64, 3), np.uint8)
    result = scene_tags.analyze_frames([dark])[0]
    assert result["model"] is False and result["time"] == {"night": 1.0}


def test_filler_endpoint_marks_registered_videos_and_drops_their_subject(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    from core.project import create_project, load_project
    from server.api import create_app
    folder = tmp_path / "fillers.zuckervid"
    project = create_project("Fillers", str(folder))
    camera, interview = str(tmp_path / "camera.mp4"), str(tmp_path / "interview.mov")
    project.data["inputs"]["videos"] = [{"path": camera}, {"path": interview}]
    project.data["settings"].setdefault("edit", {})["camera_subjects"] = {interview: "singer"}
    project.save()
    client = create_app(project_path=str(folder)).test_client()
    assert client.post("/api/v1/settings/fillers", json={"fillers": [interview]}).status_code == 200
    edit = load_project(str(folder)).data["settings"]["edit"]
    assert edit["fillers"] == [interview] and interview not in edit["camera_subjects"]
    assert client.post("/api/v1/settings/fillers", json={"fillers": ["/unregistered.mov"]}).status_code == 400
    assert client.post("/api/v1/settings/fillers", json={"fillers": "nope"}).status_code == 400


def test_scene_conditions_travel_with_the_run_and_ignore_auto(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    from core.project import create_project
    from server.api import _apply_run_options
    project = create_project("Scene", str(tmp_path / "scene.zuckervid"))
    _apply_run_options(project, {"scene_conditions": {"time": "night", "place": "auto", "rain": "no", "fire": "lava"}})
    assert project.data["settings"]["edit"]["scene_conditions"] == {"time": "night", "rain": "no"}


def test_sync_never_tries_to_sync_filler_videos(tmp_path, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    from core.project import create_project
    from core.stages import sync
    project = create_project("Sync", str(tmp_path / "sync.zuckervid"))
    camera, interview = str(tmp_path / "camera.mp4"), str(tmp_path / "interview.mov")
    project.data["inputs"]["master"] = {"path": str(tmp_path / "master.wav")}
    project.data["inputs"]["videos"] = [{"path": camera}, {"path": interview}]
    project.data["settings"].setdefault("edit", {})["fillers"] = [interview]
    synced = []
    monkeypatch.setattr(sync, "record_is_usable_camera_video", lambda record: True)
    monkeypatch.setattr(sync, "load_or_compute_master_envelope", lambda project: None)
    monkeypatch.setattr(sync, "media_duration", lambda path: 60.0)
    monkeypatch.setattr(sync, "load_sync_map", lambda *args, **kwargs: {})
    monkeypatch.setattr(sync, "sync_clip", lambda project, record, env, threshold: synced.append(record["path"]) or {"offset_sec": 0.0})
    monkeypatch.setattr(sync, "write_artifact_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(sync, "load_song_boundaries", lambda project: [])
    sync.SyncStage().run(project, lambda *args: None)
    assert synced == [camera]


def test_frontend_offers_filler_and_scene_controls():
    root = Path(__file__).parents[1]
    source = (root / "web" / "app.js").read_text(encoding="utf-8")
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    assert "data-filler-video" in source and 'api("/settings/fillers"' in source
    assert "scene_conditions: sceneConditionsFromForm()" in source
    assert all(f'data-scene="{key}"' in html for key in ("time", "place", "rain", "fire"))
