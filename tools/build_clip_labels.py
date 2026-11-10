"""Embed core.scene_tags.PROMPTS with the CLIP text encoder into labels.json.

Run after changing any prompt:
    tools/fetch_clip_model.sh --text
    .venv/bin/python tools/build_clip_labels.py
The app ships only the resulting vectors, never the text encoder.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.scene_tags import PROMPTS, model_dir, prompts_fingerprint  # noqa: E402


def main() -> None:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    text_dir = ROOT / "tools" / "clip_text"
    session = ort.InferenceSession(str(text_dir / "text_model_quantized.onnx"), providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(text_dir / "tokenizer.json"))

    def embed(prompts: list[str]) -> list[list[float]]:
        encoded = tokenizer.encode_batch(prompts)
        length = max(len(item.ids) for item in encoded)
        # CLIP pools at the end-of-text token (the largest id), so zero padding is inert.
        ids = np.array([item.ids + [0] * (length - len(item.ids)) for item in encoded], np.int64)
        vectors = session.run(None, {"input_ids": ids})[0]
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        return [[round(float(value), 6) for value in row] for row in vectors]

    payload = {
        "model": "openai/clip-vit-base-patch32 (Xenova ONNX, quantized), MIT licence",
        "prompts_fingerprint": prompts_fingerprint(),
        "prompts": PROMPTS,
        "embeddings": {attribute: {name: embed(prompts) for name, prompts in classes.items()}
                       for attribute, classes in PROMPTS.items()},
    }
    target = model_dir() / "labels.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"Wrote {target} ({target.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
