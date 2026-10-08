"""Optional exact 8-bit cubic Metal remap, with verified CPU fallback.

No runtime compiler or downloads: the native library is built with the app.
Every rendering thread owns its buffers. A qualification failure or GPU error
disables that thread's GPU route; cancellation exceptions are not intercepted.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import logging
from pathlib import Path
import sys
import threading

import cv2
import numpy as np

LOGGER = logging.getLogger(__name__)
_LOCAL = threading.local()
_STATS_LOCK = threading.Lock()
_STATS = dict(frames=0, contexts=0, fallbacks=0, unavailable=0)


class MetalRemapError(Exception):
    """Native backend failed; retry these exact pixels with CPU."""


def backend_stats() -> dict[str, int]:
    with _STATS_LOCK:
        return dict(_STATS)


def _count(name: str) -> None:
    with _STATS_LOCK:
        _STATS[name] += 1


@lru_cache(maxsize=1)
def _library():
    if sys.platform != 'darwin':
        return None
    candidates = [Path(__file__).with_name('native') / 'metal_remap.dylib']
    if not getattr(sys, 'frozen', False):
        candidates.append(Path(__file__).resolve().parents[1] / 'build/native/metal_remap.dylib')
    for path in candidates:
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.remap_create.argtypes = [ctypes.c_uint] * 4 + [ctypes.c_void_p, ctypes.c_size_t]
            lib.remap_create.restype = ctypes.c_void_p
            lib.remap_frame.argtypes = [ctypes.c_void_p] * 6 + [ctypes.c_size_t]
            lib.remap_frame.restype = ctypes.c_int
            lib.remap_destroy.argtypes = [ctypes.c_void_p]
            lib.remap_destroy.restype = None
            return lib
        except (OSError, AttributeError) as exc:
            LOGGER.warning('Metal remap unavailable: %s', exc)
    return None


class _Context:
    def __init__(self, lib, source_size, output_size):
        self.lib = lib
        self.source_size, self.output_size = source_size, output_size
        self.error = ctypes.create_string_buffer(4096)
        self.pointer = lib.remap_create(*source_size, *output_size, self.error, len(self.error))
        if not self.pointer:
            raise MetalRemapError(self.error.value.decode(errors='replace'))
        _count('contexts')

    def remap(self, source, mx, my, dst):
        sw, sh = self.source_size
        width, height = self.output_size
        if (source.shape != (sh, sw, 3) or dst.shape != (height, width, 3)
                or mx.shape != (height, width) or my.shape != (height, width)
                or source.dtype != np.uint8 or dst.dtype != np.uint8
                or mx.dtype != np.float32 or my.dtype != np.float32
                or not all(a.flags.c_contiguous for a in (source, mx, my, dst))):
            raise MetalRemapError('Unsupported Metal remap buffer layout')
        status = self.lib.remap_frame(self.pointer, source.ctypes.data, mx.ctypes.data,
                                     my.ctypes.data, dst.ctypes.data, self.error, len(self.error))
        if status:
            raise MetalRemapError(self.error.value.decode(errors='replace'))

    def close(self):
        if getattr(self, 'pointer', None):
            self.lib.remap_destroy(self.pointer)
            self.pointer = None

    def __del__(self):
        self.close()


_QUALIFICATION_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _qualify_cached() -> bool:
    lib = _library()
    if lib is None:
        return False
    context = None
    try:
        source = np.random.default_rng(3108).integers(0, 256, (64, 128, 3), dtype=np.uint8)
        yy, xx = np.indices((64, 64), dtype=np.float32)
        # Every 1/32 phase, with combinations at both wrap boundaries.
        mx = np.ascontiguousarray((xx % 32) / 32 + np.where(xx < 32, -1, 127), dtype=np.float32)
        my = np.ascontiguousarray((yy % 32) / 32 + np.where(yy < 32, -1, 63), dtype=np.float32)
        reference = cv2.remap(source, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
        candidate = np.empty_like(reference)
        context = _Context(lib, (128, 64), (64, 64))
        context.remap(source, mx, my, candidate)
        if not np.array_equal(candidate, reference):
            LOGGER.warning('Metal cubic qualification differs from OpenCV; retaining CPU')
            return False
        return True
    except MetalRemapError as exc:
        LOGGER.warning('Metal cubic qualification failed; retaining CPU: %s', exc)
        return False
    finally:
        if context is not None:
            context.close()

def _qualified() -> bool:
    # Lock outside the cache: concurrent misses must not repeat qualification.
    with _QUALIFICATION_LOCK:
        return _qualify_cached()


def try_remap(source: np.ndarray, maps: tuple[np.ndarray, np.ndarray], dst: np.ndarray) -> bool:
    """Return False for CPU fallback without swallowing cancellation exceptions."""
    if getattr(_LOCAL, 'disabled', False):
        return False
    if not _qualified():
        _LOCAL.disabled = True
        _count('unavailable')
        return False
    source_size = source.shape[1], source.shape[0]
    output_size = dst.shape[1], dst.shape[0]
    if max(*source_size, *output_size) >= 32767:
        return False  # OpenCV uses signed 16-bit source indices internally.
    try:
        context = getattr(_LOCAL, 'context', None)
        if context is None or (context.source_size, context.output_size) != (source_size, output_size):
            if context is not None:
                context.close()
            context = _Context(_library(), source_size, output_size)
            _LOCAL.context = context
        context.remap(source, *maps, dst)
        _count('frames')
        return True
    except MetalRemapError as exc:
        _LOCAL.disabled = True
        context = getattr(_LOCAL, 'context', None)
        if context is not None:
            context.close()
            _LOCAL.context = None
        _count('fallbacks')
        LOGGER.warning('Metal remap failed; using exact CPU cubic remap for this worker: %s', exc)
        return False
