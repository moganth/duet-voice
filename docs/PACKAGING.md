# Packaging: source → .exe → installer

Two commands, run in order:

```bash
installer\installer.bat      # 1. source -> installer\DuetVoice.exe
makensis installer.nsi       # 2. installer\ -> DuetVoiceSetup.exe (run from inside installer\)
```

`installer.bat` does the PyInstaller build (below), stages the resulting
`DuetVoice.exe` next to `installer.nsi`, and deletes the intermediate
`dist\` folder. It needs no admin rights and can be run from anywhere.

## 1. PyInstaller: source → single .exe (done for you by installer.bat)

The build configuration lives in `installer/DuetVoice.spec` (checked into
the repo, versioned like any other source file) rather than a long CLI
invocation. `installer.bat` runs it automatically:

```bash
pip install pyinstaller
pyinstaller --noconfirm installer/DuetVoice.spec
```

`DuetVoice.spec` is equivalent to:

```bash
pyinstaller --noconfirm --onefile --windowed ^
    --name DuetVoice ^
    --add-data "config/settings.example.yaml;config" ^
    --add-data "libs;libs" ^
    --hidden-import pystray._win32 ^
    --hidden-import sounddevice ^
    main.py
```

Notes:
- `console=False` in the spec is the `--windowed` equivalent (suppresses
  the console window - we don't need one, the tray icon is the UI, and
  logs go to a file per `utils/logger.py`).
- The `libs` folder is bundled as data so `libs/windows/opus.dll` ships
  inside the exe - make sure it's there first (see docs/SETUP.md step 2).
- `config/settings.example.yaml` is bundled as data so first-run can
  copy it (see `main.py::load_config`).
- `pystray._win32` / `sounddevice` are listed as hidden imports to work
  around both libraries loading native code dynamically - without
  these the tray icon may not appear, or audio devices may show up
  empty in the packaged exe despite working fine with `python main.py`.
- Passing `a.binaries` / `a.datas` straight into `EXE()` (no separate
  `COLLECT` step) is what makes this a single-file build, matching
  `--onefile`.
- Output lands in `dist/DuetVoice.exe`; `installer.bat` then copies it
  to `installer/DuetVoice.exe` and deletes `dist/`.

If you add more bundled files or hidden imports, edit
`installer/DuetVoice.spec` directly rather than passing new flags.

**Test the exe standalone before wrapping it in an installer** - run
`installer/DuetVoice.exe` directly on a clean-ish machine (or at least
a different folder) to make sure nothing's silently depending on your
dev environment.

## 2. NSIS: installer\ → installer

Install [NSIS](https://nsis.sourceforge.io/Download). The script at
`installer/installer.nsi` in this repo is ready to use - it:

- Installs `DuetVoice.exe` + `config/` + `libs/` to
  `%LOCALAPPDATA%\DuetVoice` (no admin rights needed - this is a
  per-user install, appropriate for a personal app like this)
- Creates a Start Menu shortcut
- Optionally adds a Startup shortcut so it launches when Windows boots
  (asked during install), starting quietly in the tray without
  auto-connecting
- Writes an uninstaller that removes everything, including the Startup
  shortcut

Build it:

```bash
# from the installer/ folder, after installer.bat has staged DuetVoice.exe
makensis installer.nsi
```

This produces `DuetVoiceSetup.exe`. That's the one file you send your
girlfriend - she double-clicks it, picks which optional bits she wants,
done.

If you change the PyInstaller output location or add more bundled
files, update the `File` / `SetOutPath` lines near the top of
`installer.nsi` to match.

## Recommended path for your use case

Just run `installer.bat` then `makensis installer.nsi`, and check
"Start with Windows" during install. That gets you exactly what you
described: install once on both PCs, it launches automatically at
login, sits in the tray without auto-connecting, and you click Connect
yourself when you're ready to play.

