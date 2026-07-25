"""
Raw UDP transport for voice packets, plain blocking sockets + one
receiver thread. No asyncio here on purpose: PortAudio callbacks and
the mic/speaker queues already run on their own native threads, and
mixing those with an asyncio event loop for the *steady-state* voice
path adds complexity for zero benefit at 2-peer scale. Asyncio is used
only briefly during connection setup (services/signaling_client.py),
on a completely separate throwaway event loop.

Wire format (all voice packets):
    byte 0      : packet type (0x01 = audio, 0x02 = keepalive)
    bytes 1-2   : sequence number, uint16 big-endian, wraps at 65536
    bytes 3+    : Opus payload (empty for keepalive)
"""
from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Callable, Optional

from utils.logger import get_logger

log = get_logger(__name__)

PKT_AUDIO = 0x01
PKT_KEEPALIVE = 0x02

_HEADER = struct.Struct("!BH")  # type, seq


def pack_audio(seq: int, opus_payload: bytes) -> bytes:
    return _HEADER.pack(PKT_AUDIO, seq % 65536) + opus_payload


def pack_keepalive(seq: int) -> bytes:
    return _HEADER.pack(PKT_KEEPALIVE, seq % 65536)


def unpack(packet: bytes) -> tuple[int, int, bytes]:
    pkt_type, seq = _HEADER.unpack_from(packet, 0)
    return pkt_type, seq, packet[_HEADER.size:]


class VoiceSocket:
    """One UDP socket, reused for STUN discovery, hole punching, and the
    voice session itself - reusing the same local port throughout is
    what keeps the NAT mapping stable."""

    def __init__(self, local_port: int):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", local_port))
        self._recv_thread: Optional[threading.Thread] = None
        self._running = False

    @property
    def local_port(self) -> int:
        return self._sock.getsockname()[1]

    def discover_public_endpoint(self, stun_addr: tuple[str, int],
                                  timeout: float = 5.0) -> tuple[str, int]:
        """Blocking STUN-lite request/response. Call before start_receiver()."""
        self._sock.settimeout(timeout)
        self._sock.sendto(b"STUN_REQ", stun_addr)
        data, _ = self._sock.recvfrom(256)
        self._sock.settimeout(None)
        if not data.startswith(b"STUN_OK "):
            raise RuntimeError(f"Unexpected STUN reply: {data!r}")
        _, ip, port = data.decode("ascii").split()
        return ip, int(port)

    @staticmethod
    def detect_local_ip(stun_addr: tuple[str, int]) -> str:
        """Return the local interface IP used to reach the relay."""
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(stun_addr)
            return probe.getsockname()[0]
        finally:
            probe.close()

    def punch(self, peer_addr: tuple[str, int], attempts: int = 15, interval_s: float = 0.1) -> None:
        """Fire a burst of keepalives at the peer's public endpoint to open
        our NAT's outbound mapping for their return traffic."""
        for i in range(attempts):
            self._sock.sendto(pack_keepalive(i), peer_addr)
            time.sleep(interval_s)

    def start_receiver(self, on_audio: Callable[[int, bytes], None],
                       on_keepalive: Optional[Callable[[], None]] = None) -> None:
        self._running = True

        def _loop():
            self._sock.settimeout(1.0)
            while self._running:
                try:
                    data, addr = self._sock.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if len(data) < _HEADER.size:
                    continue
                try:
                    pkt_type, seq, payload = unpack(data)
                except struct.error:
                    continue
                if pkt_type == PKT_AUDIO:
                    on_audio(seq, payload)
                elif pkt_type == PKT_KEEPALIVE and on_keepalive:
                    on_keepalive()

        self._recv_thread = threading.Thread(target=_loop, name="voice-udp-recv", daemon=True)
        self._recv_thread.start()

    def send_audio(self, seq: int, opus_payload: bytes, addr: tuple[str, int]) -> None:
        self._sock.sendto(pack_audio(seq, opus_payload), addr)

    def send_keepalive(self, seq: int, addr: tuple[str, int]) -> None:
        self._sock.sendto(pack_keepalive(seq), addr)

    def close(self) -> None:
        self._running = False
        if self._recv_thread:
            self._recv_thread.join(timeout=2.0)
        self._sock.close()
