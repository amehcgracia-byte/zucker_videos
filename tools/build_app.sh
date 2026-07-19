#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-"$ROOT/.venv/bin/python"}"
APP_NAME="Zucker Editor"
DIST="$ROOT/dist"
BUILD="$ROOT/build/pyinstaller"
DMG_ROOT="$ROOT/build/dmg"
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
rm -rf "$APP_BUNDLE" "$DIST/$APP_NAME" "$BUILD" "$DMG_ROOT" "$DMG_PATH"

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ROOT/assets/icon.icns" \
  --distpath "$DIST" \
  --workpath "$BUILD" \
  --specpath "$BUILD" \
  --add-data "$ROOT/web:web" \
  --hidden-import librosa \
  --hidden-import scipy.signal \
  --hidden-import soundfile \
  --hidden-import audioread \
  --hidden-import numba \
  --hidden-import llvmlite \
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

mkdir -p "$DMG_ROOT"
cp -R "$APP_BUNDLE" "$DMG_ROOT/"
ln -s /Applications "$DMG_ROOT/Applications"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$DMG_ROOT" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Built $APP_BUNDLE"
echo "Built $DMG_PATH"
