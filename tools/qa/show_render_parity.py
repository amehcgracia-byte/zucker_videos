"""Compare Show Studio's preview with the real render, frame by frame.

  python tools/qa/show_render_parity.py clip  PROJECT OUT NAME START END [WxH]
      Renders edited seconds START-END of the project's automatic Show with its
      Show Studio effects (core.show_bridge) into OUT/NAME.mp4, and the same
      window without them into OUT/NAME-cut.mp4. Prints one result per effect.

  python tools/qa/show_render_parity.py frames PROJECT OUT NAME T [T ...]
      For each T (seconds into the clip) saves the plain frame, the frame drawn
      by the render, and what the Studio preview needs to draw the same frame;
      copies the preview's own drawing functions next to them. Then serve OUT
      (python3 -m http.server) and open show_render_parity.html: the Studio
      functions draw each frame in the browser and the page reports how much the
      two drawings overlap and differ.

OUT holds frames of the private recording: keep it outside the repository.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "tools" / "qa"
sys.path.insert(0, str(ROOT))

# The preview's drawing functions, taken from the editor as they are.
EDITOR_FUNCTIONS = ("cupVisible", "stroke", "pointAt", "finger", "spiral", "cupInFrame", "coffee")


def _block(text: str, start: int) -> str:
    """From ``start`` to the brace closing the first block opened after it."""
    depth, opened = 0, False
    for index in range(start, len(text)):
        if text[index] == "{":
            depth, opened = depth + 1, True
        elif text[index] == "}":
            depth -= 1
            if opened and depth == 0:
                return text[start:index + 1]
    raise ValueError("Unbalanced block in the editor source")


def preview_functions() -> str:
    editor = (QA / "show_pilot_editor.html").read_text(encoding="utf-8")
    parts = []
    for name in EDITOR_FUNCTIONS:
        found = re.search(rf"function {name}\(", editor)
        if not found:
            raise ValueError(f"The editor no longer defines {name}()")
        parts.append(_block(editor, found.start()))
    workshop = (QA / "show_scene_workshop.js").read_text(encoding="utf-8")
    found = workshop.index("const priorFinger=finger;")
    parts.append(_block(workshop, found) + ";")        # the automatic finger ink wraps finger()
    return "\n".join(parts)


def _project(path: str):
    folder = Path(path).expanduser()
    plan = json.loads((folder / "artifacts" / "show_plan.json").read_text(encoding="utf-8"))
    return folder, plan


def clip(project: str, out: str, name: str, start: float, end: float, size: str = "1280x720") -> None:
    from core import show_bridge
    from core.gag_render import render_gags
    from core.show import window_plan
    from core.show_render import render_show
    folder, plan = _project(project)
    width, height = (int(v) for v in size.split("x"))
    target = Path(out)
    target.mkdir(parents=True, exist_ok=True)
    plan["cutaways"] = []
    part = window_plan(plan, start, end)
    studio, report = show_bridge.studio_layers(folder, part)
    for item in report:
        if item["type"] != "caption":
            print(f"{item['section']:9} {item['id']:24} {item['status']:11} {item['detail']}")
    cut = target / f"{name}-cut.mp4"
    render_show(Path(plan["source"]), part, cut, width=width, height=height)
    render_gags(cut, [], target / f"{name}.mp4", drawings=studio)
    (target / f"{name}.json").write_text(json.dumps({"start": start, "end": end, "width": width, "height": height,
                                                     "fps": part.get("fps"), "shots": part["shots"]}), encoding="utf-8")


def frames(project: str, out: str, name: str, times: list[float]) -> None:
    import numpy as np
    from PIL import Image

    from core import show_bridge
    from core.show_studio_store import DEFAULT_SECTION, StudioStore
    folder, _ = _project(project)
    target = Path(out)
    meta = json.loads((target / f"{name}.json").read_text(encoding="utf-8"))
    part = {"fps": meta["fps"], "shots": meta["shots"]}
    studio, _ = show_bridge.studio_layers(folder, part)
    events = {}
    for section in [DEFAULT_SECTION, *sorted(p.parent.name for p in (folder / "show_studio" / "sections").glob("*/composition.json"))]:
        for event in (StudioStore(folder, section).state()["composition"] or {}).get("events") or []:
            events[section, event["id"]] = event                                # ids repeat across sections
    store = StudioStore(folder)
    cases_path = target / "cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8")) if cases_path.is_file() else []
    for at in times:
        n = int(round(at * meta["fps"]))
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(target / f"{name}-cut.mp4"), "-vf", f"select=eq(n\\,{n})",
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(meta["height"], meta["width"], 3).copy()
        tag = f"{name}-{n:04d}"
        Image.fromarray(frame).save(target / f"{tag}-bg.png")
        x0, y0, w = show_bridge.window_at(part, n / meta["fps"])
        view = (x0 * show_bridge.STUDIO_W, y0 * show_bridge.STUDIO_H, meta["width"] / show_bridge.STUDIO_W / w)
        layers = []
        for layer in studio.layers:
            seconds = layer.section_time(n / meta["fps"])
            if seconds is not None:
                layer.drawer(frame, seconds, view)
                layers.append({"event": events[layer.section, layer.effect_id], "t": seconds})
        Image.fromarray(frame).save(target / f"{tag}-py.png")
        cases = [case for case in cases if case["tag"] != tag] + [{"tag": tag, "view": list(view), "layers": layers}]
    cases_path.write_text(json.dumps(cases), encoding="utf-8")
    for kind in ("automatic-hands", "finger-track"):
        (target / f"{kind}.json").write_text(json.dumps(store.load_analysis(kind) or ([] if kind == "finger-track" else {})), encoding="utf-8")
    (target / "preview_functions.js").write_text(preview_functions(), encoding="utf-8")
    shutil.copy(QA / "show_paint_tools.js", target / "show_paint_tools.js")
    shutil.copy(QA / "show_render_parity.html", target / "show_render_parity.html")
    print(f"{len(cases)} frames in {target}; serve it and open show_render_parity.html")


if __name__ == "__main__":
    command, *args = sys.argv[1:] or ["help"]
    if command == "clip" and len(args) in (5, 6):
        clip(args[0], args[1], args[2], float(args[3]), float(args[4]), *args[5:])
    elif command == "frames" and len(args) >= 4:
        frames(args[0], args[1], args[2], [float(v) for v in args[3:]])
    else:
        print(__doc__)
        sys.exit(2)
