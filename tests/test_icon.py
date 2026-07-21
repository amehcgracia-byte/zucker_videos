from __future__ import annotations

from PIL import Image

from tools import make_icon


def test_logo_recolor_colorizes_black_pixels_green(tmp_path, monkeypatch):
    source = tmp_path / "logo_mixer.png"
    green_logo = tmp_path / "logo_editor_green.png"
    web_logo = tmp_path / "web" / "logo_editor_green.png"
    white_logo = tmp_path / "logo_editor_white.png"
    web_white_logo = tmp_path / "web" / "logo_editor_white.png"
    watermark = tmp_path / "logo_watermark.png"
    web_watermark = tmp_path / "web" / "logo_watermark.png"
    icon_source = tmp_path / "logo_icon_source.png"
    image = Image.new("RGBA", (3, 1))
    image.putdata([(0, 0, 0, 255), (128, 128, 128, 255), (0, 0, 0, 0)])
    image.save(source)
    monkeypatch.setattr(make_icon, "SOURCE", source)
    monkeypatch.setattr(make_icon, "GREEN_LOGO", green_logo)
    monkeypatch.setattr(make_icon, "WEB_LOGO", web_logo)
    monkeypatch.setattr(make_icon, "WHITE_LOGO", white_logo)
    monkeypatch.setattr(make_icon, "WEB_WHITE_LOGO", web_white_logo)
    monkeypatch.setattr(make_icon, "WATERMARK", watermark)
    monkeypatch.setattr(make_icon, "WEB_WATERMARK", web_watermark)
    monkeypatch.setattr(make_icon, "ICON_SOURCE", icon_source)
    monkeypatch.setattr(make_icon, "FULL_CIRCLE_SOURCE", tmp_path / "missing_full.png")
    monkeypatch.setattr(make_icon, "SYMBOL_A_SOURCE", tmp_path / "missing_a.png")
    monkeypatch.setattr(make_icon, "SYMBOL_B_SOURCE", tmp_path / "missing_b.png")
    monkeypatch.setattr(make_icon, "INTRO_BLACK_SOURCE", tmp_path / "missing_intro.jpg")
    monkeypatch.setattr(make_icon, "INTRO_BLACK_PNG_SOURCE", tmp_path / "missing_intro.png")

    out = make_icon.recolor_logo()

    black, gray, transparent = list(out.getdata())
    white_black, white_gray, white_transparent = list(Image.open(white_logo).convert("RGBA").getdata())
    assert black[1] > black[0]
    assert black[1] > black[2]
    assert black[:3] != (0, 0, 0)
    assert gray[1] > gray[0]
    assert transparent[3] == 0
    assert white_black == (255, 255, 255, 255)
    assert white_gray == (255, 255, 255, 255)
    assert white_transparent[3] == 0
    assert green_logo.exists()
    assert web_logo.exists()
    assert web_white_logo.exists()
    assert watermark.exists()
    assert web_watermark.exists()


def test_watermark_symbol_requires_real_alpha(tmp_path):
    opaque = Image.new("RGBA", (2, 2), (255, 255, 255, 255))

    try:
        make_icon.assert_real_alpha(opaque, tmp_path / "opaque.png")
    except ValueError as exc:
        assert "no real alpha" in str(exc)
    else:
        raise AssertionError("opaque watermark should fail")


def test_choose_symbol_prefers_more_legible_alpha_asset(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    symbol_a = source_dir / "logo_symbol_a.png"
    symbol_b = source_dir / "logo_symbol_b.png"
    small = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
    small.putpixel((8, 8), (255, 255, 255, 255))
    large = Image.new("RGBA", (16, 16), (255, 255, 255, 0))
    for x in range(4, 12):
        for y in range(4, 12):
            large.putpixel((x, y), (255, 255, 255, 255))
    small.save(symbol_a)
    large.save(symbol_b)
    monkeypatch.setattr(make_icon, "SYMBOL_A_SOURCE", symbol_a)
    monkeypatch.setattr(make_icon, "SYMBOL_B_SOURCE", symbol_b)

    assert make_icon.choose_symbol_source() == symbol_b
