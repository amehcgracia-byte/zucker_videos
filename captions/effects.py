"""Caption entrance/exit effects, defined once for preview and burn.

Each effect runs once: an entrance over the first EFFECT_SEC of a cue and an
exit over the last EFFECT_SEC. The burn uses native ASS animation tags
(\\fad, \\move, \\t(\\fscx\\fscy)); web/app.js computes the same curves from
the same constants, so scrubbing the editor shows exactly what is burned.

Curves (t = elapsed share of the effect, 0..1):
  fade   opacity linear
  scale  72% -> 100%: scale = 0.72 + 0.28 * t**0.5 (ASS accel 0.5, entrance)
                       100% -> 72%: scale = 1 - 0.28 * t**2 (ASS accel 2, exit)
         with a fade
  slide  horizontal offset of 6% of the frame width, linear (ASS \\move),
         entering from the left and leaving to the right, with a fade
"""

from __future__ import annotations

EFFECT_SEC = 0.25
EFFECTS = ("none", "fade", "slide", "scale")
DEFAULT_EFFECT = "fade"
SCALE_FROM = 72
SLIDE_SHARE = 0.06
ENTRANCE_ACCEL = 0.5
EXIT_ACCEL = 2.0


def normalise(value: object) -> str:
    text = str(value or "none").strip().lower()
    return text if text in EFFECTS else "none"


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def effect_seconds(duration: float, enter: str, exit_: str) -> float:
    """Effect length: shared by entrance and exit only when one event has both."""
    both = normalise(enter) != "none" and normalise(exit_) != "none"
    return max(0.0, min(EFFECT_SEC, duration / 2 if both else duration))


def effect_phases(start: float, end: float, enter: str, exit_: str) -> list[tuple[float, float, str, str]]:
    """Split a cue into (start, end, entrance, exit) events.

    ASS allows one \\move per line, so a cue that slides both in and out is
    split where the exit begins; every other cue stays a single event.
    """
    enter, exit_ = normalise(enter), normalise(exit_)
    if enter == "slide" and exit_ == "slide" and end - start > 2 * EFFECT_SEC:
        middle = end - EFFECT_SEC
        return [(start, middle, enter, "none"), (middle, end, "none", exit_)]
    return [(start, end, enter, exit_)]


def ass_tags(duration: float, enter: str, exit_: str, *, x: float, y: float, width: int) -> list[str]:
    """Native ASS tags for one event; positions it at (x, y) unless sliding."""
    enter, exit_ = normalise(enter), normalise(exit_)
    effect_ms = _ms(effect_seconds(duration, enter, exit_))
    total_ms = _ms(duration)
    tags: list[str] = []
    fade_in = effect_ms if enter != "none" else 0
    fade_out = effect_ms if exit_ != "none" else 0
    if fade_in or fade_out:
        tags.append(f"\\fad({fade_in},{fade_out})")
    offset = width * SLIDE_SHARE
    if enter == "slide":
        tags.append(f"\\move({x - offset:g},{y:g},{x:g},{y:g},0,{effect_ms})")
    elif exit_ == "slide":
        tags.append(f"\\move({x:g},{y:g},{x + offset:g},{y:g},{total_ms - effect_ms},{total_ms})")
    else:
        tags.append(f"\\pos({x:g},{y:g})")
    if enter == "scale":
        tags.append(f"\\fscx{SCALE_FROM}\\fscy{SCALE_FROM}\\t(0,{effect_ms},{ENTRANCE_ACCEL:g},\\fscx100\\fscy100)")
    if exit_ == "scale":
        tags.append(f"\\t({total_ms - effect_ms},{total_ms},{EXIT_ACCEL:g},\\fscx{SCALE_FROM}\\fscy{SCALE_FROM})")
    return tags
