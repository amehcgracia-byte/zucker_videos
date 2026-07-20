from __future__ import annotations

from PIL import Image

from tools import make_icon


def test_logo_recolor_colorizes_black_pixels_green(tmp_path, monkeypatch):
    source = tmp_path / "logo_mixer.png"
    green_logo = tmp_path / "logo_editor_green.png"
    web_logo = tmp_path / "web" / "logo_editor_green.png"
    image = Image.new("RGBA", (3, 1))
    image.putdata([(0, 0, 0, 255), (128, 128, 128, 255), (0, 0, 0, 0)])
    image.save(source)
    monkeypatch.setattr(make_icon, "SOURCE", source)
    monkeypatch.setattr(make_icon, "GREEN_LOGO", green_logo)
    monkeypatch.setattr(make_icon, "WEB_LOGO", web_logo)

    out = make_icon.recolor_logo()

    black, gray, transparent = list(out.getdata())
    assert black[1] > black[0]
    assert black[1] > black[2]
    assert black[:3] != (0, 0, 0)
    assert gray[1] > gray[0]
    assert transparent[3] == 0
    assert green_logo.exists()
    assert web_logo.exists()
