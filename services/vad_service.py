"""
Voice-activity detection so we only send UDP packets while someone is
actually talking. This is most of why this app is lighter than Discord
in practice - Discord keeps a stream open; we go quiet between sentences.
"""
from __future__ import annotations

import time
import webrtcvad


class VoiceActivityDetector:
    def __init__(self, aggressiveness: int = 2, sample_rate: int = 48000, hangover_ms: int = 300):
        # webrtcvad only accepts 8000/16000/32000/48000 Hz and 10/20/30ms frames.
        self._vad = webrtcvad.Vad(aggressiveness)
        self._sample_rate = sample_rate
        self._hangover_s = hangover_ms / 1000.0
        self._last_speech_ts = 0.0

    def is_speech_frame(self, pcm_int16_bytes: bytes) -> bool:
        try:
            speaking = self._vad.is_speech(pcm_int16_bytes, self._sample_rate)
        except Exception:
            # webrtcvad throws on frame sizes it doesn't like - fail open
            # (treat as speech) rather than silently dropping audio.
            return True

        now = time.monotonic()
        if speaking:
            self._last_speech_ts = now
            return True

        # Hangover: keep transmitting for a short window after speech ends
        # so words don't get clipped mid-syllable.
        return (now - self._last_speech_ts) < self._hangover_s
