"""
Microphone capture and speaker playback via sounddevice (PortAudio).
Both run as callback-driven streams on their own native threads - we
never block them, we only push/pop from thread-safe queues.
"""
from __future__ import annotations

import queue
import sys
import threading
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from utils.logger import get_logger

log = get_logger(__name__)

# Serializes PortAudio (re)initialisation against stream construction.
# Without this lock, the tray's background hot-plug monitor could call
# sd._terminate()/sd._initialize() at the exact moment a MicCapture or
# SpeakerPlayback stream is being opened elsewhere, tearing down PortAudio
# state mid-construction and crashing (or corrupting) the audio pipeline.
_portaudio_lock = threading.Lock()


def list_devices() -> str:
    return str(sd.query_devices())


def _preferred_hostapi_index() -> Optional[int]:
    """On Windows, prefer WASAPI - it lists real audio endpoints only and
    skips the legacy MME/DirectSound/WDM-KS duplicates PortAudio also
    exposes for the exact same physical hardware."""
    if sys.platform != "win32":
        return None
    for i, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return i
    return None


def get_audio_devices() -> list[str]:
    """Return audio device names from the preferred host API, queried fresh
    each call so hot-plugged devices appear immediately.

    On Windows, restricts to WASAPI devices only.  This eliminates the
    legacy virtual entries (Microsoft Sound Mapper, Primary Sound Driver,
    MME/DirectSound duplicates) that PortAudio also enumerates but that
    are not real audio hardware."""
    target_hostapi = _preferred_hostapi_index()

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


def reinit_portaudio_if_safe(is_safe: Callable[[], bool]) -> bool:
    """Force PortAudio to re-enumerate devices (picks up hot-plug/unplug
    events), but only while `is_safe()` still holds once the lock is
    acquired. Re-checking under the lock closes the race where a stream
    starts opening in the small window between the caller's own check and
    actually taking the lock. Returns True if the reinit actually ran."""
    with _portaudio_lock:
        if not is_safe():
            return False
        sd._terminate()
        sd._initialize()
        return True


def _resolve_device_index(name: Optional[str], kind: str) -> Optional[int]:
    """Resolve a device name to a single unambiguous PortAudio index.

    Some devices (notably Bluetooth/USB headsets) are exposed identically
    under several PortAudio host APIs (MME, DirectSound, WASAPI, WDM-KS).
    Passing the bare name straight to sounddevice makes it do its own
    substring match across ALL host APIs, which raises a hard
    "Multiple devices found" error for exactly these devices. We instead
    resolve to one specific index ourselves, preferring the WASAPI entry
    (the same host API get_audio_devices() lists from), so selecting a
    device always succeeds deterministically instead of crashing.

    kind: "input" or "output" - used to make sure the chosen device
    actually supports the requested direction.
    """
    if name is None:
        return None

    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"
    preferred_hostapi = _preferred_hostapi_index()
    devices = sd.query_devices()

    def _candidates(hostapi_filter: Optional[int]) -> list[int]:
        return [
            i for i, d in enumerate(devices)
            if d["name"] == name and d[channel_key] > 0
            and (hostapi_filter is None or d["hostapi"] == hostapi_filter)
        ]

    matches = _candidates(preferred_hostapi) if preferred_hostapi is not None else []
    if not matches:
        matches = _candidates(None)

    if not matches:
        raise ValueError(f"Audio device '{name}' not found (it may have been unplugged).")
    if len(matches) > 1:
        log.warning(
            "Device '%s' matched %d entries for %s; using the first one (index %d).",
            name, len(matches), kind, matches[0],
        )
    return matches[0]


class MicCapture:
    """Pulls fixed-size int16 mono frames from the microphone."""

    def __init__(self, sample_rate: int, frame_size: int, device: Optional[str] = None,
                 gain: float = 1.0):
        self._frame_size = frame_size
        self._gain = gain
        self._queue: "queue.Queue[bytes]" = queue.Queue(maxsize=50)
        with _portaudio_lock:
            resolved = _resolve_device_index(device, "input")
            self._stream = sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_size,
                device=resolved,
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
        with _portaudio_lock:
            resolved = _resolve_device_index(device, "output")
            self._stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_size,
                device=resolved,
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
