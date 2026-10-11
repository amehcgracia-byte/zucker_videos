import runpy
import sys
from pathlib import Path

HOOK = Path(__file__).parents[1] / 'tools/windows_runtime_hook.py'

def test_only_bundled_managed_dll_marks_are_removed(tmp_path, monkeypatch):
    runtime = tmp_path / 'pythonnet/runtime/Python.Runtime.dll'
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b'unchanged dll')
    mark = Path(str(runtime) + ':Zone.Identifier')
    mark.write_text('[ZoneTransfer]\nZoneId=3')
    other = tmp_path / 'document.dll:Zone.Identifier'
    other.write_text('leave alone')
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    runpy.run_path(str(HOOK))
    assert not mark.exists()
    assert runtime.read_bytes() == b'unchanged dll'
    assert other.exists()

def test_development_does_not_unblock_any_files(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    runpy.run_path(str(HOOK))
