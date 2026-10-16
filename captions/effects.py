"""Caption effects, defined once for preview and burn.

Entrance/exit effects run once: an entrance over the first EFFECT_SEC of a
cue and an exit over the last EFFECT_SEC. The burn uses native ASS animation
tags (\\fad, \\move, \\t(\\fscx\\fscy)); web/app.js computes the same curves
from the same constants, so scrubbing the editor shows exactly what is burned.

Curves (t = elapsed share of the effect, 0..1):
  fade   opacity linear
  scale  72% -> 100%: scale = 0.72 + 0.28 * t**0.5 (ASS accel 0.5, entrance)
                       100% -> 72%: scale = 1 - 0.28 * t**2 (ASS accel 2, exit)
         with a fade
  slide  horizontal offset of 6% of the frame width, linear (ASS \\move),
         entering from the left and leaving to the right, with a fade

Fun effects are a separate, louder layer on top of entrance/exit:

  loops (all the time the caption is shown, repeating every period)
    pulse   grows to 120% and back
    jelly   squash and stretch (120% wide / 85% high and back)
    blink   shown, then hidden for the last 40% of every period
    bounce  jumps up by 4% of the frame height and falls back
    wobble  tilts 8 degrees each way
    shake   tilts 4 degrees each way, fast
  once (from the start of the caption)
    pop     bursts from nothing to 125%, settles at 100%
    spin    one full turn while growing from nothing
    drop / rise / fly_right / fly_left
            flies in from outside the frame (top, bottom, right, left) in
            FLY_SEC, then lands with a cartoon squash

Every fun effect is a list of segments (t1, t2, accel, targets) with libass
\\t semantics: before t1 nothing changes, after t2 the targets are reached,
in between value += (target - value) * ((t - t1) / (t2 - t1)) ** accel.
Targets: sx/sy scale (ASS \\fscx/\\fscy / 100), rot (ASS \\frz degrees,
counter-clockwise) and dy (bounce, in units of BOUNCE_SHARE of the frame
height, negative = up). ASS cannot animate \\pos, so the bounce rotates the
caption by a tiny angle around a far-away \\org: a 100 000 px arm turns a
0.044 degree rotation into a 77 px lift with no visible tilt. web/app.js
mirrors these tables (CAPTION_FUN_*), and tests compare both.
"""

from __future__ import annotations

import math

EFFECT_SEC = 0.25
EFFECTS = ("none", "fade", "slide", "scale")
DEFAULT_EFFECT = "fade"
SCALE_FROM = 72
SLIDE_SHARE = 0.06
ENTRANCE_ACCEL = 0.5
EXIT_ACCEL = 2.0

NEUTRAL = {"sx": 1.0, "sy": 1.0, "rot": 0.0, "dy": 0.0}
# name: (period seconds, segments as shares of the period)
FUN_LOOPS = {
    "pulse": (0.6, ((0.0, 0.5, 1.0, {"sx": 1.2, "sy": 1.2}), (0.5, 1.0, 1.0, {"sx": 1.0, "sy": 1.0}))),
    "jelly": (0.6, ((0.0, 0.5, 1.0, {"sx": 1.2, "sy": 0.85}), (0.5, 1.0, 1.0, {"sx": 1.0, "sy": 1.0}))),
    "blink": (0.5, ((0.6, 0.6, 1.0, {"sx": 0.0, "sy": 0.0}), (1.0, 1.0, 1.0, {"sx": 1.0, "sy": 1.0}))),
    "bounce": (0.5, ((0.0, 0.5, 0.5, {"dy": -1.0}), (0.5, 1.0, 2.0, {"dy": 0.0}))),
    "wobble": (0.8, ((0.0, 0.25, 1.0, {"rot": 8.0}), (0.25, 0.75, 1.0, {"rot": -8.0}), (0.75, 1.0, 1.0, {"rot": 0.0}))),
    "shake": (0.16, ((0.0, 0.25, 1.0, {"rot": 4.0}), (0.25, 0.75, 1.0, {"rot": -4.0}), (0.75, 1.0, 1.0, {"rot": 0.0}))),
}
FLY_SEC = 0.45
_LANDING_SQUASH = ((0.45, 0.55, 1.0, {"sx": 1.25, "sy": 0.75}), (0.55, 0.7, 1.0, {"sx": 1.0, "sy": 1.0}))
_SIDE_SQUASH = ((0.45, 0.55, 1.0, {"sx": 0.8, "sy": 1.15}), (0.55, 0.7, 1.0, {"sx": 1.0, "sy": 1.0}))
# name: (values at the start of the caption, segments in seconds)
FUN_ONCE = {
    "pop": ({"sx": 0.0, "sy": 0.0}, ((0.0, 0.18, 0.5, {"sx": 1.25, "sy": 1.25}), (0.18, 0.3, 1.0, {"sx": 1.0, "sy": 1.0}))),
    "spin": ({"sx": 0.0, "sy": 0.0, "rot": 360.0}, ((0.0, 0.5, 0.5, {"sx": 1.0, "sy": 1.0, "rot": 0.0}),)),
    "drop": ({}, _LANDING_SQUASH),
    "rise": ({}, _LANDING_SQUASH),
    "fly_right": ({}, _SIDE_SQUASH),
    "fly_left": ({}, _SIDE_SQUASH),
}
FUN_EFFECTS = ("none", *FUN_LOOPS, *FUN_ONCE)
FLY_EFFECTS = ("drop", "rise", "fly_right", "fly_left")
FLY_LANDED_SEC = 0.7
# These drive \fscx/\fscy, which the scale entrance/exit also animates.
SCALING_FUN = ("pulse", "jelly", "blink", "pop", "spin", *FLY_EFFECTS)
BOUNCE_SHARE = 0.04
BOUNCE_ORIGIN = 100000.0
# Off-frame start of a fly-in, from the caption's own layout (same estimate
# of glyph width as captions/layout.py and web/app.js).
FLY_GLYPH_WIDTH = 0.55
FLY_LINE_HEIGHT = 1.3
FLY_MARGIN = 20.0


def normalise(value: object) -> str:
    text = str(value or "none").strip().lower()
    return text if text in EFFECTS else "none"


def normalise_fun(value: object) -> str:
    text = str(value or "none").strip().lower()
    return text if text in FUN_EFFECTS else "none"


def resolve(enter: str, exit_: str, fun: str, duration: float) -> tuple[str, str]:
    """Entrance/exit once a fun effect owns the scale or the motion: a scale
    entrance/exit becomes a fade under a scaling fun effect, and a slide
    becomes a fade under a fly-in (one \\move per event)."""
    enter, exit_, fun = normalise(enter), normalise(exit_), normalise_fun(fun)
    if fun in SCALING_FUN:
        enter = "fade" if enter == "scale" else enter
        exit_ = "fade" if exit_ == "scale" else exit_
    if fun in FLY_EFFECTS:
        enter = "fade" if enter == "slide" else enter
        if exit_ == "slide" and duration - EFFECT_SEC < FLY_LANDED_SEC:
            exit_ = "fade"
    return enter, exit_


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def effect_seconds(duration: float, enter: str, exit_: str) -> float:
    """Effect length: shared by entrance and exit only when one event has both."""
    both = normalise(enter) != "none" and normalise(exit_) != "none"
    return max(0.0, min(EFFECT_SEC, duration / 2 if both else duration))


def effect_phases(start: float, end: float, enter: str, exit_: str, fun: str = "none") -> list[tuple[float, float, str, str]]:
    """Split a cue into (start, end, entrance, exit) events.

    ASS allows one \\move per line, so a cue that moves in (slide or fly-in)
    and slides out is split where the exit begins; every other cue stays a
    single event. ``enter``/``exit_`` are already resolved for ``fun``.
    """
    enter, exit_ = normalise(enter), normalise(exit_)
    moves_in = enter == "slide" or normalise_fun(fun) in FLY_EFFECTS
    if moves_in and exit_ == "slide" and end - start > 2 * EFFECT_SEC:
        middle = end - EFFECT_SEC
        return [(start, middle, enter, "none"), (middle, end, "none", exit_)]
    return [(start, end, enter, exit_)]


def _apply(state: dict[str, float], segments, t: float, scale: float = 1.0, shift: float = 0.0) -> dict[str, float]:
    for t1, t2, accel, targets in segments:
        start, end = shift + t1 * scale, shift + t2 * scale
        if t < start:
            break
        k = 1.0 if t >= end else ((t - start) / (end - start)) ** accel
        for key, target in targets.items():
            state[key] = state[key] + (target - state[key]) * k
    return state


def fun_state(fun: str, t: float) -> dict[str, float]:
    """sx, sy, rot and dy of a fun effect ``t`` seconds into its caption."""
    fun = normalise_fun(fun)
    state = dict(NEUTRAL)
    if fun in FUN_LOOPS:
        period, segments = FUN_LOOPS[fun]
        return _apply(state, segments, max(0.0, t) % period, scale=period)
    if fun in FUN_ONCE:
        base, segments = FUN_ONCE[fun]
        state.update(base)
        return _apply(state, segments, t)
    return state


def fly_start(fun: str, *, x: float, y: float, width: int, height: int, lines: int, longest: int, size: float) -> tuple[float, float]:
    """Where a fly-in starts: just outside the frame on its side."""
    across = longest * size * FLY_GLYPH_WIDTH * 1.1 + FLY_MARGIN
    down = max(1, lines) * size * FLY_LINE_HEIGHT + FLY_MARGIN
    return {
        "drop": (x, -down),
        "rise": (x, height + down),
        "fly_right": (width + across, y),
        "fly_left": (-across, y),
    }[fun]


def _fun_tag(key: str, value: float, height: int) -> str:
    if key == "sx":
        return f"\\fscx{round(value * 100, 3):g}"
    if key == "sy":
        return f"\\fscy{round(value * 100, 3):g}"
    if key == "rot":
        return f"\\frz{round(value, 4):g}"
    # dy: a lift around the far \org (positive \frz turns the text upwards).
    lift = -value * BOUNCE_SHARE * height
    return f"\\frz{round(math.degrees(math.asin(lift / BOUNCE_ORIGIN)), 6):g}"


def _transform(t1: int, t2: int, accel: float, targets: dict[str, float], height: int) -> str:
    tags = "".join(_fun_tag(key, value, height) for key, value in targets.items())
    if t2 == 0:
        # libass reads an end time of 0 as "the end of the event"; a step
        # already finished when a split event starts must stay finished.
        t2 = -1
        t1 = min(t1, t2)
    return f"\\t({t1},{t2},{accel:g},{tags})" if accel != 1 else f"\\t({t1},{t2},{tags})"


def fun_tags(fun: str, *, offset: float, duration: float, x: float, y: float, height: int) -> list[str]:
    """Native tags for ``fun`` in an event starting ``offset`` seconds into
    its caption and lasting ``duration`` seconds (fly-in motion excluded)."""
    fun = normalise_fun(fun)
    offset_ms, end_ms = _ms(offset), _ms(offset + duration)
    tags: list[str] = []
    if fun == "bounce":
        tags.append(f"\\org({x - BOUNCE_ORIGIN:g},{y:g})")
    if fun in FUN_LOOPS:
        period, segments = FUN_LOOPS[fun]
        period_ms = _ms(period)
        cycle = offset_ms // period_ms
        while cycle * period_ms < end_ms:
            base = cycle * period_ms
            for t1, t2, accel, targets in segments:
                start = base + round(t1 * period_ms)
                if start >= end_ms:
                    break
                # Times before the event (a split event continues a cycle)
                # are fine: libass treats them as already elapsed.
                tags.append(_transform(start - offset_ms, base + round(t2 * period_ms) - offset_ms, accel, targets, height))
            cycle += 1
    elif fun in FUN_ONCE:
        base, segments = FUN_ONCE[fun]
        tags.extend(_fun_tag(key, value, height) for key, value in base.items())
        for t1, t2, accel, targets in segments:
            if _ms(t1) >= end_ms:
                break
            tags.append(_transform(_ms(t1) - offset_ms, _ms(t2) - offset_ms, accel, targets, height))
    return tags


def ass_tags(duration: float, enter: str, exit_: str, *, x: float, y: float, width: int,
             height: int = 1920, fun: str = "none", offset: float = 0.0,
             fly_from: tuple[float, float] | None = None) -> list[str]:
    """Native ASS tags for one event; positions it at (x, y) unless moving."""
    enter, exit_ = normalise(enter), normalise(exit_)
    fun = normalise_fun(fun)
    effect_ms = _ms(effect_seconds(duration, enter, exit_))
    total_ms = _ms(duration)
    tags: list[str] = []
    fade_in = effect_ms if enter != "none" else 0
    fade_out = effect_ms if exit_ != "none" else 0
    if fade_in or fade_out:
        tags.append(f"\\fad({fade_in},{fade_out})")
    offset_px = width * SLIDE_SHARE
    if fun in FLY_EFFECTS and fly_from is not None and offset == 0:
        tags.append(f"\\move({fly_from[0]:g},{fly_from[1]:g},{x:g},{y:g},0,{min(total_ms, _ms(FLY_SEC))})")
    elif enter == "slide":
        tags.append(f"\\move({x - offset_px:g},{y:g},{x:g},{y:g},0,{effect_ms})")
    elif exit_ == "slide":
        tags.append(f"\\move({x:g},{y:g},{x + offset_px:g},{y:g},{total_ms - effect_ms},{total_ms})")
    else:
        tags.append(f"\\pos({x:g},{y:g})")
    if enter == "scale":
        tags.append(f"\\fscx{SCALE_FROM}\\fscy{SCALE_FROM}\\t(0,{effect_ms},{ENTRANCE_ACCEL:g},\\fscx100\\fscy100)")
    if exit_ == "scale":
        tags.append(f"\\t({total_ms - effect_ms},{total_ms},{EXIT_ACCEL:g},\\fscx{SCALE_FROM}\\fscy{SCALE_FROM})")
    tags.extend(fun_tags(fun, offset=offset, duration=duration, x=x, y=y, height=height))
    return tags
