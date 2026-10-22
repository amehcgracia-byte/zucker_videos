"""Names people recognise: new media files start with their title, and the timeline and the
libraries never show a code where a name should be."""
import json

from core.media_names import clip_name, coded, find_coded, readable, unique_file


def test_codes_are_not_names_and_titles_are_kept_whole():
    assert coded("cutout-5ea8b73641204db8ab4230211233e415.png") and coded("local-af35c9b71b8020812dde92a5.mp4") and coded("RRDh5")
    assert not coded("John D. Rockefeller, Sr") and not coded("Back to the Future") and not coded("C0273-0008s")
    assert readable("cutout-5ea8b73641204db8ab4230211233e415.png") == "cutout" and readable("clip-1a53b63ff342bb38f8d2") == ""


def test_a_clip_is_called_by_its_title_then_its_file_then_its_words(tmp_path):
    clip = tmp_path / "clip-1a53b63ff342bb38f8d2.mp4"
    clip.write_bytes(b"x")
    assert clip_name({"clip": str(clip), "selected_text": "my bike ride"}) == "Clip · “my bike ride”"
    (tmp_path / "clip-1a53b63ff342bb38f8d2.mp4.json").write_text(json.dumps({"title": "Back To The Future (La Trilogia)"}))
    assert clip_name({"clip": str(clip), "name": "clip-1a53b63ff342bb38f8d2"}) == "Back To The Future (La Trilogia)"
    assert clip_name({"name": "Willow trailer", "clip": str(clip)}) == "Willow trailer"
    assert clip_name({}) == "Clip"


def test_new_files_start_with_their_title_and_old_ones_are_still_found(tmp_path):
    named = unique_file(tmp_path, "Back To The Future (La Trilogía) | #TeLoResumo", "d2dee8e76d6257b67326", ".mp4")
    assert named.name == "Back-To-The-Future-La-Trilogia-TeLoResumo-d2dee8e76d6257b67326.mp4"
    named.write_bytes(b"x")
    assert find_coded(tmp_path, "d2dee8e76d6257b67326", ".mp4") == named
    old = tmp_path / "clip-0892d82d9183a1eb2064.mp4"
    old.write_bytes(b"x")
    assert find_coded(tmp_path, "0892d82d9183a1eb2064", ".mp4") == old
    assert find_coded(tmp_path, "nothing", ".mp4") is None
