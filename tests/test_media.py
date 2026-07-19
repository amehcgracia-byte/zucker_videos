from __future__ import annotations

from server.api import create_app


def test_media_range_request_returns_partial_content(tmp_path):
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
