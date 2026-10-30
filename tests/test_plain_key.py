"""A plain background goes automatically: blue, another strong colour or black light footage,
not a letterbox, a grey wall or (unless asked) white; the same colour inside the subject stays."""
import subprocess

import cv2
import numpy as np
from PIL import Image

from core import plain_key


def _blue_screen():
    frame = np.zeros((90, 160, 3), np.uint8)
    frame[:] = (20, 60, 210)                                   # a blue screen
    cv2.ellipse(frame, (80, 70), (30, 40), 0, 0, 360, (200, 160, 120), -1)    # a subject
    frame[60:66, 74:86] = (20, 60, 210)                        # a blue badge on the subject
    return frame


def test_a_blue_screen_goes_and_the_same_blue_inside_the_subject_stays():
    key = plain_key.find([_blue_screen()])
    assert key and key["kind"] == "blue"
    alpha = plain_key.alpha(_blue_screen(), key)
    assert alpha[5, 5] < 0.1 and alpha[70, 60] > 0.9
    assert alpha[62, 80] > 0.9                                 # enclosed: not the background


def test_letterbox_grey_walls_and_white_are_not_removed_on_their_own():
    letterbox = np.full((90, 160, 3), 120, np.uint8)
    letterbox[:12] = letterbox[-12:] = 0                        # black bars top and bottom only
    letterbox[30:60, 40:120] = (200, 60, 40)
    assert plain_key.find([letterbox]) is None
    grey = np.full((90, 160, 3), 128, np.uint8)
    grey[30:70, 60:100] = (40, 80, 200)
    assert plain_key.find([grey]) is None
    white = np.full((90, 160, 3), 250, np.uint8)
    white[30:70, 60:100] = (40, 80, 200)
    assert plain_key.find([white]) is None
    assert plain_key.find([white], allow_white=True)["kind"] == "white"


def test_a_photo_on_a_plain_colour_is_cut_out_where_it_touches_the_edges(tmp_path):
    from core.show_characters import plain_screen_mask
    photo = tmp_path / "blue.png"
    Image.fromarray(_blue_screen()).save(photo)
    mask = plain_screen_mask(photo)
    assert mask is not None and mask[5, 5] < 0.1 and mask[62, 80] > 0.9


def test_fire_on_black_uses_the_light_blend_and_a_person_on_black_is_left_alone(tmp_path):
    from core.show_video_assets import detect_background
    fire = tmp_path / "fire.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180:r=10:d=1",
                    "-vf", "drawbox=x=120:y=60:w=80:h=80:color=orange:t=fill", "-pix_fmt", "yuv420p", str(fire)], check=True)
    assert detect_background(fire) == ("black", None)
    blue = tmp_path / "blue.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=0x143cd2:s=320x180:r=10:d=1",
                    "-vf", "drawbox=x=120:y=60:w=80:h=80:color=0xc8a078:t=fill", "-pix_fmt", "yuv420p", str(blue)], check=True)
    matte, key = detect_background(blue)
    assert matte == "plain" and key["kind"] == "blue"
