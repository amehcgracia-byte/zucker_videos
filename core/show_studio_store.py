"""Show Studio's working state, kept in the project instead of the browser.

<project>/show_studio/
  composition.json     the editor's recipe (tracks, events, keys) and its revision
  materials.json       the person's own backgrounds and masks: their settings; the
                       picture itself is a project library file
  sources.json         which library files are the main video, the mix, the voice...
  analysis/<name>.json tracking prepared for the source (finger, face, hands, captions)

Pictures, videos and sounds live in the project library (library/assets, named by
their SHA-256), so a reference is an ``{"asset": <sha256>}`` that must exist there.
A save names the revision it started from: a stale one is refused instead of
overwriting another window's work. Nothing here deletes what the browser kept.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from core.project import atomic_write_json
from core.project_library import ProjectLibrary, _locked

RECIPE_VERSION = 2
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 5000
MATERIAL_KINDS = ("background", "mask")
# Tracking prepared for the source, and "proposal": the recipe the editor first proposed (its Reset).
ANALYSIS_NAMES = ("finger-track", "face-track", "automatic-hands", "captions", "cutout-sample", "proposal")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
ASSET_ID = re.compile(r"^[0-9a-f]{64}$")
SECTION = re.compile(r"^[a-z][a-z0-9-]{0,29}$")
DEFAULT_SECTION = "workshop"
BUILTIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,80}\.(png|jpe?g|webp)$")


class StaleRevision(ValueError):
    """Another window saved the composition after this one loaded it."""


def _size_checked(payload: Any, what: str) -> None:
    if len(json.dumps(payload, allow_nan=False).encode()) > MAX_JSON_BYTES:
        raise ValueError(f"The {what} is too large to keep")


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{what} must be a number of seconds")
    return float(value)


class StudioStore:
    """``section``: which part of the source this edit is ("workshop", the first one,
    kept at the top of show_studio/; others under show_studio/sections/<name>/).
    Own backgrounds and masks belong to the whole project."""

    def __init__(self, project_folder: Path, section: str = DEFAULT_SECTION):
        if not SECTION.match(str(section)):
            raise ValueError("Unknown source section")
        self.library = ProjectLibrary(Path(project_folder))
        self.base = self.library.project / "show_studio"
        self.root = self.base if section == DEFAULT_SECTION else self.base / "sections" / section
        if self.base.is_symlink() or self.root.is_symlink():
            raise ValueError("Show Studio's data must stay inside the project.")

    # ---- reading -------------------------------------------------------------

    def _load(self, name: str, default: Any) -> Any:
        path = self.root / name
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def state(self) -> dict[str, Any]:
        composition = self._load("composition.json", {"revision": 0, "recipe": None})
        folder = self.root / "analysis"
        return {"composition": composition.get("recipe"), "revision": int(composition.get("revision") or 0),
                "materials": self._shared("materials.json", {"materials": []})["materials"],
                "sources": self._load("sources.json", {"sources": {}})["sources"],
                "analysis": sorted(path.stem for path in folder.glob("*.json")) if folder.is_dir() else []}

    def _shared(self, name: str, default: Any) -> Any:
        path = self.base / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default

    def asset_path(self, asset_id: str) -> Path:
        """The library file behind an asset reference (checked against the manifest)."""
        if not ASSET_ID.match(str(asset_id)):
            raise ValueError("Not a library asset")
        record = self.library._read()["assets"].get(asset_id)
        if not record:
            raise ValueError("That file is not in this project's library")
        path = self.library._path(record["path"])
        if not path.is_file():
            raise ValueError("That library file is missing from the project")
        return path

    def load_analysis(self, name: str) -> Any:
        if name not in ANALYSIS_NAMES:
            raise ValueError("Unknown tracking data")
        return self._load(f"analysis/{name}.json", None)

    # ---- checking ------------------------------------------------------------

    def _assets_known(self, payload: Any) -> None:
        known = self.library._read()["assets"]

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                if "asset" in value and value["asset"] is not None:
                    if not ASSET_ID.match(str(value["asset"])) or value["asset"] not in known:
                        raise ValueError("A picture or sound is not in this project's library")
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        walk(payload)

    def _check_recipe(self, recipe: Any) -> None:
        if not isinstance(recipe, dict) or recipe.get("version") != RECIPE_VERSION or not isinstance(recipe.get("events"), list):
            raise ValueError("Not a Show Studio recipe (version 2 with events)")
        if len(recipe["events"]) > MAX_EVENTS:
            raise ValueError("Too many effects in one recipe")
        seen = set()
        for event in recipe["events"]:
            if not isinstance(event, dict) or not isinstance(event.get("id"), str) or not 0 < len(event["id"]) <= 120:
                raise ValueError("Every effect needs an id")
            if not isinstance(event.get("type"), str) or not 0 < len(event["type"]) <= 40:
                raise ValueError(f"Effect {event['id']} has no type")
            if event["id"] in seen:
                raise ValueError(f"Two effects share the id {event['id']}")
            seen.add(event["id"])
            start, end = _number(event.get("start"), "A start"), _number(event.get("end"), "An end")
            if end < start - 1e-6 or start < -1e-6:
                raise ValueError(f"Effect {event['id']} ends before it starts")
        _size_checked(recipe, "recipe")
        self._assets_known(recipe)

    # ---- writing -------------------------------------------------------------

    def save_composition(self, recipe: dict[str, Any], *, revision: int) -> int:
        """Keep the recipe; ``revision`` is the one the editor loaded. Returns the new revision."""
        self._check_recipe(recipe)
        with _locked(self.library.root):
            current = int(self._load("composition.json", {"revision": 0}).get("revision") or 0)
            if int(revision) != current:
                raise StaleRevision("The composition was changed in another window; reload it before saving")
            atomic_write_json(self.root / "composition.json", {"revision": current + 1, "recipe": recipe})
            return current + 1

    def save_materials(self, materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(materials, list) or len(materials) > 500:
            raise ValueError("Materials are a list")
        for item in materials:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError("Every material needs an id")
            if item.get("kind") not in MATERIAL_KINDS:
                raise ValueError("A material is a background or a mask")
            # Its picture: a library file, or one of the editor's public samples (an edited sample mask).
            if not item.get("asset") and not (isinstance(item.get("builtin"), str) and BUILTIN.match(item["builtin"])):
                raise ValueError("A material needs its picture in the library")
        _size_checked(materials, "list of materials")
        self._assets_known(materials)
        with _locked(self.library.root):
            atomic_write_json(self.base / "materials.json", {"materials": materials})
        return materials

    def save_sources(self, sources: dict[str, str]) -> dict[str, str]:
        if not isinstance(sources, dict) or not all(isinstance(key, str) and NAME.match(key) for key in sources):
            raise ValueError("Sources are named library files")
        self._assets_known([{"asset": value} for value in sources.values()])
        with _locked(self.library.root):
            merged = {**self._load("sources.json", {"sources": {}})["sources"], **sources}
            atomic_write_json(self.root / "sources.json", {"sources": merged})
        return merged

    def save_analysis(self, name: str, data: Any) -> None:
        if name not in ANALYSIS_NAMES:
            raise ValueError("Unknown tracking data")
        _size_checked(data, "tracking data")
        with _locked(self.library.root):
            atomic_write_json(self.root / "analysis" / f"{name}.json", data)       # kept as it came (a list or an object)
