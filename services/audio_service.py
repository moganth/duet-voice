"""
Microphone capture and speaker playback via sounddevice (PortAudio).
Both run as callback-driven streams on their own native threads - we
never block them, we only push/pop from thread-safe queues.
"""
from __future__ import annotations

import queue
from typing import Optional

import numpy as np
import sounddevice as sd

from utils.logger import get_logger

log = get_logger(__name__)


def list_devices() -> str:
    return str(sd.query_devices())


def get_audio_devices() -> list[str]:
    """Return audio device names from the preferred host API, queried fresh
    each call so hot-plugged devices appear immediately.

    On Windows, restricts to WASAPI devices only.  This eliminates the
    legacy virtual entries (Microsoft Sound Mapper, Primary Sound Driver,
    MME/DirectSound duplicates) that PortAudio also enumerates but that
    are not real audio hardware."""
    import sys

    # On Windows, use WASAPI - it lists real audio endpoints only and skips
    # the legacy MME/DirectSound virtual mappers and driver duplicates.
    # On Linux / macOS let all devices through (no virtual-mapper problem).
    target_hostapi: int | None = None
    if sys.platform == "win32":
        for i, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in api["name"]:
                target_hostapi = i
                break

    seen: set[str] = set()
    names: list[str] = []
    for device in sd.query_devices():
        if target_hostapi is not None and device["hostapi"] != target_hostapi:
            continue
        name = device["name"]
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


class MicCapture:
    """Pulls fixed-size int16 mono frames from the microphone."""

    def __init__(self, sample_rate: int, frame_size: int, device: Optional[str] = None,
                 gain: float = 1.0):
        self._frame_size = frame_size
        self._gain = gain
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=50)
        self._stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=frame_size,
            device=device,
            callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("Mic input status: %s", status)
        data = indata[:, 0]
        if self._gain != 1.0:
            data = np.clip(data.astype(np.int32) * self._gain, -32768, 32767).astype(np.int16)
        try:
            self._queue.put_nowait(data.tobytes())
        except queue.Full:
            # Drop the oldest frame rather than let latency creep up.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(data.tobytes())
            except queue.Empty:
                pass

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()

    def read_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def gain(self) -> float:
        return self._gain

    @gain.setter
    def gain(self, value: float) -> None:
        """Update mic amplification live - safe to call from any thread."""
        self._gain = float(value)


class SpeakerPlayback:
    """Pushes decoded int16 mono frames out to the speakers on demand."""

    def __init__(self, sample_rate: int, frame_size: int, device: Optional[str] = None,
                 pull_frame_callback=None):
        """
        pull_frame_callback: zero-arg callable returning `frame_size` int16
        samples as bytes (typically JitterBuffer.get_next_pcm). Called from
        PortAudio's realtime thread, so it must never block.
        """
        self._frame_size = frame_size
        self._pull = pull_frame_callback
        self._silence = b"\x00\x00" * frame_size
        self._stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            blocksize=frame_size,
            device=device,
            callback=self._callback,
        )

    def _callback(self, outdata, frames, time_info, status):
        if status:
            log.debug("Speaker output status: %s", status)
        pcm = self._pull() if self._pull else None
        if not pcm:
            pcm = self._silence
        outdata[:, 0] = np.frombuffer(pcm, dtype=np.int16)

    def start(self):
        self._stream.start()

    def stop(self):
        self._stream.stop()
        self._stream.close()
