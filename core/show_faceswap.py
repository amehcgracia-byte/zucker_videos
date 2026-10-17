"""Put my face on a clip: only the face changes, on the person's own Mac.

The actor's body, clothes, light and movement stay; their face takes the identity of
the person's photo (InsightFace inswapper_128, the model FaceFusion and roop use, with
SCRFD to find faces and ArcFace to say who they are). Everything runs locally with
onnxruntime; nothing is uploaded. The three models (733 MB, FaceFusion's public copies
on Hugging Face) are downloaded once, on request, and checked against their published
CRC32. inswapper_128 is licensed for non-commercial research by InsightFace.

The actor is the person seen most in the clip (largest faces, most frames), followed by
who they are rather than where they are; a frame where they cannot be seen stays as it is.
"""
from __future__ import annotations

import mmap
import ssl
import subprocess
import threading
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Callable

import numpy as np

REPO = "https://huggingface.co/facefusion/models-3.0.0/resolve/main/"
FILES = {"scrfd_2.5g.onnx": ("d0266d7d", 3_295_067),
         "arcface_w600k_r50.onnx": ("1f5fefb8", 174_388_474),
         "inswapper_128.onnx": ("7057c6ea", 555_303_150)}
SIZE_MB = round(sum(size for _, size in FILES.values()) / 1_000_000)
ARCFACE_DST = np.array([[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
                        [41.5493, 92.3655], [70.7299, 92.2041]], np.float32)
SAME_PERSON = 0.25                      # ArcFace likeness from which a face is the same person
_lock = threading.Lock()


def models_dir() -> Path:
    from core.storage import data_root
    return data_root() / "Models" / "faceswap"


def missing() -> list[str]:
    """Models not downloaded (and checked) yet."""
    folder = models_dir()
    return [name for name, (_, size) in FILES.items()
            if not (folder / name).is_file() or (folder / name).stat().st_size != size or not (folder / f"{name}.ok").is_file()]


def _crc32(path: Path) -> str:
    value = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 24):
            value = zlib.crc32(chunk, value)
    return format(value & 0xFFFFFFFF, "08x")


def download(progress: Callable[[float], None] | None = None) -> None:
    """The missing models, each checked against its published CRC32 before it is kept."""
    import certifi
    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    total = sum(FILES[name][1] for name in missing()) or 1
    done = 0
    for name in missing():
        crc, size = FILES[name]
        part = folder / f"{name}.part"
        request = urllib.request.Request(REPO + name, headers={"User-Agent": "ZuckerEditor (face swap models)"})
        with urllib.request.urlopen(request, context=context, timeout=60) as response, part.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(min(1.0, done / total))
        if part.stat().st_size != size or _crc32(part) != crc:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{name} failed verification; try the download again")
        part.replace(folder / name)
        (folder / f"{name}.ok").write_text(crc)


# ---- The swapper's identity matrix: the last initializer of inswapper_128.onnx --------------
# Read straight from the ONNX protobuf, so the app needs no extra package for it.

def _varint(buf, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, pos
        shift += 7


def _fields(buf, start: int, end: int):
    """(field number, wire type, value or (start, end)) of one protobuf message."""
    pos = start
    while pos < end:
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
            yield number, wire, value
        elif wire == 1:
            yield number, wire, (pos, pos + 8)
            pos += 8
        elif wire == 2:
            length, pos = _varint(buf, pos)
            yield number, wire, (pos, pos + length)
            pos += length
        elif wire == 5:
            yield number, wire, (pos, pos + 4)
            pos += 4
        else:
            raise ValueError(f"Unexpected protobuf wire type {wire}")


def last_initializer(path: Path) -> np.ndarray:
    """ModelProto.graph (7) -> GraphProto.initializer (5), the last one: dims (1) and raw_data (9)."""
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        graph = next(span for number, wire, span in _fields(buf, 0, len(buf)) if number == 7 and wire == 2)
        tensor = None
        for number, wire, span in _fields(buf, *graph):
            if number == 5 and wire == 2:
                tensor = span
        if tensor is None:
            raise ValueError("The swapper model has no initializer")
        dims, raw = [], None
        for number, wire, value in _fields(buf, *tensor):
            if number == 1 and wire == 0:
                dims.append(value)
            elif number == 1 and wire == 2:
                pos, end = value
                while pos < end:
                    dim, pos = _varint(buf, pos)
                    dims.append(dim)
            elif number == 9 and wire == 2:
                raw = bytes(buf[value[0]:value[1]])
        if raw is None:
            raise ValueError("The swapper's identity matrix is not stored as raw data")
        return np.frombuffer(raw, dtype="<f4").reshape(dims).copy()


# ---- Faces ----------------------------------------------------------------------------------

def _session(name: str, coreml: bool = False):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.log_severity_level = 3
    providers: list[Any] = ["CPUExecutionProvider"]
    if coreml and "CoreMLExecutionProvider" in ort.get_available_providers():
        cache = models_dir() / "coreml-cache"
        cache.mkdir(parents=True, exist_ok=True)
        providers = [("CoreMLExecutionProvider", {"ModelCacheDirectory": str(cache)}), "CPUExecutionProvider"]
    try:
        return ort.InferenceSession(str(models_dir() / name), sess_options=options, providers=providers)
    except Exception:                    # the Mac's own accelerator refused the model: the CPU does it
        if not coreml:
            raise
        return ort.InferenceSession(str(models_dir() / name), sess_options=options, providers=["CPUExecutionProvider"])


class Detector:
    """SCRFD 2.5g: faces with their 5 landmarks (eyes, nose, mouth corners)."""

    def __init__(self, size: int = 640, threshold: float = 0.35):
        self.net, self.size, self.threshold = _session("scrfd_2.5g.onnx"), size, threshold
        self.input = self.net.get_inputs()[0].name

    def __call__(self, frame: np.ndarray) -> list[dict[str, Any]]:
        import cv2
        h, w = frame.shape[:2]
        scale = self.size / max(h, w)
        resized = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))))
        canvas = np.zeros((self.size, self.size, 3), np.uint8)
        canvas[:resized.shape[0], :resized.shape[1]] = resized
        blob = cv2.dnn.blobFromImage(canvas, 1.0 / 128, (self.size, self.size), (127.5, 127.5, 127.5), swapRB=True)
        outs = self.net.run(None, {self.input: blob})
        boxes, scores, points = [], [], []
        for index, stride in enumerate((8, 16, 32)):
            score = outs[index].reshape(-1)
            keep = score >= self.threshold
            grid = self.size // stride
            ys, xs = np.mgrid[:grid, :grid]
            centres = np.repeat(np.stack([xs, ys], -1).reshape(-1, 2) * stride, 2, axis=0).astype(np.float32)[keep]
            distance = outs[index + 3].reshape(-1, 4)[keep] * stride
            boxes.append(np.concatenate([centres - distance[:, :2], centres + distance[:, 2:]], 1))
            scores.append(score[keep])
            points.append(outs[index + 6].reshape(-1, 5, 2)[keep] * stride + centres[:, None, :])
        boxes, scores, points = np.concatenate(boxes), np.concatenate(scores), np.concatenate(points)
        rects = [[float(x0), float(y0), float(x1 - x0), float(y1 - y0)] for x0, y0, x1, y1 in boxes]
        order = cv2.dnn.NMSBoxes(rects, scores.tolist(), self.threshold, 0.4) if rects else []
        return [{"box": boxes[i] / scale, "score": float(scores[i]), "kps": points[i] / scale} for i in np.array(order).reshape(-1)]


def _align(frame: np.ndarray, kps: np.ndarray, size: int) -> tuple[np.ndarray, np.ndarray]:
    import cv2
    matrix, _ = cv2.estimateAffinePartial2D(kps, ARCFACE_DST * (size / 112.0), method=cv2.LMEDS)
    return cv2.warpAffine(frame, matrix, (size, size), borderValue=0.0), matrix


class Swapper:
    def __init__(self):
        self.arcface = _session("arcface_w600k_r50.onnx")
        self.inswapper = _session("inswapper_128.onnx", coreml=True)
        cached = models_dir() / "inswapper_emap.npy"
        if not cached.is_file():
            np.save(cached, last_initializer(models_dir() / "inswapper_128.onnx"))
        self.emap = np.load(cached)

    def embedding(self, frame: np.ndarray, face: dict[str, Any]) -> np.ndarray:
        """Who this face is (ArcFace, unit length)."""
        import cv2
        crop, _ = _align(frame, face["kps"], 112)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        vector = self.arcface.run(None, {self.arcface.get_inputs()[0].name: blob})[0].reshape(-1)
        return vector / np.linalg.norm(vector)

    def latent(self, frame: np.ndarray, face: dict[str, Any]) -> np.ndarray:
        latent = self.embedding(frame, face).reshape(1, -1) @ self.emap
        return (latent / np.linalg.norm(latent)).astype(np.float32)

    def swap(self, frame: np.ndarray, face: dict[str, Any], latent: np.ndarray) -> np.ndarray:
        """The face replaced in place, blended in with a soft-edged mask (as insightface pastes it)."""
        import cv2
        crop, matrix = _align(frame, face["kps"], 128)
        blob = cv2.dnn.blobFromImage(crop, 1.0 / 255, (128, 128), (0, 0, 0), swapRB=True)
        fake = self.inswapper.run(None, {"target": blob, "source": latent})[0][0]
        fake = cv2.cvtColor((fake.transpose(1, 2, 0) * 255).clip(0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        inverse = cv2.invertAffineTransform(matrix)
        h, w = frame.shape[:2]
        warped = cv2.warpAffine(fake, inverse, (w, h), borderValue=0.0)
        mask = cv2.warpAffine(np.full((128, 128), 255, np.uint8), inverse, (w, h), borderValue=0.0)
        side = int(np.sqrt(max(1, int((mask > 20).sum()))))
        mask = cv2.erode(mask, np.ones((max(1, side // 10),) * 2, np.uint8))
        blur = max(3, side // 20) | 1
        mask = cv2.GaussianBlur(mask, (blur * 2 + 1, blur * 2 + 1), 0).astype(np.float32)[..., None] / 255
        return (warped * mask + frame * (1 - mask)).astype(np.uint8)


def photo_identity(swapper: Swapper, detector: Detector, photo: Path) -> np.ndarray:
    """The person's identity from their photo (a face that fills the photo is found once it has room)."""
    import cv2
    from PIL import Image, ImageOps
    with Image.open(photo) as opened:
        picture = cv2.cvtColor(np.asarray(ImageOps.exif_transpose(opened).convert("RGB")), cv2.COLOR_RGB2BGR)
    faces = detector(picture)
    if not faces:
        pad = max(picture.shape[:2]) // 2
        picture = cv2.copyMakeBorder(picture, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        faces = detector(picture)
    if not faces:
        raise ValueError("No face found in that photo: choose one where your face is clearly seen")
    return swapper.latent(picture, max(faces, key=lambda face: face["score"] * float(np.prod(face["box"][2:] - face["box"][:2]))))


def swap_clip(photo: Path, clip: Path, out: Path, ffmpeg: str, progress: Callable[[float], None] | None = None) -> tuple[int, int]:
    """The clip with the actor's face replaced by the photo's: (frames changed, frames)."""
    import cv2
    with _lock:                          # one at a time: the models are large
        detector, swapper = Detector(), Swapper()
        latent = photo_identity(swapper, detector, photo)
        video = cv2.VideoCapture(str(clip))
        fps = video.get(cv2.CAP_PROP_FPS) or 30.0
        count = int(video.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        # 1. Who is in the clip: faces grouped by who they are; the actor is the one seen most and largest.
        people: list[dict[str, Any]] = []
        seen: list[list[tuple[dict[str, Any], int]]] = []
        index = 0
        while True:
            ok, frame = video.read()
            if not ok:
                break
            here = []
            for face in detector(frame):
                vector = swapper.embedding(frame, face)
                likeness = [float(vector @ person["vector"]) for person in people]
                best = int(np.argmax(likeness)) if likeness else -1
                if best < 0 or likeness[best] < SAME_PERSON:
                    people.append({"vector": vector, "weight": 0.0})
                    best = len(people) - 1
                person = people[best]
                person["vector"] = person["vector"] + vector
                person["vector"] /= np.linalg.norm(person["vector"])
                person["weight"] += float(np.prod(face["box"][2:] - face["box"][:2]))
                here.append((face, best))
            seen.append(here)
            index += 1
            if progress:
                progress(0.3 * index / count)
        video.release()
        if not people:
            raise ValueError("No face in that clip to put yours on")
        actor = max(range(len(people)), key=lambda number: people[number]["weight"])
        # 2. The actor's face replaced frame by frame; the clip's own sound kept.
        video = cv2.VideoCapture(str(clip))
        width, height = int(video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(video.get(cv2.CAP_PROP_FRAME_HEIGHT))
        out.parent.mkdir(parents=True, exist_ok=True)
        pending = out.with_name(f".{out.stem}.part.mp4")
        encoder = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
                                    "-r", f"{fps}", "-i", "-", "-i", str(clip), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
                                    "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(pending)],
                                   stdin=subprocess.PIPE)
        changed = 0
        try:
            assert encoder.stdin is not None
            for index, here in enumerate(seen):
                ok, frame = video.read()
                if not ok:
                    break
                mine = [face for face, person in here if person == actor]
                if mine:
                    frame = swapper.swap(frame, max(mine, key=lambda face: face["score"]), latent)
                    changed += 1
                encoder.stdin.write(frame.tobytes())
                if progress:
                    progress(0.3 + 0.7 * (index + 1) / len(seen))
            encoder.stdin.close()
            if encoder.wait() != 0:
                raise RuntimeError("The face swap could not be encoded")
        finally:
            video.release()
            if encoder.poll() is None:
                encoder.kill()
        pending.replace(out)
        return changed, len(seen)
