"""The painter's 2 GB download survives a dropped connection: it resumes where it
stopped instead of starting again, and only a wrong complete file is thrown away."""
import hashlib
import io

import pytest

from core import gag_paint


class Response(io.BytesIO):
    def __init__(self, data, status, cut=None):
        super().__init__(data)
        self.status, self.cut, self.sent = status, cut, 0

    def read(self, size=-1):
        if self.cut is not None and self.sent >= self.cut:
            raise ConnectionResetError("connection reset by peer")
        block = super().read(size if self.cut is None else min(size, self.cut - self.sent))
        self.sent += len(block)
        return block

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _setup(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(gag_paint, "model_dir", lambda: tmp_path / "painter")
    monkeypatch.setattr(gag_paint, "MODEL_FILES", {"model.bin": (hashlib.sha256(payload).hexdigest(), len(payload))})
    monkeypatch.setattr(gag_paint, "MODEL_BYTES", len(payload))


def test_a_dropped_download_resumes_from_where_it_stopped(tmp_path, monkeypatch):
    payload = bytes(range(256)) * 4096                                   # 1 MB
    _setup(tmp_path, monkeypatch, payload)
    asked = []

    def urlopen(request, timeout):
        start = int(request.headers.get("Range", "bytes=0-")[6:-1])
        asked.append(start)
        # The first two connections drop after 300 kB each.
        return Response(payload[start:], 206 if start else 200, cut=300_000 if len(asked) <= 2 else None)
    monkeypatch.setattr(gag_paint.urllib.request, "urlopen", urlopen)
    folder = gag_paint.download_model(pause=0)
    assert (folder / "model.bin").read_bytes() == payload and gag_paint.model_ready()
    assert asked == [0, 300_000, 600_000]                                # never from zero again


def test_after_too_many_drops_what_arrived_is_kept_for_the_next_try(tmp_path, monkeypatch):
    payload = b"x" * 900_000
    _setup(tmp_path, monkeypatch, payload)
    monkeypatch.setattr(gag_paint.urllib.request, "urlopen",
                        lambda request, timeout: Response(payload[int(request.headers.get("Range", "bytes=0-")[6:-1]):], 206, cut=100_000))
    with pytest.raises(RuntimeError, match="Press Download again to continue"):
        gag_paint.download_model(attempts=3, pause=0)
    assert (tmp_path / "painter" / "model.bin.part").stat().st_size == 300_000
    monkeypatch.setattr(gag_paint.urllib.request, "urlopen",
                        lambda request, timeout: Response(payload[int(request.headers.get("Range", "bytes=0-")[6:-1]):], 206))
    assert (gag_paint.download_model(pause=0) / "model.bin").read_bytes() == payload


def test_a_complete_but_wrong_file_is_discarded(tmp_path, monkeypatch):
    payload = b"y" * 1000
    _setup(tmp_path, monkeypatch, payload)
    monkeypatch.setattr(gag_paint.urllib.request, "urlopen", lambda request, timeout: Response(b"z" * 1000, 200))
    with pytest.raises(ValueError, match="not the expected file"):
        gag_paint.download_model(pause=0)
    assert not (tmp_path / "painter" / "model.bin.part").exists() and not gag_paint.model_ready()
