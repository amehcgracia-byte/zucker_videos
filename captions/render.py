from __future__ import annotations

from .model import CueTrack, Style


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    whole = int(seconds)
    fraction = seconds - whole
    return f"{whole // 3600}:{(whole % 3600) // 60:02d}:{(whole % 60) + fraction:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


def _dialogue(cue) -> str:
    if not cue.words:
        return _escape(cue.text)
    return " ".join("{\\k%d}%s" % (max(1, int(round((word.end - word.start) * 100))), _escape(word.text)) for word in cue.words)


def render_ass(track: CueTrack, style: Style, *, width: int = 1920, height: int = 1080, header: dict | None = None) -> str:
    header_style = ""
    if header and header.get("title_enabled") and header.get("title"):
        header_style = "Style: FixedHeader,Arial Bold,42,&H00000000,&H00000000,&H00000000,&H00FFFFFF,1,0,0,0,100,100,0,0,3,12,2,8,60,60,70,1\n"
    styles = "Style: %s,%s,%s,%s,&H0000FF00,%s,%s,0,0,0,0,100,100,0,0,1,2,2,%s,%s,%s,%s,1\n" % (style.name, style.font, style.size, style.color, style.outline, style.shadow, style.alignment, style.margin_l, style.margin_r, style.margin_v)
    header_text = "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n%s%s\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" % (width, height, styles, header_style)
    events = []
    for cue in track.cues:
        events.append("Dialogue: 0,%s,%s,%s,,0,0,0,,%s" % (_ass_time(cue.start), _ass_time(cue.end), style.name, _dialogue(cue)))
    if header and header.get("title_enabled") and header.get("title"):
        duration = max((cue.end for cue in track.cues), default=3600.0)
        events.insert(0, "Dialogue: 10,0:00:00.00,%s,FixedHeader,,0,0,0,,%s" % (_ass_time(duration), _escape(str(header["title"]).upper())))
    return header_text + "\n".join(events) + ("\n" if events else "")
