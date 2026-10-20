import io
import logging

import pytest


@pytest.mark.parametrize("console", [False, True])
def test_desktop_logs_are_retained_with_or_without_a_gui_console(monkeypatch, tmp_path, console):
    import app

    stream = io.StringIO() if console else None
    configured = {}
    monkeypatch.setattr(app.sys, "stderr", stream)
    monkeypatch.setattr(app, "configure_working_storage", lambda: None)
    monkeypatch.setattr(app, "data_root", lambda: tmp_path)
    monkeypatch.setattr(app.logging, "basicConfig", lambda **kwargs: configured.update(kwargs))
    app._configure_logging()
    record = logging.LogRecord("desktop-test", logging.ERROR, __file__, 1, "Actual failure retained", (), None)
    try:
        for handler in configured["handlers"]:
            assert handler.stream is not None
            handler.handle(record)
        assert "Actual failure retained" in (tmp_path / "logs/app.log").read_text()
        if console:
            assert "Actual failure retained" in stream.getvalue()
    finally:
        for handler in configured["handlers"]:
            handler.close()
