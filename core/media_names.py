"""Names people can recognise, for media files and for what the timeline and the library show.

Files the app makes used codes (``clip-1a53b63ff342bb38f8d2.mp4``, ``local-<64 hex>.mp4``): the
library and the timeline then showed numbers, or "Video clip" on every row. New files start with
something recognisable (the video's title, the original file's name) and keep a short code at the
end so two of them never collide; what already exists is shown with the best name known.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

CODE = re.compile(r"(?:^|[-_ ])(?:[0-9a-f]{8,}|\d{6,})(?=$|[-_ .])", re.I)


def slug(text: str, limit: int = 48) -> str:
    """A file-name-safe start from a title: letters, digits and dashes."""
    ascii_text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")
    return cleaned[:limit].rstrip("-") or "media"


MEDIA_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg",
                  ".opus", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".json"}
GENERIC = {"clip", "local", "image", "out", "layer", "media", "video", "audio", "preview", "import", "source", "file"}


def _stem(name: str) -> str:
    """The name without a media file's extension (a dot inside a title is kept)."""
    text = str(name or "")
    path = Path(text)
    return text[: -len(path.suffix)] if path.suffix.lower() in MEDIA_SUFFIXES else text


def coded(name: str) -> bool:
    """True when a name is mostly a code (a long hex or digit run, or a short random token)."""
    stem = _stem(name)
    if not stem:
        return True
    if CODE.search(stem):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9]{4,6}", stem)) and bool(re.search(r"\d", stem) or re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]?[A-Z]", stem))


def readable(name: str) -> str:
    """A name with its codes taken out ("cutout-5ea8…" → "cutout"), or "" if nothing is left."""
    stem = CODE.sub("", _stem(name))
    stem = re.sub(r"[-_]+", " ", stem).strip()
    return "" if not stem or coded(stem) or stem.lower() in GENERIC else stem


def sidecar_title(path: str | Path) -> str:
    """The title kept beside a downloaded file (``<file>.json``), if any."""
    try:
        return str(json.loads(Path(str(path) + ".json").read_text(encoding="utf-8")).get("title") or "").strip()
    except (OSError, ValueError, TypeError, AttributeError):
        return ""


def clip_name(params: dict[str, Any], *, kind_label: str = "Clip") -> str:
    """What a clip (or any timeline media) is called: its name, the downloaded video's title,
    the original file's name, then the words it sits on; never a code."""
    name = str(params.get("name") or "").strip()
    if name and not coded(name):
        return name
    for key in ("clip", "source", "path", "picture"):
        if params.get(key):
            title = sidecar_title(params[key])
            if title:
                return title
    if name and readable(name):
        return readable(name)
    for key in ("title", "subject", "query", "selected_text", "text"):
        value = str(params.get(key) or "").strip()
        if value:
            return f"{kind_label} · “{value[:40]}”" if key == "selected_text" else value[:60]
    for key in ("clip", "source", "path"):
        if params.get(key) and readable(Path(str(params[key])).name):
            return readable(Path(str(params[key])).name)
    return kind_label


def unique_file(folder: Path, title: str, code: str, suffix: str) -> Path:
    """``<title>-<code><suffix>`` in ``folder``: recognisable first, the code keeps it unique."""
    return folder / f"{slug(title)}-{code}{suffix}"


def find_coded(folder: Path, code: str, suffix: str) -> Path | None:
    """A file already made for ``code`` (old ``clip-<code>`` names included)."""
    if not folder.is_dir():
        return None
    return next((path for path in sorted(folder.glob(f"*-{code}{suffix}")) if path.is_file()), None)
