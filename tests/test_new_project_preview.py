from __future__ import annotations

from pathlib import Path


APP_JS = Path(__file__).parents[1] / "web" / "app.js"


def test_new_project_registers_inputs_before_360_preview():
    source = APP_JS.read_text(encoding="utf-8")
    helper = source.index("async function registerInputsBeforePreview")
    assert 'api("/wizard/draft"' in source[helper:helper + 1800]

    step = source.index("async function prepareStep2")
    step_source = source[step:]
    assert "await registerInputsBeforePreview(inputs);" in step_source
    assert step_source.index("await registerInputsBeforePreview(inputs);") < step_source.index("renderSphericalSetup();")


def test_build_version_is_current():
    build_info = (Path(__file__).parents[1] / "core" / "build_info.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "2.5.8"' in build_info
