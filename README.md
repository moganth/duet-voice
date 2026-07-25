# Duet Voice

Ultra-lightweight peer-to-peer voice chat for two people playing co-op
games together (built for *It Takes Two*, works for anything). Built to
be lighter than Discord or WhatsApp calling so it doesn't compete with
your game for CPU, RAM, or bandwidth.

## Why this exists

Discord and WhatsApp calls carry a lot of overhead unrelated to just
"send my voice to my girlfriend": presence, rich embeds, video
pipelines, encryption layers, cloud relays. Duet Voice does exactly one
thing - raw Opus-encoded voice over UDP, directly between two PCs - and
nothing else.

- **Opus codec** (same one Discord uses) at 16-32 kbps - clear voice at
  a fraction of the bandwidth
- **Voice-activity detection** - no packets sent while you're not
  talking, unlike an always-open call
- **Peer-to-peer audio** - once connected, voice never touches a
  server; only the initial handshake does
- **Runs in the tray** - no window, no focus stealing, mute with a
  hotkey

## How it's organized

```
duet-voice/            <- this app (run on both PCs)
signaling-server/      <- tiny relay that introduces the two PCs (run on one cheap VPS)
installer/              <- NSIS script to build a Windows installer
```

You only need to think about `signaling-server/` once, when you first
set things up (see docs/SETUP.md). After that it's "open the app on
both PCs, click Connect."

## Quickstart (development / running from source)

1. Install Python 3.11+ on both PCs.
2. `pip install -r requirements.txt`
3. Download `opus.dll` and place it at `libs/windows/opus.dll` (see
   docs/SETUP.md - required, the app will not start without it).
4. Copy `config/settings.example.yaml` to `config/settings.yaml` and
   edit `room_code` (must match on both PCs) and `signaling_url`
   (point it at wherever you deployed `signaling-server/`).
5. `python main.py`
6. Right-click the tray icon → Connect on both PCs.

For turning this into a proper `DuetVoiceSetup.exe` installer that
starts automatically and needs zero Python knowledge from your
girlfriend's side, see **docs/PACKAGING.md**.

## Documentation

| Doc | What's in it |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Full first-time setup, both PCs + the relay server |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How audio flows through the code, folder-by-folder |
| [docs/NETWORKING.md](docs/NETWORKING.md) | NAT traversal, direct mode vs signaling mode, troubleshooting connections |
| [docs/PACKAGING.md](docs/PACKAGING.md) | PyInstaller → .exe → NSIS installer → auto-start on boot |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common problems and fixes |

## Known limitations (read this before you get confused)

- **No encryption yet.** Voice packets are sent in the clear. Fine for
  "me and my girlfriend on a private link," not fine if you're
  paranoid about a coffee-shop network sniffing packets. See
  docs/ARCHITECTURE.md for the easy path to adding it (`pynacl`).
- **Symmetric NAT breaks auto-connect.** Some mobile hotspots / CGNAT
  setups can't be hole-punched. Use `mode: direct` with port
  forwarding instead - see docs/NETWORKING.md.
- Built and tested for **2 peers only** by design. Not a group call app.
