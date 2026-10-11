"""Local scene tagging for filler footage: time of day, place, rain, fire, shot type.

A quantized CLIP ViT-B/32 image encoder (OpenAI, MIT licence) runs on CPU via
onnxruntime. Text prompts are embedded once at development time by
``tools/build_clip_labels.py`` into ``assets/models/clip/labels.json``; the app
ships only the image encoder and those vectors, never the text encoder.

Rain and fire are scored against a contrast set of ordinary scenes instead of
"no rain"/"no fire". Plain binary prompts read dark bars with warm lamps as
"fire" and "rain" in real concert footage; the contrast set does not.

Detections are proposals. The editor only uses them through the conservative
compatibility rules in ``core.fillers`` and the user's confirmed overrides.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)

SCENE_TAGS_VERSION = 1
INPUT_SIZE = 224
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
_LOGIT_SCALE = 100.0

ORDINARY_SCENES = [
    "a concert in a dark bar with warm lamps",
    "a band playing on a stage with stage lights",
    "an indoor room lit by lamps",
    "a sunny day outdoors",
    "a cloudy day outdoors",
    "a city street at night with street lights",
    "people in a living room",
]

# Attribute -> class -> prompt ensemble. "contrast" attributes score their
# single positive class against ORDINARY_SCENES prompt by prompt.
PROMPTS: dict[str, dict[str, list[str]]] = {
    "time": {
        "day": ["a photo taken outdoors in daylight", "a bright sunny day"],
        "sunset": ["a photo taken at sunset or dusk", "golden hour light at dusk"],
        "night": ["a photo taken at night or in a dark venue", "a dark concert at night"],
    },
    "place": {
        "indoor": ["a photo taken indoors", "inside a room or a bar"],
        "outdoor": ["a photo taken outdoors", "outside in the open air"],
    },
    "rain": {
        "yes": ["rain falling outdoors, wet ground and umbrellas", "a rainy street with raindrops and puddles"],
        "no": ORDINARY_SCENES,
    },
    "fire": {
        "yes": ["a campfire with visible orange flames", "a bonfire burning outdoors", "a fireplace with burning logs"],
        "no": ORDINARY_SCENES,
    },
    "shot": {
        "performance": ["musicians playing instruments on a stage", "a band performing a concert"],
        "face": ["a close-up of a person's face", "a portrait of a smiling person"],
        "interview": ["a person being interviewed, talking to the camera", "a person speaking to the camera"],
        "audience": ["an audience or crowd of people", "people watching a concert"],
        "ambient": ["a landscape or scenery", "an empty room or venue with no people", "a city street"],
        "detail": ["a close-up of hands or objects", "a close-up detail of a musical instrument"],
    },
}
CONTRAST_ATTRIBUTES = ("rain", "fire")
CLASS_ATTRIBUTES = ("time", "place", "shot")


def _bundle_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def model_dir() -> Path:
    return _bundle_root() / "assets" / "models" / "clip"


def prompts_fingerprint() -> str:
    from core.stages.base import stable_fingerprint
    return stable_fingerprint(PROMPTS)[:16]


@lru_cache(maxsize=1)
def _labels() -> dict[str, Any] | None:
    path = model_dir() / "labels.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("prompts_fingerprint") != prompts_fingerprint():
        LOGGER.warning("CLIP labels at %s do not match the current prompts; scene tagging disabled", path)
        return None
    vectors: dict[str, dict[str, np.ndarray]] = {}
    for attribute, classes in payload["embeddings"].items():
        vectors[attribute] = {name: np.asarray(rows, np.float32) for name, rows in classes.items()}
    return {"vectors": vectors, "model": payload.get("model")}


_SESSION_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _session() -> Any | None:
    path = model_dir() / "vision_model_quantized.onnx"
    if not path.is_file():
        return None
    try:
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    except Exception as exc:  # pragma: no cover - depends on the packaged runtime
        LOGGER.warning("CLIP image encoder unavailable at %s: %s", path, exc)
        return None


def available() -> bool:
    """Whether full scene tagging can run (model and matching labels present)."""
    with _SESSION_LOCK:
        return _labels() is not None and _session() is not None


def preprocess(frame_rgb: np.ndarray) -> np.ndarray:
    """CLIP preprocessing: bicubic short-side resize, centre crop, normalise."""
    import cv2
    height, width = frame_rgb.shape[:2]
    scale = INPUT_SIZE / min(height, width)
    resized = cv2.resize(frame_rgb, (max(INPUT_SIZE, round(width * scale)), max(INPUT_SIZE, round(height * scale))),
                         interpolation=cv2.INTER_CUBIC)
    height, width = resized.shape[:2]
    top, left = (height - INPUT_SIZE) // 2, (width - INPUT_SIZE) // 2
    crop = resized[top:top + INPUT_SIZE, left:left + INPUT_SIZE].astype(np.float32) / 255.0
    return ((crop - _MEAN) / _STD).transpose(2, 0, 1)


def _embed(frames_rgb: list[np.ndarray]) -> np.ndarray:
    session = _session()
    batch = np.stack([preprocess(frame) for frame in frames_rgb])
    with _SESSION_LOCK:
        output = session.run(None, {session.get_inputs()[0].name: batch})[0]
    return output / np.linalg.norm(output, axis=1, keepdims=True)


def _softmax(logits: np.ndarray, axis: int = -1) -> np.ndarray:
    shifted = np.exp(logits - logits.max(axis=axis, keepdims=True))
    return shifted / shifted.sum(axis=axis, keepdims=True)


def classify_embeddings(embeddings: np.ndarray, vectors: dict[str, dict[str, np.ndarray]]) -> list[dict[str, Any]]:
    """Per-frame probabilities for every attribute."""
    results: list[dict[str, Any]] = [{} for _ in range(len(embeddings))]
    for attribute in CLASS_ATTRIBUTES:
        names = list(vectors[attribute])
        centroids = np.stack([vectors[attribute][name].mean(axis=0) for name in names])
        centroids /= np.linalg.norm(centroids, axis=1, keepdims=True)
        probabilities = _softmax(_LOGIT_SCALE * embeddings @ centroids.T)
        for row, result in zip(probabilities, results):
            result[attribute] = {name: round(float(value), 4) for name, value in zip(names, row)}
    for attribute in CONTRAST_ATTRIBUTES:
        positive, negative = vectors[attribute]["yes"], vectors[attribute]["no"]
        probabilities = _softmax(_LOGIT_SCALE * embeddings @ np.concatenate([positive, negative]).T)
        for row, result in zip(probabilities, results):
            result[attribute] = round(float(row[:len(positive)].sum()), 4)
    return results


def frame_quality(frame_rgb: np.ndarray) -> dict[str, float]:
    import cv2
    gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
    return {
        "brightness": round(float(gray.mean()), 2),
        "sharpness": round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
    }


def analyze_frames(frames_rgb: list[np.ndarray], batch_size: int = 16) -> list[dict[str, Any]]:
    """Tag frames. Without the model only brightness, sharpness and a coarse
    day/night guess are returned, flagged ``model=False``."""
    qualities = [frame_quality(frame) for frame in frames_rgb]
    labels = _labels()
    if not frames_rgb or labels is None or _session() is None:
        return [{**quality, "model": False,
                 "time": {"night": 1.0} if quality["brightness"] < 45 else {}} for quality in qualities]
    tags: list[dict[str, Any]] = []
    for offset in range(0, len(frames_rgb), batch_size):
        tags.extend(classify_embeddings(_embed(frames_rgb[offset:offset + batch_size]), labels["vectors"]))
    return [{**quality, **tag, "model": True} for quality, tag in zip(qualities, tags)]
