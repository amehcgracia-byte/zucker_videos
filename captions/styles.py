from __future__ import annotations

import json
from pathlib import Path

from .model import Style

CAPTIONS_VERSION = "captions-v3"
_PRESETS = Path(__file__).with_name("presets.json")


def _catalog() -> dict[str, dict[str, object]]:
    return json.loads(_PRESETS.read_text(encoding="utf-8"))


def list_styles() -> list[Style]:
    return [get_style(name) for name in _catalog()]


def get_style(name: str) -> Style:
    catalog = _catalog()
    if name not in catalog:
        raise KeyError(f"unknown caption style: {name}")
    return Style(name=name, **catalog[name])
