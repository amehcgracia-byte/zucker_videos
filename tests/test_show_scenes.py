"""Show scenes (the shared storyboard model) and the opening: title card, logo and intro."""
import subprocess

import numpy as np
import pytest
from PIL import Image

from core import show_scenes
from core.show import preview_scene as render_scene_preview
from core.ffmpeg import ffprobe, tool_status
from core.show_identity import card_picture, with_opening
from core.show_proposals import proposal


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _talk(seconds: float):
    words, t, n = [], 0.0, 0
    while t < seconds - 0.5:
        n += 1
        words.append((f"word{n}." if n % 12 == 0 else f"word{n}", round(t, 2), round(t + 0.35, 2)))
        t += 0.42
    return words


def test_scenes_break_at_sentence_ends_or_pauses_within_their_limits():
    words = _talk(100.0)
    edges = show_scenes.split(words, [31.0, 58.0], 100.0)
    assert edges[0][0] == 0.0 and edges[-1][1] == 100.0
    assert all(show_scenes.MIN_SEC <= b - a <= show_scenes.MAX_SEC for a, b in edges[:-1])
    ends = {end for text, _, end in words if text.endswith(".")} | {31.0, 58.0}
    assert all(b in ends for _, b in edges[:-1])          # never in the middle of a sentence


def test_accepting_a_scene_approves_what_is_ready_and_plain_leaves_it_untouched():
    ready = proposal("word", 5.0, 2.0, reason="Director: WOW", kind="word", text="WOW")
    incomplete = proposal("character", 8.0, 0.0, reason='You say "this guy could speak"', source="x.mp4")
    incomplete["needs"] = ["region", "voice"]
    later = proposal("cutaway", 40.0, 2.0, reason='You say "millionaires"', source="y.mp4", source_start=1.0)
    plan = {"edited_duration": 60.0}
    document = show_scenes.build(plan, _talk(60.0), [30.0], [ready, incomplete, later], title="Channel")
    first, second = document["scenes"][0], document["scenes"][1]
    assert set(first["effects"]) == {ready["id"], incomplete["id"]} and second["effects"] == [later["id"]]
    assert first["proposal"].startswith("Proposed: a shouted word")
    show_scenes.decide(document, [ready, incomplete, later], first["id"], "accepted")
    assert ready["status"] == "approved" and incomplete["status"] == "proposed"      # still needs its region and voice
    show_scenes.decide(document, [ready, incomplete, later], second["id"], "plain")
    assert later["status"] == "discarded" and second["proposal"] == "Left without effect."
    exported = show_scenes.decisions(document)
    assert [scene["decision"] for scene in exported["scenes"]] == ["accepted", "plain"]
    assert exported["title"] == "Channel" and "asset" not in str(exported)          # no media inside
    with pytest.raises(ValueError):
        show_scenes.decide(document, [], first["id"], "maybe")


def test_placed_pictures_keep_the_storyboard_position_and_size(tmp_path):
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(tmp_path / "sun.png")
    document = {"scenes": [{"start": 10.0, "end": 30.0, "decision": "accepted",
                            "overlays": [{"asset": str(tmp_path / "sun.png"), "x": 12, "y": 12, "width": 23}]},
                           {"start": 30.0, "end": 50.0, "decision": "plain", "overlays": [{"asset": str(tmp_path / "sun.png")}]}]}
    items = show_scenes.overlay_items(document)
    assert len(items) == 1
    item = items[0]
    assert item["width"] == pytest.approx(0.23) and item["x"] == pytest.approx(0.12 + 0.115)
    assert item["y"] == pytest.approx(0.12 + 0.23 * 16 / 9 * 0.5 / 2)              # half as tall as wide


def test_the_card_shows_the_logo_as_it_is(tmp_path):
    logo = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(100):
        logo.putpixel((x, 50), (10, 200, 30, 255))
    logo.save(tmp_path / "logo.png")
    card = np.asarray(card_picture("Channel of Chema", tmp_path / "logo.png", 640, 360))
    green = np.argwhere((card[..., 1] > 150) & (card[..., 0] < 60))
    assert len(green) > 50                                   # the logo's own pixels are on the card


def test_logo_card_and_intro_go_before_the_show_with_their_sound(tmp_path):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x404040:s=1920x1080:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(tmp_path / "show.mp4")], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x480:r=25:d=1.5", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(tmp_path / "intro.mp4")], check=True)       # no sound, another size and rate
    Image.new("RGBA", (120, 120), (240, 200, 0, 255)).save(tmp_path / "logo.png")
    added = with_opening(tmp_path / "show.mp4", tmp_path / "final.mp4", title="My show", logo=tmp_path / "logo.png",
                         intro=tmp_path / "intro.mp4", ffmpeg=ffmpeg)
    assert added == pytest.approx(3.5, abs=0.1)
    probe = ffprobe(str(tmp_path / "final.mp4"))
    assert float(probe["format"]["duration"]) == pytest.approx(6.5, abs=0.15)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (1920, 1080)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "final.mp4"), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    sound = np.frombuffer(raw, dtype=np.float32)
    assert np.sqrt(np.mean(sound[1000:27000] ** 2)) < 0.01               # card and intro: silent
    tail = sound[30000:50000]
    spectrum = np.abs(np.fft.rfft(tail))
    assert abs(np.argmax(spectrum) * 8000 / len(tail) - 440) < 15       # then the show, in step


def _storyboard(tmp_path, monkeypatch):
    import json

    import server.api as api_module
    from core import show_proposals
    from core.project import create_project
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Show", str(tmp_path / "Board.zuckervid"))
    video, intro = tmp_path / "talk.mp4", tmp_path / "intro.mp4"
    video.write_bytes(b"x")
    intro.write_bytes(b"y")
    project.data["inputs"]["videos"] = [{"path": str(video)}, {"path": str(intro)}]
    project.save()
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 90.0, "cuts": 5, "source": str(video), "shots": []}))
    word = proposal("word", 5.0, 2.0, reason="Director: WOW", kind="word", text="WOW")
    cut = proposal("cutaway", 50.0, 2.0, reason='You say "money"', source=str(video), source_start=1.0)
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [word, cut], source=str(video), resources=[])
    document = show_scenes.build({"edited_duration": 90.0}, _talk(90.0), [30.0, 60.0], [word, cut])
    show_scenes.save(project.artifacts_dir / "show_scenes.json", document)
    rendered = []

    def fake_preview(project, scene_id, **kwargs):
        from core.show import scene_preview_path
        rendered.append(scene_id)
        out = scene_preview_path(project, scene_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"preview")
        return out
    monkeypatch.setattr("core.show.preview_scene", fake_preview)
    app = api_module.create_app(project_path=str(project.folder))
    return project, app.test_client(), document, rendered


def test_the_storyboard_renders_effects_only_when_asked_and_keeps_every_decision(tmp_path, monkeypatch):
    import json
    import time
    project, client, document, rendered = _storyboard(tmp_path, monkeypatch)
    board = client.get("/api/v1/show/scenes").get_json()
    assert len(board["scenes"]) == len(document["scenes"]) >= 3
    time.sleep(0.2)
    assert rendered == []                                   # each scene plays from the original at once, nothing rendered
    assert board["source_url"].startswith("/api/v1/show/source?path=") and "segments" in board["scenes"][0]
    client.post(f"/api/v1/show/scenes/{board['scenes'][0]['id']}/preview")      # "Preview with effects"
    for _ in range(50):
        if rendered:
            break
        time.sleep(0.05)
    assert rendered == [board["scenes"][0]["id"]]
    board = client.get("/api/v1/show/scenes").get_json()
    assert board["scenes"][0]["preview"]["status"] == "ready" and board["scenes"][0]["preview"]["url"]
    assert client.post(f"/api/v1/show/scenes/{board['scenes'][0]['id']}/preview", json={"next": [board["scenes"][1]["id"]]}).get_json()["status"] == "ready"
    for _ in range(50):                                     # made already: only the next scene is prepared
        if len(rendered) == 2:
            break
        time.sleep(0.05)
    assert rendered == [board["scenes"][0]["id"], board["scenes"][1]["id"]]
    first = board["scenes"][0]["id"]
    decided = client.post(f"/api/v1/show/scenes/{first}/decision", json={"decision": "plain"}).get_json()
    assert decided["decision"] == "plain"
    saved = json.loads((project.artifacts_dir / "show_scenes.json").read_text())
    assert saved["scenes"][0]["decision"] == "plain"                               # kept in the project
    stored = json.loads((project.artifacts_dir / "show_proposals.json").read_text())["proposals"]
    assert next(item for item in stored if item["kind"] == "word")["status"] == "discarded"
    assert client.post(f"/api/v1/show/scenes/{first}/decision", json={"decision": "maybe"}).status_code == 400
    exported = client.get("/api/v1/show/scenes/decisions")
    assert "attachment" in exported.headers["Content-Disposition"] and exported.get_json()["scenes"][0]["decision"] == "plain"


def test_the_scene_you_ask_for_is_prepared_at_once_and_only_once(tmp_path, monkeypatch):
    import threading
    import time
    project, client, document, rendered = _storyboard(tmp_path, monkeypatch)
    busy = threading.Event()

    def slow_preview(project, scene_id, **kwargs):
        if not rendered:
            for _ in range(250):                           # the first preview keeps the worker busy…
                kwargs["cancel"]()                         # …until the scene the person asks for stops it
                if busy.is_set():
                    break
                time.sleep(0.02)
        from core.show import scene_preview_path
        rendered.append(scene_id)
        out = scene_preview_path(project, scene_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"preview")
        return out
    monkeypatch.setattr("core.show.preview_scene", slow_preview)
    ids = [scene["id"] for scene in client.get("/api/v1/show/scenes").get_json()["scenes"]]
    last = ids[-1]
    first = client.post(f"/api/v1/show/scenes/{ids[1]}/preview")              # keeps the worker busy
    assert first.status_code == 202
    for _ in range(3):                                     # asked again while it waits
        assert client.post(f"/api/v1/show/scenes/{last}/preview").status_code == 202
    busy.set()

    def wait_for(count):
        for _ in range(100):
            if len(rendered) >= count:
                return
            time.sleep(0.05)
    wait_for(2)
    client.post(f"/api/v1/show/scenes/{ids[0]}/preview")
    wait_for(3)
    time.sleep(0.2)
    # The asked scene stopped the other one, which went on in the background lane.
    assert sorted(rendered[:2]) == sorted([last, ids[1]]) and rendered[2] == ids[0]
    assert rendered.count(last) == 1 and rendered.count(ids[1]) == 1


def test_identity_intro_and_pictures_come_only_from_the_project_and_its_library(tmp_path, monkeypatch):
    from PIL import Image
    project, client, document, _ = _storyboard(tmp_path, monkeypatch)
    intro = str(tmp_path / "intro.mp4")
    assert client.patch("/api/v1/show/scenes/identity", json={"intro": str(tmp_path / "other.mp4")}).status_code == 400
    identity = client.patch("/api/v1/show/scenes/identity", json={"title": "Channel of Chema", "intro": intro, "seconds": 9}).get_json()
    assert identity["identity"]["title"] == "Channel of Chema" and identity["identity"]["seconds"] == 6.0 and identity["intro"]["path"] == intro
    scene = document["scenes"][1]["id"]
    outside = tmp_path / "outside.png"
    Image.new("RGB", (10, 10)).save(outside)
    assert client.post(f"/api/v1/show/scenes/{scene}/overlays", json={"overlays": [{"asset": str(outside)}]}).status_code == 400
    from server.api import app_home
    flyers = app_home() / "ReelOverlays"
    flyers.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(flyers / "sun.png")
    library = client.get("/api/v1/show/library").get_json()["items"]
    sun = next(item for item in library if item["name"] == "sun")
    placed = client.post(f"/api/v1/show/scenes/{scene}/overlays", json={"overlays": [{"asset": sun["path"], "x": 10, "y": 15, "width": 25}]}).get_json()
    assert placed["overlays"][0]["width"] == 25 and placed["decision"] == "modified"
    assert client.get(sun["url"]).status_code == 200


def test_animate_element_actions_and_a_talking_character_with_its_voice(tmp_path):
    import json
    folder = tmp_path / "Characters" / "Yellow toy"
    folder.mkdir(parents=True)
    Image.new("RGBA", (100, 200), (250, 220, 40, 255)).save(folder / "cutout.png")
    (folder / "character.json").write_text(json.dumps({"mouth": {"rect": [0.3, 0.5, 0.4, 0.1]}, "pivot": [0.5, 1.0],
                                                        "voice": ["/v/C0274.MP4", 268.9, 6.0]}))
    sun = tmp_path / "sun.png"
    Image.new("RGBA", (100, 100), (255, 200, 0, 255)).save(sun)
    scene = {"start": 0.0, "end": 20.0, "decision": "accepted", "overlays": [
        {"asset": str(sun), "x": 10, "y": 10, "width": 20, "action": "up", "intensity": 1.0},
        {"asset": str(sun), "x": 10, "y": 10, "width": 20, "action": "pulse"},
        {"asset": str(folder / "cutout.png"), "x": 60, "y": 40, "width": 10, "action": "path", "path": [[0, 60, 40], [1, 90, 45]], "hops": 3},
        {"asset": str(folder / "cutout.png"), "x": 60, "y": 40, "width": 10, "action": "talk", "at": 5.0}]}
    up, pulse, run, talk = show_scenes.overlay_items({"scenes": [scene]})
    assert up["path"][1][2] == pytest.approx(up["path"][0][2] - 0.15)            # moves up
    assert pulse["effect"] == "pulse"
    assert run["path"] == [[0.0, 0.6, 0.4], [1.0, 0.9, 0.45]] and run["hops"] == 3 and run["pivot"] == [0.5, 1.0]
    assert talk["insert"] == "character" and talk["source"] == "host" and talk["voice"][1] == 268.9   # its own recorded voice
    assert talk["overlays"][0]["mouth"] == {"rect": [0.3, 0.5, 0.4, 0.1]}
    assert talk["zoom_sec"] == 0 and talk["overlays"][0]["effect"] == "none"     # by default it is there at once


@pytest.mark.parametrize("entrance, zoom, zoom_sec, effect", [("cut", 1.0, 0, "none"), ("zoom", 1.25, 0.7, "none"),
                                                              ("bounce", 1.0, 0, "rise")])
def test_a_talking_character_can_enter_three_ways(tmp_path, entrance, zoom, zoom_sec, effect):
    import json
    folder = tmp_path / "Characters" / "Yellow toy"
    folder.mkdir(parents=True)
    Image.new("RGBA", (100, 200), (250, 220, 40, 255)).save(folder / "cutout.png")
    (folder / "character.json").write_text(json.dumps({"mouth": {"rect": [0.3, 0.5, 0.4, 0.1]}, "voice": ["/v/C0274.MP4", 268.9, 6.0]}))
    scene = {"start": 0.0, "end": 20.0, "decision": "accepted", "overlays": [
        {"asset": str(folder / "cutout.png"), "x": 60, "y": 40, "width": 10, "action": "talk", "at": 5.0, "entrance": entrance}]}
    [talk] = show_scenes.overlay_items({"scenes": [scene]})
    assert (talk["entrance"], talk["zoom"], talk["zoom_sec"], talk["overlays"][0]["effect"]) == (entrance, zoom, zoom_sec, effect)
    if entrance == "zoom":                                                       # the camera pushes in on the character
        assert talk["focus"] == [pytest.approx(0.65), pytest.approx(talk["overlays"][0]["y"])]


def test_changed_effect_invalidates_ready_preview_and_accept_preserves_current_preview(tmp_path, monkeypatch):
    import time
    project, client, document, rendered = _storyboard(tmp_path, monkeypatch)
    sid = document['scenes'][0]['id']
    endpoint = f'/api/v1/show/scenes/{sid}/preview'
    client.post(endpoint)
    for _ in range(100):
        board = client.get('/api/v1/show/scenes').get_json()
        if board['scenes'][0]['preview']['status'] == 'ready':
            break
        time.sleep(.01)
    assert board['scenes'][0]['preview']['status'] == 'ready'
    assert client.post(f'/api/v1/show/scenes/{sid}/decision', json={'decision':'accepted'}).status_code == 200
    assert client.get('/api/v1/show/scenes').get_json()['scenes'][0]['preview']['status'] == 'ready'
    pid = document['scenes'][0]['effects'][0]
    assert client.patch(f'/api/v1/show/review/{pid}', json={'text':'Changed'}).status_code == 200
    scene = client.get('/api/v1/show/scenes').get_json()['scenes'][0]
    assert scene['decision'] == 'modified' and scene['preview']['status'] == 'none'
    assert client.patch(f'/api/v1/show/review/{pid}', json={'status':'discarded'}).get_json()['status'] == 'discarded'
    assert client.patch(f'/api/v1/show/review/{pid}', json={'status':'proposed'}).get_json()['status'] == 'proposed'
    assert client.post('/api/v1/show/scenes/missing/preview').status_code == 404


def test_edit_during_render_never_publishes_stale_preview_and_duplicate_requests_coalesce(tmp_path, monkeypatch):
    import threading
    import time
    project, client, document, _ = _storyboard(tmp_path, monkeypatch)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def blocked(project, scene_id, **kwargs):
        calls.append(scene_id)
        kwargs['progress'](.5, 'Painting requested drawing')
        entered.set()
        assert release.wait(5)
        finished.set()
        return project.cache_dir/'stale.mp4'
    monkeypatch.setattr('core.show.preview_scene', blocked)
    sid = document['scenes'][0]['id']
    url = f'/api/v1/show/scenes/{sid}/preview'
    client.post(url)
    assert entered.wait(5)
    assert client.get('/api/v1/show/scenes').get_json()['scenes'][0]['preview']['message'] == 'Painting requested drawing'
    client.post(url)
    pid = document['scenes'][0]['effects'][0]
    client.patch(f'/api/v1/show/review/{pid}', json={'status':'discarded'})
    release.set()
    assert finished.wait(5)
    time.sleep(.1)
    scene = client.get('/api/v1/show/scenes').get_json()['scenes'][0]
    assert calls == [sid] and scene['preview']['status'] == 'none'


def test_scene_preview_shows_the_rest_and_says_which_drawing_is_missing(tmp_path, monkeypatch):
    import json
    from core import show, show_proposals
    project, _, document, _ = _storyboard(tmp_path, monkeypatch)
    plan_path = project.artifacts_dir/'show_plan.json'
    plan = json.loads(plan_path.read_text())
    plan['words'] = []
    plan_path.write_text(json.dumps(plan))
    drawing = show_proposals.proposal('drawing', 1, 2, reason='Your idea', kind='drawing', subject='a man on a sofa', style='crayon')
    drawing['status'] = 'approved'                     # accepted: drawn for real (a suggestion is only a card)
    show_proposals.save(project.artifacts_dir/'show_proposals.json', [drawing])
    document['scenes'][0]['effects'] = [drawing['id']]
    show_scenes.save(project.artifacts_dir/'show_scenes.json', document)
    monkeypatch.setattr(show, 'render_show', lambda source, part, output, **kwargs: output.write_bytes(b'cut'))
    monkeypatch.setattr('core.gags.make_items', lambda *args, **kwargs: ([], ['Drawing painter is not installed']))
    # One drawing the painter cannot make no longer hides the whole scene: it is listed instead.
    preview = render_scene_preview(project, document['scenes'][0]['id'])
    assert preview.read_bytes() == b'cut' and show.scene_preview_notes(preview) == ['Drawing painter is not installed']


def test_scene_preview_leaves_out_an_unfinished_character_and_says_so(tmp_path, monkeypatch):
    import json
    from core import show, show_proposals
    project, _, document, _ = _storyboard(tmp_path, monkeypatch)
    plan_path = project.artifacts_dir/'show_plan.json'
    plan = json.loads(plan_path.read_text()); plan['words'] = []; plan_path.write_text(json.dumps(plan))
    character = show_proposals.proposal('character', 1, 2, reason='Speaks', source='person.mp4')
    show_proposals.save(project.artifacts_dir/'show_proposals.json', [character])
    document['scenes'][0]['effects'] = [character['id']]
    show_scenes.save(project.artifacts_dir/'show_scenes.json', document)
    monkeypatch.setattr(show, 'render_show', lambda source, part, output, **kwargs: output.write_bytes(b'cut'))
    preview = render_scene_preview(project, document['scenes'][0]['id'])
    assert preview.read_bytes() == b'cut'                                      # the scene itself, without the character
    assert show.scene_preview_notes(preview) == ['Talking character at 1.0 s needs its region and voice']


def test_failed_final_preview_pass_is_not_reused_as_a_completed_cache(tmp_path, monkeypatch):
    import json
    from core import show, show_proposals
    project, _, document, _ = _storyboard(tmp_path, monkeypatch)
    path = project.artifacts_dir/'show_plan.json'
    plan = json.loads(path.read_text()); plan['words'] = []; path.write_text(json.dumps(plan))
    show_proposals.save(project.artifacts_dir/'show_proposals.json', [])
    document['scenes'][0]['effects'] = []
    show_scenes.save(project.artifacts_dir/'show_scenes.json', document)
    renders = []
    def make_cut(source, part, output, **kwargs):
        renders.append(output)
        output.write_bytes(f'render {len(renders)}'.encode())
    monkeypatch.setattr(show, 'render_show', make_cut)
    def fail(*args):
        raise RuntimeError('audio pass failed')
    monkeypatch.setattr(show, '_lay_jingles', fail)
    with pytest.raises(RuntimeError, match='audio pass failed'):
        render_scene_preview(project, document['scenes'][0]['id'])
    monkeypatch.setattr(show, '_lay_jingles', lambda *args: None)
    result = render_scene_preview(project, document['scenes'][0]['id'])
    # Only the failed last pass is done again: the scene's cut was fine and is kept.
    assert result.read_bytes() == b'render 1' and len(renders) == 1
    assert '-building' not in result.name


def test_the_last_step_lists_what_the_render_does_with_each_show_studio_effect(tmp_path, monkeypatch):
    import json

    from core.show_studio_store import StudioStore
    project, client, _, _ = _storyboard(tmp_path, monkeypatch)
    plan = json.loads((project.artifacts_dir / "show_plan.json").read_text())
    plan.update(fps=30, shots=[{"start_frame": 466 * 30, "end_frame": 476 * 30, "zoom": 1.0}])
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps(plan))
    StudioStore(project.folder).save_composition({"version": 2, "mode": "mixed", "duration": 44.0, "events": [
        {"id": "base", "type": "video", "start": 0, "end": 44, "source": "original", "trackId": "base"},
        {"id": "zoom1", "type": "zoom", "start": 1, "end": 2, "parent": "base"},
        {"id": "spiral1", "type": "spiral", "start": 2, "end": 4, "x": 0.5, "y": 0.5, "parent": "base"},
        {"id": "gone", "type": "spiral", "start": 30, "end": 31, "x": 0.5, "y": 0.5, "parent": "base"}]}, revision=0)
    data = client.get("/api/v1/show/studio-effects").get_json()
    status = {item["id"]: item["status"] for item in data["effects"]}
    assert status == {"zoom1": "superseded", "spiral1": "applied", "gone": "failed"}      # source 496 s was cut
    assert data["counts"] == {"superseded": 1, "applied": 1, "failed": 1}


def test_a_placed_picture_can_be_stretched_turned_mirrored_and_see_through(tmp_path):
    import numpy as np
    from PIL import Image

    from core.gag_render import PREPARED_SCALE, load_sticker
    picture = tmp_path / "guy.png"
    image = Image.new("RGBA", (100, 50), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (0, 0, 50, 50))                                  # red on the left half
    image.save(picture)
    scene = {"id": "s", "start": 0.0, "end": 10.0, "decision": "accepted", "overlays": [
        {"asset": str(picture), "x": 10, "y": 10, "width": 20, "at": 1.0, "duration": 2.0, "stretch": 2.0, "rotate": 90, "flip": True, "opacity": 0.5}]}
    item = show_scenes.overlay_items({"scenes": [scene]})[0]
    assert (item["stretch"], item["rotate"], item["flip"], item["opacity"]) == (2.0, 90, True, 0.5)
    plain = load_sticker({**item, "stretch": 1, "rotate": 0, "flip": False, "opacity": 1}, 1000, 563)
    shaped = load_sticker(item, 1000, 563)
    h, w = plain.pixels.shape[:2]
    assert shaped.pixels.shape[0] == pytest.approx(w, abs=3) and shaped.pixels.shape[1] == pytest.approx(2 * h, abs=3)   # stretched, then turned
    assert shaped.pixels[..., 3].max() == pytest.approx(0.5, abs=0.02)
    # Mirrored (red to the right), then turned a quarter clockwise like the stage's CSS: red ends at the bottom.
    rows = shaped.pixels[..., 0].mean(axis=1)
    assert rows[len(rows) // 2:].mean() > rows[: len(rows) // 2].mean()
    assert plain.width_px == pytest.approx(200) and PREPARED_SCALE > 1
