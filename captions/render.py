from __future__ import annotations

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


def _dialogue(cue, style: Style, *, width: int, height: int) -> str:
    override = cue.style_override or {}
    alignment = int(override.get("alignment", style.alignment))
    tags = [f"\\an{max(1, min(9, alignment))}"]
    color = _ass_color(override.get("color"))
    if color:
        tags.append(f"\\c{color}")
    if override.get("size") is not None:
        tags.append(f"\\fs{max(8, float(override['size'])):g}")
    if override.get("vertical") is not None:
        y = max(0, min(height, height * float(override["vertical"]) / 100.0))
        tags.append(f"\\pos({width / 2:g},{y:g})")
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
    prefix = "{" + "".join(tags) + "}" if tags else ""
    if not cue.words:
        return prefix + _escape(cue.text)
    return prefix + " ".join("{\\k%d}%s" % (max(1, int(round((word.end - word.start) * 100))), _escape(word.text)) for word in cue.words)


def _glow_dialogues(cue, style: Style, *, width: int, height: int, start_layer: int = 0) -> list[str]:
    override = cue.style_override or {}
    color = _ass_color(override.get("glow_color", style.glow_color))
    try:
        blur = max(0.0, float(override.get("glow_blur", style.glow_blur)))
        layers = max(0, min(8, int(override.get("glow_layers", style.glow_layers))))
        intensity = max(0.0, min(1.0, float(override.get("glow_intensity", style.glow_intensity))))
    except (TypeError, ValueError):
        blur, layers, intensity = 0.0, 0, 1.0
    if not color or blur <= 0 or layers <= 0 or intensity <= 0:
        return []
    alignment = max(1, min(9, int(override.get("alignment", style.alignment))))
    outline_width = max(2.0, blur * 1.7)
    alpha = _ass_alpha(intensity)
    text = _escape(cue.text)
    return [
        "Dialogue: %d,%s,%s,%s,,0,0,0,,{%s}%s" % (
            start_layer + index,
            _ass_time(cue.start),
            _ass_time(cue.end),
            style.name,
            "".join((f"\\an{alignment}", f"\\1a&HFF&", f"\\3c{color}", f"\\3a{alpha}", f"\\bord{outline_width:g}", f"\\blur{blur:g}", "\\shad0")),
            text,
        )
        for index in range(layers)
    ]


def render_ass(track: CueTrack, style: Style, *, width: int = 1920, height: int = 1080, header: dict | None = None) -> str:
    header_style = ""
    if header and header.get("title_enabled") and header.get("title"):
        header_style = "Style: FixedHeader,Arial Bold,42,&H00000000,&H00000000,&H00000000,&H00FFFFFF,1,0,0,0,100,100,0,0,3,12,2,8,60,60,70,1\n"
    border_style = 3 if style.box else 1
    back_colour = style.box_color if style.box else style.shadow
    styles = "Style: %s,%s,%s,%s,&H0000FF00,%s,%s,0,0,0,0,100,100,0,0,%s,2,2,%s,%s,%s,%s,1\n" % (style.name, style.font, style.size, style.color, style.outline, back_colour, border_style, style.alignment, style.margin_l, style.margin_r, style.margin_v)
    header_text = "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n%s%s\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" % (width, height, styles, header_style)
    events = []
    for cue in track.cues:
        events.extend(_glow_dialogues(cue, style, width=width, height=height))
        events.append("Dialogue: 20,%s,%s,%s,,0,0,0,,%s" % (_ass_time(cue.start), _ass_time(cue.end), style.name, _dialogue(cue, style, width=width, height=height)))
    if header and header.get("title_enabled") and header.get("title"):
        duration = max((cue.end for cue in track.cues), default=3600.0)
        events.insert(0, "Dialogue: 10,0:00:00.00,%s,FixedHeader,,0,0,0,,%s" % (_ass_time(duration), _escape(str(header["title"]).upper())))
    return header_text + "\n".join(events) + ("\n" if events else "")
