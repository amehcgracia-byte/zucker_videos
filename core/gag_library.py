"""The gag library: your own pictures, found by what you say.

Drop images (PNG with transparency works best, JPG/WebP too) in the "Gag
library" folder of the app's storage, optionally in subfolders that name
them ("Characters/Val Kilmer.png", "Stickers/coffee.png"). A query such as
"a spider" or "Val Kilmer" is matched with CLIP (local ONNX, the image
encoder already used for scene tagging plus its text encoder) and with the
file and folder names; the best picture above a confidence floor wins.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from core import scene_tags
from core.storage import data_root

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SUBFOLDERS = ("Characters", "Stickers", "Photos")
# CLIP cosine similarity between a picture and a short description is low in
# absolute terms (~0.2-0.35); a name match counts as a strong hint.
MIN_SCORE = 0.24
NAME_BONUS = 0.08
_LOCK = threading.Lock()


def library_dir() -> Path:
    root = data_root() / "Gag library"
    for name in SUBFOLDERS:
        (root / name).mkdir(parents=True, exist_ok=True)
    readme = root / "README.txt"
    if not readme.exists():
        readme.write_text(
            "Pictures for Show gags. Name files and folders after what they show\n"
            "(Characters/Val Kilmer.png, Stickers/coffee cup.png): the names help the search.\n"
            "PNG with a transparent background looks best on screen.\n", encoding="utf-8")
    return root


def _words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return {word for word in re.split(r"[^a-z0-9]+", text) if len(word) > 2}


@lru_cache(maxsize=1)
def _text_session() -> tuple[Any, Any] | None:
    folder = scene_tags.model_dir()
    model, tokenizer_file = folder / "text_model_quantized.onnx", folder / "tokenizer.json"
    if not model.is_file() or not tokenizer_file.is_file():
        return None
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        return ort.InferenceSession(str(model), sess_options=options, providers=["CPUExecutionProvider"]), Tokenizer.from_file(str(tokenizer_file))
    except Exception:  # pragma: no cover - depends on the packaged runtime
        return None


def embed_texts(texts: list[str]) -> np.ndarray | None:
    loaded = _text_session()
    if loaded is None or not texts:
        return None
    session, tokenizer = loaded
    encoded = tokenizer.encode_batch([f"a picture of {text}" for text in texts])
    length = max(len(item.ids) for item in encoded)
    # CLIP pools at the end-of-text token (the largest id): zero padding is inert.
    ids = np.array([item.ids[:77] + [0] * (min(length, 77) - len(item.ids[:77])) for item in encoded], np.int64)
    with _LOCK:
        vectors = session.run(None, {"input_ids": ids})[0]
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def _index_file() -> Path:
    return data_root() / "Cache" / "gag_library_index.json"


def _load_rgb(path: Path) -> np.ndarray | None:
    from PIL import Image
    try:
        with Image.open(path) as image:
            image = image.convert("RGBA")
            # Transparent stickers are judged on white, as they are seen.
            canvas = Image.new("RGBA", image.size, (255, 255, 255, 255))
            canvas.alpha_composite(image)
            return np.asarray(canvas.convert("RGB"))
    except Exception:
        return None


def index_library(root: Path | None = None) -> list[dict[str, Any]]:
    """Pictures with their CLIP vectors, recomputed only for changed files."""
    root = root or library_dir()
    files = sorted(path for path in root.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file())
    try:
        cached = {row["path"]: row for row in json.loads(_index_file().read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        cached = {}
    entries: list[dict[str, Any]] = []
    pending: list[tuple[Path, np.ndarray, dict[str, Any]]] = []
    for path in files:
        stat = path.stat()
        key = {"path": str(path), "size": stat.st_size, "mtime": stat.st_mtime_ns}
        row = cached.get(str(path))
        if row and row.get("size") == stat.st_size and row.get("mtime") == stat.st_mtime_ns and row.get("vector"):
            entries.append(row)
            continue
        rgb = _load_rgb(path)
        if rgb is not None:
            pending.append((path, rgb, key))
    if pending and scene_tags._session() is not None:
        for offset in range(0, len(pending), 16):
            batch = pending[offset:offset + 16]
            vectors = scene_tags._embed([rgb for _, rgb, _ in batch])
            for (_, _, key), vector in zip(batch, vectors):
                entries.append({**key, "vector": [round(float(v), 5) for v in vector]})
    elif pending:
        entries.extend({**key, "vector": None} for _, _, key in pending)
    try:
        _index_file().parent.mkdir(parents=True, exist_ok=True)
        _index_file().write_text(json.dumps(entries), encoding="utf-8")
    except OSError:
        pass
    root_text = str(root)
    for entry in entries:
        relative = entry["path"][len(root_text):] if entry["path"].startswith(root_text) else Path(entry["path"]).name
        entry["words"] = sorted(_words(relative.rsplit(".", 1)[0]))
        entry["character"] = "/characters/" in relative.lower().replace("\\", "/") + "/"
    return entries


def search(query: str, entries: list[dict[str, Any]], *, prefer_characters: bool = False,
           exclude: set[str] | None = None) -> dict[str, Any] | None:
    """The best picture for ``query``, or None when nothing fits well enough."""
    candidates = [entry for entry in entries if entry["path"] not in (exclude or set())]
    if not candidates or not str(query).strip():
        return None
    query_words = _words(query)
    vectors = embed_texts([query])
    best, best_score = None, -1.0
    for entry in candidates:
        score = 0.0
        if vectors is not None and entry.get("vector"):
            score = float(np.dot(vectors[0], np.asarray(entry["vector"], np.float32)))
        overlap = query_words & set(entry["words"])
        if overlap:
            score += NAME_BONUS + 0.04 * (len(overlap) - 1)
        if prefer_characters and entry.get("character"):
            score += 0.02
        if score > best_score:
            best, best_score = entry, score
    if best is None or best_score < MIN_SCORE:
        return None
    return {**best, "score": round(best_score, 4)}


def named_picture(name: str, root: Path | None = None) -> Path | None:
    """The library picture the director named, exactly: its file name, the name without the
    code at its end, or that code alone. None when no picture is called that (no look-alike)."""
    wanted = " ".join(str(name or "").lower().split())
    if not wanted:
        return None
    root = root or library_dir()
    if not root.is_dir():
        return None
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
            continue
        stem = " ".join(path.stem.lower().split())
        head, _, code = stem.rpartition(" ")
        coded = re.fullmatch(r"[0-9a-f]{6}", code) is not None        # the library's own code at the end of a name
        if wanted == stem or (coded and wanted in (code, head)):
            return path
    return None


def reveal() -> Path:
    """Open the gag library folder in Finder / Explorer."""
    import os
    import subprocess
    import sys
    root = library_dir()
    if sys.platform == "darwin":
        subprocess.run(["open", str(root)], check=False, timeout=20)
    elif sys.platform == "win32":
        os.startfile(str(root))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(root)], check=False, timeout=20)
    return root
