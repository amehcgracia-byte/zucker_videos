import sys
import threading

import cv2
import numpy as np
import pytest

from core import metal_remap as metal
from core.spherical_motion import motion_pose, reproject_maps


@pytest.fixture(autouse=True)
def separate_worker(monkeypatch):
    monkeypatch.setattr(metal, '_LOCAL', threading.local())


def buffers():
    source = np.random.default_rng(42).integers(0, 256, (192, 384, 3), dtype=np.uint8)
    output = np.empty((90, 160, 3), dtype=np.uint8)
    return source, output


def test_unavailable_backend_is_not_retried_every_frame(monkeypatch):
    calls = []
    monkeypatch.setattr(metal, '_qualified', lambda: calls.append('check') or False)
    source, output = buffers()
    assert not metal.try_remap(source, (), output)
    assert not metal.try_remap(source, (), output)
    assert calls == ['check']


def test_gpu_failure_falls_back_once_without_changing_source(monkeypatch):
    monkeypatch.setattr(metal, '_qualified', lambda: True)
    monkeypatch.setattr(metal, '_library', lambda: object())
    calls = []
    class Failed:
        def __init__(self, lib, source_size, output_size):
            self.source_size, self.output_size = source_size, output_size
        def remap(self, *args):
            calls.append('gpu')
            raise metal.MetalRemapError('GPU timeout')
        def close(self):
            calls.append('closed')
    monkeypatch.setattr(metal, '_Context', Failed)
    source, output = buffers(); original = source.copy()
    maps = reproject_maps((384, 192), (160, 90), dict(yaw=170, pitch=-5, fov=82))
    assert not metal.try_remap(source, maps, output)
    assert not metal.try_remap(source, maps, output)
    assert calls == ['gpu', 'closed'] and np.array_equal(source, original)


def test_cancellation_is_not_converted_to_cpu_retry(monkeypatch):
    monkeypatch.setattr(metal, '_qualified', lambda: True)
    monkeypatch.setattr(metal, '_library', lambda: object())
    class Cancelled:
        def __init__(self, lib, source_size, output_size):
            self.source_size, self.output_size = source_size, output_size
        def remap(self, *args):
            raise RuntimeError('cancelled')
    monkeypatch.setattr(metal, '_Context', Cancelled)
    source, output = buffers()
    with pytest.raises(RuntimeError, match='cancelled'):
        metal.try_remap(source, (), output)
    assert not getattr(metal._LOCAL, 'disabled', False)


@pytest.mark.skipif(sys.platform != 'darwin', reason='Metal is macOS-only')
def test_integer_metal_matches_cpu_cubic_across_movements_seams_and_wide_views():
    if metal._library() is None:
        pytest.skip('Run bash tools/build_metal.sh to test the native backend')
    assert metal._qualified(), 'Installed Metal backend must pass exact coefficient qualification'
    source, output = buffers()
    for movement in ['hold', 'pan_left', 'pan_right', 'push_in', 'pull_out', 'reveal', 'settle', 'close_hold', 'planet_to_stage']:
        shot = dict(yaw=180, pitch=-12, fov=82, runtime_motion_enabled=True, movement=movement, roll=13)
        if movement == 'planet_to_stage':
            shot.update(type='planet', fov=290, pitch=90, reveal_target=dict(yaw=190, pitch=-5, fov=100))
        for seconds in [0, .5, 2, 3.9]:
            maps = reproject_maps((384, 192), (160, 90), shot, motion_pose(shot, 4, seconds))
            assert metal.try_remap(source, maps, output)
            reference = cv2.remap(source, *maps, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
            np.testing.assert_array_equal(output, reference)
