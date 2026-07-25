# Architecture

## Audio path, end to end

```
   YOUR PC                                          PEER'S PC
┌─────────────┐                                   ┌─────────────┐
│  Microphone │                                   │   Speakers  │
└──────┬──────┘                                   └──────▲──────┘
       │ raw PCM (int16, 48kHz, 20ms frames)              │ decoded PCM
       ▼                                                   │
┌─────────────┐    speaking?    ┌─────────────┐    ┌───────┴──────┐
│ MicCapture  ├────────────────►│ VAD gate    │    │ JitterBuffer │
│(audio_service)│  yes only     │(vad_service)│    │(reorder+PLC) │
└─────────────┘                 └──────┬──────┘    └───────▲──────┘
                                        ▼                    │
                                 ┌─────────────┐      ┌──────┴──────┐
                                 │ OpusEncoder │      │ OpusDecoder │
                                 │(codec_service)│    │(codec_service)│
                                 └──────┬──────┘      └──────▲──────┘
                                        ▼                     │
                                 ┌─────────────┐       ┌──────┴──────┐
                                 │ VoiceSocket ├──UDP──►│ VoiceSocket │
                                 │(network_service)│    │(network_service)│
                                 └─────────────┘       └─────────────┘
```

Everything above `VoiceSocket` on the sending side runs on a dedicated
`mic-sender` thread (`handlers/voice_handler.py::_sender_loop`).
Everything below `VoiceSocket` on the receiving side runs on a
dedicated `voice-udp-recv` thread. Playback is pulled by PortAudio's
own realtime thread every 20ms via `JitterBuffer.get_next_pcm()` -
that function is the one piece of code in the whole app that must
never block, which is why the jitter buffer is a plain dict behind a
lock instead of anything fancier.

## Folder-by-folder

- **`main.py`** - entry point. Loads config, sets up logging, wires
  hotkeys, starts the tray icon, optionally auto-connects.
- **`schemas/config_schema.py`** - pydantic models for
  `config/settings.yaml`. If you add a config option, add it here
  first; everything else reads from the validated `AppConfig` object.
- **`services/`** - stateless-ish building blocks, each independently
  testable:
  - `audio_service.py` - sounddevice mic capture / speaker playback
  - `codec_service.py` - Opus encode/decode (custom ctypes wrapper,
    see docs/SETUP.md for why)
  - `vad_service.py` - webrtcvad wrapper with a "hangover" window so
    words don't get clipped
  - `network_service.py` - UDP packet framing + `VoiceSocket` (STUN
    request, hole punch, send/receive)
  - `signaling_client.py` - WebSocket client for the relay server
- **`handlers/`** - orchestration layer that wires services together
  into behavior:
  - `voice_handler.py` - `VoiceSession`: connect/disconnect, the
    sender loop, mute state
  - `tray_handler.py` - the system tray icon and its menu
- **`utils/`**
  - `jitter_buffer.py` - reorders packets, fills gaps with Opus PLC
  - `logger.py` - rotating file logger (useful once this runs
    invisibly in the background - see docs/TROUBLESHOOTING.md for
    where the log file lives)

## Packet format

Every UDP packet is `[1 byte type][2 byte seq, big-endian][opus payload]`.
Type `0x01` = audio, `0x02` = keepalive (empty payload, just keeps the
NAT mapping open during silence). See `services/network_service.py`
for the exact struct format.

## Why raw UDP instead of a library like aiortc/WebRTC

WebRTC gives you encryption and better NAT traversal (ICE/TURN) out of
the box, at the cost of a much heavier dependency footprint and a
more complex connection-setup flow. For exactly two people who both
know each other's rough network setup, plain UDP + a tiny STUN-lite +
relay-for-handshake gets 90% of the benefit for a fraction of the
weight. If you outgrow this (want it to work reliably on any network,
including symmetric NAT, want built-in encryption) - swapping in
`aiortc` for the connection layer while keeping the same audio
pipeline is the natural next step.

## Adding encryption (not implemented, but the path is short)

The cleanest option is `pynacl` (libsodium bindings):
1. Both peers derive a shared key from the room code (or exchange one
   out-of-band).
2. In `VoiceSocket.send_audio`, encrypt the Opus payload with
   `nacl.secret.SecretBox` before sending.
3. In the receive callback, decrypt before handing to `JitterBuffer.put`.

That's it - about 15 lines across `network_service.py` and
`voice_handler.py`. Left out of v1 to keep the codebase small for a
private link between two people.
