"""Clay memories in the automatic Show: word-cued held poses, editable placements on
the voice's clock, and an honest result for each placement."""
import json

import numpy as np
import pytest
from PIL import Image

from core import show_clay

COLOURS = [(200, 40, 40), (40, 200, 40), (40, 40, 200), (200, 200, 40), (40, 200, 200), (200, 40, 200)]


def _story(**extra):
    shot = lambda a, b, sheet, keys, **more: {"title": f"{a}", "start": a, "end": b, "sheet": sheet, "poses": [keys[0][1]],
                                              "poseKeys": [{"time": t, "pose": p} for t, p in keys], **more}
    return {"version": 3, "id": "memory", "character_id": "presenter-clay", "character_version": 2, "duration": 10.0,
            "source": "talk.mp4", "source_start": 20.0, "source_end": 30.0, "shots": [
                shot(0, 2, "a.png", [(0, 0), (0.6, 2)]), shot(2, 5, "b.png", [(2, 1), (3, 4)]),
                shot(5, 8, "b.png", [(5, 3), (6.5, 5)], close=True), shot(8, 10, "a.png", [(8, 5)])], **extra}


def _sheet(path):
    image = Image.new("RGB", (600, 240))
    for pose, colour in enumerate(COLOURS):
        image.paste(colour, (pose % 3 * 200, pose // 3 * 120, pose % 3 * 200 + 200, pose // 3 * 120 + 120))
    image.save(path)


def test_poses_follow_the_word_cues_and_hold_as_in_the_clay_tool():
    # Reference values from tools/qa/show_clay_pose_model.js run in Node.
    shot = {"poseKeys": [{"time": 0, "pose": 0}, {"time": 0.61, "pose": 2}, {"time": 1, "pose": 3}, {"time": 1.6, "pose": 4},
                         {"time": 1.88, "pose": 0}]}
    assert [show_clay.pose_at(shot, t) for t in (0, 0.6, 0.61, 0.99, 1.7, 3, -1)] == [0, 0, 2, 2, 4, 0, 0]
    assert show_clay.pose_at({"poses": [5, 1]}, 2) == 5                          # no cues: the first pose holds, no loop
    assert show_clay.pose_at({"poseKeys": [{"time": 4.24, "pose": 1}]}, 4) == 1
    assert show_clay.tile(2048, 768, 4, False) == pytest.approx((2048 / 3 + 2, 386, 2048 / 3 - 4, 380))
    assert show_clay.tile(2048, 768, 0, True) == pytest.approx((2 + (2048 / 3 - 4) * 0.04, 2 + 38, (2048 / 3 - 4) * 0.8, 304))
    with pytest.raises(ValueError):
        show_clay.validate_story({**_story(), "source_end": 31.0})


def test_the_first_proposal_uses_every_mode_and_placements_stay_inside_the_story():
    placements = show_clay.default_placements(_story())
    assert [p["mode"] for p in placements] == ["full", "bubble", "pip", "blend", "full"]
    assert placements[2]["start"] == 6.5 and placements[0]["end"] == 2           # the question cue, then back to the host before it
    checked = show_clay.check_placement({"mode": "pip", "start": -3, "end": 99, "size": 4, "x": 2}, _story())
    assert (checked["start"], checked["end"], checked["size"], checked["x"]) == (0.0, 10.0, 1.0, 1.0)
    with pytest.raises(ValueError):
        show_clay.check_placement({"mode": "spin", "start": 0, "end": 1}, _story())


def _project(tmp_path, monkeypatch, **story_extra):
    from core.project import create_project
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Show", str(tmp_path / "Clay.zuckervid"))
    source = tmp_path / "clay"
    source.mkdir()
    _sheet(source / "a.png")
    _sheet(source / "b.png")
    Image.new("RGB", (40, 20), (90, 90, 90)).save(source / "character.png")
    (source / "story.json").write_text(json.dumps(_story(**story_extra)))
    (source / "character.json").write_text(json.dumps({"id": "presenter-clay", "version": 2, "reference": "character.png"}))
    return project, source


# Source 20-30 s is the story; the edit keeps 18-24 s and 26-32 s (24-26 s was cut).
PLAN = {"fps": 30, "source": "/videos/talk.mp4", "shots": [
    {"start_frame": 540, "end_frame": 720, "zoom": 1.0, "focus": [0.5, 0.4], "face": [0.5, 0.35]},
    {"start_frame": 780, "end_frame": 960, "zoom": 2.0, "focus": [0.5, 0.4], "face": [0.5, 0.35]}]}


def test_an_imported_story_is_drawn_on_the_voice_clock_through_the_edit(tmp_path, monkeypatch):
    from core import show_bridge
    project, source = _project(tmp_path, monkeypatch)
    store = show_clay.ClayStore(project.folder)
    data = store.import_story(source / "story.json", source / "character.json")
    assert data["revision"] == 1 and len(data["placements"]) == 5 and data["characters"][0]["version"] == 2
    assert (source / "a.png").is_file()                                         # the originals stay where they were
    drawings, report = show_bridge.studio_layers(project.folder, PLAN)
    status = {item["id"]: item["status"] for item in report}
    assert status == {"clay-1": "applied", "clay-2": "applied", "clay-3": "applied", "clay-4": "applied", "clay-5": "applied"}
    # Edited 2.5 s is source 20.5 s: story 0.5 s, the first (full) shot, pose 0 until the 0.6 s cue.
    frame = np.zeros((180, 320, 3), np.uint8)
    drawings.draw(frame, 2.5)
    assert np.abs(frame[90, 160].astype(int) - COLOURS[0]).max() <= 70             # fading in over 0.3 s
    frame = np.zeros((180, 320, 3), np.uint8)
    drawings.draw(frame, 2.0 + 0.7 + 0.3)                                       # story 1.0: the "dream" cue held
    assert np.abs(frame[90, 160].astype(int) - COLOURS[2]).max() <= 3
    # Source 24-26 s was cut: story 4-6 s never shows; edited 6.0 s is source 26 s.
    pip = next(layer for layer in drawings.layers if layer.effect_id == "clay-3")
    assert pip.section_time(6.0) is None and pip.section_time(6.6) == pytest.approx(6.6)
    frame = np.zeros((180, 320, 3), np.uint8)
    drawings.draw(frame, 7.5)                                                    # story 7.5: the small window, pose 5 (close)
    pip = show_clay.DEFAULTS["pip"]
    x, y = int(pip["x"] * 320), int(pip["y"] * 180)
    assert np.abs(frame[y, x].astype(int) - COLOURS[5]).max() <= 3 and frame[150, 280].max() == 0
    bubble = np.zeros((720, 1280, 3), np.uint8)
    next(layer for layer in drawings.layers if layer.effect_id == "clay-2").drawer(bubble, 3.5, (0, 0, 1.0), 1.5)
    spot = show_clay.DEFAULTS["bubble"]
    cx, cy, bw = spot["x"] * 1280, spot["y"] * 720, spot["size"] * 1280
    assert np.abs(bubble[int(cy), int(cx)].astype(int) - COLOURS[4]).max() <= 6      # story 3.5: pose 4 in the bubble
    head = (0.5 * 1280, 0.35 * 720)                                             # the face in the plan's first shot
    between = bubble[int(cy):int(head[1]), int(cx + bw * 0.75):int(head[0])]
    assert between.sum(axis=2).max() > 0                                        # little circles lead off towards the head
    assert bubble[int(head[1]), int(head[0])].max() == 0                        # and stop short of it


def test_every_placement_gets_an_honest_result(tmp_path, monkeypatch):
    from core import show_bridge
    project, source = _project(tmp_path, monkeypatch)
    store = show_clay.ClayStore(project.folder)
    data = store.import_story(source / "story.json")
    data["placements"] = [
        {"id": "off", "story": "memory", "mode": "full", "start": 0, "end": 1, "enabled": False},
        {"id": "gone", "story": "memory", "mode": "full", "start": 4.2, "end": 5.8},      # source 24.2-25.8: cut
        {"id": "nowhere", "story": "other", "mode": "full", "start": 0, "end": 1}]
    store.save(data, revision=data["revision"])
    assert len(list((project.folder / "show_studio" / "history").glob("clay-r1-*.json"))) == 1
    with pytest.raises(ValueError):
        store.save(data, revision=1)                                            # stale: someone saved in between
    _, report = show_bridge.studio_layers(project.folder, PLAN)
    status = {item["id"]: (item["status"], item["detail"]) for item in report}
    assert status["off"][0] == "disabled" and status["gone"] == ("failed", "Its moment was cut from the automatic edit")
    assert status["nowhere"][0] == "failed" and "not in this project" in status["nowhere"][1]
    _, report = show_bridge.studio_layers(project.folder, {**PLAN, "source": "/videos/other.mp4"})
    assert {item["id"]: item["status"] for item in report}["gone"] == "failed"
    assert any("made for talk.mp4" in item["detail"] for item in report)


def test_a_missing_pose_sheet_is_reported_and_nothing_is_drawn_instead(tmp_path, monkeypatch):
    from core import show_bridge
    project, source = _project(tmp_path, monkeypatch)
    show_clay.ClayStore(project.folder).import_story(source / "story.json")
    library = project.folder / "library" / "assets"
    for path in library.iterdir():
        if Image.open(path).size == (600, 240):
            path.unlink()
            break
    drawings, report = show_bridge.studio_layers(project.folder, PLAN)
    assert not drawings and {item["status"] for item in report} == {"failed"}
    assert all("pose sheet" in item["detail"] and "missing" in item["detail"] for item in report)


def test_placements_are_read_and_edited_through_the_app(tmp_path, monkeypatch):
    import server.api as api_module
    project, source = _project(tmp_path, monkeypatch)
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({**PLAN, "edited_duration": 12.0, "cuts": 1}))
    show_clay.ClayStore(project.folder).import_story(source / "story.json")
    client = api_module.create_app(project_path=str(project.folder)).test_client()
    state = client.get("/api/v1/show/clay").get_json()
    first = state["placements"][0]
    assert state["revision"] == 1 and first["mode"] == "full" and first["edited"] == [[2.0, 4.0, 0.0]]   # story 0-2 s is edited 2-4 s
    assert state["stories"][0]["shots"][0]["cues"] == [[0, 0, ""], [0.6, 2, ""]]
    edited = [{**item, "x": 0.2} if item["id"] == "clay-3" else item for item in state["placements"]]
    saved = client.put("/api/v1/show/clay/placements", json={"revision": 1, "placements": edited})
    assert saved.status_code == 200 and saved.get_json()["revision"] == 2
    assert next(p for p in saved.get_json()["placements"] if p["id"] == "clay-3")["x"] == 0.2
    stale = client.put("/api/v1/show/clay/placements", json={"revision": 1, "placements": edited})
    assert stale.status_code == 409
    bad = client.put("/api/v1/show/clay/placements", json={"revision": 2, "placements": [{**edited[0], "mode": "spin"}]})
    assert bad.status_code == 400 and show_clay.ClayStore(project.folder).load()["revision"] == 2


def test_a_small_window_or_bubble_moves_off_the_host_s_face():
    spot = {"mode": "pip", "x": 0.5, "y": 0.4, "size": 0.3}
    moved = show_clay.clear_of_face(spot, (0.55, 0.4), 1.0, 1280, 720, 384, 216, 7)
    assert moved["x"] < 0.5 and abs(moved["x"] * 1280 + 192 + 7 - (0.55 * 1280 - 0.085 * 1280)) < 1   # just left of the face
    assert show_clay.clear_of_face(spot, (0.9, 0.85), 1.0, 1280, 720, 384, 216, 7) is spot             # clear already
    assert show_clay.clear_of_face({**spot, "avoidFace": False}, (0.55, 0.4), 1.0, 1280, 720, 384, 216, 7)["x"] == 0.5
    close = show_clay.clear_of_face(spot, (0.5, 0.45), 1.65, 1280, 720, 384, 216, 7)
    assert close["x"] != 0.5 or close["y"] < 0.4                                # a closer framing needs a bigger berth


def test_square_pose_cells_keep_puppet_proportions_when_contained():
    record = _story()
    record['shots'][0]['fit'] = 'contain'
    # Six square cells; a central square must stay square on a wide screen.
    sheet = np.zeros((240, 360, 3), dtype=np.uint8)
    sheet[30:90, 30:90] = (240, 180, 20)
    story = show_clay.ClayStory(record, {'a.png': sheet, 'b.png': sheet})
    frame = story.picture(0.2, 320, 180)
    ys, xs = np.where(frame[:, :, 0] > 200)
    assert abs((xs.max() - xs.min()) - (ys.max() - ys.min())) <= 1
    assert tuple(frame[90, 0]) == (23, 32, 25)
    # Changing the fit mode cannot reuse a differently shaped cached picture.
    record['shots'][0].pop('fit')
    stretched = story.picture(0.2, 320, 180)
    ys, xs = np.where(stretched[:, :, 0] > 200)
    assert xs.max() - xs.min() > (ys.max() - ys.min()) * 1.5


def test_timeline_clay_can_span_source_cuts_and_matches_window_preview(tmp_path, monkeypatch):
    from core.show import window_plan
    project, source = _project(tmp_path, monkeypatch)
    store=show_clay.ClayStore(project.folder);data=store.import_story(source/'story.json',placements=False)
    placement=show_clay.check_placement(dict(id='edited-clay',story='memory',mode='full',start=0,end=10,
        timeline_at=1,timeline_duration=8,fade=0),_story())
    store.save({**data,'placements':[placement]},revision=data['revision'])
    full,report=show_clay.clay_layers(project.folder,PLAN)
    assert report[0]['status']=='applied' and full[0].pieces==[(1.,9.,0.)]
    part=window_plan(PLAN,3,6)
    sliced,_=show_clay.clay_layers(project.folder,part)
    assert sliced[0].pieces==[(0.,3.,2.)]
    a=np.zeros((90,160,3),np.uint8);b=a.copy()
    full[0].drawer(a,full[0].section_time(4),[0,0,1],4)
    sliced[0].drawer(b,sliced[0].section_time(1),[0,0,1],1)
    assert np.array_equal(a,b) and a.max()>0
    # Lengthening preserves the story, not a compressed copy bounded by a source shot.
    placement['timeline_duration']=10
    store.save({**store.load(),'placements':[placement]},revision=store.load()['revision'])
    longer,_=show_clay.clay_layers(project.folder,PLAN)
    assert longer[0].pieces==[(1.,11.,0.)]
    assert store.load_story('memory').record['duration']==10


def test_clay_draft_is_a_real_review_movie_and_only_approval_places_it(tmp_path, monkeypatch):
    import subprocess
    project, source = _project(tmp_path, monkeypatch)
    job={'id':'a'*32,'purpose':'clay','status':'generating','direction':'Simón greets a neighbour 日本, pauses, then sits down',
         'selected_text':'Everyone comes and says hello','at':1,'duration':1.2}
    folder=project.folder/'assets/show-ai'/job['id'];folder.mkdir(parents=True)
    _sheet(folder/'image.png')
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps(PLAN))
    output=show_clay.prepare_draft(project,job,folder,'ffmpeg')
    record=json.loads((folder/'story.json').read_text(encoding='utf-8'))
    assert record['direction']==job['direction']
    meta=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))['streams'][0]
    assert int(meta['nb_frames'])==30 and float(meta['duration'])==pytest.approx(1.2)
    assert show_clay.ClayStore(project.folder).load()['placements']==[]
    with pytest.raises(ValueError,match='completed'):show_clay.apply_draft(project,job,folder)
    job['status']='ready'
    saved=show_clay.apply_draft(project,job,folder,mode='full')
    assert len(saved['placements'])==1 and saved['placements'][0]['timeline_duration']==1.2
    assert len(show_clay.apply_draft(project,job,folder)['placements'])==1
    story=show_clay.ClayStore(project.folder).load_story('clay-'+job['id']).record
    keys=story['shots'][0]['poseKeys']
    assert [k['pose'] for k in keys]==list(range(6))
    assert show_clay.pose_at(story['shots'][0],.5)==2 # hold during narration, no wrapping


@pytest.mark.parametrize('changes',[{'timeline_at':float('nan'),'timeline_duration':1},{'timeline_at':0,'timeline_duration':-.1}])
def test_clay_timeline_rejects_invalid_intervals(changes):
    with pytest.raises(ValueError,match='timeline'):
        show_clay.check_placement(dict(mode='full',start=0,end=3,**changes),_story())
