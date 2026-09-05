from pathlib import Path


def test_backstage_subtitle_path_is_untouched() -> None:
    backstage = Path("core/stages/backstage.py").read_text(encoding="utf-8")
    transcription = Path("core/backstage_transcription.py").read_text(encoding="utf-8")
    assert "captions" not in backstage
    assert "captions" not in transcription


def test_caption_package_has_no_mode_dependencies() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("captions").glob("*.py"))
    for forbidden in ("server.wizard", "core.stages", "core.backstage", "sync_map.json", "edit_plan.json"):
        assert forbidden not in source
