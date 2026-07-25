"""
Microphone capture and speaker playback via sounddevice (PortAudio).
Both run as callback-driven streams on their own native threads - we
never block them, we only push/pop from thread-safe queues.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Optional

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


def get_audio_devices(kind: str = "input") -> list[str]:
    """Return live device names for `kind` ("input" or "output").

    Queried directly from the Windows Core Audio API (via pycaw), which is
    completely independent of PortAudio's own device cache. PortAudio's
    WASAPI backend only builds its device table once at Pa_Initialize()
    time, so sd.query_devices() never reflects devices plugged in or
    unplugged after that - and fully re-initialising PortAudio to force a
    refresh isn't safe while a call is active (it would tear down any open
    MicCapture/SpeakerPlayback stream). Querying Windows directly sidesteps
    that: it's always live and safe to call at any time, including mid-call.

    Falls back to PortAudio's own (possibly stale) WASAPI-filtered list if
    pycaw is unavailable (non-Windows, or not installed) or the query fails.
    """
    live = _live_endpoint_names(kind)
    if live is not None:
        return live
    return _portaudio_device_names(kind)


def _device_group_key(name: str) -> str:
    """Extract the parenthesised hardware suffix Windows appends to audio
    endpoint names, e.g. "Headset (Buds fe)" / "Headphones (Buds fe)" both
    key to "Buds fe". Used to merge the separate input/output endpoints a
    single physical device (typically Bluetooth) is split into under one
    entry in the tray's unified device picker, instead of showing two
    differently-named, easily-confused entries for the same earbuds."""
    if name.endswith(")") and "(" in name:
        return name[name.rindex("(") + 1:-1].strip()
    return name


def get_audio_device_groups() -> list[dict]:
    """Return one entry per physical device for the tray's single unified
    "Audio Device" menu: {"label": str, "input": Optional[str], "output": Optional[str]}.

    Merges the input and output endpoint names that share the same
    _device_group_key() (e.g. a Bluetooth headset's separate mic/speaker
    endpoints) into one entry, so the user picks one physical device and
    both directions switch together - matching how Windows' own sound
    picker presents it, and avoiding the "why are there two entries for
    one device" confusion.
    """
    inputs = get_audio_devices("input")
    outputs = get_audio_devices("output")

    groups: dict[str, dict] = {}
    order: list[str] = []

    def _ensure(key: str) -> dict:
        if key not in groups:
            groups[key] = {"label": key, "input": None, "output": None}
            order.append(key)
        return groups[key]

    for name in inputs:
        _ensure(_device_group_key(name))["input"] = name
    for name in outputs:
        _ensure(_device_group_key(name))["output"] = name

    result = []
    for key in order:
        g = groups[key]
        if g["input"] and g["output"] and g["input"] != g["output"]:
            label = key  # e.g. "Buds fe" instead of "Headset (Buds fe)" / "Headphones (Buds fe)"
        else:
            label = g["input"] or g["output"] or key
        result.append({"label": label, "input": g["input"], "output": g["output"]})
    return result


def _live_endpoint_names(kind: str) -> Optional[list[str]]:
    """Enumerate active Windows audio endpoints for `kind` via pycaw/Core
    Audio, bypassing PortAudio entirely. Returns None (signalling the
    caller to fall back) if pycaw isn't available or the query fails."""
    if sys.platform != "win32":
        return None
    try:
        from pycaw.pycaw import AudioUtilities, DEVICE_STATE, EDataFlow

        flow = EDataFlow.eCapture.value if kind == "input" else EDataFlow.eRender.value
        devices = AudioUtilities.GetAllDevices(
            data_flow=flow, device_state=DEVICE_STATE.ACTIVE.value
        )
        seen: set[str] = set()
        names: list[str] = []
        for device in devices:
            name = device.FriendlyName
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        return names
    except Exception as exc:
        log.debug("Live %s device enumeration unavailable, using PortAudio fallback: %s", kind, exc)
        return None


def _portaudio_device_names(kind: str) -> list[str]:
    """Fallback device list sourced from PortAudio's own (possibly stale)
    cache, filtered to WASAPI on Windows. Only used if the live Windows
    enumeration above isn't available."""
    target_hostapi = _preferred_hostapi_index()
    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"

    seen: set[str] = set()
    names: list[str] = []
    for device in sd.query_devices():
        if target_hostapi is not None and device["hostapi"] != target_hostapi:
            continue
        if device[channel_key] <= 0:
            continue
        name = device["name"]
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _resolve_device_index(name: Optional[str], kind: str) -> Optional[int]:
    """Resolve a device name to a single unambiguous PortAudio index.

    Some devices (notably Bluetooth/USB headsets) are exposed identically
    under several PortAudio host APIs (MME, DirectSound, WASAPI, WDM-KS).
    Passing the bare name straight to sounddevice makes it do its own
    substring match across ALL host APIs, which raises a hard
    "Multiple devices found" error for exactly these devices. We instead
    resolve to one specific index ourselves, preferring the WASAPI entry,
    so selecting a device always succeeds deterministically instead of
    crashing.

    kind: "input" or "output" - used to make sure the chosen device
    actually supports the requested direction.
    """
    if name is None:
        return None

    channel_key = "max_input_channels" if kind == "input" else "max_output_channels"

    def _find() -> list[int]:
        preferred_hostapi = _preferred_hostapi_index()
        devices = sd.query_devices()

        def _candidates(hostapi_filter: Optional[int]) -> list[int]:
            return [
                i for i, d in enumerate(devices)
                if d["name"] == name and d[channel_key] > 0
                and (hostapi_filter is None or d["hostapi"] == hostapi_filter)
            ]

        if preferred_hostapi is not None:
            # On Windows, only ever match the WASAPI entry. Legacy host
            # APIs (WDM-KS in particular) expose the same-named device but
            # are notoriously unreliable for opening Bluetooth endpoints
            # (e.g. "WdmSyncIoctl" PaErrorCode -9999 failures) - silently
            # falling back to them just trades a clear "not found" error
            # for a confusing crash when the stream is actually opened.
            # If WASAPI doesn't have it (yet), the caller's reinit+retry
            # is the right remedy, not a downgrade to another host API.
            return _candidates(preferred_hostapi)
        return _candidates(None)

    matches = _find()
    if not matches:
        # The device may have just been plugged in - PortAudio's WASAPI
        # device table is only built once at Pa_Initialize() and won't
        # know about it yet (see get_audio_devices() above for why we
        # don't do this proactively/periodically). It IS safe to reinit
        # right here: this function only runs while constructing a brand
        # new MicCapture/SpeakerPlayback, always under _portaudio_lock,
        # with any previous stream for this role already stopped by the
        # caller - so nothing active gets torn down under us.
        sd._terminate()
        sd._initialize()
        matches = _find()

    if not matches:
        raise ValueError(f"Audio device '{name}' not found (it may have been unplugged).")
    if len(matches) > 1:
        log.warning(
            "Device '%s' matched %d entries for %s; using the first one (index %d).",
            name, len(matches), kind, matches[0],
        )
    return matches[0]


def _open_stream_with_retry(open_fn, retries: int = 2, delay: float = 0.35):
    """Call open_fn() (which constructs an sd.InputStream/OutputStream),
    retrying after a short pause if PortAudio raises.

    Rapidly closing one stream and opening another that requires the same
    Bluetooth radio to switch profile (e.g. a headset's HFP mic and its
    separate A2DP speaker endpoint) can race Windows' teardown of the
    previous audio session, surfacing as a transient PaErrorCode -9999
    ("WdmSyncIoctl"/host error). Giving it a moment to settle before
    retrying resolves most of these without the caller having to fall back
    to a different device."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return open_fn()
        except sd.PortAudioError as exc:
            last_exc = exc
            if attempt < retries:
                log.warning(
                    "Stream open failed (attempt %d/%d): %s - retrying shortly...",
                    attempt + 1, retries + 1, exc,
                )
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


class _MicAGC:
    """Adaptive gain control for the microphone.

    A fixed multiplier (the old behaviour) can't win: turn it up enough to
    hear quiet speech from a normal mic distance and it amplifies room
    noise by the exact same amount, so "gain" mostly just makes the hiss
    louder, not the voice. Wired earbud mics in particular are only loud
    enough at point-blank range, so a static multiplier tuned for that
    distance is either too quiet everywhere else or clips/distorts.

    This instead tracks a smoothed RMS envelope and only drives the gain up
    when the level clearly exceeds a noise-floor gate (i.e. looks like
    speech, not ambient noise). Gain relaxes back down to the user's manual
    baseline during silence, so quiet room noise is never boosted past that
    baseline - only actual speech gets the extra automatic boost needed to
    reach a consistent, audible target level regardless of mic distance. A
    soft (tanh) limiter is applied after the gain so an unexpectedly loud
    transient saturates gently instead of clipping.

    The gate combines an absolute floor with a noise-floor-relative one
    (rather than a pure ratio, as before). Some devices - notably weak/quiet
    wired mics - capture speech only marginally louder than their own noise
    floor; a pure ratio gate (e.g. "3x the noise floor") never fired for
    them, so the AGC never engaged and they stayed quiet even at point-blank
    range. The absolute floor lets genuine (if quiet) speech register as
    "signal" well before that ratio would ever be satisfied.
    """

    _TARGET_RMS = 6500.0        # ~-14 dBFS RMS target for speech - comfortably loud but not hot
    _MAX_TARGET_RMS = 15000.0   # cap so a high manual gain preset can't push the target into clipping
    _MAX_AUTO_GAIN = 35.0       # weak/quiet mics (wired earbuds) may need a large boost to reach target
    _ENVELOPE_ATTACK = 0.35     # how fast the RMS envelope follower reacts to level increases
    _ENVELOPE_RELEASE = 0.05    # how fast it reacts to level decreases
    _GAIN_UP_RATE = 0.06        # slow: don't ramp up during brief pauses between words
    _GAIN_DOWN_RATE = 0.25      # fast: pull back quickly to avoid clipping on loud transients
    _NOISE_FLOOR_INIT = 120.0
    _NOISE_ATTACK = 0.02        # slow adaptation of the ambient noise-floor estimate
    _ABS_GATE = 250.0           # frames quieter than this are always treated as silence
    _NOISE_MARGIN = 1.5         # signal must beat the tracked noise floor by this much to "count"

    def __init__(self) -> None:
        self._envelope = 0.0
        self._noise_floor = self._NOISE_FLOOR_INIT
        self._gain = 1.0

    def process(self, samples: np.ndarray, manual_gain: float) -> np.ndarray:
        if samples.size == 0:
            return samples

        # Widen before squaring - int16 * int16 overflows in-place.
        rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        env_rate = self._ENVELOPE_ATTACK if rms > self._envelope else self._ENVELOPE_RELEASE
        self._envelope += (rms - self._envelope) * env_rate

        gate = max(self._ABS_GATE, self._noise_floor * self._NOISE_MARGIN)
        if self._envelope < gate:
            self._noise_floor += (self._envelope - self._noise_floor) * self._NOISE_ATTACK
            self._noise_floor = max(self._noise_floor, 20.0)

        target = min(self._MAX_TARGET_RMS, self._TARGET_RMS * manual_gain)
        if self._envelope >= gate and self._envelope > 1.0:
            desired = min(self._MAX_AUTO_GAIN, max(manual_gain, target / self._envelope))
        else:
            # Silence / ambient noise: relax back to the user's manual
            # baseline instead of holding a large boost that would blast
            # the next burst of room noise at full volume.
            desired = manual_gain

        rate = self._GAIN_UP_RATE if desired > self._gain else self._GAIN_DOWN_RATE
        self._gain += (desired - self._gain) * rate
        self._gain = max(manual_gain, min(self._MAX_AUTO_GAIN, self._gain))

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
            self._stream = _open_stream_with_retry(lambda: sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_size,
                device=resolved,
                callback=self._callback,
            ))

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
            self._stream = _open_stream_with_retry(lambda: sd.OutputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_size,
                device=resolved,
                callback=self._callback,
            ))

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
