#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-"$ROOT/.venv/bin/python"}"
APP_NAME="Zucker Editor"

cd "$ROOT"
VERSION="$("$PYTHON" -c 'from core.build_info import APP_VERSION; print(APP_VERSION)')"
if [[ -z "$VERSION" || "$VERSION" == "unknown" ]]; then
  echo "Could not determine APP_VERSION from core/build_info.py." >&2
  exit 1
fi
APP_DISPLAY_NAME="$APP_NAME $VERSION"

DIST="$ROOT/dist"
BUILD="$ROOT/build/pyinstaller"
PYI_DIST="$ROOT/build/pyinstaller-dist"
RELEASE="$ROOT/build/release"
DMG_ROOT="$ROOT/build/dmg"
DMG_RW="$ROOT/build/${APP_DISPLAY_NAME}.tmp.dmg"
BUILD_INFO="$ROOT/build/build_info.json"
VERSION="$(cd "$ROOT" && "$PYTHON" -c 'from core.build_info import APP_VERSION; print(APP_VERSION)')"
APP_DISPLAY_NAME="$APP_NAME $VERSION"
DMG_PATH="$DIST/$APP_NAME.dmg"
APP_BUNDLE="$RELEASE/$APP_NAME.app"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python not found at $PYTHON. Create the venv and install requirements first." >&2
  exit 1
fi

"$PYTHON" - <<'PY'
import importlib.util
missing = [name for name in ("PIL", "PyInstaller", "faster_whisper", "ctranslate2") if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit("Missing build dependencies: " + ", ".join(missing) + ". Run: .venv/bin/python -m pip install -r requirements.txt -r requirements-build.txt")
PY

"$PYTHON" tools/make_icon.py
# CLIP image encoder for filler scene tagging (not in git): pinned + SHA-256 verified.
bash "$ROOT/tools/fetch_clip_model.sh"
bash "$ROOT/tools/build_metal.sh"
# Preserve existing user/local build products, including the hand-edited spec.
BUILD_BACKUP="$ROOT/build/previous-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BUILD_BACKUP"
for previous in "$APP_BUNDLE" "$PYI_DIST" "$DIST/$APP_NAME" "$BUILD" "$DMG_ROOT" "$DMG_PATH" "$DMG_RW"; do
  if [[ -e "$previous" ]]; then
    mv "$previous" "$BUILD_BACKUP/$(basename "$previous")"
  fi
done
restore_local_spec() {
  if [[ -f "$BUILD_BACKUP/pyinstaller/Zucker Editor.spec" ]]; then
    mkdir -p "$BUILD"
    if [[ -f "$BUILD/Zucker Editor.spec" ]]; then
      cp "$BUILD/Zucker Editor.spec" "$BUILD_BACKUP/generated-Zucker Editor.spec"
    fi
    cp "$BUILD_BACKUP/pyinstaller/Zucker Editor.spec" "$BUILD/Zucker Editor.spec"
  fi
}
trap restore_local_spec EXIT
mkdir -p "$DIST" "$RELEASE"
mkdir -p "$ROOT/build"
COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
"$PYTHON" - <<PY
import json
from pathlib import Path
Path("$BUILD_INFO").write_text(json.dumps({"version": "$VERSION", "git_commit": "$COMMIT"}, indent=2) + "\n", encoding="utf-8")
PY

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ROOT/assets/icon.icns" \
  --distpath "$PYI_DIST" \
  --workpath "$BUILD" \
  --specpath "$BUILD" \
  --add-data "$ROOT/web:web" \
  --add-data "$ROOT/assets/intro_card_watermark.png:assets" \
  --add-data "$ROOT/assets/models:assets/models" \
  --add-data "$ROOT/captions/presets.json:captions" \
  --add-data "$ROOT/assets/parchment_full.png:assets" \
  --add-data "$ROOT/core/vendor:core/vendor" \
  --add-binary "$ROOT/build/native/metal_remap.dylib:core/native" \
  --add-data "$ROOT/core/native/NOTICE.txt:core/native" \
  --add-data "/System/Library/Fonts/Supplemental/Verdana Bold.ttf:assets/fonts" \
  --add-data "/System/Library/Fonts/Supplemental/Arial.ttf:assets/fonts" \
  --add-data "/System/Library/Fonts/Supplemental/BigCaslon.ttf:assets/fonts" \
  --add-data "$BUILD_INFO:." \
  --add-data "$ROOT/README_APP.md:." \
  --collect-submodules demucs \
  --collect-data demucs \
  --hidden-import torchaudio \
  --collect-data faster_whisper \
  --collect-data whisper \
  --collect-data onnxruntime \
  --collect-data tokenizers \
  --hidden-import librosa \
  --hidden-import cv2 \
  --hidden-import scipy.signal \
  --hidden-import soundfile \
  --hidden-import audioread \
  --hidden-import numba \
  --hidden-import llvmlite \
  --hidden-import server.api \
  --hidden-import faster_whisper \
  --collect-submodules faster_whisper \
  --hidden-import onnxruntime \
  --hidden-import tokenizers \
  --hidden-import ctranslate2 \
  --collect-submodules server \
  --collect-submodules core \
  --exclude-module pytest \
  --exclude-module tests \
  --exclude-module scipy.tests \
  --exclude-module numpy.tests \
  --exclude-module librosa.tests \
  --exclude-module numba.tests \
  "$ROOT/app.py"

rm -rf "$APP_BUNDLE"
cp -R "$PYI_DIST/$APP_NAME.app" "$APP_BUNDLE"
rm -rf "$PYI_DIST" "$DIST/$APP_NAME"
find "$APP_BUNDLE" -type d -name tests -prune -exec rm -rf {} +
# Do not match arbitrary names containing "tests": NumPy legitimately ships
# binaries such as numpy/core/_multiarray_tests.cpython-311-darwin.so.
find "$APP_BUNDLE" \( -iname '*pytest*' -o -iname 'test_*.py' \) -print -exec rm -rf {} +

PLIST="$APP_BUNDLE/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PLIST"

/usr/libexec/PlistBuddy -c "Add :NSMicrophoneUsageDescription string Record spoken phrases for editable Reel captions." "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :NSMicrophoneUsageDescription Record spoken phrases for editable Reel captions." "$PLIST"

codesign --force --deep -s - "$APP_BUNDLE"

# A successful PyInstaller invocation is not enough: import the app and
# initialize Flask from the actual frozen executable before making a DMG.
SELFTEST_LOG="$ROOT/build/packaged-selftest.log"
SELFTEST_AUDIO="${ZUCKER_SELFTEST_AUDIO:-$HOME/ZuckerVideos/WizardUploads/C0130.MP4}"
if [[ -f "$SELFTEST_AUDIO" ]]; then
  if ! ZUCKER_WHISPER_BACKEND=faster-whisper ZUCKER_SELFTEST_AUDIO="$SELFTEST_AUDIO" "$APP_BUNDLE/Contents/MacOS/$APP_NAME" --selftest >"$SELFTEST_LOG" 2>&1; then
    cat "$SELFTEST_LOG" >&2
    exit 1
  fi
  cat "$SELFTEST_LOG"
else
  echo "Packaged self-test skipped: set ZUCKER_SELFTEST_AUDIO to a real audio/video file to run it." | tee "$SELFTEST_LOG"
fi

"$PYTHON" - <<PY
import json
from pathlib import Path

bundle_info = Path("$APP_BUNDLE/Contents/Resources/build_info.json")
payload = json.loads(bundle_info.read_text(encoding="utf-8"))
actual = payload["git_commit"]
if payload.get("version") != "$VERSION":
    raise SystemExit("Packaged build_info version does not match APP_VERSION")
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
  -volname "$APP_DISPLAY_NAME" \
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
  if [[ "${ZUCKER_SKIP_FINDER_LAYOUT:-0}" != "1" ]]; then
  osascript <<OSA || true
tell application "Finder"
  tell disk "$APP_DISPLAY_NAME"
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
  fi
  cp "$ROOT/assets/icon.icns" "$VOLUME/.VolumeIcon.icns"
  SetFile -a C "$VOLUME" || true
  sync
  hdiutil detach "$DEVICE"
fi

hdiutil convert "$DMG_RW" -format UDZO -ov -o "$DMG_PATH"
rm -f "$DMG_RW"

# Keep the tested app bundle and unrelated dist products for review.
# Restore the user's tracked spec; PyInstaller's generated spec stays in backup.
if [[ -f "$BUILD_BACKUP/pyinstaller/Zucker Editor.spec" ]]; then
  cp "$BUILD/Zucker Editor.spec" "$BUILD_BACKUP/generated-Zucker Editor.spec"
  cp "$BUILD_BACKUP/pyinstaller/Zucker Editor.spec" "$BUILD/Zucker Editor.spec"
fi

echo "Built version $VERSION: $DMG_PATH"
echo "Installer hand-off directory contains:"
find "$DIST" -maxdepth 1 -type f -print
