import json
from pathlib import Path
from core.normalization import _referenced_segment_names


def test_reference_inventory_protects_segments_and_sidecars_without_repeated_listing(tmp_path, monkeypatch):
    projects = tmp_path / 'Projects'; projects.mkdir()
    cache = tmp_path / 'Cache' / 'segments'; cache.mkdir(parents=True)
    first = 'a' * 24 + '.mp4'; second = 'b' * 24 + '.mp4'
    for name in (first, second): (cache / name).touch()
    (projects / 'audit.json').write_text(json.dumps({'path': str(cache / first), 'stamp': str(cache / (second + '.json'))}))
    for index in range(100):
        (projects / f'reserve-{index}.json').write_text(json.dumps({'path': 'unrelated-camera.mp4'}))
    original = Path.glob
    listings = []
    def counted_glob(path, pattern):
        if path == cache: listings.append(pattern)
        return original(path, pattern)
    monkeypatch.setattr(Path, 'glob', counted_glob)
    assert _referenced_segment_names(projects) == {first, first + '.json', second, second + '.json'}
    assert listings == ['*.mp4']
