# Setup Guide

## 0. What you need

- Two Windows PCs (yours + your girlfriend's), each with a mic/headset
- A place to run `signaling-server/` - the cheapest option is a small
  VPS (Oracle Cloud's free tier, a $4-6/mo DigitalOcean droplet, etc.)
  running Linux. You only set this up once, ever.
- Python 3.11+ on both gaming PCs (only needed if running from source
  - skip if you're using the packaged `.exe`)

## 1. Deploy the signaling server

On your VPS:

```bash
git clone <your-repo> duet-voice
cd duet-voice/signaling-server
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open two ports on the VPS's firewall / cloud security group:
- **TCP 8000** - signaling WebSocket
- **UDP 40000** - STUN-lite endpoint discovery

To keep it running after you close the SSH session, either use
`systemd` (recommended - ask if you want a unit file) or run it inside
`tmux`/`screen` for a quick test.

Note your VPS's public IP - you'll need it in both PCs' config.

## 2. Get libopus for Windows

The app needs the Opus codec library. Windows doesn't ship it, so:

1. Download a prebuilt Windows Opus DLL. The most reliable source is
  the official `opus-tools` / `libopus` Windows builds referenced from
  [opus-codec.org/downloads](https://opus-codec.org/downloads/) - grab
  the release matching your Python architecture (`x64` for the normal
  64-bit Python install, `x86` only if you intentionally use 32-bit
  Python).
2. Rename it to `opus.dll` if it isn't already.
3. Place it at `duet-voice/libs/windows/opus.dll` (create the folders
   if they don't exist).
4. Repeat on the other PC.

`services/codec_service.py` looks here first before falling back to a
system search, so this is the only step that needs doing per machine.
If you see `WinError 193`, the DLL architecture does not match your
Python process.

If you want to test a second instance on the same machine, copy
`config/settings.yaml` to another file and launch it with
`python main.py --config config/settings.peer.yaml` (or any other
alternate path you choose).

## 3. Configure each PC

On **your PC**:

```bash
cd duet-voice
pip install -r requirements.txt
cp config/settings.example.yaml config/settings.yaml
```

Edit `config/settings.yaml`:

```yaml
display_name: "YourName"
network:
  mode: signaling
  signaling_url: "ws://YOUR_VPS_IP:8000/ws"
  room_code: "pick-something-only-you-two-know"
```

On **her PC**, same steps, but:

```yaml
display_name: "HerName"
network:
  mode: signaling
  signaling_url: "ws://YOUR_VPS_IP:8000/ws"   # same VPS
  room_code: "pick-something-only-you-two-know"  # MUST match yours exactly
```

## 4. Run it

```bash
python main.py
```

A tray icon appears (blurple = idle, green = connected, red = muted).
It auto-connects on launch by default. If both of you have the app
open with the same `room_code` within a couple minutes of each other,
you'll see a status change to "Connected" in the tray menu's status
line.

Order doesn't matter - whoever opens first just waits for the other.

## 5. (Optional) Test your mic/speaker devices first

```bash
python main.py --list-devices
```

Prints every audio device Windows sees with its index/name. Put the
exact name into `audio.input_device` / `audio.output_device` in your
config if the default device isn't the one you want (e.g. you want to
force your headset mic instead of a webcam mic).

## 6. Packaging it so she doesn't need Python at all

Once it works from source, see **docs/PACKAGING.md** to build a single
`DuetVoiceSetup.exe` installer she can just double-click.
