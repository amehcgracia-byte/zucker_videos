"""Podcast: microphones and cameras on one clock, who speaks, which camera, the mix, the program."""
import subprocess
import wave

import numpy as np
import pytest

from core import show_podcast as pod
from core.ffmpeg import tool_status


def _ffmpeg() -> str:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "")
    if not ffmpeg:
        pytest.skip("ffmpeg unavailable")
    return ffmpeg


def _wav(path, samples, rate=8000):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return str(path)


def _talk(seconds, rate=8000, seed=0):
    """Speech-like bursts: syllables of noise with pauses between them."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * rate), np.float32)
    at = 0.2
    while at < seconds - 0.5:
        length = rng.uniform(0.08, 0.4)
        a, b = int(at * rate), int((at + length) * rate)
        out[a:b] = rng.normal(0, 0.3, b - a) * np.hanning(b - a)
        at += length + rng.uniform(0.05, 0.6)
    return out


def test_recordings_are_put_on_the_first_microphones_clock(tmp_path):
    ffmpeg, rate = _ffmpeg(), 8000
    room = _talk(40, rate)
    first = _wav(tmp_path / "mic1.wav", room)
    late = _wav(tmp_path / "mic2.wav", room[int(1.5 * rate):] * 0.5)                         # started 1.5 s later
    early = _wav(tmp_path / "cam.wav", np.concatenate([np.zeros(2 * rate, np.float32), room * 0.3]))  # 2 s earlier
    clock = pod.align([first, late, early], ffmpeg)
    assert clock[first]["offset"] == 0
    assert clock[late]["offset"] == pytest.approx(-1.5, abs=0.02)        # its time = the program's - 1.5
    assert clock[early]["offset"] == pytest.approx(2.0, abs=0.02)
    assert min(c["confidence"] for c in clock.values()) > 0.3


def _db(spans, people=3, seconds=20.0):
    """Microphone loudness: each speaker -20 dB on their microphone, -34 dB on the others."""
    frames = int(seconds / pod.STEP)
    db = np.full((people, frames), -62.0) + np.random.default_rng(1).normal(0, 1, (people, frames))
    for who, a, b in spans:
        sl = slice(int(a / pod.STEP), int(b / pod.STEP))
        db[:, sl] = np.maximum(db[:, sl], -34.0)
        db[who, sl] = -20.0
    return db


def test_who_speaks_and_the_turns_of_the_conversation():
    spans = [(0, 1, 6), (1, 6, 13), (2, 9, 9.5), (0, 13, 15), (1, 13, 15)]   # 2 says "yes" at 9 s; 0 and 1 at once at 13 s
    db = _db(spans)
    speaking = pod.activity(db)
    assert speaking[0, 20] and not speaking[1, 20] and not speaking[2, 20]  # the voice reaching other mics is not theirs
    turns = pod.turns(speaking, db)
    assert [(t.speaker, t.together) for t in turns if t.length > 0.5][:2] == [(None, []), (0, [])]
    assert all(t.speaker != 2 for t in turns)                              # a "yes" does not take the floor
    assert any(set(t.together) == {0, 1} for t in turns)                   # both at once
    assert turns[-1].end == pytest.approx(20.0)


def _cameras():
    seats = [{"yaw": -120, "pitch": 0, "person": 0}, {"yaw": 0, "pitch": 0, "person": 1}, {"yaw": 120, "pitch": 0, "person": 2}]
    return [{"kind": "360", "path": "c360.mp4", "seats": seats, "offset": 0, "duration": 100},
            {"kind": "flat", "path": "camA.mp4", "people": [0, 1], "offset": 0, "duration": 100}]


def test_the_cameras_show_who_speaks_the_table_and_the_listener():
    cameras = _cameras()
    every = pod.views(cameras, 3)
    assert sum(view.kind == "single" for view in every) == 3 and any(view.kind == "table" for view in every)
    turns = [pod.Turn(0, 3, None), pod.Turn(3, 9, 0), pod.Turn(9, 25, 1), pod.Turn(25, 28, None, [0, 1]), pod.Turn(28, 40, 2)]
    shots = pod.plan(turns, cameras, 3, 40.0)
    assert shots[0]["start"] == 0 and every[shots[0]["view"]].kind == "table"
    assert all(a["end"] == b["start"] for a, b in zip(shots, shots[1:])) and shots[-1]["end"] == 40.0
    assert all(s["end"] - s["start"] >= pod.MIN_SHOT - 1e-6 for s in shots)

    def at(t):
        return every[next(s for s in shots if s["start"] <= t < s["end"])["view"]]
    assert 0 in at(5).people and 1 in at(12).people and 2 in at(31).people
    assert {0, 1} <= set(at(26.5).people)                                  # both talking: both in the picture
    listening = [s for s in shots if s["why"] == "listens"]
    assert listening and 0 in every[listening[0]["view"]].people          # in 1's long answer, 0 listening
    assert len({s["view"] for s in shots if 9.5 < s["start"] < 25 and s["why"] == "speaks"}) >= 1


def test_a_camera_that_was_not_recording_is_not_used():
    cameras = _cameras()
    cameras[1]["offset"], cameras[1]["duration"] = 0, 10                   # the flat camera stopped at 10 s
    every = pod.views(cameras, 3)
    shots = pod.plan([pod.Turn(0, 30, 0)], cameras, 3, 30.0)
    assert all(every[s["view"]].camera == 0 for s in shots if s["end"] > 10.2)


def test_the_mix_lowers_a_microphone_while_its_person_is_silent(tmp_path):
    ffmpeg, rate = _ffmpeg(), 48000
    a, b = _talk(10, rate, 1), _talk(10, rate, 2)
    first_half = np.r_[np.ones(5 * rate), np.zeros(5 * rate)].astype(np.float32)
    mic0 = _wav(tmp_path / "m0.wav", a * first_half + 0.3 * b * (1 - first_half), rate)   # 0 speaks, then hears 1
    mic1 = _wav(tmp_path / "m1.wav", b * (1 - first_half) + 0.3 * a * first_half, rate)
    speaking = np.zeros((2, 100), bool)
    speaking[0, :50], speaking[1, 50:] = True, True
    alone = pod.mix([mic1], [0.0], speaking[1:], 10.0, ffmpeg, rate=rate)
    both = pod.mix([mic0, mic1], [0.0, 0.0], speaking, 10.0, ffmpeg, rate=rate)
    second = slice(6 * rate, 10 * rate)
    # In the second half 1 speaks: 0's microphone (1's voice, late and weak) adds little.
    ratio = np.sqrt(np.mean(both[second] ** 2)) / np.sqrt(np.mean(alone[second] ** 2))
    assert 1.0 <= ratio < 1.2
    assert np.abs(both).max() <= 0.9


def test_the_program_cuts_between_cameras_on_the_program_clock(tmp_path):
    ffmpeg = _ffmpeg()
    red, blue = tmp_path / "red.mp4", tmp_path / "blue.mp4"
    for path, colour, length in ((red, "red", 12), (blue, "blue", 14)):
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color={colour}:s=320x180:r=30:d={length}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    setup = {"people": [{"name": "A"}, {"name": "B"}],
             "cameras": [{"kind": "flat", "path": str(red), "people": [0], "offset": 0, "duration": 12},
                         {"kind": "flat", "path": str(blue), "people": [1], "offset": 2.0, "duration": 14}]}
    shots = [{"start": 0, "end": 3, "view": 0}, {"start": 3, "end": 6, "view": 1}, {"start": 6, "end": 8, "view": 0}]
    audio = np.zeros(8 * 48000, np.float32)
    program = pod.render(setup, shots, audio, tmp_path / "program.mp4", ffmpeg, width=160, height=90)
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(program), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 90, 160, 3)
    assert len(frames) == 240
    reds = [frame[..., 0].mean() > frame[..., 2].mean() for frame in frames]
    assert reds[:90] == [True] * 90 and reds[90:180] == [False] * 90 and reds[180:] == [True] * 60


def test_a_whole_podcast_becomes_one_program_with_its_mix(tmp_path, monkeypatch):
    import json
    from core.project import create_project
    ffmpeg = _ffmpeg()
    monkeypatch.setenv("ZUCKER_DATA_ROOT", str(tmp_path / "data"))
    project = create_project("Pod", str(tmp_path / "Pod.zuckervid"))
    rate = 48000
    a, b = _talk(16, rate, 4), _talk(16, rate, 5)
    turn = np.r_[np.ones(8 * rate), np.zeros(8 * rate)].astype(np.float32)
    mic_a = _wav(tmp_path / "a.wav", a * turn + 0.2 * b * (1 - turn), rate)
    mic_b = _wav(tmp_path / "b.wav", b * (1 - turn) + 0.2 * a * turn, rate)
    room = (a * turn + b * (1 - turn)) * 0.4
    cams = []
    for name, colour in (("left", "red"), ("right", "blue")):
        sound = _wav(tmp_path / f"{name}.wav", np.concatenate([np.zeros(rate, np.float32), room]), rate)  # rolling 1 s early
        video = tmp_path / f"{name}.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"color={colour}:s=320x180:r=30:d=17", "-i", sound,
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
        cams.append(str(video))
    (project.artifacts_dir / "show_podcast.json").write_text(json.dumps({
        "people": [{"name": "Ana", "mic": mic_a}, {"name": "Bea", "mic": mic_b}],
        "cameras": [{"kind": "flat", "path": cams[0], "people": [0]}, {"kind": "flat", "path": cams[1], "people": [1]}]}))
    program = pod.build(project, progress=lambda *a: None, cancel=lambda: None, width=160, height=90)
    saved = json.loads((project.artifacts_dir / "show_podcast.json").read_text())
    assert program.is_file() and (program.parent / "program-mix.mp3").is_file()
    assert all(c["offset"] == pytest.approx(1.0, abs=0.05) for c in saved["cameras"])
    assert {s["shows"][0] for s in saved["shots"] if s["why"] == "speaks"} == {"Ana", "Bea"}
    raw = subprocess.run([ffmpeg, "-v", "error", "-ss", "4", "-i", str(program), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    early = np.frombuffer(raw, np.uint8).reshape(90, 160, 3)
    raw = subprocess.run([ffmpeg, "-v", "error", "-ss", "13", "-i", str(program), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    late = np.frombuffer(raw, np.uint8).reshape(90, 160, 3)
    # Without a table view the first seconds show the first speaker: Ana (red); later Bea (blue).
    assert early[..., 0].mean() > early[..., 2].mean() and late[..., 2].mean() > late[..., 0].mean()
