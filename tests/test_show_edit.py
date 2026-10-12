"""Show mode edit decisions from word timestamps."""
from core import show_edit
from core.show_edit import CHOE, plan_show


def _transcript(timed_words):
    """[(text, start, end), ...] as one Whisper segment."""
    return {"segments": [{"words": [{"word": text, "start_sec": start, "end_sec": end} for text, start, end in timed_words]}]}


def _speak(text, start=0.0, word=0.32, gap=0.08):
    words, t = [], start
    for token in text.split():
        words.append((token, round(t, 3), round(t + word, 3)))
        t += word + gap
    return words, t


def test_fillers_and_long_silences_are_cut_out():
    first, t = _speak("hola a todos bienvenidos al programa")
    filler = [("eh", t + 0.1, t + 0.5)]
    second, end = _speak("hoy vamos a hablar de música", start=t + 1.8)
    plan = plan_show(_transcript(first + filler + second), duration=end + 0.5)
    kept = {w["text"] for w in plan["words"] if not w["removed"]}
    assert "eh" not in kept and plan["removed_words"]["filler"] == 1
    # The filler and the 1.3 s pause are gone: no shot covers them.
    filler_start, filler_end = filler[0][1], filler[0][2]
    assert not any(s["start"] < filler_end - 0.05 and s["end"] > filler_start + 0.05 for s in plan["shots"])
    assert plan["edited_duration"] < plan["source_duration"] - 1.0
    assert plan["shots"][len(plan["shots"]) // 2]["cut_before"] in {"filler", "silence", "pace"}


def test_the_last_take_of_a_repeated_phrase_is_kept():
    take1, t = _speak("lo más importante de la música es")
    take2, t = _speak("lo más importante de la música es el ritmo", start=t + 0.9)
    plan = plan_show(_transcript(take1 + take2), duration=t + 0.3)
    removed = [w for w in plan["words"] if w["removed"] == "retake"]
    assert len(removed) == len(take1)
    kept_text = " ".join(w["text"] for w in plan["words"] if not w["removed"])
    assert kept_text == "lo más importante de la música es el ritmo"


def test_long_speech_is_cut_into_fast_shots_with_changing_framing():
    words, t = _speak(" ".join(["palabra"] * 60))  # 24 s without a pause
    plan = plan_show(_transcript(words), duration=t)
    shots = plan["shots"]
    lengths = sorted(s["end"] - s["start"] for s in shots)
    assert CHOE.min_shot - 0.05 <= lengths[0] and lengths[-1] <= CHOE.max_shot + 0.05
    median = lengths[len(lengths) // 2]
    assert 1.0 <= median <= 2.2  # Choe pace: ~1.5 s shots
    cuts_per_minute = plan["cuts"] / (plan["edited_duration"] / 60)
    assert 20 <= cuts_per_minute <= 50
    assert all(a["zoom"] != b["zoom"] for a, b in zip(shots, shots[1:]))
    # Splits fall between words: no word is cut in half.
    edges = [s["end"] for s in shots[:-1]]
    for text, start, end in words:
        assert not any(start + 0.02 < edge < end - 0.02 for edge in edges)


def test_shot_edges_sit_on_the_frame_grid_and_never_overlap():
    first, t = _speak("uno dos tres cuatro")
    second, t = _speak("cinco seis siete", start=t + 1.0)
    plan = plan_show(_transcript(first + second), duration=t)
    for shot in plan["shots"]:
        assert shot["end_frame"] > shot["start_frame"]
        assert abs(shot["start"] - shot["start_frame"] / CHOE.fps) < 1e-5
        assert abs(shot["end"] - shot["end_frame"] / CHOE.fps) < 1e-5
    assert all(a["end_frame"] <= b["start_frame"] for a, b in zip(plan["shots"], plan["shots"][1:]))


def test_hesitations_are_recognised_in_any_spelling():
    assert all(show_edit.normalise(text) in show_edit.FILLERS for text in ["Eh,", "ehm...", "Mmm", "Um", "uh"])
    assert show_edit.normalise("¿Música?") == "musica"


def test_captions_follow_the_edited_timeline_and_never_end_on_a_little_word():
    from core.show import edited_captions
    take1, t = _speak("lo más importante de la música es")
    take2, t = _speak("lo más importante de la música es el ritmo, porque el ritmo te mueve", start=t + 0.9)
    plan = plan_show(_transcript(take1 + take2), duration=t + 0.3)
    cues = edited_captions(plan, "choe")
    text = " ".join(cue["lines"][0] for cue in cues)
    assert text == "lo más importante de la música es el ritmo, porque el ritmo te mueve"
    assert cues[0]["start"] <= 0.1 and cues[-1]["end"] <= plan["edited_duration"] + 1e-6
    assert all(a["end"] <= b["start"] for a, b in zip(cues, cues[1:]))
    endings = [cue["lines"][0].split()[-1].strip(",.").lower() for cue in cues[:-1]]
    assert not set(endings) & {"el", "la", "de", "es", "te"}
    assert all(cue["style_override"]["fun_effect"] == "pop" for cue in cues)
