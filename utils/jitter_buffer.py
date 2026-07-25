"""
Small jitter buffer: absorbs the fact that UDP packets don't arrive at
a perfectly even 20ms cadence. Holds a few frames before playback starts,
reorders out-of-order arrivals, and signals the caller when a frame is
missing so it can apply packet-loss concealment.

This is intentionally simple (dict + sliding expected-sequence pointer)
rather than a full RTP jitter buffer implementation - for a 2-person
voice link that's plenty.

Decoding is deliberately NOT done here. This class stores and sequences
raw Opus payloads; AudioPlayer (utils/audio_player.py) owns the Opus
decoder and the PLC-ceiling logic so the two concerns stay separate.
"""
from __future__ import annotations

import threading
from typing import Optional


class JitterBuffer:
    def __init__(self, target_frames: int = 5, max_buffered: int = 0,
                 max_consecutive_misses: int = 15):
        self._target_frames = target_frames
        # Defaults to 8× target so there is plenty of headroom without
        # unbounded memory use.
        self._max_buffered = max_buffered if max_buffered > 0 else max(25, target_frames * 8)
        # How many consecutive empty slots (~20ms each) to chase via PLC
        # before giving up and re-priming. Defaults to 15 (~300ms) to match
        # AudioPlayer's own PLC ceiling - see get_next_frame() for why this
        # matters.
        self._max_consecutive_misses = max_consecutive_misses

        self._lock = threading.Lock()
        self._packets: dict[int, bytes] = {}
        self._expected_seq: Optional[int] = None
        self._primed = False
        self._consecutive_misses = 0

    def put(self, seq: int, payload: bytes) -> None:
        with self._lock:
            if self._expected_seq is not None and seq < self._expected_seq:
                # Distinguish a genuinely late/duplicate packet from a
                # mute-resume or seq-wrap situation.  When the buffer has
                # drained completely and the gap is larger than max_buffered,
                # _expected_seq has been advancing purely through PLC while the
                # sender was silent (muted, VAD-gated, or wrapped at 65536).
                # Reset so the resumed stream re-primes cleanly instead of
                # having every incoming packet silently dropped.
                if not self._packets and (self._expected_seq - seq) > self._max_buffered:
                    self._reset_locked()
                    # fall through to store the packet below
                else:
                    return  # genuinely late / duplicate - discard
            self._packets[seq] = payload
            if len(self._packets) > self._max_buffered:
                oldest = min(self._packets)
                del self._packets[oldest]

    def get_next_frame(self) -> Optional[bytes]:
        """Return the next raw Opus payload, or None when the slot is missing
        (packet loss) or the buffer has not yet primed.  Never blocks.

        Every call - even ones that find nothing to play - used to advance
        `_expected_seq`, whether or not any packet actually arrived. During a
        deliberate silence (VAD gating / mute), that meant _expected_seq kept
        climbing at the full ~50/s output rate while the sender's sequence
        numbers stayed frozen. When speech resumed, the new seq numbers
        looked "behind" the drifted _expected_seq: too close to be treated
        as a stale-gap reset (that only fires once the gap exceeds
        max_buffered) but still `< _expected_seq`, so every resumed packet
        was silently discarded as a "duplicate" until the gap finally grew
        past max_buffered - explaining "packets sent but nothing heard"
        right after a pause. Capping consecutive misses stops the drift far
        earlier (~300ms) and re-primes cleanly on the next real packet.
        """
        with self._lock:
            if not self._primed:
                if len(self._packets) < self._target_frames:
                    return None  # still buffering - caller outputs silence
                self._primed = True
                self._expected_seq = min(self._packets)
                self._consecutive_misses = 0

            seq = self._expected_seq
            payload = self._packets.pop(seq, None)

            if payload is None:
                self._consecutive_misses += 1
                if self._consecutive_misses > self._max_consecutive_misses:
                    # The sender has gone quiet (mute/VAD/disconnect), not
                    # just dropped one packet - stop chasing a seq number
                    # that may never come and go back to idle so the next
                    # real packet re-primes fresh instead of being discarded.
                    self._reset_locked()
                    return None
            else:
                self._consecutive_misses = 0

            self._expected_seq = seq + 1
            return payload  # None → caller applies PLC or silence

    @property
    def primed(self) -> bool:
        """True once the initial buffering phase has completed."""
        return self._primed

    @property
    def buffered(self) -> int:
        """Current number of stored frames (useful for diagnostics)."""
        with self._lock:
            return len(self._packets)

    def _reset_locked(self) -> None:
        """Drop back to the unprimed state. Caller must hold `_lock`."""
        self._packets.clear()
        self._expected_seq = None
        self._primed = False
        self._consecutive_misses = 0

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()
