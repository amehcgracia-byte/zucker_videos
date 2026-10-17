from pathlib import Path
import shutil
import pytest
from core.stages import backstage as b


def test_packaged_font_is_scalable_on_windows_without_system_fonts(tmp_path,monkeypatch):
    font=Path('assets/fonts/DejaVuSans.ttf')
    target=tmp_path/'assets/fonts/DejaVuSans.ttf';target.parent.mkdir(parents=True);shutil.copyfile(font,target)
    monkeypatch.setattr(b.sys,'_MEIPASS',str(tmp_path),raising=False)
    monkeypatch.setattr(b.sys,'platform','win32')
    assert b._subtitle_font_path()==target
    small=b._subtitle_font(24);large=b._subtitle_font(48)
    assert large.getbbox('Readable subtitles')[2]>small.getbbox('Readable subtitles')[2]*1.8
