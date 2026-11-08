import subprocess
import cv2
import numpy as np
from PIL import Image
from core.media_framing import active_rectangle, frame, inspect
from core.show_inserts import load_clip
from core.show_geometry import place_frame


def test_borders_must_be_black_across_all_samples():
    a=np.zeros((100,160,3),np.uint8);a[10:90,20:140]=[80,140,200]
    assert active_rectangle([a]*3)==(20,10,120,80)
    b=a.copy();b[:]=80
    assert active_rectangle([a,a,b])==(0,0,160,100)
    assert active_rectangle([np.zeros_like(a)]*4) is None


def test_portrait_keeps_center_and_fills_sides():
    source=np.full((160,90,3),[60,120,200],np.uint8)
    source[40:120,30:60]=[220,20,30]
    fitted=frame(source,320,180)
    assert np.mean(fitted[:,:50])>30
    assert fitted[90,160,0]>180
    assert fitted.shape==(180,320,3)


def test_real_clip_decode_has_blurred_sides_and_applies_geometry(tmp_path):
    source=tmp_path/'portrait.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=orange:s=90x160:r=10:d=0.5','-c:v','libx264','-threads','1',str(source)],check=True)
    assert inspect(source)['portrait']
    raw={'source':str(source),'at':0,'clip_length':.4}
    insert=load_clip(raw,160,90,10,'ffmpeg')
    assert insert.clip.shape[0]==4
    assert insert.clip[0,:,0].mean()>25
    transformed=load_clip({**raw,'person_scale':.5,'person_offset':[0,0]},160,90,10,'ffmpeg')
    assert transformed.clip[0,0].max()==0
    assert transformed.clip[0,70,80].mean()>25


def test_huge_scale_keeps_fixed_canvas():
    pixels=np.full((90,160,3),80,np.uint8)
    assert place_frame(pixels,{'person_scale':30}).shape==pixels.shape


def _moving(count=9, size=(180, 320), seed=1):
    rng = np.random.default_rng(seed)
    frames = []
    for index in range(count):                       # every sample a different shot
        frame = cv2.resize(rng.integers(0, 255, (size[0] // 12, size[1] // 12, 3), dtype=np.uint8), (size[1], size[0]), interpolation=cv2.INTER_CUBIC)
        frame = cv2.normalize(frame, None, 30, 230, cv2.NORM_MINMAX)
        frames.append(frame)
    return frames


def _logo(frame, x, y):
    cv2.putText(frame, "CH", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3)
    cv2.rectangle(frame, (x - 4, y - 26), (x + 46, y + 6), (255, 255, 255), 2)


def test_a_corner_logo_on_a_changing_clip_is_zoomed_out_and_a_still_shot_is_left_alone():
    from core.media_framing import hide_box, watermark_box
    frames = _moving()
    for frame in frames:
        _logo(frame, 262, 32)                         # top-right channel mark
    box = watermark_box(frames)
    assert box is not None and box[0] > 0.75 and box[3] < 0.3
    x, y, width, height = hide_box(box)
    assert x + width <= box[0] + 1e-6 or y >= box[3] - 1e-6   # the mark is out of the window
    assert width == height and 1 / width <= 1.3              # same shape, at most 1.3x
    still = [_moving(1, seed=5)[0].copy() for _ in range(9)]  # a still tripod shot: scenery, not a mark
    for frame in still:
        _logo(frame, 262, 32)
    assert watermark_box(still) is None
    assert watermark_box(_moving()) is None                    # no mark, nothing found
    assert hide_box((0.30, 0.0, 0.70, 0.45)) is None           # too big to hide without a large zoom


def test_the_zoom_reaches_the_clip_and_the_person_can_keep_the_mark(tmp_path):
    from core.media_framing import inspect, set_watermark
    clip = tmp_path / "clip.mp4"
    frames = _moving(30, seed=3)
    for frame in frames:
        _logo(frame, 262, 32)
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for frame in frames:
        writer.write(frame)
    writer.release()
    info = inspect(clip)
    assert info["watermark"]["hidden"] and info["width"] < 320 and 1 < info["watermark"]["zoom"] <= 1.3
    assert info["x"] == 0                                      # cut from the mark's side
    set_watermark(clip, keep=True)
    kept = inspect(clip)
    assert not kept["watermark"]["hidden"] and kept["width"] == 320
    set_watermark(clip, keep=False)
    assert inspect(clip)["watermark"]["hidden"]


def test_the_clip_settings_say_what_was_done_and_keep_the_mark_on_request(tmp_path, monkeypatch):
    import json
    from core import show_proposals
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Marks", str(tmp_path / "Marks.zuckervid"))
    clip = project.folder / "assets" / "clips" / "clip-mark.mp4"
    clip.parent.mkdir(parents=True)
    frames = _moving(30, seed=4)
    for frame in frames:
        _logo(frame, 262, 32)
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 180))
    for frame in frames:
        writer.write(frame)
    writer.release()
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": [], "cuts": 0, "source": str(clip)}))
    item = show_proposals.proposal("clip", 1.0, 2.0, reason="Added by you", clip=str(clip))
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [item], resources=[])
    client = create_app(str(project.folder)).test_client()
    found = client.get(f"/api/v1/show/clips/{item['id']}/framing").get_json()
    assert found["watermark"]["hidden"] and found["crop"]["width"] < 320
    kept = client.post(f"/api/v1/show/clips/{item['id']}/framing", json={"keep_watermark": True}).get_json()
    assert not kept["watermark"]["hidden"] and kept["crop"]["width"] == 320
    assert show_proposals.load(project.artifacts_dir / "show_proposals.json")["proposals"][0]["params"]["watermark"] == "keep"
    assert client.post(f"/api/v1/show/clips/{item['id']}/framing", json={"keep_watermark": "yes"}).status_code == 400
    assert client.get("/api/v1/show/clips/nope/framing").status_code == 404


def test_a_clip_is_read_while_it_plays_not_unpacked_to_disk(tmp_path, monkeypatch):
    import tempfile
    def no_scratch(*a, **kw):
        raise AssertionError("clip frames must not be written to a scratch file")
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", no_scratch)
    source = tmp_path / "count.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=1", "-pix_fmt", "yuv420p", str(source)], check=True)
    insert = load_clip({"source": str(source), "at": 0, "clip_length": 1.5}, 160, 90, 10, "ffmpeg")
    assert len(insert.clip) == 10                                  # one second of picture, not the 1.5 asked
    first, last = np.array(insert.clip[0]), np.array(insert.clip[9])
    assert not np.array_equal(first, last)
    assert np.array_equal(np.array(insert.clip[30]), last)          # past its end the last picture holds
    assert np.array_equal(np.array(insert.clip[0]), first)          # going back reads it again
    insert.clip.close()


def test_a_clips_frame_is_served_keyed_for_the_stage(tmp_path, monkeypatch):
    import json
    from PIL import Image as PILImage
    from core import show_proposals
    from core.project import create_project
    from server.api import create_app
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Frames", str(tmp_path / "Frames.zuckervid"))
    clip = project.folder / "assets" / "clips" / "green.mp4"
    clip.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=0x00c800:s=320x180:r=10:d=2",
                    "-vf", "drawbox=x=120:y=50:w=80:h=80:color=0xc04040:t=fill", "-pix_fmt", "yuv420p", str(clip)], check=True)
    (project.artifacts_dir / "show_plan.json").write_text(json.dumps({"edited_duration": 10, "shots": [], "words": [], "cuts": 0, "source": str(clip)}))
    item = show_proposals.proposal("clip", 1.0, 2.0, reason="test", clip=str(clip), clip_start=0, clip_length=2.0, matte="green", audio_mode="mix")
    show_proposals.save(project.artifacts_dir / "show_proposals.json", [item], resources=[])
    client = create_app(str(project.folder)).test_client()
    response = client.get(f"/api/v1/show/clips/{item['id']}/frame?t=1.5&w=320")
    assert response.status_code == 200 and response.mimetype == "image/png"
    picture = np.asarray(PILImage.open(__import__("io").BytesIO(response.data)))
    assert picture.shape[2] == 4 and picture[5, 5, 3] < 30 and picture[90, 160, 3] > 200    # the green goes, the subject stays
    assert client.get("/api/v1/show/clips/nope/frame?t=1").status_code == 404
