"""Show resources: usable stretches of close-ups and matching them to the talk."""
import numpy as np
import pytest

from core import show_resources
from core.show_resources import intervals_from_metrics, match


def test_steady_and_handheld_sharp_stretches_are_found_blur_is_not():
    fps = 2
    # 0-3 s steady and sharp, 3-4 s blurred, 4-7 s hand-held but sharp, 7-8 s a whip pan.
    sharp = [300] * 6 + [20] * 2 + [280] * 6 + [250] * 2
    motion = [2] * 6 + [5] * 2 + [20] * 6 + [60] * 2
    found = intervals_from_metrics(sharp, motion, fps)
    assert [(item["kind"], item["start"], item["end"]) for item in found] == [("still", 0.0, 3.0), ("moving", 4.0, 7.0)]


def test_short_flashes_are_not_intervals():
    assert intervals_from_metrics([300, 300, 10, 300, 300, 10], [1] * 6, 2) == []


def test_matching_ranks_by_meaning_and_discounts_pictures_that_resemble_everything(monkeypatch):
    # Three pictures: money, a guitar, and a "hub" that resembles every line a bit more.
    money, guitar, hub = np.eye(3, dtype=np.float32)
    lines = {"there will be millionaires": money * 0.30 + hub * 0.28, "I play the guitar": guitar * 0.30 + hub * 0.28,
             "hello everybody": hub * 0.29}
    monkeypatch.setattr("core.gag_library.embed_texts", lambda texts: np.asarray([lines[text] for text in texts]))
    resources = [{"intervals": [{"vector": list(money)}, {"vector": list(hub)}]}, {"intervals": [{"vector": list(guitar)}]}]
    ranked = match(list(lines), resources, top=2)
    assert (ranked[0][0]["resource"], ranked[0][0]["interval"]) == (0, 0)   # money, not the hub
    assert (ranked[1][0]["resource"], ranked[1][0]["interval"]) == (1, 0)   # the guitar
    assert ranked[0][0]["score"] > ranked[0][1]["score"]


def test_without_pictures_there_is_nothing_to_propose():
    assert match(["a line"], []) == [[]]


def test_a_photo_resource_is_one_still_interval(tmp_path, monkeypatch):
    from PIL import Image
    Image.new("RGB", (200, 100), (30, 120, 200)).save(tmp_path / "poster.png")
    monkeypatch.setattr("core.scene_tags.available", lambda: False)
    result = show_resources.analyze(tmp_path / "poster.png", tmp_path / "cache")
    assert result["kind"] == "photo" and result["speech"] == []
    assert result["intervals"][0]["kind"] == "photo" and (tmp_path / "cache").exists()
    again = show_resources.analyze(tmp_path / "poster.png", tmp_path / "cache")   # cached
    assert again == result
