# Packaging: source → .exe → installer → background service

Three separate steps, each optional depending on how far you want to go:

1. **PyInstaller** - bundle Python + all dependencies into one `.exe`
   (required for the rest of this doc)
2. **NSIS** - wrap that `.exe` into a proper `DuetVoiceSetup.exe`
   installer with a Start Menu entry, uninstaller, etc.
3. **WinSW** - optionally run it as a real Windows background service
   instead of a tray app (most people should skip this - the tray app
   already runs in the background fine; WinSW is for the "I want it
   running even before I log in" case)

## 1. PyInstaller: source → single .exe

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed ^
    --name DuetVoice ^
    --add-data "config/settings.example.yaml;config" ^
    --add-data "libs;libs" ^
    main.py
```

Notes:
- `--windowed` suppresses the console window (we don't need one - the
  tray icon is the UI, and logs go to a file per `utils/logger.py`).
- `--add-data "libs;libs"` bundles your `opus.dll` from
  `libs/windows/opus.dll` into the exe - make sure it's there first
  (see docs/SETUP.md step 2).
- `--add-data "config/settings.example.yaml;config"` ships the example
  config so first-run can copy it (see `main.py::load_config`).
- Output lands in `dist/DuetVoice.exe`.

**Test the exe standalone before wrapping it in an installer** - run
`dist/DuetVoice.exe` directly on a clean-ish machine (or at least a
different folder) to make sure nothing's silently depending on your
dev environment.

A gotcha specific to this project: `pystray` and `sounddevice` both
load native libraries dynamically. If the tray icon doesn't appear or
audio devices show as empty in the packaged exe but work fine with
`python main.py`, add these to the PyInstaller command:

```bash
    --hidden-import pystray._win32 ^
    --hidden-import sounddevice
```

## 2. NSIS: .exe → installer

Install [NSIS](https://nsis.sourceforge.io/Download). The script at
`installer/installer.nsi` in this repo is ready to use - it:

- Installs `DuetVoice.exe` + `config/` + `libs/` to
  `%LOCALAPPDATA%\DuetVoice` (no admin rights needed - this is a
  per-user install, appropriate for a personal app like this)
- Creates a Start Menu shortcut
- Optionally adds a Startup shortcut so it launches when Windows boots
  (asked during install)
- Writes an uninstaller that removes everything including the Startup
  shortcut

Build it:

```bash
# from the installer/ folder, after step 1 has produced dist/DuetVoice.exe
makensis installer.nsi
```

This produces `DuetVoiceSetup.exe`. That's the one file you send your
girlfriend - she double-clicks it, picks "start on boot" or not, done.

If you change the PyInstaller output location or add more bundled
files, update the `File` / `SetOutPath` lines near the top of
`installer.nsi` to match.

## 3. (Optional) WinSW: run as a real background Windows service

Most people don't need this - the tray app + "start on boot" from the
NSIS installer already covers "runs in the background." Consider WinSW
only if you specifically want the voice link active *before anyone
logs into Windows* (e.g. always-on always-connected between two
machines). Trade-off: a service has no tray icon and no GUI, so mute
toggling would need to go through the hotkey only.

1. Download [WinSW](https://github.com/winsw/winsw/releases) (the
   `.NET` or `-x64.exe` build), rename it `DuetVoiceService.exe`,
   place it next to `DuetVoice.exe`.
2. Create `DuetVoiceService.xml` next to it:

```xml
<service>
  <id>DuetVoice</id>
  <name>Duet Voice</name>
  <description>Lightweight P2P voice link for co-op gaming.</description>
  <executable>%BASE%\DuetVoice.exe</executable>
  <arguments>--no-autoconnect</arguments>
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec"/>
  <logpath>%LOCALAPPDATA%\DuetVoice\logs</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>2048</sizeThreshold>
    <keepFiles>3</keepFiles>
  </log>
</service>
```

3. Install and start it (as Administrator):

```bat
DuetVoiceService.exe install
DuetVoiceService.exe start
```

Because a Windows service has no tray/UI, the sample XML uses
`--no-autoconnect` so it starts quietly and waits for a manual connect.
If you want it to attempt connection automatically on boot, remove
that argument. This mode is genuinely niche for a 2-person co-op
voice app - the tray app covers the actual ask ("run in the background
while I play") without the extra complexity. Included here for
completeness since you asked about it.

## Recommended path for your use case

Just do steps 1 and 2. Tray app + NSIS installer + "start on boot"
checkbox gets you exactly what you described: install once on both
PCs, launch it (or let it auto-start), it sits in the tray, you play
It Takes Two. Skip WinSW unless a specific need for it shows up later.
