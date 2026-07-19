"""Zucker Editor desktop/dev entry point."""

from __future__ import annotations

import argparse
import logging
import subprocess
import socket
import threading
from pathlib import Path

from server.api import create_app
from server.inbox import load_global_config

APP_NAME = "Zucker Editor"


class DesktopApi:
    """pywebview JavaScript bridge for native desktop-only actions."""

    def pick_master(self) -> list[str]:
        """Open a native file dialog for master audio."""
        return _open_file_dialog(["Audio files (*.wav;*.mp3;*.flac;*.aiff;*.aif)"], allow_multiple=False)

    def pick_songs(self) -> list[str]:
        """Open a native file dialog for songs.json."""
        return _open_file_dialog(["JSON files (*.json)"], allow_multiple=False)

    def pick_videos(self) -> list[str]:
        """Open a native multi-file dialog for video clips."""
        return _open_file_dialog(["Video files (*.mp4;*.mov;*.mts;*.m4v)"], allow_multiple=True)

    def pick_video_folder(self) -> list[str]:
        """Open a native folder dialog for a folder of video clips."""
        return _open_folder_dialog()

    def reveal_in_finder(self, path: str | None = None) -> bool:
        """Reveal a path in Finder."""
        target = Path(path or load_global_config()["inbox_path"]).expanduser()
        subprocess.run(["open", "-R", str(target)], check=False)
        return True


def find_free_port() -> int:
    """Ask the OS for a free localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def choose_dev_port(preferred: int = 5179) -> int:
    """Return the preferred dev port or a free fallback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            return find_free_port()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--dev", action="store_true", help="Run Flask only with CORS enabled")
    parser.add_argument("--project", help="Open an existing .zuckervid project folder")
    return parser.parse_args()


def main() -> None:
    """Run Zucker Editor in desktop or dev-server mode."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args()
    app = create_app(project_path=args.project, dev=args.dev)
    port = choose_dev_port() if args.dev else find_free_port()
    if args.dev:
        app.run(host="127.0.0.1", port=port, debug=True, use_reloader=False)
        return

    import webview

    url = f"http://127.0.0.1:{port}"
    server = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    server.start()
    window = webview.create_window(APP_NAME, url, width=1200, height=820, js_api=DesktopApi())

    def on_loaded() -> None:
        config = load_global_config()
        if not config.get("ffmpeg_path") or not config.get("ffprobe_path"):
            window.create_confirmation_dialog(
                "ffmpeg is missing",
                "Zucker Editor needs ffmpeg and ffprobe for media analysis.\n\nInstall them with Homebrew:\n\nbrew install ffmpeg",
            )

    window.events.loaded += on_loaded
    webview.start()


def _open_file_dialog(file_types: list[str], allow_multiple: bool) -> list[str]:
    import webview

    if not webview.windows:
        return []
    result = webview.windows[0].create_file_dialog(
        webview.OPEN_DIALOG,
        allow_multiple=allow_multiple,
        file_types=file_types,
    )
    return [str(Path(path).resolve()) for path in (result or [])]


def _open_folder_dialog() -> list[str]:
    import webview

    if not webview.windows:
        return []
    result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
    return [str(Path(path).resolve()) for path in (result or [])]


if __name__ == "__main__":
    main()
