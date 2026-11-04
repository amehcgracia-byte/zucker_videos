import json
import os
from server.api import _refresh_composition_base


def test_resume_cached_base_preserves_provenance(tmp_path):
    source = tmp_path / "original.mp4"
    cache = tmp_path / "composition-base.mp4"
    meta = tmp_path / "composition-base.json"
    source.write_bytes(b"clean original")
    assert not _refresh_composition_base(source, cache, meta)
    provenance = meta.read_bytes()
    assert _refresh_composition_base(cache, cache, meta)
    assert cache.read_bytes() == b"clean original"
    assert meta.read_bytes() == provenance
    alias = tmp_path / "alias.mp4"
    os.link(cache, alias)
    assert _refresh_composition_base(alias, cache, meta)
    assert meta.read_bytes() == provenance


def test_changed_base_and_broken_metadata_refresh(tmp_path):
    source = tmp_path / "original.mp4"
    cache = tmp_path / "composition-base.mp4"
    meta = tmp_path / "composition-base.json"
    source.write_bytes(b"first")
    assert not _refresh_composition_base(source, cache, meta)
    assert _refresh_composition_base(source, cache, meta)
    source.write_bytes(b"new longer edit")
    assert not _refresh_composition_base(source, cache, meta)
    assert cache.read_bytes() == source.read_bytes()
    meta.write_text("invalid json")
    assert not _refresh_composition_base(source, cache, meta)
    assert json.loads(meta.read_text())["source"] == str(source)
