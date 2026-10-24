"""Podcast: a conversation filmed live with one microphone per person and several cameras,
edited afterwards into one program (who speaks is on screen) that the Show then cleans,
captions and decorates like any talking video.

1. Everything is put on one clock by its sound (the first microphone is the reference).
2. Who speaks, moment by moment, from each microphone: a person speaks when their own
   microphone is well above its quiet level and as loud as the loudest one (another
   person's voice reaching it is several decibels lower).
3. Cameras: a normal camera shows the people it was pointed at (often two); a 360 camera in
   the middle gives a close shot of every seat and a wide one of the table. The program
   shows who speaks (a moment after they start, never for less than a couple of seconds),
   the table when several talk at once or nobody does, another angle in a long answer and,
   halfway through it, the person listening (their reaction).
4. The microphones are mixed: each one levelled, and lowered while its person is silent so
   the others' voices it picks up (and the room) do not echo.
"""
from __future__ import annotations

import json
import math
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

STEP = 0.1              # activity is read in steps of this many seconds
SYNC_RATE = 100         # envelope samples per second used to put tracks on one clock
SPEAK_ABOVE_FLOOR = 10  # dB over a microphone's quiet level to count as speech
DOMINANCE = 6           # dB: a voice within this of the loudest microphone is its own
MIN_TURN = 1.0          # shorter turns ("yes", "mm") do not move the camera
MIN_SHOT = 1.8          # no shot is shorter than this
CUT_DELAY = 0.25        # the camera goes to a new speaker this long after they start
ANGLE_EVERY = 9.0       # in a long answer, another angle of the same person every so often
HOLD = 6.0              # a picture on screen this long changes at the next speaker, even on the same camera
REACTION_AFTER = 10.0   # an answer this long shows the listener once...
REACTION_LENGTH = 2.2   # ...for this long
OPENING = 4.0           # the program opens on the whole table


# ---- Putting everything on one clock ---------------------------------------------------------

def _decode(path: str | Path, ffmpeg: str, rate: int) -> np.ndarray:
    raw = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
                          "-f", "f32le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.float32)


def envelope(samples: np.ndarray, rate: int) -> np.ndarray:
    """Where sounds begin (speech onsets), SYNC_RATE times a second, normalised: what two
    recordings of the same room have in common even when one is close to one mouth."""
    hop = max(1, rate // SYNC_RATE)
    count = len(samples) // hop
    if count < 4:
        return np.zeros(0, np.float32)
    frames = samples[:count * hop].reshape(count, hop)
    energy = np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
    onset = np.maximum(0.0, np.diff(energy, prepend=energy[0]))
    onset -= onset.mean()
    std = float(onset.std())
    return (onset / std).astype(np.float32) if std > 1e-9 else np.zeros_like(onset)


def offset_between(reference: np.ndarray, other: np.ndarray) -> tuple[float, float]:
    """(seconds, confidence) with ``reference time = other time + seconds``."""
    from core.stages.sync import confidence_from_correlation, normalized_overlap_correlation
    lags, curve = normalized_overlap_correlation(reference, other)
    if curve.size == 0:
        return 0.0, 0.0
    peak = int(np.argmax(curve))
    return float(lags[peak]) / SYNC_RATE, float(confidence_from_correlation(curve))


def align(paths: list[str], ffmpeg: str, *, cancel: Callable[[], None] | None = None) -> dict[str, dict[str, float]]:
    """Offsets of every recording on the first one's clock: {path: {"offset", "confidence",
    "duration"}}; a recording's time t is the program's time t + offset."""
    rate = 8000
    first = envelope(_decode(paths[0], ffmpeg, rate), rate)
    out: dict[str, dict[str, float]] = {}
    for path in paths:
        if cancel:
            cancel()
        samples = _decode(path, ffmpeg, rate)
        env = envelope(samples, rate)
        seconds, confidence = (0.0, 1.0) if path == paths[0] else offset_between(first, env)
        # reference = recording + seconds, so the recording's time is the program's minus seconds.
        out[path] = {"offset": round(-seconds, 3), "confidence": round(confidence, 3), "duration": round(len(samples) / rate, 3)}
    return out


# ---- Who speaks -------------------------------------------------------------------------------

def levels(samples: np.ndarray, rate: int, offset: float, frames: int) -> np.ndarray:
    """Loudness (dBFS) of a recording in STEP windows of the program; the recording's time is
    the program's plus ``offset``."""
    hop = int(rate * STEP)
    out = np.full(frames, -100.0, np.float32)
    first = int(round(-offset / STEP))               # the program frame the recording starts at
    usable = len(samples) // hop
    blocks = samples[:usable * hop].reshape(usable, hop)
    db = 10 * np.log10(np.mean(blocks.astype(np.float64) ** 2, axis=1) + 1e-10)
    lo, hi = max(0, first), min(frames, first + usable)
    if hi > lo:
        out[lo:hi] = db[lo - first:hi - first]
    return out


def activity(db: np.ndarray) -> np.ndarray:
    """(people, frames) True where each person speaks, from their microphones' loudness."""
    floor = np.percentile(db, 15, axis=1, keepdims=True)
    above = db - floor
    loudest = db.max(axis=0, keepdims=True)
    raw = (above >= SPEAK_ABOVE_FLOOR) & (db >= loudest - DOMINANCE) & (db > -55)
    # Short gaps inside speech close; lone blips go.
    out = raw.copy()
    for person in range(raw.shape[0]):
        row = out[person]
        _fill(row, gap=int(0.5 / STEP))
        _drop(row, shorter=int(0.3 / STEP))
    return out


def _runs(row: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate([[0], row.astype(np.int8), [0]])))
    return list(zip(edges[::2], edges[1::2]))


def _fill(row: np.ndarray, gap: int) -> None:
    runs = _runs(row)
    for (a, b), (c, _) in zip(runs, runs[1:]):
        if c - b <= gap:
            row[b:c] = True


def _drop(row: np.ndarray, shorter: int) -> None:
    for a, b in _runs(row):
        if b - a < shorter:
            row[a:b] = False


@dataclass
class Turn:
    start: float
    end: float
    speaker: int | None            # None: nobody (silence, laughter, music)
    together: list[int] = field(default_factory=list)   # several at once: who

    @property
    def length(self) -> float:
        return self.end - self.start


TOGETHER = 1 << 20      # a frame key above this: several people at once (the bits say who)


def turns(speaking: np.ndarray, db: np.ndarray) -> list[Turn]:
    """The conversation as turns: who has the floor, stretches where several speak at once,
    and silences. Anything shorter than MIN_TURN (a "yes", a laugh) joins its neighbours."""
    people, frames = speaking.shape
    count = speaking.sum(axis=0)
    floor = np.percentile(db, 15, axis=1, keepdims=True)
    main = np.argmax(np.where(speaking, db - floor, -np.inf), axis=0)
    bits = (speaking.astype(np.int64) * (1 << np.arange(people))[:, None]).sum(axis=0)
    keys = np.where(count == 0, -1, np.where(count == 1, main, TOGETHER + bits))
    runs: list[list[int]] = []
    for index, key in enumerate(keys.tolist()):
        if runs and runs[-1][0] == key:
            runs[-1][2] = index + 1
        else:
            runs.append([key, index, index + 1])
    shortest = int(MIN_TURN / STEP)
    # The shortest run first joins the neighbour it belongs with: the same speaker on both
    # sides, else the longer neighbour.
    while len(runs) > 1:
        i = min(range(len(runs)), key=lambda n: runs[n][2] - runs[n][1])
        if runs[i][2] - runs[i][1] >= shortest:
            break
        left = runs[i - 1] if i else None
        right = runs[i + 1] if i + 1 < len(runs) else None
        if left and right and left[0] == right[0]:
            left[2] = right[2]
            del runs[i:i + 2]
            continue
        host = left if left and (not right or (left[2] - left[1]) >= (right[2] - right[1])) else right
        host[1], host[2] = min(host[1], runs[i][1]), max(host[2], runs[i][2])
        del runs[i]
    out = []
    for key, a, b in runs:
        if key >= TOGETHER:
            together = [p for p in range(people) if (key - TOGETHER) >> p & 1]
            out.append(Turn(round(a * STEP, 3), round(b * STEP, 3), None, together))
        else:
            out.append(Turn(round(a * STEP, 3), round(b * STEP, 3), None if key < 0 else int(key)))
    return out


# ---- Cameras and their views ------------------------------------------------------------------

@dataclass
class View:
    camera: int
    people: tuple[int, ...]        # who it shows (all of them for the table)
    kind: str                      # "single", "pair", "group", "table"
    yaw: float | None = None       # a 360 camera's view
    pitch: float = 0.0
    fov: float = 0.0
    projection: str = "flat"       # "flat", or "planet": the whole table seen from the middle


def views(cameras: list[dict[str, Any]], people: int) -> list[View]:
    """Every picture the cameras can give. A normal camera: what it was pointed at. A 360
    camera: each seat close, each two neighbouring seats together, and the whole table."""
    out: list[View] = []
    for index, camera in enumerate(cameras):
        if camera.get("kind") == "360":
            seats = sorted([seat for seat in camera.get("seats") or [] if seat.get("person") is not None], key=lambda seat: seat["yaw"])
            for seat in seats:
                out.append(View(index, (int(seat["person"]),), "single", float(seat["yaw"]), float(seat.get("pitch") or 0.0), 58.0))
            for a, b in zip(seats, seats[1:] + seats[:1]):
                if len(seats) < 2 or a is b:
                    continue
                span = (b["yaw"] - a["yaw"]) % 360
                if span <= 110:
                    middle = (a["yaw"] + span / 2 + 180) % 360 - 180
                    out.append(View(index, (int(a["person"]), int(b["person"])), "pair", middle,
                                    (float(a.get("pitch") or 0) + float(b.get("pitch") or 0)) / 2, min(120.0, span + 55)))
            out.append(View(index, tuple(range(people)), "table", float(camera.get("table_yaw") or 0.0), -90.0, 250.0, "planet"))
        else:
            shown = tuple(int(p) for p in camera.get("people") or [])
            kind = "single" if len(shown) == 1 else "pair" if len(shown) == 2 else "group" if shown else "table"
            out.append(View(index, shown or tuple(range(people)), kind))
    return out


def _covers(camera: dict[str, Any], start: float, end: float) -> bool:
    """The camera was recording during the whole stretch (on the program clock)."""
    offset, duration = float(camera.get("offset") or 0.0), float(camera.get("duration") or 1e9)
    return start + offset >= -0.05 and end + offset <= duration + 0.05


def _since(shots: list[dict[str, Any]]) -> float:
    """When the picture now on screen came (shots of the same view in a row are one)."""
    at = shots[-1]["start"]
    for shot in reversed(shots[:-1]):
        if shot["view"] != shots[-1]["view"] or abs(shot["end"] - at) > 0.5:
            break
        at = shot["start"]
    return at


def plan(turn_list: list[Turn], cameras: list[dict[str, Any]], people: int, duration: float) -> list[dict[str, Any]]:
    """Shots of the program: {"start", "end", "view": index into views(), "why"}."""
    every = views(cameras, people)
    if not every:
        raise ValueError("Add at least one camera")
    shots: list[dict[str, Any]] = []
    last_camera = [-1]

    def pick(wanted: tuple[int, ...], start: float, end: float, kinds: tuple[str, ...], avoid: int | None = None,
             close: bool = False) -> int | None:
        options = [i for i, view in enumerate(every) if set(wanted) <= set(view.people) and view.kind in kinds
                   and _covers(cameras[view.camera], start, end)]
        if not options:
            return None
        # Another picture than the one just seen; a real camera before a view cut out of the
        # 360 (sharper), unless a close face is wanted (a listener); the closest framing;
        # another camera than the shot before; the least used.
        order = {"single": 0, "pair": 1, "group": 2, "table": 3}
        used = {i: sum(1 for shot in shots if shot["view"] == i) for i in options}
        return min(options, key=lambda i: (i == avoid, 0 if close else cameras[every[i].camera].get("kind") == "360",
                                           order[every[i].kind], every[i].camera == last_camera[0] and len(options) > 1, used[i]))

    def table(start: float, end: float) -> int | None:
        return pick((), start, end, ("table",)) or pick((), start, end, ("group", "pair", "single"))

    def add(start: float, end: float, view: int | None, why: str) -> None:
        if view is None or end - start <= 0.05:
            return
        if shots and shots[-1]["view"] == view and abs(shots[-1]["end"] - start) < 1e-6:
            shots[-1]["end"] = round(end, 3)
            return
        shots.append({"start": round(start, 3), "end": round(end, 3), "view": view, "why": why})
        last_camera[0] = every[view].camera

    previous_speaker: int | None = None
    for turn in turn_list:
        start = max(0.0, turn.start + (CUT_DELAY if shots else 0.0))
        end = turn.end
        if turn.together:
            add(start, end, pick(tuple(turn.together), start, end, ("pair", "group", "table")) or table(start, end), "several at once")
        elif turn.speaker is None:
            add(start, end, table(start, end) if turn.length >= 2.0 else (shots[-1]["view"] if shots else table(start, end)), "nobody speaks")
        else:
            who = turn.speaker
            # A picture already on screen for a while (two people talking on the same
            # camera) gives way to another one of the new speaker: their close-up.
            held = shots[-1]["view"] if shots and turn.start - _since(shots) >= HOLD else None
            first = pick((who,), start, end, ("single", "pair", "group"), avoid=held)
            if first is None:
                add(start, end, table(start, end), "speaks, seen with everyone")
            else:
                # A long answer: another angle now and then, and once the listener's face.
                moments = [start]
                at = start + ANGLE_EVERY
                while at < end - ANGLE_EVERY / 2:
                    moments.append(at)
                    at += ANGLE_EVERY
                moments.append(end)
                reaction = None
                if turn.length >= REACTION_AFTER and previous_speaker is not None and previous_speaker != who:
                    middle = start + turn.length * 0.55
                    listener = pick((previous_speaker,), middle, middle + REACTION_LENGTH, ("single",), close=True)
                    if listener is not None:
                        reaction = (middle, middle + REACTION_LENGTH, listener)
                view, cursor = first, start
                for a, b in zip(moments, moments[1:]):
                    a = max(a, cursor)                    # after the listener, back to the speaker
                    if reaction and a <= reaction[0] < b:
                        add(a, reaction[0], view, "speaks")
                        add(reaction[0], reaction[1], reaction[2], "listens")
                        a = cursor = reaction[1]
                        reaction = None
                    if b > a:
                        add(a, b, view, "speaks")
                        cursor = b
                    view = pick((who,), b, min(end, b + ANGLE_EVERY), ("single", "pair", "group"), avoid=view) or view
            previous_speaker = who
    if not shots:
        add(0.0, duration, table(0.0, duration), "nobody speaks")
    return vary(tidy(shots, duration, table(0.0, min(duration, OPENING))), turn_list, every, cameras)


LONGEST = 12.0      # no picture of someone speaking stays longer: another angle of them comes


def vary(shots: list[dict[str, Any]], turn_list: list[Turn], every: list[View], cameras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shots of people speaking longer than LONGEST are split where someone starts talking
    (or after ANGLE_EVERY), the rest showing whoever speaks then from another angle."""
    out = [dict(shot) for shot in shots]
    index = 0
    while index < len(out):
        shot = out[index]
        if shot["end"] - shot["start"] <= LONGEST or every[shot["view"]].kind == "table":
            index += 1
            continue
        target = shot["start"] + ANGLE_EVERY
        starts = [t.start for t in turn_list if shot["start"] + MIN_SHOT * 2 <= t.start <= shot["end"] - MIN_SHOT * 2 and t.speaker is not None]
        at = min(starts, key=lambda t: abs(t - target)) if starts and min(abs(t - target) for t in starts) <= 3.0 else target
        if at >= shot["end"] - MIN_SHOT * 2:
            index += 1
            continue
        speaker = next((t.speaker for t in turn_list if t.start <= at + 0.05 < t.end), None)
        options = [i for i, view in enumerate(every) if i != shot["view"] and view.kind in ("single", "pair", "group")
                   and speaker is not None and speaker in view.people and _covers(cameras[view.camera], at, shot["end"])]
        if not options:
            index += 1
            continue
        other = min(options, key=lambda i: ({"single": 0, "pair": 1, "group": 2}[every[i].kind], every[i].camera == every[shot["view"]].camera))
        out.insert(index + 1, {"start": round(at, 3), "end": shot["end"], "view": other, "why": "another angle"})
        shot["end"] = round(at, 3)
        index += 1
    return out


def tidy(shots: list[dict[str, Any]], duration: float, opening: int | None) -> list[dict[str, Any]]:
    """Shots back to back from 0 to the end, none shorter than MIN_SHOT (a short one joins
    the shot before it), the program opening on the table."""
    shots = [dict(shot) for shot in sorted(shots, key=lambda shot: shot["start"]) if shot["end"] - shot["start"] > 0.01]
    if not shots:
        return []
    for a, b in zip(shots, shots[1:]):
        a["end"] = b["start"]                          # the camera stays until the next cut
    shots[0]["start"], shots[-1]["end"] = 0.0, round(duration, 3)
    if opening is not None and shots[0]["view"] != opening:
        shots = [shot for shot in shots if shot["end"] > OPENING]
        if shots:
            shots[0]["start"] = max(shots[0]["start"], min(OPENING, duration))
        shots.insert(0, {"start": 0.0, "end": shots[0]["start"] if shots else round(duration, 3), "view": opening, "why": "opening"})

    def joined(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for shot in items:
            if out and out[-1]["view"] == shot["view"]:
                out[-1]["end"] = shot["end"]
            else:
                out.append(shot)
        return out

    shots = joined(shots)
    while True:
        short = next((i for i, shot in enumerate(shots) if shot["end"] - shot["start"] < MIN_SHOT - 1e-6 and len(shots) > 1), None)
        if short is None:
            break
        if short == 0:
            shots[1]["start"] = shots[0]["start"]
        else:
            shots[short - 1]["end"] = shots[short]["end"]
        del shots[short]
        shots = joined(shots)
    for shot in shots:
        shot["start"], shot["end"] = round(shot["start"], 3), round(shot["end"], 3)
    return shots


# ---- The mix ------------------------------------------------------------------------------

def mix(paths: list[str], offsets: list[float], speaking: np.ndarray, duration: float, ffmpeg: str,
        *, rate: int = 48000, cancel: Callable[[], None] | None = None) -> np.ndarray:
    """All microphones on the program clock: each levelled to the same speaking loudness and
    lowered by 12 dB while its person is silent. Mono float32."""
    total = int(duration * rate)
    out = np.zeros(total, np.float32)
    step = int(STEP * rate)
    for person, (path, offset) in enumerate(zip(paths, offsets)):
        if cancel:
            cancel()
        samples = _decode(path, ffmpeg, rate)
        placed = np.zeros(total, np.float32)
        first = int(round(-offset * rate))             # the program sample the recording starts at
        lo, hi = max(0, first), min(total, first + len(samples))
        if hi > lo:
            placed[lo:hi] = samples[lo - first:hi - first]
        del samples
        voice = np.repeat(speaking[person].astype(np.float32), step)[:total]
        voice = np.pad(voice, (0, max(0, total - len(voice))))
        speech = placed[voice > 0]
        loud = float(np.sqrt(np.mean(speech ** 2))) if speech.size > rate else float(np.sqrt(np.mean(placed ** 2)) or 1e-4)
        gain = float(np.clip(0.1 / max(loud, 1e-5), 0.25, 8.0))      # about -20 dBFS while speaking
        # Opens fast (50 ms) when the person speaks, closes slowly (400 ms).
        target = np.where(voice > 0, 1.0, 0.25).astype(np.float32)
        out += placed * gain * _smooth(target, rate)
    from core.show_inserts import tame_peaks
    out = tame_peaks(out[:, None], ceiling=0.6, rate=rate)[:, 0]
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    return out * (0.89 / peak) if peak > 0.89 else out


def _smooth(target: np.ndarray, rate: int) -> np.ndarray:
    """A gain curve that rises in 50 ms and falls in 400 ms (computed on 10 ms blocks)."""
    block = max(1, rate // 100)
    coarse = target[::block]
    up, down = 1 - math.exp(-0.01 / 0.05), 1 - math.exp(-0.01 / 0.4)
    level, smooth = float(coarse[0]) if coarse.size else 1.0, np.empty_like(coarse)
    for i, value in enumerate(coarse):
        level += (value - level) * (up if value > level else down)
        smooth[i] = level
    return np.repeat(smooth, block)[:len(target)]


# ---- The program ---------------------------------------------------------------------------

def view_filter(view: View, camera: dict[str, Any], width: int, height: int) -> str:
    if camera.get("kind") == "360":
        from core.spherical_view import spherical_view_filter
        projection = "raw_insv" if str(camera["path"]).lower().endswith(".insv") else "equirect"
        return spherical_view_filter(projection, view.yaw or 0.0, view.pitch, view.fov, "planet" if view.projection == "planet" else "",
                                     width=width, height=height)
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"


def render(setup: dict[str, Any], shots: list[dict[str, Any]], audio: np.ndarray, output: Path, ffmpeg: str, *,
           width: int = 1920, height: int = 1080, fps: int = 30, rate: int = 48000,
           progress: Callable[[float], None] | None = None, cancel: Callable[[], None] | None = None) -> Path:
    """The program: each shot cut from its camera (on the program clock) and framed, joined,
    with the mix. Shots are made one by one so a long conversation never holds every camera
    open at once."""
    from core.ffmpeg import h264_args
    cameras = setup["cameras"]
    every = views(cameras, len(setup["people"]))
    work = output.with_name(f".{output.stem}-shots-{uuid.uuid4().hex[:8]}")  # its own: two makings never share pieces
    work.mkdir(parents=True, exist_ok=True)
    pieces: list[Path] = []
    for index, shot in enumerate(shots):
        if cancel:
            cancel()
        view = every[shot["view"]]
        camera = cameras[view.camera]
        first = round(shot["start"] * fps)
        frames = max(1, round(shot["end"] * fps) - first)
        piece = work / f"{index:05d}.mp4"
        at = first / fps + float(camera.get("offset") or 0.0)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-ss", f"{max(0.0, at):.3f}", "-i", str(camera["path"]),
                        "-frames:v", str(frames), "-an",
                        "-vf", f"{view_filter(view, camera, width, height)},fps={fps},format=yuv420p",
                        "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(piece)],
                       check=True, capture_output=True)
        pieces.append(piece)
        if progress:
            progress((index + 1) / len(shots))
    listing = work / "pieces.txt"
    listing.write_text("".join(f"file '{piece.name}'\n" for piece in pieces), encoding="utf-8")
    sound = work / "mix.wav"
    _write_wav(sound, audio, rate)
    pending = output.with_name(f".{output.stem}.part{output.suffix}")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(sound),
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", str(pending)], check=True, capture_output=True)
    pending.replace(output)
    for piece in pieces:
        piece.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)
    sound.unlink(missing_ok=True)
    work.rmdir()
    return output


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    import wave
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm.tobytes())


# ---- Seats of a 360 camera -------------------------------------------------------------------

def seats(path: str | Path, *, samples: int = 8, cancel: Callable[[], None] | None = None) -> list[dict[str, Any]]:
    """Where the people sit around a 360 camera: faces found in a few moments of its picture,
    as {"yaw", "pitch", "seen"} (degrees; "seen" is the share of moments a face was there)."""
    import cv2
    from core.show_reframe import detector
    detect = detector()
    capture = cv2.VideoCapture(str(path))
    found: list[tuple[float, float]] = []
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        for index in np.linspace(total * 0.05, max(0, total * 0.95 - 1), samples).astype(int):
            if cancel:
                cancel()
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, bgr = capture.read()
            if not ok:
                continue
            rgb = cv2.cvtColor(cv2.resize(bgr, (1920, 960)), cv2.COLOR_BGR2RGB)
            for shift in (0, 960):                       # a face across the seam is whole in the shifted copy
                picture = np.roll(rgb, -shift, axis=1)
                for x, y, w in detect(picture):
                    if w < 0.008:
                        continue
                    x = (x + shift / 1920) % 1.0
                    found.append(((x - 0.5) * 360, (0.5 - y) * 180))
    finally:
        capture.release()
    groups: list[list[tuple[float, float]]] = []
    for yaw, pitch in sorted(found):
        group = next((g for g in groups if abs((np.median([v for v, _ in g]) - yaw + 180) % 360 - 180) < 14), None)
        (group.append((yaw, pitch)) if group else groups.append([(yaw, pitch)]))
    out = []
    for group in groups:
        seen = len(group) / max(1, samples * 1.4)     # each moment looks twice (the shifted copy)
        if seen >= 0.18:                               # a face behind a microphone is seen less often
            out.append({"yaw": round(float(np.median([v for v, _ in group])), 1),
                        "pitch": round(float(np.median([p for _, p in group])), 1), "seen": round(min(1.0, seen), 2)})
    return sorted(out, key=lambda seat: seat["yaw"])


# ---- Mouths: who speaks, seen in the 360 picture ---------------------------------------------

def _mouth_reader():
    """A function (RGB picture) -> how open the largest face's mouth is (the gap between the
    inner lips over the mouth's width), or None; the Mac's face landmarks."""
    import ctypes
    import sys
    import warnings
    if sys.platform != "darwin":
        return None
    try:
        import objc
        import Quartz
        bundle: dict[str, Any] = {}
        objc.loadBundle("Vision", bundle, bundle_path="/System/Library/Frameworks/Vision.framework")
    except Exception:
        return None
    warnings.filterwarnings("ignore", category=getattr(objc, "ObjCPointerWarning", Warning))
    space = Quartz.CGColorSpaceCreateDeviceRGB()

    def points(region) -> np.ndarray:
        count = int(region.pointCount())
        raw = ctypes.cast(region.normalizedPoints().pointerAsInteger, ctypes.POINTER(ctypes.c_double * (2 * count))).contents
        return np.array(raw).reshape(count, 2)

    def read(rgb: np.ndarray) -> float | None:
        height, width = rgb.shape[:2]
        data = np.dstack([rgb[..., ::-1], np.full((height, width, 1), 255, np.uint8)]).tobytes()
        provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
        image = Quartz.CGImageCreate(width, height, 8, 32, width * 4, space,
                                     Quartz.kCGBitmapByteOrder32Little | Quartz.kCGImageAlphaPremultipliedFirst,
                                     provider, None, False, Quartz.kCGRenderingIntentDefault)
        handler = bundle["VNImageRequestHandler"].alloc().initWithCGImage_options_(image, {})
        request = bundle["VNDetectFaceLandmarksRequest"].alloc().init()
        handler.performRequests_error_([request], None)
        faces = list(request.results() or [])
        if not faces:
            return None
        face = max(faces, key=lambda f: f.boundingBox().size.width)
        marks = face.landmarks()
        if marks is None or marks.innerLips() is None or marks.outerLips() is None:
            return None
        inner, outer = points(marks.innerLips()), points(marks.outerLips())
        wide = float(outer[:, 0].max() - outer[:, 0].min())
        return float(inner[:, 1].max() - inner[:, 1].min()) / wide if wide > 1e-6 else None
    return read


def mouth_motion(path: str | Path, seats_list: list[dict[str, Any]], ffmpeg: str, *, fps: float = 5.0,
                 start: float = 0.0, duration: float | None = None, flat: bool = False,
                 progress: Callable[[float], None] | None = None, cancel: Callable[[], None] | None = None) -> np.ndarray:
    """(places, frames at ``fps``): how much the mouth of the person at each place moves (0
    where no face is read), from ``start`` (seconds of the camera) for ``duration``. Places
    are seats of a 360 camera ({"yaw", "pitch"}) or, with ``flat``, faces of a normal camera
    ({"x", "y", "w"}: shares of its picture)."""
    read = _mouth_reader()
    if read is None:
        raise RuntimeError("Reading mouths needs the Mac's face detector")
    import cv2
    width, height = (1280, 720) if flat else (1920, 960)
    window = ["-ss", f"{start:.2f}"] if start else []
    length = ["-t", f"{duration:.2f}"] if duration else []
    reader = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", *window, "-i", str(path), *length,
                               "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                              stdout=subprocess.PIPE)
    side = int(width * 44 / 360)                            # 44 degrees around each face
    openness: list[list[float]] = [[] for _ in seats_list]
    try:
        index = 0
        while True:
            raw = reader.stdout.read(width * height * 3)
            if len(raw) < width * height * 3:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
            for seat_index, seat in enumerate(seats_list):
                if flat:
                    size = int(max(48, float(seat["w"]) * width * 2.2))
                    cx, cy = int(float(seat["x"]) * width), int(float(seat["y"]) * height)
                    crop = frame[max(0, cy - size // 2):cy + size // 2, max(0, cx - size // 2):cx + size // 2]
                else:
                    cx = int((float(seat["yaw"]) / 360 + 0.5) * width)
                    cy = int((0.5 - float(seat.get("pitch") or 0) / 180) * height)
                    crop = np.roll(frame, width // 2 - cx, axis=1)[max(0, cy - side // 2):cy + side // 2, width // 2 - side // 2:width // 2 + side // 2]
                value = read(cv2.resize(crop, (448, 448), interpolation=cv2.INTER_CUBIC)) if crop.size else None
                openness[seat_index].append(np.nan if value is None else value)
            index += 1
            if index % 25 == 0:
                if cancel:
                    cancel()
                if progress:
                    progress(index)
    finally:
        reader.kill()
        reader.wait()
    out = []
    window = max(1, int(round(fps * 0.8)))
    for row in openness:
        values = np.asarray(row, np.float64)
        filled = np.where(np.isnan(values), np.nanmedian(values) if np.isfinite(values).any() else 0.0, values)
        change = np.abs(np.diff(filled, prepend=filled[0]))
        motion = np.convolve(change, np.ones(window) / window, mode="same")
        motion[np.isnan(values)] = 0.0
        out.append(motion)
    return np.vstack(out) if out else np.zeros((0, 0))


def _on_steps(motion: np.ndarray, fps: float, offset: float, frames: int) -> np.ndarray:
    """Mouth motion (camera clock, ``fps``) on the program's STEP frames (``offset``: the
    camera's time is the program's plus it)."""
    times = np.arange(frames) * STEP + offset
    own = np.arange(motion.shape[1]) / fps
    return np.vstack([np.interp(times, own, row, left=0.0, right=0.0) for row in motion])


def match_seats(speaking: np.ndarray, motion: np.ndarray) -> dict[int, int]:
    """{seat: person}: each microphone's person is the face whose mouth moves when they speak
    (the most alike first)."""
    if not speaking.size or not motion.size:
        return {}
    z = (motion - motion.mean(axis=1, keepdims=True)) / (motion.std(axis=1, keepdims=True) + 1e-9)
    talk = speaking.astype(np.float64)
    talk = (talk - talk.mean(axis=1, keepdims=True)) / (talk.std(axis=1, keepdims=True) + 1e-9)
    alike = z @ talk.T / max(1, z.shape[1])                 # (seats, people) correlations
    out: dict[int, int] = {}
    for _ in range(min(alike.shape)):
        seat, person = np.unravel_index(int(np.argmax(alike)), alike.shape)
        if alike[seat, person] <= 0.05:
            break
        out[int(seat)] = int(person)
        alike[seat, :] = -np.inf
        alike[:, person] = -np.inf
    return out


def speakers_from_mouths(db: np.ndarray, motion: np.ndarray) -> np.ndarray:
    """(seats, frames) who speaks from one recording of the room (``db``: its loudness on STEP
    frames) and the mouths (``motion`` on the same frames): when there is a voice, the seat
    whose mouth moves most (each seat measured against itself), held at least MIN_TURN."""
    floor = np.percentile(db, 15)
    voiced = db > floor + SPEAK_ABOVE_FLOOR
    z = (motion - motion.mean(axis=1, keepdims=True)) / (motion.std(axis=1, keepdims=True) + 1e-9)
    window = max(1, int(0.8 / STEP))
    z = np.vstack([np.convolve(row, np.ones(window) / window, mode="same") for row in z])
    who = np.where(voiced, np.argmax(z, axis=0), -1)
    out = np.zeros_like(z, dtype=bool)
    for seat in range(z.shape[0]):
        out[seat] = who == seat
        _fill(out[seat], gap=int(0.6 / STEP))
        _drop(out[seat], shorter=int(MIN_TURN / STEP))
    return out


def faces(path: str | Path, *, samples: int = 8) -> list[dict[str, float]]:
    """The people a normal camera shows: faces found in a few moments, as {"x", "y", "w"}
    (shares of its picture), left to right."""
    import cv2
    from core.show_reframe import detector
    detect = detector()
    capture = cv2.VideoCapture(str(path))
    found: list[tuple[float, float, float]] = []
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        for index in np.linspace(total * 0.05, max(0, total * 0.95 - 1), samples).astype(int):
            capture.set(cv2.CAP_PROP_POS_FRAMES, int(index))
            ok, bgr = capture.read()
            if ok:
                found += [face for face in detect(cv2.cvtColor(cv2.resize(bgr, (1280, 720)), cv2.COLOR_BGR2RGB)) if face[2] >= 0.03]
    finally:
        capture.release()
    groups: list[list[tuple[float, float, float]]] = []
    for face in sorted(found):
        group = next((g for g in groups if abs(np.median([f[0] for f in g]) - face[0]) < 0.08), None)
        (group.append(face) if group else groups.append([face]))
    return [{"x": round(float(np.median([f[0] for f in g])), 4), "y": round(float(np.median([f[1] for f in g])), 4),
             "w": round(float(np.median([f[2] for f in g])), 4)} for g in groups if len(g) >= max(2, samples * 0.3)]


SAMPLE = 240.0      # seconds of conversation looked at to know who sits where


def _busy_window(speaking: np.ndarray, length: float) -> tuple[int, int]:
    """The stretch (STEP frames) where most people speak: the best sample of who is who."""
    span = int(length / STEP)
    if speaking.shape[1] <= span:
        return 0, speaking.shape[1]
    starts = range(0, speaking.shape[1] - span, int(30 / STEP))
    best = max(starts, key=lambda a: (int((speaking[:, a:a + span].sum(axis=1) > 20).sum()), int(speaking[:, a:a + span].sum())))
    return best, best + span


def identify(cameras: list[dict[str, Any]], speaking: np.ndarray, ffmpeg: str, *,
             progress: Callable[[str], None] | None = None, cancel: Callable[[], None] | None = None) -> None:
    """Who is where, without asking (in place): the seats of a 360 camera and the faces of a
    normal one are found, and each is given the person whose microphone speaks while that
    mouth moves, on a few busy minutes. What the person set by hand stays."""
    a, b = _busy_window(speaking, SAMPLE)
    for camera in cameras:
        is360 = camera.get("kind") == "360"
        if is360 and any(seat.get("person") is not None for seat in camera.get("seats") or []):
            continue
        if not is360 and camera.get("people"):
            continue
        if progress:
            progress(f"Finding who is who in {Path(str(camera['path'])).name}")
        places = (camera.get("seats") or seats(camera["path"], samples=16)) if is360 else faces(camera["path"], samples=12)
        if not places:
            continue
        offset = float(camera.get("offset") or 0.0)
        start = max(0.0, a * STEP + offset)
        motion = mouth_motion(camera["path"], places, ffmpeg, fps=4.0, start=start, duration=(b - a) * STEP, flat=not is360, cancel=cancel)
        on_steps = np.vstack([np.interp(np.arange(b - a) * STEP, np.arange(motion.shape[1]) / 4.0, row, left=0, right=0) for row in motion])
        match = match_seats(speaking[:, a:b], on_steps)
        if is360:
            camera["seats"] = [{**place, "person": match.get(i)} for i, place in enumerate(places)]
        else:
            camera["people"] = sorted(match.values())
            camera["faces"] = [{**place, "person": match.get(i)} for i, place in enumerate(places)]


# ---- The whole of it -------------------------------------------------------------------------

def setup_path(project) -> Path:
    return project.artifacts_dir / "show_podcast.json"


def load_setup(project) -> dict[str, Any]:
    try:
        return json.loads(setup_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"people": [], "cameras": []}


def build(project, *, progress: Callable[[int, str], None], cancel: Callable[[], None],
          width: int = 1920, height: int = 1080) -> Path:
    """The program of the podcast in the project (assets/podcast/program.mp4), from the
    setup saved by the person: microphones (one per person, with names) and cameras."""
    from core.ffmpeg import tool_status
    from core.project import atomic_write_json
    ffmpeg = str(tool_status().get("ffmpeg_path") or "ffmpeg")
    setup = load_setup(project)
    people, cameras = setup.get("people") or [], setup.get("cameras") or []
    if len(people) < 1 or not all(Path(str(p.get("mic") or "")).is_file() for p in people):
        raise RuntimeError("Add one microphone track for each person")
    if not cameras or not all(Path(str(c.get("path") or "")).is_file() for c in cameras):
        raise RuntimeError("Add the cameras of the podcast")
    mics = [str(p["mic"]) for p in people]
    progress(2, "Putting microphones and cameras on one clock (by their sound)")
    clock = align(mics + [str(c["path"]) for c in cameras], ffmpeg, cancel=cancel)
    for camera in cameras:
        camera.update(offset=clock[str(camera["path"])]["offset"], duration=clock[str(camera["path"])]["duration"],
                      sync_confidence=clock[str(camera["path"])]["confidence"])
    for person in people:
        person.update(offset=clock[str(person["mic"])]["offset"], sync_confidence=clock[str(person["mic"])]["confidence"])
    duration = clock[mics[0]]["duration"]
    frames = int(duration / STEP)
    progress(12, "Listening to who speaks on each microphone")
    rate = 8000
    db = np.vstack([levels(_decode(mic, ffmpeg, rate), rate, float(person["offset"]), frames) for mic, person in zip(mics, people)])
    speaking = activity(db)
    identify(cameras, speaking, ffmpeg, cancel=cancel, progress=lambda text: progress(16, text))
    turn_list = turns(speaking, db)
    progress(20, f"Choosing the cameras: {len(turn_list)} turns of conversation")
    shots = plan(turn_list, cameras, len(people), duration)
    progress(24, "Mixing the microphones (each levelled, quiet while its person is silent)")
    audio = mix(mics, [float(p["offset"]) for p in people], speaking, duration, ffmpeg, cancel=cancel)
    folder = project.assets_dir / "podcast" if hasattr(project, "assets_dir") else project.folder / "assets" / "podcast"
    folder.mkdir(parents=True, exist_ok=True)
    mp3 = folder / "program-mix.mp3"                    # every sound the app keeps is MP3
    wav = folder / ".program-mix.wav"
    _write_wav(wav, audio, 48000)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-nostdin", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3)],
                   check=True, capture_output=True)
    wav.unlink(missing_ok=True)
    program = render(setup, shots, audio, folder / "program.mp4", ffmpeg, width=width, height=height, cancel=cancel,
                     progress=lambda share: progress(26 + int(share * 26), f"Cutting the cameras — {share:.0%}"))
    every = views(cameras, len(people))
    names = [str(p.get("name") or f"Person {i + 1}") for i, p in enumerate(people)]
    atomic_write_json(setup_path(project), {**setup, "people": people, "cameras": cameras, "duration": round(duration, 3),
        "program": str(program), "mix": str(mp3),
        "turns": [{"start": t.start, "end": t.end, "speaker": t.speaker, "together": t.together} for t in turn_list],
        "shots": [{**shot, "camera": every[shot["view"]].camera, "kind": every[shot["view"]].kind,
                   "shows": [names[p] for p in every[shot["view"]].people]} for shot in shots]})
    return program
