"""
Ties every service together into one voice session:

    mic -> VAD gate -> Opus encode -> UDP send
    UDP recv -> jitter buffer -> Opus decode (inside jitter buffer) -> speakers

Connection setup (STUN + signaling + hole punch) happens in `connect()`;
`start_audio()` / `stop_audio()` can be toggled independently so the
tray icon can mute without tearing down the network link.
"""
from __future__ import annotations

import threading
import time
from typing import Optional
from urllib.parse import urlparse

from schemas.config_schema import AppConfig
from services.audio_service import MicCapture, SpeakerPlayback
from services.codec_service import OpusEncoder, OpusDecoder
from services.network_service import VoiceSocket
from services.signaling_client import PublicEndpoint, exchange_endpoints_sync
from services.vad_service import VoiceActivityDetector
from utils.jitter_buffer import JitterBuffer
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
        self._jitter: Optional[JitterBuffer] = None

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
        self._jitter = JitterBuffer(self._decoder, self.frame_size, net.jitter_buffer_frames)

        self._voice_socket.start_receiver(self._on_audio_received)
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
        if self._jitter:
            self._jitter.put(seq, payload)
        self._rx_packets += 1
        if self._rx_packets % 50 == 0:
            log.info("Received %d audio packets", self._rx_packets)

    def _keepalive_loop(self) -> None:
        seq = 0
        interval = self.config.network.keepalive_interval_s
        while not self._stop_flag.is_set():
            if self._voice_socket and self._peer_addrs:
                for peer_addr in self._peer_addrs:
                    self._voice_socket.send_keepalive(seq, peer_addr)
                seq += 1
            self._stop_flag.wait(interval)

    # ---- audio pipeline -----------------------------------------------------

    def start_audio(self) -> None:
        if self._audio_running:
            return
        audio_cfg = self.config.audio

        self._mic = MicCapture(
            audio_cfg.sample_rate, self.frame_size, audio_cfg.input_device, audio_cfg.mic_gain
        )
        self._speaker = SpeakerPlayback(
            audio_cfg.sample_rate, self.frame_size, audio_cfg.output_device,
            pull_frame_callback=self._jitter.get_next_pcm if self._jitter else None,
        )
        self._mic.start()
        self._speaker.start()

        self._audio_running = True
        self._sender_thread = threading.Thread(target=self._sender_loop, name="mic-sender", daemon=True)
        self._sender_thread.start()
        log.info("Audio pipeline started.")

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
        if self._speaker:
            self._speaker.stop()
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

    def disconnect(self) -> None:
        self._stop_flag.set()
        self.stop_audio()
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=2.0)
        if self._voice_socket:
            self._voice_socket.close()
        self._connected = False
        log.info("Disconnected.")
