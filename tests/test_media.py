from __future__ import annotations

from server.api import create_app


def test_media_range_request_returns_partial_content(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "server.inbox.ffprobe",
        lambda path: {
            "format": {"duration": "3.0", "format_name": "mov"},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720}],
        },
    )
    media = tmp_path / "clip.mov"
    media.write_bytes(b"0123456789abcdef")
    folder = tmp_path / "Range.zuckervid"

    app = create_app()
    client = app.test_client()
    client.post("/api/v1/project", json={"name": "Range", "folder": str(folder)})
    client.post("/api/v1/inputs/videos", json={"paths": [str(media)]})

    response = client.get("/api/v1/media/videos/0", headers={"Range": "bytes=2-5"})

    assert response.status_code == 206
    assert response.headers["Content-Range"] == "bytes 2-5/16"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.data == b"2345"


def test_an_open_range_on_a_long_video_is_answered_in_parts(tmp_path, monkeypatch):
    # Players ask for "bytes=0-": reading a 3.7 GB original into memory broke the connection.
    from flask import Flask

    import server.media as media
    monkeypatch.setattr(media, "MAX_RANGE_BYTES", 1024)
    big = tmp_path / "long.mp4"
    big.write_bytes(bytes(range(256)) * 20)                       # 5120 bytes
    app = Flask(__name__)
    with app.test_request_context(headers={"Range": "bytes=0-"}):
        response = media.send_file_with_range(str(big))
        assert response.status_code == 206
        assert response.headers["Content-Range"] == "bytes 0-1023/5120" and response.headers["Content-Length"] == "1024"
        assert response.get_data() == big.read_bytes()[:1024]
    with app.test_request_context(headers={"Range": "bytes=4096-"}):
        tail = media.send_file_with_range(str(big))
        assert tail.headers["Content-Range"] == "bytes 4096-5119/5120" and tail.get_data() == big.read_bytes()[4096:]
