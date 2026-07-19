"""HTTP Range-aware local media serving."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from flask import Response, abort, request, send_file

RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)$")


def send_file_with_range(path: str) -> Response:
    """Serve a file with support for single byte-range requests."""
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        abort(404)
    size = file_path.stat().st_size
    range_header = request.headers.get("Range")
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    if not range_header:
        response = send_file(file_path, mimetype=content_type, conditional=True)
        response.headers["Accept-Ranges"] = "bytes"
        return response

    match = RANGE_RE.match(range_header.strip())
    if not match:
        abort(416)
    start_text, end_text = match.groups()
    if start_text == "" and end_text == "":
        abort(416)
    if start_text == "":
        suffix_length = int(end_text)
        start = max(0, size - suffix_length)
        end = size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    if start >= size or end < start:
        abort(416)
    end = min(end, size - 1)
    length = end - start + 1
    with file_path.open("rb") as fh:
        fh.seek(start)
        body = fh.read(length)
    response = Response(body, 206, mimetype=content_type, direct_passthrough=False)
    response.headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Length"] = str(length)
    return response
