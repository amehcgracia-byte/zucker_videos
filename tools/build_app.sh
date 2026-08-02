#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-"$ROOT/.venv/bin/python"}"
APP_NAME="Zucker Editor"
DIST="$ROOT/dist"
BUILD="$ROOT/build/pyinstaller"
DMG_ROOT="$ROOT/build/dmg"
DMG_RW="$ROOT/build/Zucker Editor.tmp.dmg"
BUILD_INFO="$ROOT/build/build_info.json"
DMG_PATH="$DIST/Zucker Editor.dmg"
APP_BUNDLE="$DIST/$APP_NAME.app"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found at $PYTHON. Create the venv and install requirements first." >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import importlib.util
missing = [name for name in ("PIL", "PyInstaller") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing build dependencies: " + ", ".join(missing) + ". Run: .venv/bin/python -m pip install -r requirements-build.txt")
PY

cd "$ROOT"
"$PYTHON" tools/make_icon.py
rm -rf "$APP_BUNDLE" "$DIST/$APP_NAME" "$BUILD" "$DMG_ROOT" "$DMG_PATH" "$DMG_RW"
mkdir -p "$ROOT/build"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
"$PYTHON" - <<PY
import json
from pathlib import Path
Path("$BUILD_INFO").write_text(json.dumps({"version": "0.1", "git_commit": "$COMMIT"}, indent=2) + "\n", encoding="utf-8")
PY

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ROOT/assets/icon.icns" \
  --distpath "$DIST" \
  --workpath "$BUILD" \
  --specpath "$BUILD" \
  --add-data "$ROOT/web:web" \
  --add-data "$ROOT/assets/models:assets/models" \
  --add-data "$ROOT/core/vendor:core/vendor" \
  --add-data "/System/Library/Fonts/Supplemental/Verdana Bold.ttf:assets/fonts" \
  --add-data "$BUILD_INFO:." \
  --hidden-import librosa \
  --hidden-import cv2 \
  --hidden-import scipy.signal \
  --hidden-import soundfile \
  --hidden-import audioread \
  --hidden-import numba \
  --hidden-import llvmlite \
  --hidden-import server.api \
  --collect-submodules server \
  --collect-submodules core \
  --exclude-module pytest \
  --exclude-module tests \
  --exclude-module scipy.tests \
  --exclude-module numpy.tests \
  --exclude-module librosa.tests \
  --exclude-module numba.tests \
  "$ROOT/app.py"

rm -rf "$DIST/$APP_NAME"
find "$APP_BUNDLE" -type d -name tests -prune -exec rm -rf {} +
find "$APP_BUNDLE" \( -iname '*pytest*' -o -iname '*_tests*' -o -iname '*tests*' \) -print -exec rm -rf {} +

codesign --force --deep -s - "$APP_BUNDLE"

# A successful PyInstaller invocation is not enough: import the app and
# initialize Flask from the actual frozen executable before making a DMG.
SELFTEST_LOG="$ROOT/build/packaged-selftest.log"
if ! "$APP_BUNDLE/Contents/MacOS/$APP_NAME" --selftest >"$SELFTEST_LOG" 2>&1; then
  cat "$SELFTEST_LOG" >&2
  exit 1
fi
cat "$SELFTEST_LOG"

"$PYTHON" - <<PY
import json
from pathlib import Path

bundle_info = Path("$APP_BUNDLE/Contents/Resources/build_info.json")
actual = json.loads(bundle_info.read_text(encoding="utf-8"))["git_commit"]
expected = "$COMMIT"
if actual != expected:
    raise SystemExit(f"Packaged build_info commit {actual!r} does not match HEAD {expected!r}")
print(f"Verified packaged build_info git_commit={actual}")
PY

mkdir -p "$DMG_ROOT"
cp -R "$APP_BUNDLE" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
cp "$ROOT/assets/icon.icns" "$DMG_ROOT/.VolumeIcon.icns"
mkdir -p "$DMG_ROOT/.background"
cp "$ROOT/build/dmg_background.png" "$DMG_ROOT/.background/background.png"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_ROOT" \
  -ov \
  -fs HFS+ \
  -format UDRW \
  "$DMG_RW"

ATTACH_OUTPUT="$(hdiutil attach "$DMG_RW" -readwrite -noverify -noautoopen)"
DEVICE="$(printf '%s\n' "$ATTACH_OUTPUT" | awk 'index($0, "/Volumes/") {print $1; exit}')"
VOLUME="$(printf '%s\n' "$ATTACH_OUTPUT" | awk 'index($0, "/Volumes/") {for (i=3; i<=NF; i++) printf (i==3 ? "" : " ") $i; print ""; exit}')"
if [[ -n "$DEVICE" && -d "$VOLUME" ]]; then
  cp "$ROOT/assets/icon.icns" "$VOLUME/.VolumeIcon.icns"
  if command -v SetFile >/dev/null 2>&1; then
    SetFile -a C "$VOLUME" || true
  fi
  osascript <<OSA || true
tell application "Finder"
  tell disk "$APP_NAME"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set bounds of container window to {120, 120, 760, 520}
    set theViewOptions to the icon view options of container window
    set arrangement of theViewOptions to not arranged
    set icon size of theViewOptions to 104
    set background picture of theViewOptions to file ".background:background.png"
    set position of item "$APP_NAME.app" of container window to {170, 205}
    set position of item "Applications" of container window to {470, 205}
    close
    open
    update without registering applications
    delay 1
    close
  end tell
end tell
OSA
  cp "$ROOT/assets/icon.icns" "$VOLUME/.VolumeIcon.icns"
  SetFile -a C "$VOLUME" || true
  sync
  hdiutil detach "$DEVICE"
fi

hdiutil convert "$DMG_RW" -format UDZO -ov -o "$DMG_PATH"
rm -f "$DMG_RW"

echo "Built $APP_BUNDLE"
echo "Built $DMG_PATH"
