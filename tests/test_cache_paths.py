from __future__ import annotations

from core.normalization import CACHE_SUBDIRS, ensure_global_cache_dirs


def test_manual_cache_deletion_is_repaired(tmp_path, monkeypatch):
    monkeypatch.setattr("core.normalization.global_cache_root", lambda: tmp_path / "Cache")
    root = ensure_global_cache_dirs()
    assert root.exists()
    assert {path.name for path in root.iterdir()} == set(CACHE_SUBDIRS)
