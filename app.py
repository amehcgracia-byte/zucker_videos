"""Zucker Editor desktop/dev entry point."""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import subprocess
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from core.build_info import startup_label
from core.ffmpeg import tool_status
from server.api import create_app
from server.inbox import load_global_config

APP_NAME = "Zucker Editor"


class DesktopApi:
    """pywebview JavaScript bridge for native desktop-only actions."""

    def pick_master(self) -> list[str]:
        """Open a native file dialog for master audio."""
        return _open_file_dialog(allow_multiple=False)

    def pick_songs(self) -> list[str]:
        """Open a native file dialog for songs.json."""
        return _open_file_dialog(allow_multiple=False)

    def pick_videos(self) -> list[str]:
        """Open a native multi-file dialog for video clips."""
        return _open_file_dialog(allow_multiple=True)

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


def wait_for_server(url: str, timeout_seconds: float = 10.0) -> None:
    """Wait until the local Flask server accepts requests before opening WebView."""
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/api/v1/app/config", timeout=0.5) as response:
                if 200 <= response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.05)
    raise RuntimeError(f"Local Zucker Editor server did not become ready: {last_error}")

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--dev", action="store_true", help="Run Flask only with CORS enabled")
    parser.add_argument("--project", help="Open an existing .zuckervid project folder")
    parser.add_argument("--webgl-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--operator-avoidance-probe", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    """Run Zucker Editor in desktop or dev-server mode."""
    multiprocessing.freeze_support()
    _configure_logging()
    logging.getLogger(__name__).info("Starting %s", startup_label())
    args = parse_args()
    if args.webgl_probe:
        raise SystemExit(_run_webgl_probe())
    if args.operator_avoidance_probe:
        raise SystemExit(_run_operator_avoidance_probe())
    config = load_global_config()
    project_path = args.project or config.get("last_project_path")
    if project_path and not Path(project_path).exists():
        project_path = None
    app = create_app(project_path=project_path, dev=args.dev)
    port = choose_dev_port() if args.dev else find_free_port()
    if args.dev:
        logging.getLogger(__name__).info("js_api bridge attached: no")
        app.run(host="127.0.0.1", port=port, debug=True, use_reloader=False)
        return

    import webview

    url = f"http://127.0.0.1:{port}"
    server = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    server.start()
    wait_for_server(url)
    js_api = DesktopApi()
    logging.getLogger(__name__).info("js_api bridge attached: yes")
    window = webview.create_window(APP_NAME, url, width=1200, height=820, js_api=js_api, background_color="#ffffff")

    def on_loaded() -> None:
        tools = tool_status()
        if not tools.get("ok"):
            window.create_confirmation_dialog(
                "ffmpeg is missing",
                "Zucker Editor needs ffmpeg and ffprobe for media analysis.\n\nInstall them with Homebrew:\n\nbrew install ffmpeg",
            )

    def on_closing() -> None:
        state = app.config.get("ZUCKER_STATE")
        if state:
            state.wizard.cancel()
            state.engine.shutdown()

    window.events.loaded += on_loaded
    window.events.closing += on_closing
    webview.start()


def _run_webgl_probe() -> int:
    """Check WebGL and bundled Three.js inside the pywebview runtime."""
    import webview

    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    three_source = (root / "web" / "vendor" / "three.module.min.js").read_text(encoding="utf-8")
    result: dict[str, object] = {"ok": False, "three": False, "webgl": False, "error": "probe did not finish"}
    html = f"""
<!doctype html>
<html>
  <body>
    <canvas id="probe" width="64" height="64"></canvas>
    <script>
      const moduleSource = {json.dumps(three_source)};
      const moduleUrl = URL.createObjectURL(new Blob([moduleSource], {{ type: "text/javascript" }}));
      import(moduleUrl).then((THREE) => {{
      try {{
        const canvas = document.getElementById("probe");
        const gl = canvas.getContext("webgl2");
        const renderer = new THREE.WebGLRenderer({{ canvas, context: gl, antialias: false }});
        renderer.setSize(64, 64, false);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 10);
        camera.position.z = 2;
        scene.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({{ color: 0x7fbf62 }})));
        renderer.render(scene, camera);
        window.__webglProbe = {{
          ok: Boolean(gl && THREE.WebGLRenderer),
          webgl: Boolean(gl),
          three: Boolean(THREE.WebGLRenderer),
          renderer: gl ? gl.getParameter(gl.RENDERER) : "",
          pixel: Array.from(new Uint8Array(gl ? (() => {{ const p = new Uint8Array(4); gl.readPixels(32, 32, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, p); return p; }})() : [0, 0, 0, 0])),
          error: null
        }};
      }} catch (error) {{
        window.__webglProbe = {{ ok: false, webgl: false, three: Boolean(THREE && THREE.WebGLRenderer), error: String(error) }};
      }}
      }}).catch((error) => {{
        window.__webglProbe = {{ ok: false, webgl: false, three: false, error: String(error) }};
      }});
    </script>
  </body>
</html>
"""
    window = webview.create_window("WebGL Probe", html=html, hidden=True, background_color="#ffffff")

    def loaded() -> None:
        nonlocal result
        for _ in range(80):
            try:
                candidate = window.evaluate_js("window.__webglProbe || null")
            except Exception as exc:
                candidate = {"ok": False, "error": str(exc)}
            if candidate:
                result = candidate
                break
            time.sleep(0.05)
        threading.Timer(0.1, window.destroy).start()

    window.events.loaded += loaded
    webview.start(debug=False)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


def _run_operator_avoidance_probe() -> int:
    """Run real MobileNet-SSD person detection from inside the packaged bundle.

    Verifies the bundled cv2 + model files actually work from the frozen app,
    not just from the dev venv. Prints a JSON result and returns 0/1.
    """
    import numpy as np

    from core.operator_avoidance import _detect_frame, _detector

    result: dict[str, object] = {"ok": False, "error": None}
    try:
        net = _detector()
        if net is None:
            result["error"] = "detector failed to load (model files missing or cv2 import failed)"
        else:
            result["detector_loaded"] = True
            image_path = os.environ.get("ZUCKER_PROBE_IMAGE")
            if image_path:
                import cv2

                frame = cv2.imread(image_path)
                if frame is None:
                    result["error"] = f"could not read image at {image_path}"
                else:
                    blobs = _detect_frame(net, frame)
                    result["ok"] = True
                    result["blobs_on_real_image"] = blobs
            else:
                frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
                blobs = _detect_frame(net, frame)
                result["ok"] = True
                result["blobs_on_random_noise"] = len(blobs)
    except Exception as exc:
        result["error"] = str(exc)
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("ok") else 1


def _open_file_dialog(allow_multiple: bool) -> list[str]:
    import webview

    if not webview.windows:
        return []
    result = webview.windows[0].create_file_dialog(
        webview.OPEN_DIALOG,
        allow_multiple=allow_multiple,
    )
    return [str(Path(path).resolve()) for path in (result or [])]


def _open_folder_dialog() -> list[str]:
    import webview

    if not webview.windows:
        return []
    result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=False)
    return [str(Path(path).resolve()) for path in (result or [])]


def _configure_logging() -> None:
    """Write startup/runtime logs to both stderr and the user app log folder."""
    log_dir = Path.home() / "ZuckerVideos" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(log_dir / "app.log", encoding="utf-8")],
        force=True,
    )


if __name__ == "__main__":
    main()
