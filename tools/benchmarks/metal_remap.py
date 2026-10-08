"""Explicit benchmark-only Metal remap. No production import or auto-compilation."""
import ctypes
from pathlib import Path

import numpy as np


class MetalRemap:
    def __init__(self, library, source_size, output_size):
        self.lib = ctypes.CDLL(str(Path(library).resolve()))
        self.lib.remap_create.argtypes = [ctypes.c_uint] * 4 + [ctypes.c_void_p, ctypes.c_size_t]
        self.lib.remap_create.restype = ctypes.c_void_p
        self.lib.remap_frame.argtypes = [ctypes.c_void_p] * 6 + [ctypes.c_size_t]
        self.lib.remap_frame.restype = ctypes.c_int
        self.lib.remap_destroy.argtypes = [ctypes.c_void_p]
        self.error = ctypes.create_string_buffer(4096)
        self.context = self.lib.remap_create(*source_size, *output_size, self.error, len(self.error))
        if not self.context:
            raise RuntimeError(self.error.value.decode())

    def remap(self, source, mx, my, *, dst, **kwargs):
        assert all(a.flags.c_contiguous for a in (source, mx, my, dst))
        assert source.dtype == dst.dtype == np.uint8 and mx.dtype == my.dtype == np.float32
        status = self.lib.remap_frame(self.context, source.ctypes.data, mx.ctypes.data,
                                     my.ctypes.data, dst.ctypes.data, self.error, len(self.error))
        if status:
            raise RuntimeError(self.error.value.decode())
        return dst

    def __del__(self):
        if getattr(self, 'context', None):
            self.lib.remap_destroy(self.context)
            self.context = None
