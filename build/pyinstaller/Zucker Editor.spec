# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['librosa', 'cv2', 'scipy.signal', 'soundfile', 'audioread', 'numba', 'llvmlite', 'server.api']
hiddenimports += collect_submodules('server')
hiddenimports += collect_submodules('core')


a = Analysis(
    ['/Users/macbookair/zucker_videos/app.py'],
    pathex=[],
    binaries=[],
    datas=[('/Users/macbookair/zucker_videos/web', 'web'), ('/Users/macbookair/zucker_videos/assets/intro_card_watermark.png', 'assets'), ('/Users/macbookair/zucker_videos/assets/models', 'assets/models'), ('/Users/macbookair/zucker_videos/core/vendor', 'core/vendor'), ('/System/Library/Fonts/Supplemental/Verdana Bold.ttf', 'assets/fonts'), ('/System/Library/Fonts/Supplemental/Arial.ttf', 'assets/fonts'), ('/Users/macbookair/zucker_videos/build/build_info.json', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['pytest', 'tests', 'scipy.tests', 'numpy.tests', 'librosa.tests', 'numba.tests'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Zucker Editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['/Users/macbookair/zucker_videos/assets/icon.icns'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Zucker Editor',
)
app = BUNDLE(
    coll,
    name='Zucker Editor.app',
    icon='/Users/macbookair/zucker_videos/assets/icon.icns',
    bundle_identifier=None,
)
