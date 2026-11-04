from __future__ import annotations


def test_app_selftest_initializes_flask(monkeypatch):
    import app
    from core import scene_tags

    # This unit test exercises startup, not downloading/loading CLIP weights.
    # The packaged --selftest still executes the real model check unchanged.
    monkeypatch.setattr(scene_tags, "available", lambda: True)
    monkeypatch.setattr(scene_tags, "analyze_frames", lambda frames: [
        {"model": "test-encoder", "time": {"night": .9, "day": .1}},
        {"model": "test-encoder", "time": {"night": .1, "day": .9}},
    ])

    assert app._run_selftest() == 0
