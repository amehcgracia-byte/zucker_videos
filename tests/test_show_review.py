"""Show with review, end to end: prepare proposals, approve some, render only those."""
import json
import subprocess

import numpy as np
import pytest

from core import show, show_proposals, show_resources
from core.ffmpeg import ffprobe, tool_status
from core.project import create_project


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _words(text: str, start: float, step: float = 0.4):
    out = []
    for token in text.split():
        out.append({"word": token, "start_sec": round(start, 2), "end_sec": round(start + step - 0.08, 2)})
        start += step
    return out


@pytest.fixture
def media(tmp_path):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x303030:s=320x180:r=25:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(tmp_path / "talk.mp4")], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=6",
                    "-f", "lavfi", "-i", "aevalsrc='if(between(t,2,3),0.5*sin(2*PI*1000*t),0)':s=48000:d=6",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(tmp_path / "closeup.mp4")], check=True)
    return tmp_path


def test_only_reviewed_effects_are_rendered_and_captions_follow_the_interruption(media, monkeypatch):
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(media / "data"))
    project = create_project("Show", str(media / "Show.zuckervid"))
    words = _words("there will be millionaires one day", 0.5) + _words("this guy could speak", 3.4) + _words("and that was the end", 5.6)

    def fake_transcribe(sources, artifact, **kwargs):
        assert kwargs["vad_filter"] is True      # language detected on speech, not on the silent start
        return {"status": "ready", "sources": [{"language": "en", "segments": [{"words": words}]}]}
    monkeypatch.setattr("core.backstage_transcription.transcribe_sources", fake_transcribe)
    monkeypatch.setattr("core.show_framing.face_track", lambda *a, **k: [(t * 0.5, 0.6, 0.3, 0.14) for t in range(16)])
    monkeypatch.setattr(show_resources, "_speech", lambda *a, **k: [{"start": 2.0, "end": 3.0, "text": "hey bro", "language": "en"}])
    monkeypatch.setattr("core.scene_tags.available", lambda: False)

    def fake_match(lines, resources, top=3):
        return [[{"resource": 0, "interval": 0, "score": 0.05}] if "millionaires" in line else [] for line in lines]
    monkeypatch.setattr(show_resources, "match", fake_match)

    summary = show.prepare_show(project, str(media / "talk.mp4"), [str(media / "talk.mp4"), str(media / "closeup.mp4")],
                                gags="off", progress=lambda *a: None, cancel=lambda: None)
    assert summary["by_kind"] == {"cutaway": 1, "character": 1}
    stored = show_proposals.load(project.artifacts_dir / "show_proposals.json")
    items = stored["proposals"]
    cut = next(item for item in items if item["kind"] == "cutaway")
    character = next(item for item in items if item["kind"] == "character")
    assert character["needs"] == ["region", "voice"]
    show_proposals.update(items, cut["id"], status="approved")
    show_proposals.update(items, character["id"], region={"rect": [0.4, 0.5, 0.2, 0.15]}, zoom_sec=0.4,
                          voice=character["params"]["voices"][0], status="approved")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", items)
    from core import show_caption_layout
    show_caption_layout.set_enabled(project, True)              # speech captions are the person's choice (off by default)

    destination, manifest = show.render_reviewed_show(project, progress=lambda *a: None, cancel=lambda: None)
    plan = json.loads((project.artifacts_dir / "show_plan.json").read_text())
    inserted = 0.4 + 1.0                                        # push-in + one second of voice
    duration = float(ffprobe(str(destination))["format"]["duration"])
    assert duration == pytest.approx(plan["edited_duration"] + inserted, abs=0.1)
    assert manifest["show"]["close_ups"] == 1 and manifest["show"]["characters"] == 1
    cues = json.loads((project.folder / "cue_track.json").read_text())["cues"]
    after = [cue for cue in cues if "end" in " ".join(cue["lines"])]
    before = [cue for cue in cues if "millionaires" in " ".join(cue["lines"])]
    character_at = character["at"]
    assert before[0]["start"] < character_at                         # earlier captions stay put
    assert after[0]["start"] >= character_at + inserted - 0.05       # later ones wait for the character


def test_a_scene_plays_its_source_stretches_in_order_without_the_cuts():
    plan = {"fps": 25, "shots": [{"start_frame": 0, "end_frame": 50}, {"start_frame": 100, "end_frame": 175}, {"start_frame": 300, "end_frame": 350}]}
    assert show.scene_segments(plan, 0.0, 5.0) == [[0.0, 2.0], [4.0, 7.0]]          # 2 s + 3 s of edit
    assert show.scene_segments(plan, 3.0, 7.0) == [[5.0, 7.0], [12.0, 14.0]]        # starts inside the second shot


def test_a_window_of_the_edit_keeps_its_frames_and_moves_its_close_ups():
    plan = {"fps": 30, "edited_duration": 6.0, "shots": [{"start_frame": 0, "end_frame": 60}, {"start_frame": 100, "end_frame": 220}],
            "cutaways": [{"at": 3.0, "duration": 1.0, "source": "x"}, {"at": 0.2, "duration": 0.5, "source": "y"}]}
    part = show.window_plan(plan, 1.5, 4.5)
    assert [(s["start_frame"], s["end_frame"]) for s in part["shots"]] == [(45, 60), (100, 175)]
    assert part["cutaways"] == [{"at": 1.5, "duration": 1.0, "source": "x"}]


def test_a_character_never_interrupts_the_host_mid_word():
    # "you know?" | silence cut | "this kind of guy": a character placed inside "know?".
    words = [{"text": t, "start": a, "end": b} for t, a, b in
             [("you", 0.2, 0.4), ("know?", 0.4, 0.9), ("this", 2.0, 2.3), ("kind", 2.3, 2.6), ("of", 2.6, 2.7), ("guy", 2.7, 3.1)]]
    plan = {"fps": 30, "words": words, "shots": [{"start_frame": 0, "end_frame": 30, "words": [0, 1]},
                                                 {"start_frame": 57, "end_frame": 120, "words": [2, 3, 4, 5], "cut_before": "silence"}]}
    talk = {"insert": "character", "at": 0.6, "source": "host"}
    assert show.between_words(plan, [talk])[0]["at"] == 1.0                    # the pause after "know?"
    late = {"insert": "character", "at": 2.1, "source": "host"}                # inside "guy" (1.8-2.2 edited)
    assert show.between_words(plan, [late])[0]["at"] == pytest.approx(2.2)      # its end: no pause near
    sticker = {"image": "x.png", "at": 0.6}
    assert show.between_words(plan, [sticker]) == [sticker]                     # only interruptions move


def _review_project(tmp_path, monkeypatch):
    import server.api as api_module
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Show", str(tmp_path / "Review.zuckervid"))
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"x")
    plan = {"fps": 30, "edited_duration": 10.0, "cuts": 3, "source": str(video), "shots": [{"start_frame": 0, "end_frame": 300}]}
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps(plan))
    character = show_proposals.proposal("character", 4.0, 0.0, reason="test", source=str(video), still_time=1.0, zoom=1.25,
                                        focus=[0.5, 0.5], region=None, voice=None, voices=[])
    word = show_proposals.proposal("word", 2.0, 2.0, reason="test", kind="word", text="WOW")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [word, character], source=str(video), resources=[])
    app = api_module.create_app(project_path=str(project.folder))
    return project, app.test_client(), word, character


def test_the_review_api_adjusts_approves_and_refuses_what_is_incomplete(tmp_path, monkeypatch):
    project, client, word, character = _review_project(tmp_path, monkeypatch)
    review = client.get("/api/v1/show/review").get_json()
    assert [item["kind"] for item in review["proposals"]] == ["word", "character"]
    assert review["plan"]["edited_duration"] == 10.0
    refused = client.patch(f"/api/v1/show/review/{character['id']}", json={"status": "approved"})
    assert refused.status_code == 400 and "region" in refused.get_json()["error"]["message"]
    changed = client.patch(f"/api/v1/show/review/{word['id']}", json={"text": "WHOA", "at": 2.5, "status": "approved", "evil": 1}).get_json()
    assert changed["params"]["text"] == "WHOA" and changed["at"] == 2.5 and changed["status"] == "approved" and "evil" not in changed["params"]
    stored = show_proposals.load(project.artifacts_dir / "show_proposals.json")
    assert stored["proposals"][0]["status"] == "approved" and stored["source"]   # saved, metadata kept
    assert client.patch("/api/v1/show/review/nope", json={"status": "approved"}).status_code == 404
    default = client.get("/api/v1/show/drawing-prompt?subject=a%20guitar&style=crayon").get_json()["prompt"]
    assert "a guitar" in default and "crayon" in default
    drawing = client.post("/api/v1/show/review/add", json={"kind": "drawing", "at": 3.0, "subject": "a guitar", "style": "crayon"}).get_json()
    assert drawing["kind"] == "drawing" and drawing["params"]["subject"] == "a guitar" and drawing["params"]["style"] == "crayon"
    edited = client.patch(f"/api/v1/show/review/{drawing['id']}", json={"prompt": "a guitar drawn by me, very wobbly"}).get_json()
    assert edited["params"]["prompt"] == "a guitar drawn by me, very wobbly"
    assert client.patch(f"/api/v1/show/review/{drawing['id']}", json={"prompt": "x" * 2000}).status_code == 400
    entered = client.patch(f"/api/v1/show/review/{character['id']}", json={"entrance": "bounce"}).get_json()
    assert entered["params"]["entrance"] == "bounce"
    assert client.patch(f"/api/v1/show/review/{character['id']}", json={"entrance": "explode"}).status_code == 400
    # A drawing can use one of the person's library pictures instead of a painting; nothing from elsewhere.
    from core.gag_library import library_dir
    own = library_dir() / "bulb.png"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_bytes(b"png")
    chosen = client.patch(f"/api/v1/show/review/{drawing['id']}", json={"picture": str(own.resolve())})
    assert chosen.status_code == 200 and chosen.get_json()["params"]["picture"] == str(own.resolve())
    assert client.patch(f"/api/v1/show/review/{drawing['id']}", json={"picture": "/etc/hosts"}).status_code == 400
    assert client.patch(f"/api/v1/show/review/{drawing['id']}", json={"picture": ""}).get_json()["params"]["picture"] == ""


def test_the_review_only_serves_its_own_files(tmp_path, monkeypatch):
    project, client, _, _ = _review_project(tmp_path, monkeypatch)
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    assert client.get(f"/api/v1/show/media?path={secret}").status_code == 404
    inside = project.cache_dir / "show_previews" / "a.mp4"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(b"video")
    assert client.get(f"/api/v1/show/media?path={inside}").status_code in (200, 206)
    assert client.get(f"/api/v1/show/still?source={secret}&t=1").status_code == 404


def test_without_a_prepared_review_there_is_nothing_to_show(tmp_path, monkeypatch):
    import server.api as api_module
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Empty", str(tmp_path / "Empty.zuckervid"))
    client = api_module.create_app(project_path=str(project.folder)).test_client()
    assert client.get("/api/v1/show/review").status_code == 404


def test_the_person_can_add_steam_and_a_character_from_this_show_only(tmp_path, monkeypatch):
    project, client, _, _ = _review_project(tmp_path, monkeypatch)
    steam = client.post("/api/v1/show/review/add", json={"kind": "steam", "at": 3.0})
    assert steam.status_code == 201 and steam.get_json()["params"]["point"] == [0.35, 0.75]
    foreign = client.post("/api/v1/show/review/add", json={"kind": "character", "at": 5.0, "source": str(tmp_path / "elsewhere.mp4")})
    assert foreign.status_code == 400
    talk = str(tmp_path / "talk.mp4")
    character = client.post("/api/v1/show/review/add", json={"kind": "character", "at": 5.0, "source": talk, "still_time": 1.0}).get_json()
    assert character["needs"] and "region" in character["needs"]
    kinds = [item["kind"] for item in client.get("/api/v1/show/review").get_json()["proposals"]]
    assert kinds.count("steam") == 1 and kinds.count("character") == 2


def test_timeline_pieces_stay_on_their_effect_row_and_a_split_carries_on(tmp_path, monkeypatch):
    project, client, word, character = _review_project(tmp_path, monkeypatch)
    client.patch(f"/api/v1/show/review/{word['id']}", json={"status": "approved"})
    pasted = client.post(f"/api/v1/show/review/{word['id']}/piece", json={"at": 6.0, "duration": 2.0})
    assert pasted.status_code == 201, pasted.get_json()
    pasted = pasted.get_json()
    assert pasted["id"] != word["id"] and pasted["params"]["group"] == word["id"]          # same row as the original
    assert pasted["status"] == "approved" and pasted["params"]["text"] == "WOW" and pasted["at"] == 6.0
    # A close-up split in two: the second part carries on in its source, not from the start again.
    cut = show_proposals.proposal("cutaway", 1.0, 4.0, reason="test", source=str(tmp_path / "talk.mp4"), source_start=10.0)
    stored = show_proposals.load(project.artifacts_dir / "show_proposals.json")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", stored["proposals"] + [cut], source=stored["source"], resources=[])
    second = client.post(f"/api/v1/show/review/{cut['id']}/piece", json={"at": 2.5, "duration": 2.5, "offset": 1.5}).get_json()
    assert second["params"]["source_start"] == 11.5 and second["params"]["group"] == cut["id"]
    # A talking character plays whole; pieces must fit the scene and start somewhere real.
    assert client.post(f"/api/v1/show/review/{character['id']}/piece", json={"at": 5.0, "duration": 0.0, "offset": 1.0}).status_code == 400
    assert client.post(f"/api/v1/show/review/{word['id']}/piece", json={"at": "soon", "duration": 1}).status_code == 400
    assert client.post("/api/v1/show/review/nope/piece", json={"at": 1, "duration": 1}).status_code == 404



def test_a_library_picture_the_director_named_is_linked_so_the_live_view_shows_it(tmp_path, monkeypatch):
    from PIL import Image
    from core import gag_library
    project, client, word, _ = _review_project(tmp_path, monkeypatch)
    library = gag_library.library_dir()
    Image.new("RGBA", (40, 40), (10, 20, 30, 255)).save(library / "a tiny kid with a big afro b53e89.png")
    named = show_proposals.proposal("drawing", 3.0, 2.0, reason="Director: kid", kind="photo", query="a tiny kid with a big afro b53e89")
    unknown = show_proposals.proposal("drawing", 5.0, 2.0, reason="Director: x", kind="photo", query="nothing called this")
    stored = show_proposals.load(project.artifacts_dir / "show_proposals.json")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", stored["proposals"] + [named, unknown], source=stored["source"], resources=[])
    review = {item["id"]: item for item in client.get("/api/v1/show/review").get_json()["proposals"]}
    assert review[named["id"]]["params"]["picture"].endswith(".png") and "b53e89" in review[named["id"]]["params"]["image_engine"]
    assert not review[unknown["id"]]["params"].get("picture")
    x, y, width = review[named["id"]]["params"]["shown"]                 # where the render draws it, for the live view
    assert 0 < x < 1 and 0 < y < 1 and 0 < width <= 0.25
    moved = client.patch(f"/api/v1/show/review/{word['id']}", json={"action": "jump"})
    assert moved.status_code == 200 and moved.get_json()["params"]["action"] == "jump"
    assert client.patch(f"/api/v1/show/review/{word['id']}", json={"action": "fly away"}).status_code == 400
