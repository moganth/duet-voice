"""
Ties every service together into one voice session:

    mic -> VAD gate -> Opus encode -> UDP send
    UDP recv -> jitter buffer -> AudioPlayer (Opus decode + PLC ceiling) -> speakers

Connection setup (STUN + signaling + hole punch) happens in `connect()`;
`start_audio()` / `stop_audio()` can be toggled independently so the
tray icon can mute without tearing down the network link.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from schemas.config_schema import AppConfig
from services.audio_service import MicCapture, SpeakerPlayback
from services.codec_service import OpusEncoder, OpusDecoder
from services.network_service import VoiceSocket
from services.signaling_client import PublicEndpoint, exchange_endpoints_sync
from services.vad_service import VoiceActivityDetector
from utils.audio_player import AudioPlayer
from utils.logger import get_logger

log = get_logger(__name__)


class VoiceSession:
    def __init__(self, config: AppConfig):
        self.config = config
        self.frame_size = config.frame_size_samples

        self._voice_socket: Optional[VoiceSocket] = None
        self._peer_addrs: list[tuple[str, int]] = []

        self._encoder: Optional[OpusEncoder] = None
        self._decoder: Optional[OpusDecoder] = None
        self._player: Optional[AudioPlayer] = None

        self._mic: Optional[MicCapture] = None
        self._speaker: Optional[SpeakerPlayback] = None

        self._vad = VoiceActivityDetector(
            aggressiveness=config.vad.aggressiveness,
            sample_rate=config.audio.sample_rate,
            hangover_ms=config.vad.hangover_ms,
        )

        self._send_seq = 0
        self._tx_packets = 0
        self._rx_packets = 0
        self._muted = False
        self._connected = False
        self._audio_running = False

        self._sender_thread: Optional[threading.Thread] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        # Connection health tracking.
        self._connect_ts: float = 0.0
        self._last_any_rx_ts: float = 0.0
        self._link_up: bool = False
        # Fired by the keepalive loop whenever is_audio_flowing changes state.
        # Set by TrayApp so the icon updates without polling.
        self.on_state_change: Optional[Callable[[], None]] = None
        # Fired after start_audio() has to fall back to system-default
        # devices because the configured ones failed to open (e.g. a
        # Bluetooth headset whose mic and speaker profiles won't open at
        # the same time). Set by TrayApp so it can persist the fallback
        # instead of retrying - and failing - the same broken combo on
        # every future launch.
        self.on_device_fallback: Optional[Callable[[], None]] = None

    # ---- connection setup -------------------------------------------------

    def connect(self) -> None:
        net = self.config.network
        self._voice_socket = VoiceSocket(net.local_udp_port)

        if net.mode == "direct":
            if not net.peer_ip or not net.peer_port:
                raise ValueError("network.peer_ip / peer_port must be set for 'direct' mode.")
            self._peer_addrs = [(net.peer_ip, net.peer_port)]
            log.info("Direct mode: peer at %s:%s", *self._peer_addrs[0])
        else:
            self._validate_signaling_url(net.signaling_url)
            stun_host = self._stun_host_from_signaling_url(net.signaling_url)
            log.info("Discovering public endpoint via %s ...", stun_host)
            my_ip, my_port = self._voice_socket.discover_public_endpoint((stun_host, 40000))
            my_local_ip = self._voice_socket.detect_local_ip((stun_host, 40000))
            log.info("Our public endpoint: %s:%s", my_ip, my_port)

            peer = exchange_endpoints_sync(
                net.signaling_url, net.room_code,
                PublicEndpoint(
                    my_ip,
                    my_port,
                    my_local_ip,
                    self._voice_socket.local_port,
                ),
                self.config.display_name,
            )
            self._peer_addrs = [(peer.ip, peer.port)]
            if peer.local_ip and peer.local_port:
                self._peer_addrs.append((peer.local_ip, peer.local_port))
            self._peer_addrs = list(dict.fromkeys(self._peer_addrs))
            log.info("Peer public endpoint: %s:%s - punching...", peer.ip, peer.port)
            if peer.local_ip and peer.local_port:
                log.info("Peer local endpoint: %s:%s", peer.local_ip, peer.local_port)
            for peer_addr in self._peer_addrs:
                self._voice_socket.punch(peer_addr)

        self._encoder = OpusEncoder(
            self.config.audio.sample_rate, self.config.audio.channels,
            self.config.codec.bitrate_bps, self.config.codec.vbr,
            self.config.codec.fec, self.config.codec.expected_packet_loss_pct,
        )
        self._decoder = OpusDecoder(self.config.audio.sample_rate, self.config.audio.channels)
        self._player = AudioPlayer(self._decoder, self.frame_size, net.jitter_buffer_frames)

        self._connect_ts = time.monotonic()
        self._last_any_rx_ts = 0.0
        self._link_up = False
        self._voice_socket.start_receiver(self._on_audio_received,
                                          on_keepalive=self._on_keepalive_received)
        self._connected = True

        self._stop_flag.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop, name="keepalive", daemon=True
        )
        self._keepalive_thread.start()

    @staticmethod
    def _stun_host_from_signaling_url(signaling_url: str) -> str:
        # ws://1.2.3.4:8000/ws -> 1.2.3.4  (STUN-lite listens on UDP/40000
        # on the same host as the signaling server; see signaling-server/)
        parsed = urlparse(signaling_url)
        return parsed.hostname or signaling_url

    @staticmethod
    def _validate_signaling_url(signaling_url: str) -> None:
        parsed = urlparse(signaling_url)
        host = parsed.hostname or ""
        if host.upper().startswith("YOUR_"):
            raise ValueError(
                "network.signaling_url still uses the example placeholder host. "
                "Set it to the real signaling server address, for example "
                "ws://127.0.0.1:8000/ws for a local test run or "
                "ws://<your-server-ip>:8000/ws for the VPS."
            )

    def _on_audio_received(self, seq: int, payload: bytes) -> None:
        self._last_any_rx_ts = time.monotonic()
        if self._player:
            self._player.put(seq, payload)
        self._rx_packets += 1
        if self._rx_packets % 50 == 0:
            log.info("Received %d audio packets", self._rx_packets)

    def _on_keepalive_received(self) -> None:
        """Called when a UDP keepalive arrives - proves the P2P link is alive."""
        self._last_any_rx_ts = time.monotonic()

    def _keepalive_loop(self) -> None:
        seq = 0
        interval = self.config.network.keepalive_interval_s
        while not self._stop_flag.is_set():
            if self._voice_socket and self._peer_addrs:
                for peer_addr in self._peer_addrs:
                    self._voice_socket.send_keepalive(seq, peer_addr)
                seq += 1

            # Health: fire on_state_change when link up/down status flips.
            now_up = self.is_audio_flowing
            if now_up != self._link_up:
                self._link_up = now_up
                if not now_up:
                    secs = (time.monotonic() - self._last_any_rx_ts
                            if self._last_any_rx_ts else float("inf"))
                    log.warning(
                        "No packets received for %.0f s — "
                        "connection may be lost or NAT punch failed.", secs
                    )
                if self.on_state_change:
                    self.on_state_change()

            self._stop_flag.wait(interval)

    # ---- audio pipeline -----------------------------------------------------

    def start_audio(self) -> None:
        if self._audio_running:
            return
        audio_cfg = self.config.audio

        try:
            self._open_streams(audio_cfg.input_device, audio_cfg.output_device)
        except Exception as exc:
            if audio_cfg.input_device is None and audio_cfg.output_device is None:
                raise  # already the system default - nothing safer left to fall back to
            log.error(
                "Failed to open configured audio devices (mic=%s speaker=%s): %s - "
                "falling back to system default devices so the call isn't left silent.",
                audio_cfg.input_device or "(system default)",
                audio_cfg.output_device or "(system default)", exc,
            )
            self._open_streams(None, None)
            audio_cfg.input_device = None
            audio_cfg.output_device = None
            if self.on_device_fallback:
                self.on_device_fallback()

        self._finish_starting()

    def _finish_starting(self) -> None:
        self._audio_running = True
        self._sender_thread = threading.Thread(target=self._sender_loop, name="mic-sender", daemon=True)
        self._sender_thread.start()
        log.info("Audio pipeline started.")

    def _open_streams(self, input_device: Optional[str], output_device: Optional[str]) -> None:
        """Open the mic + speaker PortAudio streams for the given devices,
        cleaning up any half-opened stream if either one fails."""
        try:
            self._mic = MicCapture(
                self.config.audio.sample_rate, self.frame_size, input_device, self.config.audio.mic_gain
            )
            self._speaker = SpeakerPlayback(
                self.config.audio.sample_rate, self.frame_size, output_device,
                pull_frame_callback=self._player.get_next_pcm if self._player else None,
            )
            self._mic.start()
            self._speaker.start()
        except Exception:
            # Don't leave a half-open stream behind on failure (e.g. an
            # ambiguous/unplugged device) - without this, self._mic could
            # stay non-None forever, blocking any future start_audio() retry.
            if self._mic:
                try:
                    self._mic.stop()
                except Exception:
                    pass
                self._mic = None
            if self._speaker:
                try:
                    self._speaker.stop()
                except Exception:
                    pass
                self._speaker = None
            raise

    def _sender_loop(self) -> None:
        while self._audio_running:
            frame = self._mic.read_frame(timeout=1.0) if self._mic else None
            if frame is None:
                continue
            if self._muted:
                continue

            speaking = True
            if self.config.vad.enabled:
                speaking = self._vad.is_speech_frame(frame)
            if not speaking:
                continue

            try:
                payload = self._encoder.encode(frame, self.frame_size)
            except RuntimeError as exc:
                log.warning("Encode failed: %s", exc)
                continue

            if self._voice_socket and self._peer_addrs:
                for peer_addr in self._peer_addrs:
                    self._voice_socket.send_audio(self._send_seq, payload, peer_addr)
                self._send_seq += 1
                self._tx_packets += 1
                if self._tx_packets % 50 == 0:
                    log.info("Sent %d audio packets", self._tx_packets)

    def stop_audio(self) -> None:
        self._audio_running = False
        if self._sender_thread:
            self._sender_thread.join(timeout=2.0)
        if self._mic:
            self._mic.stop()
            self._mic = None
        if self._speaker:
            self._speaker.stop()
            self._speaker = None
        log.info("Audio pipeline stopped.")

    # ---- controls -----------------------------------------------------------

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        log.info("Mic %s", "muted" if muted else "unmuted")

    def toggle_mute(self) -> bool:
        self.set_muted(not self._muted)
        return self._muted

    @property
    def is_muted(self) -> bool:
        return self._muted

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_audio_flowing(self) -> bool:
        """True when the P2P link is alive (audio or keepalives arriving recently).
        Returns True during the 15 s grace period after connecting so the icon
        does not flash a warning before the first keepalive has had time to arrive.
        Becomes False after 15 s with zero received packets — connection likely lost."""
        if not self._connected:
            return False
        elapsed = time.monotonic() - self._connect_ts
        if elapsed < 15.0:
            return True
        if self._last_any_rx_ts == 0.0:
            return False
        return (time.monotonic() - self._last_any_rx_ts) < 15.0

    @property
    def is_audio_running(self) -> bool:
        """True when the microphone and speaker PortAudio streams are active.
        Used to guard PortAudio re-initialisation in the device monitor."""
        return self._audio_running

    @property
    def current_input_device(self) -> Optional[str]:
        """Currently configured microphone device name (None = system default)."""
        return self.config.audio.input_device

    @property
    def current_output_device(self) -> Optional[str]:
        """Currently configured speaker device name (None = system default)."""
        return self.config.audio.output_device

    def switch_devices(self, input_device: Optional[str], output_device: Optional[str]) -> None:
        """Hot-swap the input and/or output stream to new devices without
        disconnecting the network link. Pass None for the system default.

        If the new selection fails to open (e.g. it was just unplugged, or
        two BlueTooth profiles it needs conflict), rolls back to the
        previous devices so the call keeps working instead of being left
        silently dead."""
        was_running = self._audio_running
        previous_input = self.config.audio.input_device
        previous_output = self.config.audio.output_device

        if was_running:
            self.stop_audio()
            if self._player:
                self._player.reset()

        self.config.audio.input_device = input_device
        self.config.audio.output_device = output_device

        if not was_running:
            log.info(
                "Audio devices set to: mic=%s speaker=%s (will apply on next connect)",
                input_device or "(system default)", output_device or "(system default)",
            )
            return

        try:
            self._open_streams(input_device, output_device)
            self._finish_starting()
            log.info(
                "Audio devices switched to: mic=%s speaker=%s",
                input_device or "(system default)", output_device or "(system default)",
            )
        except Exception as exc:
            log.error(
                "Failed to switch devices (mic=%s speaker=%s): %s - reverting to previous devices",
                input_device or "(system default)", output_device or "(system default)", exc,
            )
            self.config.audio.input_device = previous_input
            self.config.audio.output_device = previous_output
            try:
                self._open_streams(previous_input, previous_output)
                self._finish_starting()
                log.info(
                    "Reverted to previous audio devices: mic=%s speaker=%s",
                    previous_input or "(system default)", previous_output or "(system default)",
                )
            except Exception as exc2:
                log.error("Rollback to previous devices also failed: %s - audio pipeline is stopped", exc2)
            raise

    def set_mic_gain(self, gain: float) -> None:
        """Update microphone gain live without restarting the audio stream."""
        self.config.audio.mic_gain = gain
        if self._mic:
            self._mic.gain = gain
        log.info("Mic gain set to %.1f×", gain)

    def disconnect(self) -> None:
        self._stop_flag.set()
        self.stop_audio()
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=2.0)
        if self._voice_socket:
            self._voice_socket.close()
        if self._player:
            self._player.reset()
        self._connected = False
        log.info("Disconnected.")
