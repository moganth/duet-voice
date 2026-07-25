"""
Small jitter buffer: absorbs the fact that UDP packets don't arrive at
a perfectly even 20ms cadence. Holds a few frames before playback starts,
reorders out-of-order arrivals, and asks the Opus decoder for packet-loss
concealment when a frame never shows up.

This is intentionally simple (dict + sliding expected-sequence pointer)
rather than a full RTP jitter buffer implementation - for a 2-person
voice link that's plenty.
"""
from __future__ import annotations

import threading
from typing import Optional

from services.codec_service import OpusDecoder


class JitterBuffer:
    def __init__(self, decoder: OpusDecoder, frame_size: int, target_frames: int = 3,
                 max_buffered: int = 25):
        self._decoder = decoder
        self._frame_size = frame_size
        self._target_frames = target_frames
        self._max_buffered = max_buffered

        self._lock = threading.Lock()
        self._packets: dict[int, bytes] = {}
        self._expected_seq: Optional[int] = None
        self._primed = False

    def put(self, seq: int, payload: bytes) -> None:
        with self._lock:
            if self._expected_seq is not None and seq < self._expected_seq:
                return  # too late, already played past this point
            self._packets[seq] = payload
            if len(self._packets) > self._max_buffered:
                oldest = min(self._packets)
                del self._packets[oldest]

    def get_next_pcm(self) -> bytes:
        """Called once per playback tick (every frame_ms). Never blocks."""
        with self._lock:
            if not self._primed:
                if len(self._packets) < self._target_frames:
                    return b"\x00\x00" * self._frame_size
                self._primed = True
                self._expected_seq = min(self._packets)

            seq = self._expected_seq
            payload = self._packets.pop(seq, None)
            self._expected_seq = seq + 1

        if payload is not None:
            return self._decoder.decode(payload, self._frame_size)
        return self._decoder.decode(None, self._frame_size)  # PLC fill-in

    def reset(self) -> None:
        with self._lock:
            self._packets.clear()
            self._expected_seq = None
            self._primed = False
