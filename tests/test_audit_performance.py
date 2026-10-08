from pathlib import Path
import json

from core import shot_quality
from core.project import Project
from core.stages.base import stable_fingerprint
from core.storage import cache_file_signature
from server.wizard import _tail_lines


def test_log_tail_matches_existing_output(tmp_path):
    path = tmp_path / 'large.log'
    for content in ('', 'last line', 'αβγ\n' * 20000 + 'final\n', 'a\n' + 'z' * 20000):
        path.write_text(content)
        for limit in (1, 2, 120):
            assert _tail_lines(path, limit) == content.splitlines()[-limit:]
    assert _tail_lines(path, 0) == []
    assert _tail_lines(tmp_path / 'missing', 10) == []


def test_quality_shared_across_projects_and_retimed(tmp_path, monkeypatch):
    camera = tmp_path / 'camera.mp4'
    camera.write_bytes(b'original')
    monkeypatch.setattr(shot_quality, 'global_cache_root', lambda: tmp_path / 'shared')
    one, two = Project(tmp_path / 'one', {}), Project(tmp_path / 'two', {})
    key = stable_fingerprint({'recipe': shot_quality.SHOT_QUALITY_VERSION, **cache_file_signature(camera)})[:24]
    legacy = one.cache_dir / 'shot_quality' / f'{key}.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({'windows': [{'clip_start_sec': 2, 'clip_end_sec': 4, 'master_start_sec': 99, 'master_end_sec': 101, 'score': .8}], 'summary': {'eligible': 1}}))
    first = shot_quality._analyze_source_quality(one, {'path': str(camera), 'offset_sec': 5})
    legacy.unlink()
    monkeypatch.setattr(shot_quality, 'tool_status', lambda: (_ for _ in ()).throw(AssertionError('source analyzed twice')))
    second = shot_quality._analyze_source_quality(two, {'path': str(camera), 'offset_sec': -1})
    assert first['windows'][0]['master_start_sec'] == 7
    assert second['windows'][0]['master_start_sec'] == 1
    assert second['windows'][0]['score'] == .8
    assert first['summary'] == second['summary']
