from pathlib import Path

from core.project import Project
from core.stages import export


def test_moving_original_does_not_need_export_proxy():
    info = {'probe': {'projection': 'equirect', 'bit_depth': 8}}
    moving = {'spherical_shot': {'movement': 'push_in', 'runtime_motion_enabled': True}}
    assert export._spherical_uses_original_motion(info, moving)
    for probe in ({'projection': 'raw_insv'}, {'projection': 'equirect', 'hdr': True},
                  {'projection': 'equirect', 'bit_depth': 10}):
        assert not export._spherical_uses_original_motion({'probe': probe}, moving)
    assert not export._spherical_uses_original_motion(info, {'spherical_shot': {'runtime_motion_enabled': False}})


def test_export_proxy_reused_across_projects(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'camera')
    monkeypatch.setattr(export, 'global_cache_root', lambda: tmp_path / 'shared')
    monkeypatch.setattr(export, '_ffmpeg_path', lambda: 'ffmpeg')
    calls = []
    def encode(command, *_):
        calls.append(command)
        Path(command[-1]).write_bytes(b'completed proxy')
    monkeypatch.setattr(export, '_run_ffmpeg_progress', encode)
    info = {'source_path': str(source), 'probe': {'projection': 'equirect', 'duration': 10}}
    first, _ = export._spherical_export_source_info(Project(tmp_path/'one', {}), info, {})
    second, _ = export._spherical_export_source_info(Project(tmp_path/'two', {}), info, {})
    assert first['source_path'] == second['source_path']
    assert len(calls) == 1
    source.write_bytes(b'changed camera source')
    third, _ = export._spherical_export_source_info(Project(tmp_path/'three', {}), info, {})
    assert third['source_path'] != first['source_path']
    assert len(calls) == 2


def test_review_proxy_reused_across_projects(tmp_path, monkeypatch):
    from core import shot_review, normalization
    from core.storage import cache_file_signature, cache_mtime_ns
    import hashlib
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'camera')
    monkeypatch.setattr(normalization, 'global_cache_root', lambda: tmp_path/'shared')
    key = hashlib.sha256(f"{cache_file_signature(source)['path']}|{source.stat().st_size}|{cache_mtime_ns(source)}|equirect|190.000|1".encode()).hexdigest()[:24]
    cached = tmp_path/'shared'/'spherical_analysis'/f'equirect-{key}.mp4'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'completed proxy')
    segment = {'source_path': str(source), 'spherical_shot': {'type': 'singer'}, 'projection': 'equirect'}
    first = shot_review._spherical_analysis_source(Project(tmp_path/'one', {}), segment)
    second = shot_review._spherical_analysis_source(Project(tmp_path/'two', {}), segment)
    assert first == second
    assert first == str(cached)


def test_cold_review_source_does_not_encode_a_complete_movie(tmp_path, monkeypatch):
    from core import shot_review, normalization
    source = tmp_path/'source.mp4'
    source.write_bytes(b'camera')
    monkeypatch.setattr(normalization,'global_cache_root',lambda: tmp_path/'shared')
    monkeypatch.setattr(export,'_run_ffmpeg_progress',lambda *args: (_ for _ in ()).throw(AssertionError('Whole-movie transcode')))
    segment = {'source_path':str(source),'spherical_shot':{'type':'singer'},'projection':'equirect'}
    assert shot_review._spherical_analysis_source(Project(tmp_path/'one',{}),segment) == str(source)
    assert not (tmp_path/'shared').exists()


def test_preparation_skips_unused_proxy_and_deduplicates_needed_source(tmp_path, monkeypatch):
    monkeypatch.setattr(export, '_segment_source_info', lambda project, segment: {
        'source_path': segment['source_path'], 'probe': {'projection': 'equirect'}})
    moving = {'source_path': '/moving.mp4', 'spherical_shot': {'movement': 'push_in'}}
    static = {'source_path': '/static.mp4', 'spherical_shot': {'runtime_motion_enabled': False}}
    sources = export._spherical_sources_to_prepare(Project(tmp_path, {}), [moving, moving, static, static])
    assert list(sources) == ['/static.mp4']
