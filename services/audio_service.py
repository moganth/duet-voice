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


class _MicAGC:
    """Adaptive gain control for the microphone.

    A fixed multiplier (the old behaviour) can't win: turn it up enough to
    hear quiet speech from a normal mic distance and it amplifies room
    noise by the exact same amount, so "gain" mostly just makes the hiss
    louder, not the voice. Wired earbud mics in particular are only loud
    enough at point-blank range, so a static multiplier tuned for that
    distance is either too quiet everywhere else or clips/distorts.

    This instead tracks a slow-moving noise-floor estimate and only drives
    the gain up when the instantaneous level clearly exceeds it (i.e. looks
    like speech, not ambient noise). Gain relaxes back down to the user's
    manual baseline during silence, so quiet room noise is never boosted
    past that baseline - only actual speech gets the extra automatic boost
    needed to reach a consistent, audible target level regardless of mic
    distance. A soft (tanh) limiter is applied after the gain so an
    unexpectedly loud transient saturates gently instead of clipping.
    """

    _TARGET_PEAK = 11000.0      # ~-9.5 dBFS target for speech peaks
    _MAX_TARGET_PEAK = 26000.0  # cap so a high manual gain can't push into hard clipping territory
    _NOISE_FLOOR_INIT = 150.0
    _MAX_AUTO_GAIN = 14.0
    _GAIN_ATTACK = 0.20         # fast: catch quiet speech quickly
    _GAIN_RELEASE = 0.03        # slow: don't let gain snap back and "pump" between words
    _NOISE_ATTACK = 0.02        # slow adaptation of the ambient noise-floor estimate
    _SPEECH_MARGIN = 3.0        # signal must exceed the noise floor by this factor to count as speech

    def __init__(self) -> None:
        self._noise_floor = self._NOISE_FLOOR_INIT
        self._gain = 1.0

    def process(self, samples: np.ndarray, manual_gain: float) -> np.ndarray:
        # int16 abs() overflows at -32768, so widen before taking the peak.
        peak = float(np.abs(samples.astype(np.int32)).max()) if samples.size else 0.0

        if peak < self._noise_floor * self._SPEECH_MARGIN:
            self._noise_floor += (peak - self._noise_floor) * self._NOISE_ATTACK
            self._noise_floor = max(self._noise_floor, 20.0)

        is_speech_like = peak > self._noise_floor * self._SPEECH_MARGIN
        target_peak = min(self._MAX_TARGET_PEAK, self._TARGET_PEAK * manual_gain)

        if is_speech_like and peak > 1.0:
            desired = min(self._MAX_AUTO_GAIN, max(1.0, target_peak / peak))
            rate = self._GAIN_ATTACK if desired > self._gain else self._GAIN_RELEASE
            self._gain += (desired - self._gain) * rate
        else:
            # Silence / ambient noise: relax back to the user's manual
            # baseline instead of holding a large boost that would blast
            # the next burst of room noise at full volume.
            self._gain += (manual_gain - self._gain) * self._GAIN_RELEASE

        boosted = samples.astype(np.float32) * self._gain
        limited = np.tanh(boosted / 32767.0) * 32767.0
        return limited.astype(np.int16)


class MicCapture:
    """Pulls fixed-size int16 mono frames from the microphone."""

    def __init__(self, sample_rate: int, frame_size: int, device: Optional[str] = None,
                 gain: float = 1.0):
        self._frame_size = frame_size
        self._gain = gain
        self._agc = _MicAGC()
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
        data = self._agc.process(indata[:, 0], self._gain)
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
