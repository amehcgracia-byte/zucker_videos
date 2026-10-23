"""Show mode: clean a talking video and pace it like a fast YouTube show.

From word timestamps (local Whisper), the edit keeps what is said and drops:
  silence  pauses longer than ``max_pause`` (the jump cuts of a talking show)
  filler   pure hesitation sounds ("eh", "em", "uh", "um"...)
  retake   a phrase the speaker started again: of two phrases that open with
           the same words a little apart, the last take is kept

Then the kept speech is cut into shots of about ``target_shot`` seconds at
word boundaries, alternating framing (punch-in zooms) so every cut is
visible, the way David Choe's editors pace his videos: measured on his
channel, 24-43 cuts a minute, half the shots between 0.7 and 2.5 seconds,
about a quarter of the cuts staying in the same scene (jump/punch-in).
Everything is plain data; rendering lives elsewhere.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

FILLERS = frozenset({
    "eh", "ehh", "ehm", "em", "emm", "mm", "mmm", "hmm", "mhm", "ah", "ahh",
    "uh", "uhh", "um", "umm", "erm", "er",
})


@dataclass(frozen=True)
class ShowStyle:
    """Pacing of the finished show (seconds)."""
    max_pause: float = 0.35      # longer silences are cut out
    lead: float = 0.06           # kept before the first word of a run
    tail: float = 0.10           # kept after its last word
    target_shot: float = 1.5     # typical shot length
    min_shot: float = 0.7        # never shorter than this when splitting
    max_shot: float = 3.2        # longer speech runs are split
    zooms: tuple[float, ...] = (1.0, 1.3, 1.65)   # wide, medium, close on the face
    push_every: int = 5          # every Nth long shot slowly pushes in...
    reveal_every: int = 9        # ...and every Mth starts close and opens up
    move_min_sec: float = 2.0    # only shots this long move
    glide_every: int = 2         # where the picture runs on, every Nth change of framing is a camera move, not a cut
    remove_fillers: bool = True  # "eh", "um" taken out (not in a conversation: it would cut another voice)
    retake_window: float = 25.0  # how far apart two takes of a phrase can be
    fps: int = 30


CHOE = ShowStyle()
CALM = ShowStyle(max_pause=0.6, target_shot=3.5, min_shot=1.5, max_shot=7.0, zooms=(1.0, 1.15, 1.35), reveal_every=0, glide_every=1)
# A conversation: the cameras already change with who speaks, so no zooms; only long silences
# go (the rhythm of a conversation stays) and nothing is taken out from under another voice.
PODCAST = ShowStyle(max_pause=1.2, lead=0.15, tail=0.3, target_shot=30.0, min_shot=30.0, max_shot=600.0, zooms=(1.0,),
                    push_every=0, reveal_every=0, glide_every=0, retake_window=0.0, remove_fillers=False)
# A narration over pictures: the pictures change with the story, so no zooms; only long
# pauses go, and a sentence said twice keeps its last take (a narration recorded phrase by phrase).
STORYTELLING = ShowStyle(max_pause=1.2, lead=0.12, tail=0.25, target_shot=30.0, min_shot=30.0, max_shot=600.0, zooms=(1.0,),
                         push_every=0, reveal_every=0, glide_every=0)
STYLES = {"choe": CHOE, "calm": CALM, "fast": ShowStyle(target_shot=1.75,min_shot=1.5,max_shot=2.0), "normal": CALM, "podcast": PODCAST, "storytelling": STORYTELLING}


@dataclass
class Word:
    text: str
    start: float
    end: float
    index: int
    norm: str = ""
    removed: str | None = None   # "filler" / "retake"


@dataclass
class Shot:
    start: float                 # source seconds
    end: float
    zoom: float = 1.0
    zoom_end: float | None = None  # a camera move: the zoom reached at the end of the shot
    words: list[int] = field(default_factory=list)
    cut_before: str = "start"    # start / silence / filler / retake / pace

    @property
    def duration(self) -> float:
        return self.end - self.start


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9']+", "", text)


def words_from_transcript(transcript: dict[str, Any]) -> list[Word]:
    """Words of a backstage_transcription source (segments with words)."""
    words: list[Word] = []
    for segment in transcript.get("segments") or []:
        for item in segment.get("words") or []:
            text = str(item.get("word") or item.get("text") or "").strip()
            start = float(item.get("start_sec", item.get("start", 0.0)))
            end = float(item.get("end_sec", item.get("end", start)))
            # Whisper writes "..." over silences and noises: not speech, never kept.
            if not re.search(r"\w", text) or end < start:
                continue
            words.append(Word(text, start, max(end, start + 0.02), len(words), normalise(text)))
    return words


def _phrases(words: list[Word], pause: float = 0.6) -> list[list[Word]]:
    """Runs of spoken words split at long pauses or sentence punctuation."""
    phrases: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if word.removed:
            continue
        if current and (word.start - current[-1].end > pause or re.search(r"[.!?…]$", current[-1].text)):
            phrases.append(current)
            current = []
        current.append(word)
    if current:
        phrases.append(current)
    return phrases


def _is_retake(first: list[Word], later: list[Word]) -> bool:
    """``later`` says ``first`` again: same opening, or ``first`` was cut short."""
    a = [w.norm for w in first if w.norm]
    b = [w.norm for w in later if w.norm]
    if len(a) < 2 or len(b) < 2:
        return False
    opening = min(3, len(a), len(b))
    # A new take says about as much; a short echo ("let's put it here") of a
    # longer phrase is not one, and dropping the long phrase would lose words.
    if a[:opening] == b[:opening] and len(b) >= 0.6 * len(a):
        return True
    # An abandoned start: most of the first phrase reappears at the start of the next.
    head = b[:len(a) + 1]
    return len(a) >= 3 and SequenceMatcher(None, a, head).ratio() >= 0.8


def mark_removals(words: list[Word], style: ShowStyle = CHOE) -> list[Word]:
    for word in words:
        if word.norm in FILLERS and style.remove_fillers:
            word.removed = "filler"
    phrases = _phrases(words)
    for index, phrase in enumerate(phrases):
        for later in phrases[index + 1:index + 4]:
            if later[0].start - phrase[-1].end > style.retake_window:
                break
            if _is_retake(phrase, later):
                for word in phrase:
                    word.removed = "retake"
                break
    return words


def speech_runs(words: list[Word], duration: float, style: ShowStyle = CHOE) -> list[Shot]:
    """Continuous stretches of kept speech; every gap between them is a cut."""
    runs: list[Shot] = []
    pending: str | None = None   # why the next kept word starts a new run
    last: Word | None = None
    for position, word in enumerate(words):
        if word.removed:
            pending = pending or word.removed
            continue
        start = max(0.0, word.start - style.lead)
        end = min(duration, word.end + style.tail)
        # Margins never reach into a removed word next to this one.
        if position > 0 and words[position - 1].removed:
            start = max(start, words[position - 1].end)
        if position + 1 < len(words) and words[position + 1].removed:
            end = min(end, max(word.end, words[position + 1].start))
        if last is not None and pending is None and word.start - last.end <= style.max_pause:
            runs[-1].end = max(runs[-1].end, end)
            runs[-1].words.append(word.index)
        else:
            reason = pending or ("silence" if runs else "start")
            if runs and start < runs[-1].end:
                start = runs[-1].end
            runs.append(Shot(start, end, words=[word.index], cut_before=reason))
        pending = None
        last = word
    return [run for run in runs if run.duration > 0.05]


def pace(runs: list[Shot], words: list[Word], style: ShowStyle = CHOE) -> list[Shot]:
    """Split long runs at word gaps into shots near ``target_shot`` and give
    each shot a framing different from the one before."""
    shots: list[Shot] = []
    for run in runs:
        pieces = [run]
        while pieces[-1].duration > style.max_shot:
            piece = pieces[-1]
            target = piece.start + style.target_shot
            gaps = [(abs(words[a].end - target), words[a].end, (words[a].end + words[b].start) / 2, i)
                    for i, (a, b) in enumerate(zip(piece.words, piece.words[1:]))
                    if words[a].end - piece.start >= style.min_shot and piece.end - words[b].start >= style.min_shot]
            if not gaps:
                break
            _, _, split, i = min(gaps)
            first = Shot(piece.start, split, words=piece.words[:i + 1], cut_before=piece.cut_before)
            second = Shot(split, piece.end, words=piece.words[i + 1:], cut_before="pace")
            pieces[-1:] = [first, second]
        shots.extend(pieces)
    previous = None
    long_shots = 0
    for index, shot in enumerate(shots):
        # Alternate framings so each cut reads as a cut; a new idea after a
        # silence returns to the wide framing.
        if shot.cut_before in {"start", "retake"}:
            zoom = style.zooms[0]
        else:
            options = [z for z in style.zooms if z != previous] or list(style.zooms)
            zoom = options[index % len(options)]
        shot.zoom = zoom
        previous = zoom
        if shot.duration >= style.move_min_sec:
            # Counted over the shots long enough to move, so moves keep coming
            # whatever the rhythm of the talk.
            if style.reveal_every and long_shots % style.reveal_every == style.reveal_every // 2:
                shot.zoom, shot.zoom_end = style.zooms[-1], style.zooms[0]   # close, then the whole room
                previous = style.zooms[0]
            elif style.push_every and long_shots % style.push_every == style.push_every // 2:
                shot.zoom_end = round(zoom + 0.15, 3)                         # slow push in
                previous = shot.zoom_end
            long_shots += 1
    return shots


def snap(shots: list[Shot], fps: int) -> list[Shot]:
    """Shot edges on the output frame grid, so video and audio stay in step."""
    frame = 1.0 / fps
    for shot in shots:
        shot.start = round(shot.start / frame) * frame
        shot.end = max(shot.start + frame, round(shot.end / frame) * frame)
    return shots


def plan_show(transcript: dict[str, Any], duration: float, style: ShowStyle = CHOE) -> dict[str, Any]:
    """The whole edit decision for one talking source, JSON-ready."""
    words = mark_removals(words_from_transcript(transcript), style)
    shots = snap(pace(speech_runs(words, duration, style), words, style), style.fps)
    kept = sum(shot.duration for shot in shots)
    removed = {reason: sum(1 for w in words if w.removed == reason) for reason in ("filler", "retake")}
    return {
        "version": 1,
        "source_duration": round(duration, 3),
        "edited_duration": round(kept, 3),
        "removed_words": removed,
        "cuts": max(0, len(shots) - 1),
        "words": [{"index": w.index, "text": w.text, "start": round(w.start, 3), "end": round(w.end, 3), "removed": w.removed} for w in words],
        "fps": style.fps,
        # Frames are exact; seconds are for reading.
        "shots": [{"start_frame": round(s.start * style.fps), "end_frame": round(s.end * style.fps),
                   "start": round(s.start, 6), "end": round(s.end, 6), "zoom": s.zoom, **({"zoom_end": s.zoom_end} if s.zoom_end else {}), "words": s.words, "cut_before": s.cut_before} for s in shots],
    }


def edited_time(plan: dict[str, Any], seconds: float) -> float:
    """Where a moment of the source lands on the edited video. A moment that
    was cut lands where the edit resumes."""
    fps = int(plan.get("fps") or 30)
    offset = 0.0
    for shot in plan["shots"]:
        start, end = shot["start_frame"] / fps, shot["end_frame"] / fps
        if seconds < end:
            return round(offset + max(0.0, seconds - start), 3)
        offset += end - start
    return round(offset, 3)
