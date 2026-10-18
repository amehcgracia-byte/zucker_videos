"""Tracking an object through absence, cuts and scale changes; shared nature controls."""
import numpy as np
import pytest

from core.show_textures import ObjectTracker, PRESETS
from core.show_inserts import load_steam, draw_steam
from core import show_proposals


def scene(patch=None, x=40, y=80):
    frame = np.full((180, 320), 35, np.uint8)
    if patch is not None:
        h, w = patch.shape
        frame[y:y+h, x:x+w] = patch
    return frame


def test_marked_object_recovers_after_absence_in_another_position():
    patch = np.random.default_rng(78).integers(10, 245, (30, 40), dtype=np.uint8)
    tracker = ObjectTracker([40., 80., 40., 30.])
    assert tracker.update(scene(patch)) is not None
    assert tracker.update(scene(patch, 47))[:2] == [47., 80.]
    missing = [tracker.update(scene()) for _ in range(8)]
    assert all(value is None for value in missing[1:])
    returned = [tracker.update(scene(patch, 220, 25)) for _ in range(12)]
    assert returned[-1] is not None
    assert returned[-1][:2] == [220., 25.]
    assert np.array_equal(tracker.reference['template'], patch)  # no drift into the wall


def test_next_framing_reuses_original_reference_not_empty_anchor():
    import cv2
    patch = np.random.default_rng(19).integers(10, 245, (30, 40), dtype=np.uint8)
    reference = {}
    first = ObjectTracker([40., 80., 40., 30.], reference=reference)
    first.update(scene(patch))
    next_shot = ObjectTracker([40., 80., 60., 45.], reference=reference, scale=1.5)
    zoomed = cv2.resize(patch, (60, 45), interpolation=cv2.INTER_LINEAR)
    for _ in range(12):
        found = next_shot.update(scene(zoomed, 200, 20))
    assert found is not None and found[:2] == pytest.approx([200, 20], abs=1)
    assert np.array_equal(reference['template'], patch)


def test_untextured_selection_never_claims_to_find_an_object():
    tracker = ObjectTracker([40., 80., 40., 30.])
    assert tracker.update(scene()) is None
    assert 'template' not in tracker.reference


@pytest.mark.parametrize('preset', PRESETS)
def test_each_texture_renders_and_animates_with_common_controls(preset):
    item = show_proposals.proposal('texture', 0., 4., reason='Selected words',
                                  point=[.5, .7], texture=preset, scale=1., density=35, strength=.6)
    item['status'] = 'approved'
    _, raw, _ = show_proposals.render_inputs([item])
    texture = load_steam(raw[0], 320, 180)
    before = np.full((180, 320, 3), 40, np.uint8)
    a, b = before.copy(), before.copy()
    draw_steam(a, texture, 1.)
    draw_steam(b, texture, 1.4)
    assert np.any(a != before), preset
    assert np.any(a != b), preset


def test_textures_track_clean_picture_not_previous_overlays():
    rng = np.random.default_rng(18)
    clean = rng.integers(0, 255, (180, 320, 3), dtype=np.uint8)
    effect = load_steam({'steam': [.3, .5], 'anchor': [.2, .5, .1, .2], 'at': 0, 'duration': 5}, 320, 180)
    draw_steam(clean.copy(), effect, 1, tracking_frame=clean)
    moved = np.roll(clean, 8, axis=1)
    output = np.zeros_like(clean)  # a prior background replacement must not drive tracking
    draw_steam(output, effect, 1.1, tracking_frame=moved)
    assert not effect.lost
    assert effect.x == pytest.approx(.3*320+8, abs=1)


def test_texture_creation_edit_and_render_contract(tmp_path, monkeypatch):
    import json
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv('ZUCKER_DATA_ROOT', str(tmp_path/'data'))
    project = create_project('Textures', str(tmp_path/'Textures.zuckervid'))
    source = tmp_path/'talk.mp4'
    source.write_bytes(b'x')
    plan = {'source': str(source), 'fps': 25, 'edited_duration': 8,
            'shots': [{'start_frame': 0, 'end_frame': 200, 'zoom': 1, 'focus': [.5, .5]}]}
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps(plan))
    path = project.artifacts_dir/'show_proposals.json'
    show_proposals.save(path, [], source=str(source), resources=[])
    client = create_app(project_path=str(project.folder)).test_client()
    response = client.post('/api/v1/show/review/add', json={'kind':'texture', 'texture':'rain', 'at':0, 'duration':8})
    assert response.status_code == 201, response.get_json()
    made = response.get_json()
    assert made['params']['subject'] == 'Rain'
    response = client.patch('/api/v1/show/review/'+made['id'], json={'texture':'smoke', 'anchor':[.2,.5,.1,.2], 'status':'approved'})
    assert response.status_code == 200, response.get_json()
    _, entries, _ = show_proposals.render_inputs(show_proposals.load(path)['proposals'])
    assert entries[0]['texture'] == 'smoke'
    assert entries[0]['anchor'] == [.2,.5,.1,.2]
    assert client.patch('/api/v1/show/review/'+made['id'], json={'texture':'unknown'}).status_code == 400
    assert client.post('/api/v1/show/review/add', json={'kind':'texture', 'texture':'unknown'}).status_code == 400


def test_unanchored_textures_span_the_requested_time_without_cut_clipping():
    from core.show import steam_within_its_shot, framed_layer
    plan = {'fps':25, 'shots':[{'start_frame':0,'end_frame':50,'zoom':1,'focus':[.5,.5]},
                              {'start_frame':60,'end_frame':110,'zoom':2,'focus':[.7,.5]}]}
    item = {'at':0., 'duration':4., 'steam':[.2,.5], 'texture':'rain'}
    assert framed_layer(plan, [item]) == [item]
    assert steam_within_its_shot(plan, [item]) == [item]


def test_an_attached_texture_moves_with_the_foreground_video_layer():
    from types import SimpleNamespace
    clean = np.random.default_rng(45).integers(0,255,(180,320,3),dtype=np.uint8)
    texture = load_steam({'steam':[.5,.8],'anchor':[.45,.8,.1,.1], 'texture':'smoke','at':0,'duration':4},320,180)
    output = np.zeros_like(clean)
    draw_steam(output,texture,1,tracking_frame=clean,person_layer=SimpleNamespace(scale=.5,offset=(-.25,0)))
    changed = np.argwhere(output.sum(axis=2)>0)
    assert len(changed) > 0 and changed[:,1].mean() < 100
    assert texture.x == pytest.approx(160)  # tracker remains on source coordinates; no accumulated transform


def test_texture_does_not_restart_its_fade_at_every_edited_cut():
    from core.show_framing import steam_on_screen
    plan = {'fps':25,'shots':[{'start_frame':0,'end_frame':50,'zoom':1,'focus':[.5,.5]},
                             {'start_frame':60,'end_frame':110,'zoom':1,'focus':[.5,.5]}]}
    pieces = steam_on_screen(plan, {'at':0,'duration':4,'steam':[.5,.7],'texture':'rainbow','anchor':[.45,.7,.1,.1]})
    assert [(p['fade_in'],p['fade_out']) for p in pieces] == [(True,False),(False,True)]
    # Exactly at the internal cut the texture is visible, without a second fade-in.
    effect = load_steam({**pieces[1],'anchor':None},320,180)
    frame = np.full((180,320,3),40,np.uint8)
    draw_steam(frame,effect,2.)
    assert np.any(frame != 40)
