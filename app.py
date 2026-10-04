"""Zucker Editor desktop/dev entry point."""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import multiprocessing
import os
import subprocess
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from core.storage import data_root, configure_working_storage

from core.build_info import startup_label
from server.api import create_app
from server.inbox import load_global_config, save_global_config
from core.storage import storage_ready, save_data_root, require_available

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

    def choose_storage(self) -> str | None:
        """Select where all imported media and working caches will live."""
        paths = _open_folder_dialog()
        if not paths:
            return None
        parent = Path(paths[0])
        root = parent if parent.name == "Zucker Editor" else parent / "Zucker Editor"
        save_data_root(str(root))
        callback = getattr(self, "_storage_selected", None)
        return callback() if callback else str(root)

    def pick_project_location(self) -> str | None:
        """Choose the session folder before creating a new project."""
        config = load_global_config()
        paths = _open_folder_dialog(str(config.get("project_root") or data_root()))
        if not paths:
            return None
        parent = require_available(Path(paths[0]))
        config = load_global_config()
        config["project_root"] = str(parent)
        config["project_roots"] = list(dict.fromkeys([*config.get("project_roots", []), str(parent)]))
        save_global_config(config)
        return str(parent)

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
    parser.add_argument("--webgl-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--operator-avoidance-probe", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> None:
    """Run Zucker Editor in desktop or dev-server mode."""
    multiprocessing.freeze_support()
    args = parse_args()
    if not (args.dev or args.selftest or args.webgl_probe or args.operator_avoidance_probe) and not storage_ready():
        import webview
        bridge = DesktopApi()
        bridge._storage_selected = lambda: _start_desktop_server(args)
        html = """<!doctype html><html lang="es"><meta charset="utf-8">
        <style>body{font:18px system-ui;max-width:650px;margin:70px auto;padding:20px}button{font:inherit;padding:14px;border-radius:12px}#error{color:#a22}</style>
        <h1>Elige dónde guardar tu trabajo</h1>
        <p>Vídeos importados, cachés y proyectos se guardarán en esta ubicación. Puedes elegir tu disco externo.</p>
        <button id="choose" onclick="choose()">Seleccionar ubicación</button><p id="error"></p>
        <script>async function choose(){const b=document.getElementById('choose');b.disabled=true;try{const url=await window.pywebview.api.choose_storage();if(url)location.href=url;}catch(e){document.getElementById('error').textContent=e.message;}finally{b.disabled=false;}}document.getElementById('choose').disabled=true;window.addEventListener('pywebviewready',()=>document.getElementById('choose').disabled=false);</script></html>"""
        webview.create_window(APP_NAME, html=html, width=850, height=540, js_api=bridge)
        webview.start()
        return
    _configure_logging()
    logging.getLogger(__name__).info("Starting %s", startup_label())
    if args.webgl_probe:
        raise SystemExit(_run_webgl_probe())
    if args.operator_avoidance_probe:
        raise SystemExit(_run_operator_avoidance_probe())
    if args.selftest:
        report = os.environ.get("ZUCKER_SELFTEST_REPORT")
        if report:
            # Windows GUI executables have no stdout; retain machine-readable proof.
            with Path(report).open("w", encoding="utf-8") as output, contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                raise SystemExit(_run_selftest())
        raise SystemExit(_run_selftest())
    config = load_global_config()
    # A normal relaunch is intentionally a fresh Step 1 session. Existing
    # projects remain on disk and are available from the project shelf; only an
    # explicit --project request should reopen one automatically.
    project_path = args.project
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
    js_api = DesktopApi()
    logging.getLogger(__name__).info("js_api bridge attached: yes")
    window = webview.create_window(APP_NAME, url, width=1200, height=820, js_api=js_api, background_color="#ffffff")

    def on_loaded() -> None:
        config = load_global_config()
        if not config.get("ffmpeg_path") or not config.get("ffprobe_path"):
            window.create_confirmation_dialog(
                "ffmpeg is missing",
                "Zucker Editor needs ffmpeg and ffprobe for media analysis.\n\nInstall them with Homebrew:\n\nbrew install ffmpeg",
            )

    window.events.loaded += on_loaded
    webview.start()


def _start_desktop_server(args: argparse.Namespace) -> str:
    _configure_logging()
    logging.getLogger(__name__).info("Starting %s", startup_label())
    project_path = args.project if args.project and Path(args.project).exists() else None
    app = create_app(project_path=project_path, dev=False)
    port = find_free_port()
    threading.Thread(target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False), daemon=True).start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.1):
                return f"http://127.0.0.1:{port}"
        except OSError:
            time.sleep(.02)
    raise RuntimeError("The application server could not start.")


def _run_selftest() -> int:
    """Initialize the packaged app and exercise bundle-sensitive invariants."""
    try:
        app = create_app(project_path=None, dev=False)
        with app.test_client() as client:
            response = client.get("/")
        if response.status_code != 200:
            print(json.dumps({"ok": False, "status": response.status_code}, sort_keys=True))
            return 1
        from core.stages.export import _intro_card_path, _media_duration, _render_logo_clip
        from core.backstage_transcription import transcribe_sources
        from core.ffmpeg import ensure_tools_on_path
        from core.engine import PipelineEngine
        from core.normalization import CACHE_SUBDIRS, ensure_global_cache_dirs
        from core.project import create_project

        card = _intro_card_path()
        if card is None:
            raise RuntimeError("intro_card_watermark.png is not present in the packaged assets")
        cache_root = ensure_global_cache_dirs()
        missing_cache_dirs = [name for name in CACHE_SUBDIRS if not (cache_root / name).is_dir()]
        if missing_cache_dirs:
            raise RuntimeError(f"cache tree was not recreated: {missing_cache_dirs}")
        with tempfile.TemporaryDirectory(prefix="zucker-selftest-project-") as project_dir:
            project = create_project("Packaged selftest", project_dir)
            project.data["settings"]["wizard"] = {"platform": "reel"}
            engine = PipelineEngine()
            try:
                if engine._plan("cut", project) != ["cut"]:
                    raise RuntimeError("Reel pipeline unexpectedly includes sync")
                project.data["settings"]["wizard"] = {"platform": "backstage"}
                if engine._plan("export", project) != ["ingest", "cut", "edit", "export"]:
                    raise RuntimeError("Backstage pipeline unexpectedly includes sync or coverage stages")
            finally:
                engine.shutdown()
        flyers = app.test_client().get("/api/v1/wizard/flyers")
        if flyers.status_code != 200 or not isinstance(flyers.get_json().get("items"), list):
            raise RuntimeError("Packaged flyer library endpoint is unavailable")
        with tempfile.TemporaryDirectory(prefix="zucker-selftest-") as temp_dir:
            rendered = Path(temp_dir) / "intro.mp4"
            _render_logo_clip(rendered, "youtube", "intro", 0.5, 1_000_000, None)
            rendered_duration = _media_duration(str(rendered))
            # Exercise the exact frozen dependency path that previously failed:
            # ffmpeg -> short audio fragment -> faster-whisper -> Silero VAD.
            # This is deliberately mandatory for the frozen executable. Unit
            # tests call _run_selftest too, but must not load the native model
            # unless they explicitly opt in.
            selftest_transcription = False
            if getattr(sys, "_MEIPASS", None) or os.environ.get("ZUCKER_SELFTEST_TRANSCRIPTION") == "1":
                audio_source = os.environ.get("ZUCKER_SELFTEST_AUDIO")
                if not audio_source:
                    candidates = sorted((data_root() / "WizardUploads").glob("*.MP4"))
                    audio_source = str(candidates[0]) if candidates else ""
                if not audio_source or not Path(audio_source).is_file():
                    raise RuntimeError("No real audio source available for packaged transcription self-test")
                tools = ensure_tools_on_path()
                if not tools.get("ffmpeg_path"):
                    raise RuntimeError("ffmpeg is required for packaged transcription self-test")
                fragment = Path(temp_dir) / "transcription-fragment.wav"
                subprocess.run([
                    str(tools["ffmpeg_path"]), "-y", "-hide_banner", "-loglevel", "error",
                    "-ss", "0", "-t", "6", "-i", audio_source,
                    "-vn", "-ac", "1", "-ar", "16000", str(fragment),
                ], check=True)
                transcription = transcribe_sources(
                    [{"path": str(fragment), "filename": fragment.name}],
                    Path(temp_dir) / "transcription.json",
                    model_name="tiny",
                )
                if transcription.get("status") != "ready" or transcription.get("backend") != "faster-whisper":
                    raise RuntimeError(f"Packaged transcription failed: {transcription}")
                if not transcription.get("sources"):
                    raise RuntimeError("Packaged transcription returned no source")
                selftest_transcription = True
        backstage_result = None
        backstage_project = os.environ.get("ZUCKER_SELFTEST_BACKSTAGE_PROJECT")
        if backstage_project:
            from core.project import load_project

            project = load_project(backstage_project)
            project.data["settings"].setdefault("wizard", {})["platform"] = "backstage"
            project.mark_all_stale_from("ingest")
            engine = PipelineEngine()
            try:
                engine.run_sync(project, "export")
            finally:
                engine.shutdown()
            manifest = json.loads((project.artifacts_dir / "export_manifest.json").read_text(encoding="utf-8"))
            export_path = Path(manifest["exports"][0]["path"])
            if not export_path.is_file() or export_path.stat().st_size <= 0:
                raise RuntimeError(f"Packaged Backstage export missing: {export_path}")
            backstage_result = str(export_path)
        print(json.dumps({
            "ok": True,
            "status": response.status_code,
            "intro_card": str(card),
            "intro_rendered": rendered_duration > 0.0,
            "transcription": selftest_transcription,
            "backstage_export": backstage_result,
        }, sort_keys=True))
        return 0
    except Exception as exc:
        logging.getLogger(__name__).exception("Packaged self-test failed")
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1


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


def _open_folder_dialog(directory: str = "") -> list[str]:
    import webview

    if not webview.windows:
        return []
    result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, directory=directory, allow_multiple=False)
    return [str(Path(path).resolve()) for path in (result or [])]


def _configure_logging() -> None:
    """Write startup/runtime logs to both stderr and the user app log folder."""
    configure_working_storage()
    from logging.handlers import RotatingFileHandler
    log_dir = data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), RotatingFileHandler(log_dir / "app.log", maxBytes=5*1024*1024, backupCount=2, encoding="utf-8")],
        force=True,
    )


if __name__ == "__main__":
    main()
