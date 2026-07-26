# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Duet Voice. Equivalent to running:
#
#   pyinstaller --noconfirm --onefile --windowed --name DuetVoice \
#       --add-data "config/settings.example.yaml;config" \
#       --add-data "libs;libs" \
#       --hidden-import pystray._win32 \
#       --hidden-import sounddevice \
#       main.py
#
# ...but kept here as a checked-in spec (like MLExtensions.spec in the
# other project) instead of a long CLI invocation, so the exact bundling
# config is versioned and reviewable. installer.bat runs this file
# directly: `pyinstaller DuetVoice.spec`.
import os

# SPECPATH is a built-in PyInstaller variable pointing to this spec
# file's own directory (installer/) - the project root is its parent.
PROJECT_ROOT = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'main.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=[
        # Ships the example config so first-run can copy it - see
        # main.py::load_config.
        (os.path.join(PROJECT_ROOT, 'config', 'settings.example.yaml'), 'config'),
        # Bundles opus.dll from libs/windows/ - make sure it's there
        # first (see docs/SETUP.md step 2).
        (os.path.join(PROJECT_ROOT, 'libs'), 'libs'),
    ],
    hiddenimports=[
        # Both pystray and sounddevice load native code dynamically -
        # without these hidden imports the tray icon may not appear, or
        # audio devices may show up empty in the packaged exe despite
        # working fine with `python main.py`.
        'pystray._win32',
        'sounddevice',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# a.binaries / a.datas passed directly into EXE (rather than via a
# separate COLLECT step) is what makes this a single-file ("--onefile")
# build. console=False is the "--windowed" equivalent - no console
# window, since the tray icon is the UI and logs go to a file (see
# utils/logger.py).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='DuetVoice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
