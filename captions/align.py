"""Pure word-level forced alignment for user-supplied lyrics."""
from __future__ import annotations

import difflib
import re

from .model import Cue, CueTrack, Word


def _key(value: str) -> str:
    return re.sub(r"[^\w']+", "", value.casefold(), flags=re.UNICODE)


def _nw(known: list[str], heard: list[str]) -> list[tuple[int, int]]:
    gap = -1.0
    dp = [[0.0] * (len(heard) + 1) for _ in range(len(known) + 1)]
    move = [[" "] * (len(heard) + 1) for _ in range(len(known) + 1)]
    for i in range(1, len(known) + 1): dp[i][0], move[i][0] = dp[i - 1][0] + gap, "u"
    for j in range(1, len(heard) + 1): dp[0][j], move[0][j] = dp[0][j - 1] + gap, "l"
    for i in range(1, len(known) + 1):
        for j in range(1, len(heard) + 1):
            score = 2.0 if known[i - 1] == heard[j - 1] else (1.0 if difflib.SequenceMatcher(None, known[i - 1], heard[j - 1]).ratio() >= .65 else -1.0)
            choices = [(dp[i - 1][j - 1] + score, "d"), (dp[i - 1][j] + gap, "u"), (dp[i][j - 1] + gap, "l")]
            dp[i][j], move[i][j] = max(choices, key=lambda item: item[0])
    pairs: list[tuple[int, int]] = []
    i, j = len(known), len(heard)
    while i or j:
        action = move[i][j]
        if action == "d": pairs.append((i - 1, j - 1)); i -= 1; j -= 1
        elif action == "u": i -= 1
        else: j -= 1
    return list(reversed(pairs))


def align_known_lyrics(lyrics: CueTrack, whisper: CueTrack) -> CueTrack:
    """Return the exact lyric text with times anchored to Whisper words.

    Unmatched user words are linearly interpolated between neighboring anchors;
    no Whisper text is ever copied into the returned track.
    """
    known_words: list[tuple[int, str]] = []
    for ci, cue in enumerate(lyrics.cues):
        for token in re.findall(r"\S+", " ".join(cue.lines)):
            known_words.append((ci, token))
    heard_words = [word for cue in whisper.cues for word in cue.words]
    pairs = _nw([_key(word) for _, word in known_words], [_key(word.text) for word in heard_words])
    anchors = {ki: Word(known_words[ki][1], heard_words[wi].start, heard_words[wi].end) for ki, wi in pairs}
    positions = [None] * len(known_words)
    for index, word in anchors.items(): positions[index] = word
    for index in range(len(known_words)):
        if positions[index] is not None: continue
        left = max((i for i in range(index) if positions[i] is not None), default=None)
        right = min((i for i in range(index + 1, len(known_words)) if positions[i] is not None), default=None)
        cue = lyrics.cues[known_words[index][0]]
        left_end = positions[left].end if left is not None else cue.start
        right_start = positions[right].start if right is not None else cue.end
        span = max(0.05, right_start - left_end)
        offset = index - (left if left is not None else index) + 1
        count = (right - (left if left is not None else index) if right is not None else 1)
        start = left_end + span * max(0, offset - 1) / max(1, count)
        end = left_end + span * offset / max(1, count)
        positions[index] = Word(known_words[index][1], start, max(start + .03, end))
    grouped: list[list[Word]] = [[] for _ in lyrics.cues]
    for (cue_index, _), word in zip(known_words, positions): grouped[cue_index].append(word)
    cues = []
    for cue, words in zip(lyrics.cues, grouped):
        if words:
            cues.append(Cue(cue.lines, max(cue.start, words[0].start), min(cue.end, words[-1].end), tuple(words)))
        else: cues.append(cue)
    return CueTrack(tuple(cues), lyrics.lang)
