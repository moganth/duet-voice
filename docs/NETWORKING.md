# Networking

## The problem this solves

For two PCs behind home routers to send UDP directly to each other,
each router's NAT needs to be tricked into thinking the connection was
initiated locally. That's "hole punching," and it's what `mode:
signaling` does automatically. It fails on some networks (see below),
so there's also a manual `mode: direct` fallback.

## Mode: `signaling` (default, automatic)

1. Your app sends a UDP packet to the relay server's STUN-lite port
   (40000). The server sees which public IP:port that packet actually
   came from (your router's NAT mapping) and replies with it.
2. Your app connects to the relay's WebSocket, joins a "room" keyed by
   `room_code`, and sends that public IP:port.
3. When your girlfriend's PC does the same in the same room, the relay
   forwards each side's public IP:port to the other.
4. Both apps now fire a burst of UDP packets at each other's public
   endpoint at roughly the same time - each router sees "outbound
   traffic to X" and lets return traffic from X back in. Voice now
   flows directly between the two PCs; the relay is no longer involved.

This works behind **full-cone** and **restricted-cone NAT**, which
covers most home routers (including most ISP-provided ones and most
Wi-Fi routers in default configuration).

### When it won't work: symmetric NAT

Symmetric NAT assigns a *different* public port for every destination
you talk to, which breaks the "both sides guess the same port" trick
hole punching relies on. This shows up on:
- Some mobile carrier networks / hotspots
- Some corporate or campus networks
- CGNAT (common with some ISPs, especially outside the US)

If you wait a minute after clicking Connect and the tray still says
"Not connected," this is the most likely cause. Use `mode: direct`
instead (below).

## Mode: `direct` (manual, always works if you can port-forward)

One of you forwards a UDP port on your router to your PC, and you tell
each other your public IP and that port directly.

```yaml
network:
  mode: direct
  local_udp_port: 52222   # the port you'll forward
  peer_ip: "203.0.113.5"   # the OTHER person's public IP
  peer_port: 52222         # the OTHER person's forwarded port
```

Only the person being connected *to* needs to forward a port - if
you're the one with `peer_ip` set to their address, they're the one
who needs port forwarding on their router (look up your router model +
"port forwarding UDP"). The other person can leave their router alone.

To find your own public IP: search "what is my ip" or visit
`https://ifconfig.me`.

Trade-off: your public IP can change (most home connections aren't
static), so this may need re-checking occasionally. `mode: signaling`
doesn't have this problem since it re-discovers your endpoint on every
connect.

## Firewall notes (Windows)

Windows Firewall may prompt the first time `main.py` (or the packaged
`.exe`) tries to bind a UDP socket. Allow it on **Private networks** at
minimum. If you don't get a prompt and connections still fail, check
Windows Defender Firewall → Allow an app manually.

## Bandwidth expectations

At the default `bitrate_bps: 24000`, each direction uses roughly
3 KB/s (24 kbps) while talking, effectively 0 while silent thanks to
VAD. That's a rounding error next to what the game itself uses -
this was never going to be your bottleneck once you're off Discord/WhatsApp.
