"""Hand-made-looking drawings for gags, painted on this computer.

The painter is DreamShaper 8 LCM (a Stable Diffusion 1.5 fine-tune distilled
to paint in four steps; CreativeML OpenRAIL-M licence, commercial use allowed)
running on the CPU with torch, which the app already ships. No account, no
API key and nothing is sent anywhere: the model (2.1 GB) is downloaded once
from Hugging Face at a pinned revision and checked against its SHA-256.

Every gag gets a style from STYLES, a mix of hand techniques (ink, marker,
pencil, watercolour, crayon, oil, engraving...), and is cut off its white
background into a sticker.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import threading
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageFilter

from core.storage import data_root

MODEL_REPO = "Lykon/dreamshaper-8-lcm"
MODEL_REVISION = "4645d8bc6a8e6b106d21606d63e8460cdad4f1a6"
CHECKPOINT = "DreamShaper8_LCM.safetensors"
# path: (sha256, bytes) at MODEL_REVISION.
MODEL_FILES: dict[str, tuple[str, int]] = {
    CHECKPOINT: ("a4f3e1526c5dc4fcbe342f5c410d83ae202c7a415fcefcbb92e0f93fcd0a87c3", 2133804992),
    "model_index.json": ("6cb265a1e507f1912e5c313aefc22b1b7de5d93c733d04f7ed332f2968cef830", 624),
    "scheduler/scheduler_config.json": ("046682b1586a024906e65fbe98cd9960fc7b4b2138381f3ee32a65429e37513b", 374),
    "text_encoder/config.json": ("10bddfdedc5621be3f4563750b70e0a29f464b8a2b2cc8f51e70e118934522cb", 560),
    "tokenizer/merges.txt": ("9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a", 524619),
    "tokenizer/special_tokens_map.json": ("6a757fc443f84ad1def3b893aac3858178f2a1513e3cf97b35ff131b5d88edd5", 586),
    "tokenizer/tokenizer_config.json": ("7eb36a76a02551ce8583dbee08d062e672997a5fe1db82ebc642d88c7e79cc96", 704),
    "tokenizer/vocab.json": ("e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349", 1059962),
    "unet/config.json": ("8dc20d0d5bfa8c1e2e43cca57e1dfe43b85bf099ec9a1aeabf83895901e1450a", 1778),
    "vae/config.json": ("c3b1cc063b68106756b455960b024493b4ff5a200e848b80bd492b0e7221ba09", 601),
    "feature_extractor/preprocessor_config.json": ("6536797f78e533c899ba0fbc64575d87be8c1c9204dd68396790dc6347534bed", 548),
    "safety_checker/config.json": ("a476c773d57fd2acc712cf1878eb1e23295331190d631e47e82771370cd3312f", 692),
}
MODEL_BYTES = sum(size for _, size in MODEL_FILES.values())
SIZE = 512


@dataclass(frozen=True)
class Style:
    label: str
    prompt: str            # "{subject}" is replaced by what to draw
    guidance: float = 1.8  # above 1 the negative prompt applies (at twice the time)
    steps: int = 5


# The approved look: rough, imperfect, silly hand drawings - pencil, crayon
# and marker - never glossy, never a photo of paper on a desk. "on white
# paper" made the model paint the sheet, the desk and the pencils, so the
# drawing floats on white and the negative prompt names those things.
NEGATIVE = ("photo, photograph, photo of paper, paper at an angle, paper sheet, notebook, desk, table, pencils, pens, markers, crayons, perspective, cast shadow, "
            "3d render, realistic, smooth shading, gradient, glossy, plush, sticker, vector art, clean perfect lines, "
            "anime, manga, chibi, kawaii, big shiny eyes, polished illustration, digital painting, frame, border, text, watermark")

STYLES: dict[str, Style] = {
    "pencil": Style("Lápiz", "rough graphite pencil sketch of {subject}, messy imperfect wobbly lines, unfinished doodle, amateur and funny, flat scan, isolated on white background"),
    "crayon": Style("Ceras", "{subject}, crude amateur wax crayon scribble, wobbly broken outlines, uneven flat colour with white gaps, awkward naive proportions, unfinished handmade doodle, isolated on white background"),
    "marker": Style("Rotulador", "felt-tip marker doodle of {subject}, thick wobbly black outlines, flat uneven colouring with gaps, clumsy amateur scribble, flat scan, isolated on white background"),
}


def model_dir() -> Path:
    return data_root() / "Models" / "gag-painter" / MODEL_REVISION[:12]


def _verified_marker() -> Path:
    return model_dir() / ".verified"


def model_ready() -> bool:
    folder = model_dir()
    if not _verified_marker().is_file():
        return False
    return all((folder / name).is_file() and (folder / name).stat().st_size == size for name, (_, size) in MODEL_FILES.items())


def can_paint() -> bool:
    """The model is here and the painting libraries are in this build."""
    import importlib.util
    return model_ready() and all(importlib.util.find_spec(name) for name in ("torch", "diffusers", "transformers"))


def _url(name: str) -> str:
    return f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{name}"


def download_model(progress: Callable[[int, int], None] | None = None, cancel: Callable[[], bool] | None = None,
                   *, attempts: int = 8, pause: float = 2.0) -> Path:
    """Fetch the painter once; every file is checked against its SHA-256.

    A dropped connection does not start the 2 GB again: what arrived is kept in a
    ``.part`` file and the download resumes from there (HTTP range), retrying by
    itself a few times; pressing Download again later also resumes. Only a file
    that arrives complete but different is thrown away."""
    import time
    import urllib.error
    from http.client import HTTPException
    folder = model_dir()
    done = 0
    for name, (expected_sha, expected_size) in MODEL_FILES.items():
        target = folder / name
        if target.is_file() and target.stat().st_size == expected_size and (_verified_marker().is_file() or _sha256(target) == expected_sha):
            done += expected_size
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        if partial.is_file() and partial.stat().st_size > expected_size:
            partial.unlink()
        failures = 0
        while True:
            # What already arrived counts: its bytes go into the checksum before the rest.
            digest = hashlib.sha256()
            received = 0
            if partial.is_file():
                with partial.open("rb") as kept:
                    for block in iter(lambda: kept.read(1 << 24), b""):
                        digest.update(block)
                        received += len(block)
            if progress:
                progress(done + received, MODEL_BYTES)
            if received < expected_size:
                headers = {"User-Agent": "Zucker-Editor", **({"Range": f"bytes={received}-"} if received else {})}
                try:
                    with urllib.request.urlopen(urllib.request.Request(_url(name), headers=headers), timeout=60) as response:
                        if received and getattr(response, "status", 200) != 206:
                            # The server sent the whole file again: start this file over.
                            digest, received = hashlib.sha256(), 0
                        with partial.open("ab" if received else "wb") as output:
                            while block := response.read(1 << 20):
                                if cancel and cancel():
                                    raise InterruptedError("Download cancelled")
                                received += len(block)
                                if received > expected_size:
                                    raise ValueError(f"{name} is larger than expected")
                                output.write(block)
                                digest.update(block)
                                if progress:
                                    progress(done + received, MODEL_BYTES)
                except InterruptedError:
                    raise
                except (urllib.error.URLError, HTTPException, TimeoutError, ConnectionError, OSError) as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 404, 410):
                        raise RuntimeError(f"{name} is not available at {_url(name)} ({exc.code})") from exc
                    failures += 1
                    if failures >= attempts:
                        raise RuntimeError(f"The connection kept dropping ({received / 1e6:.0f} MB of {expected_size / 1e6:.0f} MB of {name} "
                                           "arrived, and are kept). Press Download again to continue from there.") from exc
                    time.sleep(pause * min(failures, 5))
                    continue
            if received < expected_size:
                # The connection closed early without an error: carry on from where it stopped.
                failures += 1
                if failures >= attempts:
                    raise RuntimeError(f"The connection kept closing early ({received / 1e6:.0f} MB of {expected_size / 1e6:.0f} MB of {name} "
                                       "arrived, and are kept). Press Download again to continue from there.")
                time.sleep(pause * min(failures, 5))
                continue
            if digest.hexdigest() != expected_sha:
                partial.unlink(missing_ok=True)
                raise ValueError(f"{name} arrived complete but is not the expected file; it was discarded. Try the download again.")
            partial.replace(target)
            break
        done += expected_size
    _verified_marker().write_text(MODEL_REVISION, encoding="utf-8")
    if progress:
        progress(MODEL_BYTES, MODEL_BYTES)
    return folder


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1 << 22):
            digest.update(block)
    return digest.hexdigest()


def _physical_cores() -> int:
    # Hyper-threads slow the painter down (measured: 4 cores 24 s, 8 threads 28 s).
    if platform.system() == "Darwin":
        try:
            return max(1, int(subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True, timeout=5).stdout))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return max(1, (os.cpu_count() or 2) // 2)


_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _pipeline() -> Any:
    if not model_ready():
        raise RuntimeError("The drawing painter is not downloaded yet")
    import torch
    from diffusers import LCMScheduler, StableDiffusionPipeline
    torch.set_num_threads(_physical_cores())
    folder = model_dir()
    pipe = StableDiffusionPipeline.from_single_file(
        str(folder / CHECKPOINT), config=str(folder), local_files_only=True, torch_dtype=torch.float32,
        safety_checker=None, feature_extractor=None, requires_safety_checker=False)
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    pipe.set_progress_bar_config(disable=True)
    return pipe


def prompt_for(subject: str, style: str) -> str:
    return STYLES[style].prompt.format(subject=subject.strip().rstrip("."))


def paint(subject: str, style: str, *, seed: int = 0, size: int = SIZE,
          step: Callable[[int, int], None] | None = None, prompt: str | None = None) -> Image.Image:
    """One drawing of ``subject`` in ``style`` (a key of STYLES), with the
    person's own ``prompt`` if they wrote one."""
    import torch
    recipe = STYLES[style]
    with _LOCK:
        pipe = _pipeline()
        callback = None
        if step:
            def callback(_pipe, index, _timestep, kwargs):  # noqa: ANN001 - diffusers signature
                step(index + 1, recipe.steps)
                return kwargs
        result = pipe(prompt or prompt_for(subject, style), negative_prompt=NEGATIVE if recipe.guidance > 1 else None,
                      num_inference_steps=recipe.steps, guidance_scale=recipe.guidance, width=size, height=size,
                      generator=torch.Generator().manual_seed(int(seed)), callback_on_step_end=callback)
    return result.images[0]


def clean_background(image: Image.Image) -> bool:
    """The drawing floats on white: at least three sides of the picture are plain
    light paper colour (a bust may touch the bottom); a sheet lying on a desk,
    or a pencil beside it, fails. Calibrated on DreamShaper's own output."""
    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    sides = [rgb[:8].reshape(-1, 3), rgb[-8:].reshape(-1, 3), rgb[:, :8].reshape(-1, 3), rgb[:, -8:].reshape(-1, 3)]
    clean = [float(((side.min(axis=1) > 185) & ((side.max(axis=1) - side.min(axis=1)) < 40)).mean()) >= 0.85 for side in sides]
    light = float(np.median(np.concatenate(sides).min(axis=1))) >= 210
    return sum(clean) >= 3 and light


DARK_LIMIT = 0.45   # black share of the drawing's box: doodles 0-39 %, the painter's black blobs more


def mostly_black(sticker: Image.Image) -> bool:
    """A cut-out drawing that is mostly black fill: a blob, not a drawing."""
    rgba = np.asarray(sticker.convert("RGBA"))
    ink = (rgba[..., 3] > 200) & (rgba[..., :3].max(axis=2) < 50)
    return float(ink.mean()) > DARK_LIMIT


class UnusableDrawing(RuntimeError):
    """No try came out on a plain background (a sheet on a desk, a black fill)."""


def paint_clean(subject: str, style: str, *, seed: int = 0, tries: int = 3, step: Callable[[int, int], None] | None = None,
                prompt: str | None = None) -> Image.Image:
    """Paint until the drawing floats on white (the model sometimes paints the
    sheet on a desk) and is a drawing, not a black blob. If no try is clean the
    drawing is refused: cut out, it would show as a blob on the video."""
    for attempt in range(max(1, tries)):
        image = paint(subject, style, seed=seed + attempt * 101, step=step, prompt=prompt)
        if clean_background(image) and not mostly_black(cut_out(image)):
            return image
    raise UnusableDrawing(f"No clean drawing of {subject} in {tries} tries")


def cut_out(image: Image.Image, tolerance: int = 38) -> Image.Image:
    """The drawing without its background: the paper-coloured area touching
    the edges becomes transparent. A drawing that fills the frame keeps a
    rounded card shape instead."""
    from scipy import ndimage
    image = image.convert("RGB")
    image = image.crop((6, 6, image.width - 6, image.height - 6))  # the painter sometimes rules a border line
    rgb = np.asarray(image).astype(np.int16)
    edges = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    paper = np.median(edges, axis=0)
    near = np.abs(rgb - paper).max(axis=2) < tolerance
    labels, _ = ndimage.label(near)
    touching = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))) - {0}
    background = np.isin(labels, list(touching))
    if background.mean() < 0.15:
        from core.gag_stickers import rounded_card
        return rounded_card(image.convert("RGBA"))
    keep = ndimage.binary_opening(~background, iterations=1)
    keep = ndimage.binary_fill_holes(ndimage.binary_closing(keep, iterations=3))
    parts, count = ndimage.label(keep)
    if count:
        sizes = ndimage.sum(keep, parts, range(1, count + 1))
        keep = np.isin(parts, [index + 1 for index, area in enumerate(sizes) if area >= sizes.max() * 0.03])
    alpha = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    cut = image.convert("RGBA")
    cut.putalpha(alpha)
    return cut.crop(cut.getbbox() or (0, 0, *cut.size))


_DOWNLOAD: dict[str, Any] = {"status": "idle", "received": 0, "total": MODEL_BYTES, "error": ""}
_DOWNLOAD_LOCK = threading.Lock()


def download_status() -> dict[str, Any]:
    with _DOWNLOAD_LOCK:
        return {**_DOWNLOAD, "ready": model_ready()}


def start_download() -> dict[str, Any]:
    """Fetch the painter in the background (once); returns the status."""
    with _DOWNLOAD_LOCK:
        if _DOWNLOAD["status"] == "running":
            return {**_DOWNLOAD, "ready": False}
        _DOWNLOAD.update(status="running", received=0, error="")

    def progress(received: int, total: int) -> None:
        with _DOWNLOAD_LOCK:
            _DOWNLOAD.update(received=received, total=total)

    def run() -> None:
        try:
            download_model(progress)
            with _DOWNLOAD_LOCK:
                _DOWNLOAD.update(status="done", received=MODEL_BYTES)
        except Exception as exc:  # shown to the person; they can try again
            with _DOWNLOAD_LOCK:
                _DOWNLOAD.update(status="failed", error=str(exc))

    threading.Thread(target=run, daemon=True, name="zucker-painter-download").start()
    return download_status()
