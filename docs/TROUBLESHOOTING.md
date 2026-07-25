# Troubleshooting

## Where to find logs

`%LOCALAPPDATA%\DuetVoice\logs\duet-voice.log` (Windows). This is the
first thing to check for any issue - `utils/logger.py` writes every
connection step, encode/decode errors, and socket errors here, even
when running as a windowed exe with no visible console.

## "libopus could not be found"

You skipped or misplaced the Opus DLL step. See docs/SETUP.md step 2 -
`opus.dll` needs to be at `libs/windows/opus.dll` relative to
`main.py` (or next to the packaged `.exe`, same relative structure).

## Tray icon never turns green / stays "Not connected"

In order of likelihood:
1. **Room code mismatch** - check both `config/settings.yaml` files
   have the *exact* same `room_code` (it's case-sensitive).
2. **Wrong `signaling_url`** - should point at your VPS's IP, port
   8000, path `/ws`. Test it's reachable: `curl http://YOUR_VPS_IP:8000/health`
   should return `{"status":"ok"}` from each gaming PC.
3. **Firewall ports not open on the VPS** - TCP 8000 and UDP 40000
   both need to be open. Cloud providers often block ports by default
   in their security group / firewall UI, separate from the OS
   firewall.
4. **Symmetric NAT** - see docs/NETWORKING.md, switch to `mode: direct`.
5. **Windows Firewall blocking the app locally** - allow it under
   Private networks.

## Connected, but no audio / one-way audio

- Run `python main.py --list-devices` and confirm the device you
  expect is actually the default (or explicitly set
  `audio.input_device` / `audio.output_device` in config to force it).
- Check the log for `Encode failed` or `opus_decode failed` lines -
  usually means a corrupt/truncated packet, which is more likely on
  very lossy connections; the FEC settings in `codec` help but aren't
  magic above ~20-30% packet loss.
- Make sure `vad.enabled` isn't filtering out your voice - if you're
  quiet-spoken or your mic is far away, try `vad.aggressiveness: 0` or
  disable VAD entirely (`vad.enabled: false`) to rule it out.

## Choppy / robotic audio

- Increase `network.jitter_buffer_frames` (default 3 = 60ms of
  buffering). More frames = smoother audio but more delay. Try 5 if
  your connection is inconsistent.
- Increase `codec.expected_packet_loss_pct` if your connection drops
  packets often - this tells Opus's FEC to be more conservative.

## Game itself still lags after switching off Discord/WhatsApp

At that point the bottleneck likely isn't voice chat anymore - check
your actual internet bandwidth during a play session, and confirm
neither PC has other heavy uploads/downloads running (game updates,
cloud backups, etc.) competing for the same connection.

## "Room already has two peers connected"

Someone else (or a stale connection from a crash) is still holding a
slot in that room code on the signaling server. Either wait ~30s for
the stale WebSocket to time out, or just pick a different `room_code`
briefly and switch back.

## I only have one laptop and want to test anyway

Run two Duet Voice instances on the same machine, each with its own
config file and UDP port:

1. Copy `config/settings.yaml` to `config/settings.peer.yaml`.
2. Change `display_name` in the copy so the tray menus are easy to
  tell apart.
3. Change `network.local_udp_port` in the copy to a different free port
  such as `52223`.
4. Start the first instance normally.
5. Start the second instance with `python main.py --config config/settings.peer.yaml`.
6. Use the same `network.signaling_url` and `room_code` in both files.

That lets one laptop act as both peers for a connection test. If both
instances reach "Connected", the signaling path is working; the only
remaining variable is whether your audio devices can be shared by two
processes at once.

## Hotkeys don't do anything

The `keyboard` package needs to be installed (`pip install keyboard`,
already in requirements.txt) and on some Windows setups needs the app
run as Administrator to hook global keys. If that's not viable, use
the tray menu's Mute checkbox instead - it always works regardless of
hotkey permissions.
