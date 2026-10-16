"""A missing private recording must never silently become a synthetic pass."""
import importlib.util
from pathlib import Path
import sys

import pytest


def test_private_journey_requires_real_inputs_before_browser_or_fixture_generation(monkeypatch, tmp_path, capsys):
    path = Path(__file__).resolve().parents[1] / "tools/qa/application_journeys.py"
    spec = importlib.util.spec_from_file_location("application_journeys", path)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    media = tmp_path / "private-media"
    media.mkdir()
    output = tmp_path / "results"
    monkeypatch.setattr(sys, "argv", [str(path), "--mode", "360", "--media", str(media), "--output", str(output)])

    def forbidden_fixtures(*args):
        pytest.fail("Missing private footage was replaced by synthetic fixtures")

    monkeypatch.setattr(audit, "fixtures", forbidden_fixtures)
    with pytest.raises(SystemExit) as error:
        audit.main()
    assert error.value.code == 2
    assert "sphere-360.mp4" in capsys.readouterr().err
    assert not list(media.iterdir())
    assert not (output / "fixtures").exists()
