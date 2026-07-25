"""
Sits between JitterBuffer and SpeakerPlayback, owning the Opus decoder
and the packet-loss concealment strategy.

Separating this from the jitter buffer means:
  - JitterBuffer stays codec-agnostic and fully unit-testable without Opus.
  - PLC policy (ceiling, silence fallback) lives in one place.
  - The timestamp of the last real packet is tracked here for use by
    VoiceSession's health monitoring without polluting the buffer.

Called from the PortAudio realtime callback thread - must never block.
"""
from __future__ import annotations

import time

from services.codec_service import OpusDecoder
from utils.jitter_buffer import JitterBuffer
from utils.logger import get_logger

log = get_logger(__name__)

# After this many consecutive PLC frames (≈300 ms at 20 ms/frame) switch to
# clean silence.  Opus PLC degrades audibly after ~200 ms of continuous
# concealment; the ceiling keeps artefacts inaudible while still covering
# short burst losses.
_PLC_CEILING = 15


class AudioPlayer:
    """Decoding layer wrapping a JitterBuffer plus an OpusDecoder.

    put()          — called by the UDP receive thread to enqueue compressed frames.
    get_next_pcm() — called every frame_ms by the PortAudio output callback.
    """

    def __init__(self, decoder: OpusDecoder, frame_size: int,
                 target_frames: int, max_buffered: int = 0):
        self._decoder = decoder
        self._frame_size = frame_size
        self._silence = b"\x00\x00" * frame_size
        self._buffer = JitterBuffer(target_frames, max_buffered)
        self._consecutive_plc = 0
        self._last_real_ts: float = 0.0

    # ---------------------------------------------------------------- feeder

    def put(self, seq: int, payload: bytes) -> None:
        """Enqueue a compressed audio frame. Called from the UDP receive thread."""
        self._buffer.put(seq, payload)
        self._last_real_ts = time.monotonic()

    # --------------------------------------------------------------- player

    def get_next_pcm(self) -> bytes:
        """Decode and return the next PCM frame. Never blocks."""
        frame = self._buffer.get_next_frame()

        if frame is not None:
            # Real packet: reset the PLC run counter and decode normally.
            self._consecutive_plc = 0
            return self._decoder.decode(frame, self._frame_size)

        if not self._buffer.primed:
            # Still in the initial buffering window; output clean silence so
            # the speaker stream starts without clicks or artefacts.
            return self._silence

        # Packet loss: use Opus PLC up to the ceiling, then fall back to
        # silence.  This prevents the audible noise that accumulates when PLC
        # is called hundreds of times in a row (muted peer, dropped connection).
        self._consecutive_plc += 1
        if self._consecutive_plc > _PLC_CEILING:
            return self._silence
        return self._decoder.decode(None, self._frame_size)

    # ----------------------------------------------------------------- stats

    @property
    def consecutive_plc(self) -> int:
        """Number of consecutive PLC frames in the current run (0 when flowing)."""
        return self._consecutive_plc

    @property
    def seconds_since_last_packet(self) -> float:
        """Elapsed seconds since the last real (non-PLC) frame arrived."""
        if self._last_real_ts == 0.0:
            return float("inf")
        return time.monotonic() - self._last_real_ts

    # --------------------------------------------------------------- control

    def reset(self) -> None:
        """Clear buffer and reset PLC state."""
        self._buffer.reset()
        self._consecutive_plc = 0
