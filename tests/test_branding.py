"""Branding override (config.json "branding"): absent means Zucker's own look."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from core.stages import export


def _png(path: Path, alpha: bool = True) -> Path:
    if alpha:  # transparent background with an opaque mark, like a real watermark
        image = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
        image.paste((255, 80, 40, 255), (16, 8, 48, 24))
    else:
        image = Image.new("RGB", (64, 32), (255, 80, 40))
    image.save(path)
    return path


def _config(monkeypatch, value: dict) -> None:
    monkeypatch.setattr(export, "_global_config", lambda: value)


def test_without_branding_the_bundled_assets_are_used(monkeypatch):
    _config(monkeypatch, {})
    assert export._branding() == {}
    assert export._branding_none() is False
    card = export._intro_card_path()
    assert card is None or card.name == "intro_card_watermark.png"


def test_none_removes_watermark_intro_card_and_bookends(monkeypatch, tmp_path):
    _config(monkeypatch, {"branding": {"mode": "none"}, "personal_logo_path": str(_png(tmp_path / "mine.png"))})
    assert export._branding_none() is True
    assert export._watermark_path() is None
    assert export._intro_card_path() is None


def test_custom_assets_replace_the_bundled_logo_and_watermark(monkeypatch, tmp_path):
    logo = _png(tmp_path / "logo.png")
    mark = _png(tmp_path / "mark.png")
    _config(monkeypatch, {"branding": {"logo_path": str(logo), "watermark_path": str(mark), "intro_card": False}})
    assert export._watermark_path() == mark
    assert export._logo_path() == logo
    assert export._intro_card_path() is None
    assert export._bookend_asset_path("intro") == logo
    assert export._bookend_asset_path("outro") == logo


def test_personal_logo_still_wins_over_branding(monkeypatch, tmp_path):
    personal = _png(tmp_path / "personal.png")
    _config(monkeypatch, {"personal_logo_path": str(personal), "branding": {"logo_path": str(_png(tmp_path / "app.png"))}})
    assert export._bookend_asset_path("intro") == personal
    assert export._watermark_path() == personal


def test_opaque_branding_watermark_is_ignored(monkeypatch, tmp_path):
    _config(monkeypatch, {"branding": {"watermark_path": str(_png(tmp_path / "flat.png", alpha=False))}})
    watermark = export._watermark_path()
    assert watermark is None or watermark.name != "flat.png"


def test_missing_branding_files_fall_back(monkeypatch, tmp_path):
    _config(monkeypatch, {"branding": {"logo_path": str(tmp_path / "nope.png")}})
    logo = export._logo_path()
    assert logo is None or logo.name != "nope.png"
