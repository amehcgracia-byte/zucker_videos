"""A new background behind the person: masks made once from the cut and kept, the picture
behind the person (never over them), and the effect in the storyboard and the API."""
import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from core import show_backgrounds, show_proposals
from core.project import create_project

FFMPEG = shutil.which("ffmpeg")


class FakeSegmenter:
    """A person in the middle third of the frame."""
    calls = 0

    def __call__(self, rgb):
        FakeSegmenter.calls += 1
        mask = np.zeros((show_backgrounds.MASK_SIZE[1], show_backgrounds.MASK_SIZE[0]), np.uint8)
        width = show_backgrounds.MASK_SIZE[0]
        mask[:, width // 3: 2 * width // 3] = 255
        return mask


def _plan(source, frames=90):
    return {"fps": 30, "source": str(source), "edited_duration": frames / 30, "framing": "whole",
            "shots": [{"start_frame": 0, "end_frame": frames, "zoom": 1.0, "focus": [0.5, 0.5]}]}


def test_the_new_background_goes_behind_the_person_and_fades_in(tmp_path):
    picture = tmp_path / "kitchen.png"
    Image.new("RGB", (320, 180), (20, 200, 40)).save(picture)
    masks = np.zeros((30, show_backgrounds.MASK_SIZE[1], show_backgrounds.MASK_SIZE[0]), np.uint8)
    masks[:, :, 150:234] = 255                                               # the person, in the middle
    layer = show_backgrounds.Background(1.0, 1.0, show_backgrounds.cover(picture, 160, 90), masks, [[0.5, 0.6]], 30.0)
    frame = np.full((90, 160, 3), 200, np.uint8)
    show_backgrounds.apply(frame, layer, 1.5)
    assert abs(int(frame[45, 80, 0]) - 200) < 8                              # the person stays
    assert tuple(frame[45, 5]) == (20, 200, 40)                              # the place is behind them
    start = np.full((90, 160, 3), 200, np.uint8)
    show_backgrounds.apply(start, layer, 1.0 + show_backgrounds.FADE_SEC / 2)
    assert 20 < start[45, 5, 0] < 200                                        # fading in, not a jump
    assert layer.active(1.2) and not layer.active(1.55) and not layer.active(2.1)   # a close-up keeps its own picture


@pytest.mark.skipif(not FFMPEG, reason="needs ffmpeg")
def test_masks_are_made_once_and_shared_by_every_render_size(tmp_path, monkeypatch):
    monkeypatch.setattr(show_backgrounds, "Segmenter", FakeSegmenter)
    source = tmp_path / "talk.mp4"
    cut = tmp_path / "cut.mp4"
    for path, size in ((source, "320x180"), (cut, "320x180")):
        subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=3", "-pix_fmt", "yuv420p", str(path)], check=True)
    plan = _plan(source)
    entries = [{"background": "kitchen.png", "at": 1.0, "duration": 1.0}]
    FakeSegmenter.calls = 0
    notes = show_backgrounds.prepare(cut, entries, plan, tmp_path / "masks", hidden=[(1.5, 1.7)], ffmpeg=FFMPEG)
    assert notes == [] and FakeSegmenter.calls == 16                         # every other frame of 30, and the last
    masks = np.load(entries[0]["masks"])
    assert masks.shape == (30, show_backgrounds.MASK_SIZE[1], show_backgrounds.MASK_SIZE[0])
    assert masks[1].max() == 255                                             # blended between two masks
    assert entries[0]["hidden"] == [[0.5, 0.7]]
    # The same span in a scene preview (its cut starts later) finds the same masks.
    again = [{"background": "kitchen.png", "at": 0.5, "duration": 1.0}]
    show_backgrounds.prepare(cut, again, plan, tmp_path / "masks", offset=0.5, ffmpeg=FFMPEG)
    assert again[0]["masks"] == entries[0]["masks"] and FakeSegmenter.calls == 16
    # Another framing of the shot is another cut-out.
    plan["shots"][0]["zoom"] = 1.4
    other = [{"background": "kitchen.png", "at": 1.0, "duration": 1.0}]
    show_backgrounds.prepare(cut, other, plan, tmp_path / "masks", ffmpeg=FFMPEG)
    assert other[0]["masks"] != entries[0]["masks"]


def test_without_the_macs_person_cut_out_the_background_is_left_out_and_said(tmp_path, monkeypatch):
    def unavailable():
        raise show_backgrounds.BackgroundUnavailable("Changing the background needs macOS 12 or later")
    monkeypatch.setattr(show_backgrounds, "Segmenter", unavailable)
    entries = [{"background": "kitchen.png", "at": 0.0, "duration": 1.0}]
    notes = show_backgrounds.prepare(tmp_path / "cut.mp4", entries, _plan(tmp_path / "talk.mp4"), tmp_path / "masks")
    assert notes and "macOS" in notes[0] and entries[0]["background"] is None


def test_a_background_needs_its_picture_and_reaches_the_render(tmp_path):
    item = show_proposals.proposal("background", 2.0, 4.0, reason="Added by you", subject="in the kitchen", picture="")
    assert "a picture of the new background" in show_proposals.check(item)
    picture = tmp_path / "kitchen.png"
    Image.new("RGB", (32, 18), (1, 2, 3)).save(picture)
    item["params"]["picture"] = str(picture)
    item["status"] = "approved"
    assert show_proposals.check(item) == []
    _, layer, _ = show_proposals.render_inputs([item])
    assert layer == [{"proposal": item["id"], "background": str(picture), "at": 2.0, "duration": 4.0}]


def test_the_person_adds_a_background_and_chooses_its_picture(tmp_path, monkeypatch):
    import server.api as api_module
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Show", str(tmp_path / "Back.zuckervid"))
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"x")
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps(_plan(video, 300)))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [], source=str(video), resources=[])
    client = api_module.create_app(project_path=str(project.folder)).test_client()
    made = client.post("/api/v1/show/review/add", json={"kind": "background", "at": 2.0, "duration": 3.0, "subject": "in my atelier"})
    assert made.status_code == 201, made.get_json()
    made = made.get_json()
    assert made["kind"] == "background" and made["needs"] == ["a picture of the new background"]
    library = project.folder / "assets" / "show-library"
    library.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), (90, 60, 30)).save(library / "atelier.png")
    chosen = client.post(f"/api/v1/show/review/{made['id']}/picture", json={"path": str((library / "atelier.png").resolve())})
    assert chosen.status_code == 200, chosen.get_json()
    assert chosen.get_json()["params"]["picture"] and chosen.get_json()["needs"] == []
    assert client.post("/api/v1/show/review/add", json={"kind": "background", "at": 2.0, "picture": "/etc/hosts"}).status_code == 400


def test_moving_background_and_moving_black_silhouette_reach_the_compositor(tmp_path):
    video = tmp_path/'moving.mp4'
    subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=1',
                    '-c:v','libx264','-pix_fmt','yuv420p',str(video)], check=True)
    masks = np.zeros((10, 90, 160), np.uint8)
    masks[:,:,30:70] = 255
    path = tmp_path/'mask.npy'
    np.save(path, masks)
    layer = show_backgrounds.load({'at':0,'duration':1,'background':str(video),'masks':str(path),
                                    'person_style':'silhouette'}, 160, 90, 10)
    try:
        a = np.full((90,160,3), 180, np.uint8)
        b = a.copy()
        show_backgrounds.apply(a,layer,.3)
        show_backgrounds.apply(b,layer,.6)
        assert tuple(a[45,50]) == (8,8,8)
        assert np.any(a[:,100:] != b[:,100:])  # the background is another moving video
        assert np.array_equal(layer.video.at(.3),layer.video.at(1.3))  # loops, not a frozen last frame
    finally:
        layer.close()
    assert not layer.video.capture.isOpened()


def test_project_layer_editor_saves_the_original_preset_and_placement(tmp_path, monkeypatch):
    import server.api as api_module
    monkeypatch.setenv('ZUCKER_DATA_ROOT',str(tmp_path/'data'))
    project = create_project('Layers',str(tmp_path/'Layers.zuckervid'))
    source = tmp_path/'host.mp4'
    source.write_bytes(b'x')
    (project.artifacts_dir/'show_plan.json').write_text(json.dumps(_plan(source,300)))
    show_proposals.save(project.artifacts_dir/'show_proposals.json',[],source=str(source),resources=[])
    client = api_module.create_app(project_path=str(project.folder)).test_client()
    made = client.post('/api/v1/show/review/add',json={'kind':'background','at':1,'duration':4}).get_json()
    url = '/api/v1/show/video-layers/'+made['id']
    page = client.get(url+'/editor')
    assert page.status_code == 200 and b'Use in scene' in page.data and b'show_video_layers.js' in page.data
    saved = client.post(url+'/save',data={'settings':json.dumps({'preset':'space.png','person_style':'silhouette','person_scale':.7,'person_offset':[-.2,0]})})
    assert saved.status_code == 200, saved.get_json()
    item = saved.get_json()
    assert item['needs'] == [] and item['params']['person_style'] == 'silhouette'
    bundled = api_module.Path(api_module.__file__).resolve().parents[1]/'tools/qa/show-backgrounds/space.png'
    assert api_module.Path(item['params']['picture']).read_bytes() == bundled.read_bytes()
    item['status']='approved'
    _, layers, _ = show_proposals.render_inputs([item])
    assert layers[0]['person_offset'] == [-.2,0]
    assert layers[0]['person_scale'] == .7
    assert client.post(url+'/save',data={'settings':json.dumps({'preset':'../../etc/hosts'})}).status_code == 400
    assert client.get(url+'/media/no-such-file.png').status_code == 404


def test_compositing_preserves_the_final_original_audio_packets(tmp_path):
    from core.gag_render import render_gags
    source, output = tmp_path/'voice.mp4', tmp_path/'composed.mp4'
    subprocess.run([FFMPEG,'-v','error','-y','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=1',
                    '-f','lavfi','-i','sine=frequency=500:sample_rate=48000:duration=1',
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(source)],check=True)
    render_gags(source,[{'steam':[.5,.7],'texture':'rainbow','at':0,'duration':1}],output,ffmpeg=FFMPEG)
    def digest(path):
        return subprocess.check_output([FFMPEG,'-v','error','-i',str(path),'-map','0:a:0','-c','copy',
                                        '-f','hash','-hash','sha256','-'])
    assert digest(source) == digest(output)
