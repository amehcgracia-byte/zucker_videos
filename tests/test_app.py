from __future__ import annotations


def test_app_selftest_initializes_flask():
    import app

    assert app._run_selftest() == 0
