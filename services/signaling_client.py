"""
Client side of the optional signaling-assisted connection mode.
Runs on its own short-lived asyncio event loop, separate from the
voice socket's plain-thread world (see network_service.py) - this
module is only alive during connection setup.

Flow:
 1. voice_handler already ran VoiceSocket.discover_public_endpoint()
    (STUN-lite via the relay) to learn our own public ip:port.
 2. connect_and_exchange() opens a WebSocket to the relay, joins
    `room_code`, sends our public ip:port, and waits for the relay to
    forward the other peer's public ip:port once they've joined too.
 3. voice_handler then calls VoiceSocket.punch() on both sides at
    roughly the same time to open the NAT mapping.

This only works reliably behind full-cone / restricted-cone NAT, which
covers most home routers. Symmetric NAT (common on some mobile/CGNAT
connections) won't punch through - fall back to "direct" mode with
port forwarding in that case. See docs/NETWORKING.md.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import websockets

from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class PublicEndpoint:
    ip: str
    port: int


async def _exchange(signaling_url: str, room_code: str, my_endpoint: PublicEndpoint,
                     display_name: str, timeout: float) -> PublicEndpoint:
    url = f"{signaling_url.rstrip('/')}/{room_code}"
    async with websockets.connect(url, ping_interval=20) as ws:
        await ws.send(json.dumps({
            "type": "endpoint",
            "ip": my_endpoint.ip,
            "port": my_endpoint.port,
            "name": display_name,
        }))
        log.info("Waiting for peer to join room '%s'...", room_code)
        async for raw in _with_timeout(ws, timeout):
            msg = json.loads(raw)
            if msg.get("type") == "peer_endpoint":
                return PublicEndpoint(msg["ip"], msg["port"])
            if msg.get("type") == "room_full_error":
                raise RuntimeError("That room code already has two peers connected.")
        raise ConnectionError("Signaling connection closed before peer joined.")


async def _with_timeout(ws, timeout: float):
    while True:
        yield await asyncio.wait_for(ws.recv(), timeout=timeout)


def exchange_endpoints_sync(signaling_url: str, room_code: str, my_endpoint: PublicEndpoint,
                             display_name: str, timeout: float = 120.0) -> PublicEndpoint:
    """Blocks (spinning up its own event loop) until the peer's public
    endpoint arrives from the relay, or `timeout` seconds pass."""
    return asyncio.run(_exchange(signaling_url, room_code, my_endpoint, display_name, timeout))
