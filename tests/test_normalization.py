from __future__ import annotations

from core.normalization import EQUIRECT_FILTER, needs_normalization, normalization_filter, normalized_path
from core.project import create_project, file_record


def test_normalization_cache_decision_uses_source_signature(tmp_path):
    project = create_project("Norm", str(tmp_path / "Norm.zuckervid"))
    source = tmp_path / "clip.mov"
    source.write_bytes(b"video")
    record = file_record(str(source))
    destination = normalized_path(project, record)
    destination.parent.mkdir(parents=True)

    assert needs_normalization(record, destination) is True
    destination.write_bytes(b"normalized")
    record["normalized"] = {
        "path": str(destination),
        "source_size": record["size"],
        "source_mtime": record["mtime"],
    }
    assert needs_normalization(record, destination) is False

    source.write_bytes(b"changed")
    assert needs_normalization(record, destination) is True


def test_normalization_filter_uses_equirect_and_hdr_paths():
    assert normalization_filter({"projection": "equirect"}) == EQUIRECT_FILTER
    assert "tonemap" in normalization_filter({"hdr": True, "bit_depth": 10})
    assert "trunc(iw/2)*2" in normalization_filter({"hdr": False, "bit_depth": 8})
