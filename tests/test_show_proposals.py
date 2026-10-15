"""Show proposals: proposed, adjusted, approved or discarded; only approved effects reach the render."""
import pytest

from core.show_proposals import (approved, check, from_gags, propose_characters, propose_cutaways, proposal,
                                 render_inputs, update)

RESOURCES = [
    {"path": "/v/C0274.MP4", "kind": "video", "intervals": [{"start": 80.0, "end": 83.0, "thumb": "a.jpg"}, {"start": 205.0, "end": 244.0, "thumb": "b.jpg"}],
     "speech": [{"start": 268.9, "end": 275.0, "text": "hey bro I'm also here"}]},
    {"path": "/v/poster.png", "kind": "photo", "intervals": [{"start": 0.0, "end": 0.0, "thumb": "c.jpg"}], "speech": []},
]
LINES = [(10.0, 12.0, "there will be millionaires"), (12.5, 14.0, "and some more money"), (30.0, 33.0, "some paintings of someone else"),
         (34.0, 36.0, "like this guy could speak")]
MATCHES = [[{"resource": 0, "interval": 0, "score": 0.03}, {"resource": 1, "interval": 0, "score": 0.01}],
           [{"resource": 0, "interval": 0, "score": 0.03}],
           [{"resource": 0, "interval": 1, "score": 0.02}],
           [{"resource": 0, "interval": 1, "score": 0.002}]]


def test_close_ups_are_proposed_for_clear_matches_spaced_out_and_never_reused():
    cuts = propose_cutaways(LINES, RESOURCES, MATCHES)
    # Each close-up is centred in its usable stretch.
    assert [(c["at"], c["params"]["source_start"]) for c in cuts] == [(10.0, pytest.approx(80.3)), (30.0, pytest.approx(223.0, abs=0.1))]
    assert cuts[0]["duration"] == pytest.approx(2.4) and cuts[0]["status"] == "proposed"
    assert cuts[0]["params"]["alternatives"][0]["thumb"] == "c.jpg"     # other candidates to choose from


def test_shots_of_people_are_kept_for_reactions_not_used_as_close_ups():
    cuts = propose_cutaways(LINES, RESOURCES, MATCHES, people={(0, 0)})
    assert [c["params"]["source_start"] for c in cuts] == [pytest.approx(223.0, abs=0.1)]


WORDS = [("like", 34.0, 34.3), ("this", 34.4, 34.6), ("guy", 34.7, 34.9), ("could", 35.0, 35.2), ("speak", 35.3, 35.6),
         ("or", 35.7, 35.8), ("something", 35.9, 36.2)]


def test_a_talking_line_proposes_a_character_in_the_next_pause_that_still_needs_its_region_and_voice():
    cuts = propose_cutaways(LINES, RESOURCES, MATCHES)
    characters = propose_characters(WORDS, [12.0, 36.25, 50.0], RESOURCES, cuts)
    assert len(characters) == 1 and characters[0]["at"] == 36.25       # where the host paused
    assert propose_characters(WORDS, [], RESOURCES, cuts)[0]["at"] == 35.6   # no pause: right after "speak"
    assert characters[0]["needs"] == ["region", "voice"]
    assert characters[0]["params"]["voices"][0]["text"] == "hey bro I'm also here"
    with pytest.raises(ValueError, match="region"):
        update(characters, characters[0]["id"], status="approved")
    voice = characters[0]["params"]["voices"][0]
    item = update(characters, characters[0]["id"], region={"rect": [0.4, 0.5, 0.1, 0.05]}, voice=voice, status="approved")
    assert item["status"] == "approved" and item["needs"] == []


def test_a_region_left_off_screen_by_the_zoom_must_be_readjusted():
    item = proposal("character", 5.0, 0.0, reason="test", source="/v/C0274.MP4", zoom=2.0, focus=[0.5, 0.5],
                    region={"rect": [0.05, 0.05, 0.1, 0.1]}, voice={"source": "/v/C0274.MP4", "start": 1.0, "length": 2.0})
    assert any(need.startswith("adjust region") for need in check(item))
    item["params"]["focus"] = [0.25, 0.25]
    assert check(item) == []


def test_only_approved_effects_reach_the_render():
    cuts = propose_cutaways(LINES, RESOURCES, MATCHES)
    gags = from_gags([{"kind": "word", "at": 3.0, "duration": 2.0, "text": "WOW"}, {"kind": "warp", "at": 40.0, "duration": 3.0, "warp": "swirl"}])
    steam = proposal("steam", 50.0, 4.0, reason="placed by you", point=[0.3, 0.8], anchor=[0.28, 0.8, 0.05, 0.08])
    items = cuts + gags + [steam]
    update(items, cuts[0]["id"], status="approved", zoom=1.4)
    update(items, cuts[1]["id"], status="discarded")
    update(items, gags[0]["id"], status="approved")
    update(items, steam["id"], status="approved")
    cutaways, layer, director = render_inputs(items)
    assert [c["zoom"] for c in cutaways] == [1.4]
    assert [d["kind"] for d in director] == ["word"]
    assert layer == [{"proposal": steam["id"], "steam": [0.3, 0.8], "anchor": [0.28, 0.8, 0.05, 0.08], "at": 50.0, "duration": 4.0, "scale": 1.0}]
    assert len(approved(items)) == 3
    assert director[0]["proposal"] == gags[0]["id"] and cutaways[0]["proposal"] == cuts[0]["id"]   # each keeps its id


def test_every_proposal_gets_a_result_by_id():
    from core.show_proposals import results
    gags = from_gags([{"kind": "drawing", "at": 3.0, "duration": 2.0, "subject": "lightbulb"},
                      {"kind": "word", "at": 9.0, "duration": 2.0, "text": "WOW"},
                      {"kind": "warp", "at": 40.0, "duration": 3.0, "warp": "swirl"}])
    update(gags, gags[0]["id"], status="approved")
    update(gags, gags[1]["id"], status="approved")
    update(gags, gags[2]["id"], status="discarded")
    lines = {line["id"]: line for line in results(gags, {gags[0]["id"]: ("failed", "The painter could not draw it on a clean background"),
                                                         gags[1]["id"]: ("applied", "word: WOW")})}
    assert lines[gags[0]["id"]]["status"] == "failed" and lines[gags[0]["id"]]["name"] == "lightbulb"
    assert lines[gags[1]["id"]]["status"] == "applied"
    assert lines[gags[2]["id"]]["status"] == "disabled"


def test_a_question_gets_a_listening_reaction_in_the_pause_after_it(tmp_path):
    from core.show_proposals import propose_reactions
    face_thumb = tmp_path / "face.jpg"     # "faces" already counted: the detector is not needed here
    resources = [{"path": "/v/C0277.MP4", "kind": "video", "intervals": [{"start": 48.0, "end": 76.0, "kind": "still", "thumb": str(face_thumb), "faces": 1}]},
                 {"path": "/v/C0274.MP4", "kind": "video", "intervals": [{"start": 0.0, "end": 9.0, "kind": "still", "thumb": "x.jpg", "faces": 0}]}]
    lines = [(1.0, 3.0, "how are you doing, guys?"), (4.0, 6.0, "so this is my show"), (10.0, 12.0, "what the fuck?"), (50.0, 52.0, "really?")]
    reactions = propose_reactions(lines, [3.1, 6.2, 12.05, 52.1], resources)
    assert [round(item["at"], 2) for item in reactions] == [3.1, 52.1]       # questions, spaced 30 s apart
    assert all(item["params"]["source"] == "/v/C0277.MP4" and item["params"]["reaction"] for item in reactions)
    assert propose_reactions(lines, [], resources) == []                        # no pause, no reaction
