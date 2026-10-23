"""Make a podcast test recording (one microphone per person, several cameras) from a round
table filmed with a 360 camera in the middle.

    python tools/podcast_reference.py ROUNDTABLE_360.mp4 OUTPUT_FOLDER

Who speaks is read from the mouths at each seat (core.show_podcast.mouth_motion) and the
room's sound; each person's "microphone" is the room's sound at full level while they speak
and 15 dB lower otherwise (the others' voices reaching it), with its own quiet noise floor.
Normal cameras are flat views of the 360 aimed at two neighbouring seats, some of them
started a few seconds late, as real cameras are. The result (MP3 microphones, MP4 cameras and
setup.json for the Show's podcast) stays in OUTPUT_FOLDER; private media never go to Git.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import show_podcast as pod  # noqa: E402
from core.ffmpeg import tool_status  # noqa: E402
from core.spherical_view import spherical_view_filter  # noqa: E402

BLEED = 10 ** (-15 / 20)
CAMERA_LATE = [0.0, 3.2, 7.5]       # seconds each normal camera started after the 360
FPS = 4.0
CHUNK = 150.0                        # seconds of video each worker reads mouths in


def _mouths(job: tuple[str, list, str, float, float]) -> np.ndarray:
    source, seats, ffmpeg, start, length = job
    return pod.mouth_motion(source, seats, ffmpeg, fps=FPS, start=start, duration=length)


def mouths(source: Path, seats: list, ffmpeg: str, duration: float) -> np.ndarray:
    """Mouth movement of every seat, read by several workers on stretches of the video."""
    from concurrent.futures import ProcessPoolExecutor
    jobs = [(str(source), seats, ffmpeg, start, min(CHUNK, duration - start)) for start in np.arange(0.0, duration, CHUNK)]
    with ProcessPoolExecutor(max_workers=4) as pool:
        parts = []
        for number, part in enumerate(pool.map(_mouths, jobs)):
            parts.append(part)
            print(f"  mouths: {min(duration, (number + 1) * CHUNK) / 60:.1f} of {duration / 60:.1f} min", flush=True)
    return np.hstack(parts)


def main(source: Path, out: Path) -> None:
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    out.mkdir(parents=True, exist_ok=True)
    seats = [seat for seat in pod.seats(source, samples=12) if seat["seen"] >= 0.3]
    print(f"{len(seats)} seats:", [(s["yaw"], s["pitch"]) for s in seats], flush=True)
    rate = 48000
    room = pod._decode(source, ffmpeg, rate)
    duration = len(room) / rate
    cache = out / "mouths.npy"
    if cache.is_file():
        motion = np.load(cache)
    else:
        motion = mouths(source, seats, ffmpeg, duration)
        np.save(cache, motion)
    frames = int(duration / pod.STEP)
    db = pod.levels(pod._decode(source, ffmpeg, 8000), 8000, 0.0, frames)
    speaking = pod.speakers_from_mouths(db, pod._on_steps(motion, FPS, 0.0, frames))
    print("speaking share per seat:", speaking.mean(axis=1).round(3).tolist(), flush=True)
    rng = np.random.default_rng(7)
    people = []
    for index, seat in enumerate(seats):
        gain = np.where(speaking[index], 1.0, BLEED).astype(np.float32)
        curve = pod._smooth(np.repeat(gain, int(rate * pod.STEP))[:len(room)], rate)
        curve = np.pad(curve, (0, max(0, len(room) - len(curve))), constant_values=BLEED)
        mic = room * curve + rng.normal(0, 10 ** (-58 / 20), len(room)).astype(np.float32)
        wav, mp3 = out / f".mic-{index + 1}.wav", out / f"mic-{index + 1}.mp3"
        pod._write_wav(wav, np.clip(mic, -1, 1), rate)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "160k", str(mp3)], check=True)
        wav.unlink()
        people.append({"name": f"Person {index + 1}", "mic": str(mp3)})
        print("  microphone", mp3.name, flush=True)
    # Normal cameras: two neighbouring seats each (the widest gaps left out).
    order = sorted(range(len(seats)), key=lambda i: seats[i]["yaw"])
    pairs = []
    for a, b in zip(order, order[1:] + order[:1]):
        span = (seats[b]["yaw"] - seats[a]["yaw"]) % 360
        if span <= 80:
            pairs.append((a, b, span))
    cameras = [{"kind": "360", "path": str(source), "seats": [{**seat, "person": i} for i, seat in enumerate(seats)]}]
    for number, (a, b, span) in enumerate(pairs[:3]):
        yaw = (seats[a]["yaw"] + span / 2 + 180) % 360 - 180
        pitch = (seats[a]["pitch"] + seats[b]["pitch"]) / 2
        late = CAMERA_LATE[number % len(CAMERA_LATE)]
        path = out / f"camera-{number + 1}.mp4"
        view = spherical_view_filter("equirect", yaw, pitch, min(110.0, span + 50), width=1280, height=720)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{late:.2f}", "-i", str(source), "-vf", view,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "128k", str(path)], check=True)
        cameras.append({"kind": "flat", "path": str(path), "people": [a, b]})
        print("  camera", path.name, "shows", a + 1, "and", b + 1, f"(started {late} s late)", flush=True)
    (out / "setup.json").write_text(json.dumps({"people": people, "cameras": cameras, "source": str(source),
                                                 "speaking_share": speaking.mean(axis=1).round(3).tolist()}, indent=1))
    print("done", out / "setup.json", flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
