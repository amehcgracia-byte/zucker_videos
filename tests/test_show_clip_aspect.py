import pytest
from core.show_proposals import render_inputs
from core.show_render import cutaway_filters


@pytest.mark.parametrize("audio", [{"clip_audio": False}, {"audio_mode": "mix"}])
def test_imported_clip_keeps_frame_when_host_audio_continues(audio, monkeypatch):
    monkeypatch.setattr("core.media_framing.inspect", lambda source: dict(x=0,y=0,width=180,height=320,portrait=True))
    clip = {"id": "portrait", "kind": "clip", "status": "approved", "at": 1,
            "duration": 2, "params": {"clip": "portrait.mp4", **audio}}
    cuts, _, _ = render_inputs([clip])
    _, filters, _ = cutaway_filters({"cutaways": cuts}, 1, 640, 360)
    assert any("force_original_aspect_ratio=decrease" in f for f in filters)
    assert any("gblur=" in f for f in filters)
    assert any("overlay=(W-w)/2:(H-h)/2" in f for f in filters)


def test_deliberate_closeup_keeps_its_zoom():
    _, filters, _ = cutaway_filters({"cutaways": [{"source": "closeup.mp4", "at": 0,
        "duration": 2, "zoom": 1.5}]}, 1, 640, 360)
    assert "crop=w=iw/1.5000" in filters[0]
    assert "force_original_aspect_ratio=increase,crop=640:360" in filters[0]
