from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Cue:
    lines: tuple[str, ...]
    start: float
    end: float
    words: tuple[Word, ...] = ()
    style_override: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("cue end must not precede start")
        object.__setattr__(self, "lines", tuple(self.lines))
        object.__setattr__(self, "words", tuple(self.words))
        object.__setattr__(self, "style_override", dict(self.style_override))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class CueTrack:
    cues: tuple[Cue, ...]
    lang: str = "und"

    def __post_init__(self) -> None:
        object.__setattr__(self, "cues", tuple(self.cues))


@dataclass(frozen=True)
class Style:
    name: str
    font: str
    size: float
    color: str
    outline: str
    shadow: str
    alignment: int
    margin_l: int
    margin_r: int
    margin_v: int
    entrance: str = "none"
    exit: str = "none"
    animation: str = "none"
    box: bool = False
    box_color: str = "&H00000000"
