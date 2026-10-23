from __future__ import annotations

from .effects import (DEFAULT_EFFECT, FLY_EFFECTS, ass_tags as effect_tags, effect_phases, fly_start,
                      normalise as normalise_effect, normalise_fun, resolve as resolve_effects)
from .layout import caption_layout
from .model import CueTrack, Style


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    fraction = seconds - whole
    return f"{whole // 3600}:{(whole % 3600) // 60:02d}:{(whole % 60) + fraction:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _ass_color(value: object) -> str:
    text = str(value or "").strip().lstrip("#")
    if text.upper().startswith("&H"):
        return text.upper() if text.endswith("&") else f"{text.upper()}&"
    if len(text) != 6:
        return ""
    return f"&H00{text[4:6]}{text[2:4]}{text[0:2]}&".upper()


def _ass_alpha(opacity: object) -> str:
    try:
        value = max(0.0, min(1.0, float(opacity)))
    except (TypeError, ValueError):
        value = 1.0
    return f"&H{round((1.0 - value) * 255):02X}&"


def _usable_words(cue) -> tuple:
    """Return only word timings that can safely drive an ASS karaoke event.

    Auto Read deliberately falls back to segment-only transcription when the
    backend cannot align words.  A style must never turn that valid caption
    into an empty event just because it requests word animation.
    """
    words = []
    for word in cue.words or ():
        text = str(getattr(word, "text", "") or "").strip()
        try:
            start = float(word.start)
            end = float(word.end)
        except (TypeError, ValueError):
            continue
        if text and end > start:
            words.append((text, start, end))
    return tuple(words)


def _effects(cue) -> tuple[str, str, str]:
    """Entrance, exit and fun effect, with entrance/exit resolved for the
    fun effect (see captions.effects.resolve)."""
    override = cue.style_override or {}
    # The editor's Enter/Exit menus show "fade" until another effect is
    # chosen; an unset effect is that fade, so the menu matches the burn.
    enter = normalise_effect(override.get("animation_in", DEFAULT_EFFECT))
    exit_ = normalise_effect(override.get("animation_out", DEFAULT_EFFECT))
    # Plans saved by older builds carried fades as private millisecond keys.
    if enter == "none" and float(override.get("_fade_in_ms") or 0) > 0:
        enter = "fade"
    if exit_ == "none" and float(override.get("_fade_out_ms") or 0) > 0:
        exit_ = "fade"
    fun = normalise_fun(override.get("fun_effect"))
    enter, exit_ = resolve_effects(enter, exit_, fun, float(cue.end) - float(cue.start))
    return enter, exit_, fun


def _placement(cue, style: Style, *, width: int, height: int) -> tuple[int, float, float, bool]:
    """Alignment and anchor point. A chosen position (percent of the frame,
    set by dragging the caption in the editor) centres the text on that
    point, exactly like the editor preview (translate -50%/-50%)."""
    override = cue.style_override or {}
    if override.get("vertical") is not None:
        y = max(0.0, min(float(height), height * float(override["vertical"]) / 100.0))
        x = width / 2
        if override.get("horizontal") is not None:
            x = max(0.0, min(float(width), width * float(override["horizontal"]) / 100.0))
        return 5, x, y, True
    alignment = max(1, min(9, int(override.get("alignment", style.alignment))))
    column, row = (alignment - 1) % 3, (alignment - 1) // 3
    x = style.margin_l if column == 0 else width / 2 if column == 1 else width - style.margin_r
    y = height - style.margin_v if row == 0 else height / 2 if row == 1 else style.margin_v
    return alignment, float(x), float(y), False


def _position_tags(cue, style: Style, *, width: int, height: int, duration: float, enter: str, exit_: str,
                   fun: str = "none", offset: float = 0.0) -> list[str]:
    alignment, x, y, explicit = _placement(cue, style, width=width, height=height)
    tags = [f"\\an{alignment}"]
    if explicit or enter != "none" or exit_ != "none" or fun != "none":
        fly_from = None
        if fun in FLY_EFFECTS:
            lines, fitted_size, _requested = _layout(cue, style, width=width)
            fly_from = fly_start(fun, x=x, y=y, width=width, height=height, lines=len(lines),
                                 longest=max((len(line) for line in lines), default=0), size=fitted_size)
        tags.extend(effect_tags(duration, enter, exit_, x=x, y=y, width=width, height=height,
                                fun=fun, offset=offset, fly_from=fly_from))
    return tags


def _layout(cue, style: Style, *, width: int) -> tuple[tuple[str, ...], float, float]:
    override = cue.style_override or {}
    requested_size = float(override.get("size", style.size))
    lines, fitted_size = caption_layout(
        cue.text,
        width=width,
        requested_size=requested_size,
        margin_l=style.margin_l,
        margin_r=style.margin_r,
    )
    return lines, fitted_size, requested_size


def _karaoke(word_lines, event_start: float) -> str:
    """\\k syllables from absolute word times: gaps become empty syllables and
    words already spoken before ``event_start`` are highlighted at once."""
    cursor = event_start
    rendered = []
    for word_line in word_lines:
        parts = []
        for text, start, end in word_line:
            gap = max(0.0, start - cursor)
            spoken = max(0.0, end - max(start, cursor))
            prefix = "{\\k%d}" % round(gap * 100) if gap >= 0.01 else ""
            parts.append(prefix + "{\\k%d}%s" % (max(0, round(spoken * 100)), _escape(text)))
            cursor = max(cursor, end)
        rendered.append(" ".join(parts))
    return r"\N".join(rendered)


def _dialogue_body(cue, style: Style, *, width: int, event_start: float, first_event: bool) -> tuple[list[str], str]:
    """Style tags (colours, size, outline, shadow) and the text, without
    placement or effects."""
    override = cue.style_override or {}
    lines, fitted_size, requested_size = _layout(cue, style, width=width)
    tags: list[str] = []
    font = str(override.get("font") or "").strip()
    if font and all(ch not in font for ch in "\\{}"):
        tags.append(f"\\fn{font}")
    color = _ass_color(override.get("color"))
    if color:
        tags.append(f"\\c{color}")
    if override.get("size") is not None or fitted_size != requested_size:
        tags.append(f"\\fs{max(8, fitted_size):g}")
    if override.get("outline_width") is not None:
        tags.append(f"\\bord{max(0.0, float(override['outline_width'])):g}")
    outline_color = _ass_color(override.get("outline_color"))
    if outline_color:
        tags.append(f"\\3c{outline_color}")
    if override.get("shadow_distance") is not None:
        tags.append(f"\\shad{max(0.0, float(override['shadow_distance'])):g}")
    shadow_color = _ass_color(override.get("shadow_color"))
    if shadow_color:
        tags.append(f"\\4c{shadow_color}")
    if override.get("shadow_opacity") is not None:
        tags.append(f"\\4a{_ass_alpha(override['shadow_opacity'])}")
    escaped_lines = r"\N".join(_escape(line) for line in lines)
    if style.animation == "phrase":
        phrase_color = _ass_color(style.secondary)
        if phrase_color:
            # The whole phrase switches colour once, 250 ms into the cue; a
            # second event of the same cue (slide in + out) starts switched.
            tags.append(f"\\t(250,250,\\c{phrase_color})" if first_event else f"\\c{phrase_color}")
        return tags, escaped_lines
    words = _usable_words(cue)
    if style.animation != "word" or not words:
        # Word styles degrade to a visible, fixed-colour phrase when Whisper
        # had to use its no-word-timestamps fallback. A colour the user chose
        # for this caption always wins over the style's fallback colour.
        if style.animation == "word" and not color:
            fallback_color = _ass_color(style.secondary)
            if fallback_color:
                tags.append(f"\\c{fallback_color}")
        return tags, escaped_lines
    # Rebuild the same word-boundary lines used above, retaining karaoke
    # timings and inserting ASS line breaks at the selected word boundaries.
    word_lines = []
    cursor = 0
    for line in lines:
        count = len(line.split())
        word_lines.append(words[cursor:cursor + count])
        cursor += count
    return tags, _karaoke(word_lines, event_start)


def _glow_tags(cue, style: Style, *, width: int) -> tuple[list[str], int] | None:
    override = cue.style_override or {}
    color = _ass_color(override.get("glow_color", style.glow_color))
    try:
        blur = max(0.0, float(override.get("glow_blur", style.glow_blur)))
        layers = max(0, min(8, int(override.get("glow_layers", style.glow_layers))))
        intensity = max(0.0, min(1.0, float(override.get("glow_intensity", style.glow_intensity))))
    except (TypeError, ValueError):
        blur, layers, intensity = 0.0, 0, 1.0
    if not color or blur <= 0 or layers <= 0 or intensity <= 0:
        return None
    _lines, fitted_size, requested_size = _layout(cue, style, width=width)
    tags = []
    font = str(override.get("font") or "").strip()
    if font and all(ch not in font for ch in "\\{}"):
        tags.append(f"\\fn{font}")
    if override.get("size") is not None or fitted_size != requested_size:
        tags.append(f"\\fs{max(8, fitted_size):g}")
    tags.extend((f"\\1a&HFF&", f"\\3c{color}", f"\\3a{_ass_alpha(intensity)}", f"\\bord{max(2.0, blur * 1.7):g}", f"\\blur{blur:g}", "\\shad0"))
    return tags, layers


def _cue_events(cue, style: Style, *, width: int, height: int) -> list[str]:
    """Glow layers and the caption itself, sharing placement and effects so
    the glow enters, moves and leaves with its text."""
    enter, exit_, fun = _effects(cue)
    glow = _glow_tags(cue, style, width=width)
    events = []
    phases = effect_phases(float(cue.start), float(cue.end), enter, exit_, fun)
    for index, (start, end, phase_in, phase_out) in enumerate(phases):
        position = _position_tags(cue, style, width=width, height=height, duration=end - start, enter=phase_in, exit_=phase_out,
                                  fun=fun, offset=start - float(cue.start))
        if glow:
            glow_tags, layers = glow
            lines, _fitted, _requested = _layout(cue, style, width=width)
            text = r"\N".join(_escape(line) for line in lines)
            for layer in range(layers):
                events.append("Dialogue: %d,%s,%s,%s,,0,0,0,,{%s}%s" % (
                    layer, _ass_time(start), _ass_time(end), style.name, "".join(position + glow_tags), text))
        tags, body = _dialogue_body(cue, style, width=width, event_start=start, first_event=index == 0)
        events.append("Dialogue: 20,%s,%s,%s,,0,0,0,,{%s}%s" % (
            _ass_time(start), _ass_time(end), style.name, "".join(position + tags), body))
    return events


def render_ass(track: CueTrack, style: Style, *, width: int = 1920, height: int = 1080, header: dict | None = None) -> str:
    header_style = ""
    if header and header.get("title_enabled") and header.get("title"):
        header_style = "Style: FixedHeader,Arial Bold,42,&H00000000,&H00000000,&H00000000,&H00FFFFFF,1,0,0,0,100,100,0,0,3,12,2,8,60,60,70,1\n"
    border_style = 3 if style.box else 1
    back_colour = style.box_color if style.box else style.shadow
    styles = "Style: %s,%s,%s,%s,%s,%s,%s,0,0,0,0,100,100,0,0,%s,2,2,%s,%s,%s,%s,1\n" % (style.name, style.font, style.size, style.color, style.secondary, style.outline, back_colour, border_style, style.alignment, style.margin_l, style.margin_r, style.margin_v)
    header_text = "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n%s%s\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" % (width, height, styles, header_style)
    events = []
    for cue in track.cues:
        events.extend(_cue_events(cue, style, width=width, height=height))
    if header and header.get("title_enabled") and header.get("title"):
        duration = max((cue.end for cue in track.cues), default=3600.0)
        events.insert(0, "Dialogue: 10,0:00:00.00,%s,FixedHeader,,0,0,0,,%s" % (_ass_time(duration), _escape(str(header["title"]).upper())))
    return header_text + "\n".join(events) + ("\n" if events else "")
