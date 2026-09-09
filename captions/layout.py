from __future__ import annotations

import math
import re


# Keep this approximation identical to the constants in web/app.js.  ASS and
# CSS use the same logical 1080-wide composition canvas; using one layout rule
# prevents the browser from showing a wrapped caption that the burn does not.
AVERAGE_GLYPH_WIDTH = 0.55
MIN_CAPTION_SIZE = 12.0


def _wrap_words(text: str, max_chars: int) -> list[str]:
    words = re.findall(r"\S+", str(text or ""))
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def caption_layout(
    text: str,
    *,
    width: int,
    requested_size: float,
    margin_l: float,
    margin_r: float,
) -> tuple[tuple[str, ...], float]:
    """Fit a caption into at most two word-boundary lines.

    The font-size reduction is deliberately deterministic and shared with the
    browser implementation. A word is never split; if an unusually long word
    remains wider than the available canvas at the minimum size, it is kept
    intact rather than corrupted.
    """
    available = max(80.0, float(width) - float(margin_l) - float(margin_r))
    size = max(MIN_CAPTION_SIZE, float(requested_size))
    while size >= MIN_CAPTION_SIZE:
        max_chars = max(8, int(math.floor(available / (size * AVERAGE_GLYPH_WIDTH))))
        lines = _wrap_words(text, max_chars)
        if len(lines) <= 2:
            return tuple(lines), size
        size = round(size - 1.0, 3)
    # There is no legal two-line layout at the minimum size. Preserve whole
    # words and balance the text into two lines as a final safe fallback.
    words = re.findall(r"\S+", str(text or ""))
    if not words:
        return ("",), MIN_CAPTION_SIZE
    midpoint = max(1, len(words) // 2)
    return (" ".join(words[:midpoint]), " ".join(words[midpoint:])), MIN_CAPTION_SIZE
