"""Jingles for the Show: found by what they sound like, placed at any moment, credited."""
import io
import json
import subprocess

import numpy as np
import pytest

from core import show_music
from core.ffmpeg import ffprobe, tool_status

FOUND = {"result_count": 3, "results": [
    {"id": "a1b2c3d4-0000", "title": "Xylophone sting 2", "creator": "nomiqbomi", "license": "cc0", "license_version": "1.0",
     "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "source": "freesound",
     "foreign_landing_url": "https://freesound.org/s/1/", "url": "https://cdn.freesound.org/x.mp3", "duration": 1900, "filetype": "mp3",
     "attribution": ""},
    {"id": "e5f6-1111", "title": "Silly kazoo", "creator": "someone", "license": "by", "license_version": "4.0",
     "license_url": "https://creativecommons.org/licenses/by/4.0/", "source": "freesound", "foreign_landing_url": "https://freesound.org/s/2/",
     "url": "https://cdn.freesound.org/y.mp3", "duration": 3600, "filetype": "mp3", "attribution": '"Silly kazoo" by someone is licensed under CC BY 4.0.'},
    {"id": "nc-2222", "title": "Not for us", "creator": "x", "license": "by-nc", "url": "https://cdn/z.mp3", "duration": 1000}]}


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_search_asks_openverse_for_free_sounds_and_keeps_only_reusable_ones(monkeypatch):
    asked = []

    def fake_urlopen(request, timeout=0):
        asked.append(request.full_url)
        return _Response(json.dumps(FOUND).encode())
    monkeypatch.setattr(show_music, "urlopen", fake_urlopen)
    found = show_music.search("funny jingle")
    assert "q=funny+jingle" in asked[0] and "license=cc0" in asked[0]           # public domain only, unless asked
    assert [item["title"] for item in found] == ["Xylophone sting 2"]
    assert found[0]["duration"] == 1.9 and found[0]["page"] == "https://freesound.org/s/1/"
    wider = show_music.search("kazoo", licenses=("cc0", "by", "by-nc"))
    assert "license=cc0%2Cby" in asked[1] and "nc" not in asked[1]
    assert [item["title"] for item in wider] == ["Xylophone sting 2", "Silly kazoo"]   # never a non-commercial one


def test_a_chosen_sound_is_kept_once_with_its_licence(tmp_path, monkeypatch):
    ffmpeg = _ffmpeg()
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1.5", str(tmp_path / "x.mp3")], check=True)
    payload = (tmp_path / "x.mp3").read_bytes()
    calls = []

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        return _Response(payload)
    monkeypatch.setattr(show_music, "urlopen", fake_urlopen)
    item = show_music.search_result(FOUND["results"][0])
    record = show_music.fetch(item, tmp_path / "music", ffmpeg=ffmpeg)
    assert record["path"].endswith(".mp3") and record["license"] == "cc0" and record["duration"] == pytest.approx(1.5, abs=0.1)
    saved = json.loads((tmp_path / "music" / "sounds.json").read_text())
    assert saved[0]["page"] == "https://freesound.org/s/1/"                        # where to check its licence
    again = show_music.fetch(item, tmp_path / "music", ffmpeg=ffmpeg)
    assert again["path"] == record["path"] and len(calls) == 1                     # downloaded once
    with pytest.raises(ValueError):
        show_music.fetch({**item, "license": "by-nc"}, tmp_path / "music", ffmpeg=ffmpeg)


def test_only_cc_by_sounds_need_a_credit_and_openverse_is_named():
    cc0, by = (show_music.search_result(raw) for raw in FOUND["results"][:2])
    text = show_music.credits([{**cc0, "path": "a.mp3"}, {**by, "path": "b.mp3"}])
    assert '"Silly kazoo" by someone' in text and "Xylophone" not in text and "Openverse" in text
    assert show_music.credits([{**cc0, "path": "a.mp3"}]) == ""


def test_a_jingle_plays_at_its_moment_and_the_voice_dips_under_it(tmp_path):
    ffmpeg = _ffmpeg()
    # The host: a steady 300 Hz voice for 3 s; the jingle: 1500 Hz.
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=25:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(tmp_path / "show.mp4")], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=1500:duration=4", str(tmp_path / "jingle.wav")], check=True)
    jingles = [{"jingle": str(tmp_path / "jingle.wav"), "at": 1.0, "duration": 1.0, "start": 0.5, "volume": 0.8, "fade": 0.05, "duck": 0.3}]
    show_music.mix(tmp_path / "show.mp4", jingles, tmp_path / "out.mp4", ffmpeg=ffmpeg)
    probe = ffprobe(str(tmp_path / "out.mp4"))
    assert float(probe["format"]["duration"]) == pytest.approx(3.0, abs=0.1)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(tmp_path / "out.mp4"), "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    sound = np.frombuffer(raw, dtype=np.float32)

    def level(a, b, hz):
        part = sound[int(a * 8000):int(b * 8000)]
        spectrum = np.abs(np.fft.rfft(part))
        k = int(hz * len(part) / 8000)
        return float(spectrum[k - 3:k + 4].max())
    assert level(0.2, 0.8, 1500) < 0.05 * level(1.2, 1.8, 1500)                   # silent before its moment
    assert level(2.2, 2.8, 1500) < 0.05 * level(1.2, 1.8, 1500)                   # and after its length
    assert level(1.2, 1.8, 300) < 0.6 * level(0.2, 0.8, 300)                      # the voice dips under it


def test_a_jingle_proposal_reaches_the_render_as_a_sound_not_a_picture():
    from core.show_proposals import proposal, render_inputs
    item = proposal("jingle", 12.0, 2.0, reason="Added by you", path="/m/x.mp3", start=0.0, volume=0.7, fade=0.1, duck=0.4, title="X")
    item["status"] = "approved"
    _, layer, gags = render_inputs([item])
    assert layer == [{"jingle": "/m/x.mp3", "at": 12.0, "duration": 2.0, "start": 0.0, "volume": 0.7, "fade": 0.1, "duck": 0.4}] and gags == []
