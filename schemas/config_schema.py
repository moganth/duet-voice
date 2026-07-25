"""
Pydantic models describing the app's config file (config/settings.yaml).
Keeping this separate means main.py never has to guess what keys exist —
if the YAML is malformed or missing a field, this raises a clear error
at startup instead of failing weirdly mid-call.
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


class AudioConfig(BaseModel):
    sample_rate: int = 48000          # Opus works natively at 8/12/16/24/48 kHz
    channels: int = 1                 # mono is plenty for voice, halves bandwidth
    frame_ms: int = 20                # Opus frame size in ms (10/20/40 typical)
    input_device: Optional[str] = None    # None = system default mic
    output_device: Optional[str] = None   # None = system default speakers
    mic_gain: float = 1.0


class CodecConfig(BaseModel):
    bitrate_bps: int = 24000          # 16-32 kbps is transparent for voice
    vbr: bool = True
    fec: bool = True                  # in-band forward error correction
    expected_packet_loss_pct: int = 15


class VadConfig(BaseModel):
    enabled: bool = True
    aggressiveness: int = Field(default=2, ge=0, le=3)  # 0=lenient..3=aggressive
    hangover_ms: int = 300            # keep sending briefly after speech stops


class NetworkConfig(BaseModel):
    mode: Literal["direct", "signaling"] = "signaling"
    local_udp_port: int = 52222
    # direct mode: fill in the peer's public IP/port yourself (port-forward required)
    peer_ip: Optional[str] = None
    peer_port: Optional[int] = None
    # signaling mode: both peers point at the same relay server + room code
    signaling_url: str = "ws://YOUR_SERVER_IP:8000/ws"
    room_code: str = "changeme-room"
    jitter_buffer_frames: int = 3     # ~60ms at 20ms frames
    keepalive_interval_s: float = 5.0


class HotkeyConfig(BaseModel):
    push_to_talk: Optional[str] = None   # e.g. "ctrl+alt+space", None = voice-activity mode
    mute_toggle: str = "ctrl+alt+m"


class AppConfig(BaseModel):
    display_name: str = "Player"
    audio: AudioConfig = AudioConfig()
    codec: CodecConfig = CodecConfig()
    vad: VadConfig = VadConfig()
    network: NetworkConfig = NetworkConfig()
    hotkeys: HotkeyConfig = HotkeyConfig()
    log_level: str = "INFO"

    @property
    def frame_size_samples(self) -> int:
        return int(self.audio.sample_rate * self.audio.frame_ms / 1000)
