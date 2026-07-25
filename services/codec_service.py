"""
Thin ctypes wrapper around libopus.

Why not opuslib / pyogg?
- opuslib relies on ctypes.util.find_library('opus') at import time. On
  Windows that only works if opus.dll is already discoverable, which it
  usually isn't unless the user installs it separately. That's exactly
  the kind of "why is my installer broken" bug we don't want.
- We instead load a DLL/so we ship ourselves (see `libs/`), with a
  fallback to searching the system if it's not bundled. This has been
  tested end-to-end against libopus in this repo's CI sandbox (encode
  and decode round-trip verified).

Only the handful of C functions we actually need are declared, so this
file is short and easy to audit.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import platform
import sys
from pathlib import Path

from utils.logger import get_logger

log = get_logger(__name__)

OPUS_APPLICATION_VOIP = 2048
OPUS_SET_BITRATE_REQUEST = 4002
OPUS_SET_VBR_REQUEST = 4006
OPUS_SET_INBAND_FEC_REQUEST = 4012
OPUS_SET_PACKET_LOSS_PERC_REQUEST = 4014
OPUS_SET_SIGNAL_REQUEST = 4024
OPUS_SIGNAL_VOICE = 3001


def _candidate_lib_names() -> list[str]:
    system = platform.system()
    if system == "Windows":
        return ["opus.dll", "libopus-0.dll"]
    if system == "Darwin":
        return ["libopus.0.dylib", "libopus.dylib"]
    return ["libopus.so.0", "libopus.so"]


def _process_is_64bit() -> bool:
    return sys.maxsize > 2**32


def _windows_pe_machine(path: Path) -> int | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None

    if len(data) < 0x40 or data[:2] != b"MZ":
        return None

    pe_offset = int.from_bytes(data[0x3C:0x40], byteorder="little", signed=False)
    machine_offset = pe_offset + 4
    if machine_offset + 2 > len(data):
        return None
    return int.from_bytes(data[machine_offset:machine_offset + 2], byteorder="little", signed=False)


def _load_libopus() -> ctypes.CDLL:
    # 1. Look next to this file / in libs/<platform>/ first (bundled copy).
    here = Path(__file__).resolve().parent.parent
    bundled_dir = here / "libs" / platform.system().lower()
    bundled_errors: list[str] = []
    for name in _candidate_lib_names():
        candidate = bundled_dir / name
        if candidate.exists():
            if platform.system() == "Windows":
                machine = _windows_pe_machine(candidate)
                if machine == 0x014C and _process_is_64bit():
                    bundled_errors.append(
                        f"{candidate.name} is x86 but this Python process is 64-bit"
                    )
                    log.warning(
                        "Skipping bundled libopus %s because it is x86 and this process is 64-bit",
                        candidate,
                    )
                    continue
                if machine == 0x8664 and not _process_is_64bit():
                    bundled_errors.append(
                        f"{candidate.name} is x64 but this Python process is 32-bit"
                    )
                    log.warning(
                        "Skipping bundled libopus %s because it is x64 and this process is 32-bit",
                        candidate,
                    )
                    continue

            log.debug("Loading bundled libopus: %s", candidate)
            try:
                return ctypes.CDLL(str(candidate))
            except OSError as exc:
                bundled_errors.append(f"{candidate.name}: {exc}")
                log.warning("Failed to load bundled libopus %s: %s", candidate, exc)
                continue

    # 2. Fall back to whatever the OS can find.
    found = ctypes.util.find_library("opus")
    if found:
        log.debug("Loading system libopus: %s", found)
        return ctypes.CDLL(found)

    # 3. Last resort: try the raw names directly (works if it's on PATH).
    for name in _candidate_lib_names():
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue

    raise OSError(
        "libopus could not be loaded. Place a matching Windows Opus DLL "
        "in libs/windows/ (x64 for 64-bit Python, x86 for 32-bit Python; see docs/SETUP.md) "
        "or install libopus system-wide."
        + (f" Bundled load attempts: {'; '.join(bundled_errors)}" if bundled_errors else "")
    )


_lib = _load_libopus()

_lib.opus_encoder_create.restype = ctypes.c_void_p
_lib.opus_encoder_create.argtypes = [
    ctypes.c_int32, ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
]
_lib.opus_encode.restype = ctypes.c_int32
_lib.opus_encode.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_int16), ctypes.c_int,
    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int32,
]
_lib.opus_encoder_destroy.argtypes = [ctypes.c_void_p]

_lib.opus_decoder_create.restype = ctypes.c_void_p
_lib.opus_decoder_create.argtypes = [
    ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)
]
_lib.opus_decode.restype = ctypes.c_int
_lib.opus_decode.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int32,
    ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int,
]
_lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]

_lib.opus_encoder_ctl.restype = ctypes.c_int
_lib.opus_encoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int]

_lib.opus_decoder_ctl.restype = ctypes.c_int
_lib.opus_decoder_ctl.argtypes = [ctypes.c_void_p, ctypes.c_int]

# opus_encoder_ctl / opus_decoder_ctl are variadic in C. We call them
# without fixed argtypes so ctypes uses its default (c_int) promotion,
# which matches how the opus headers define these control requests.


class OpusEncoder:
    def __init__(self, sample_rate: int, channels: int, bitrate_bps: int,
                 vbr: bool = True, fec: bool = True, expected_loss_pct: int = 15):
        err = ctypes.c_int()
        self._enc = _lib.opus_encoder_create(
            sample_rate, channels, OPUS_APPLICATION_VOIP, ctypes.byref(err)
        )
        if err.value != 0 or not self._enc:
            raise RuntimeError(f"opus_encoder_create failed: error {err.value}")

        _lib.opus_encoder_ctl(self._enc, OPUS_SET_BITRATE_REQUEST, ctypes.c_int(bitrate_bps))
        _lib.opus_encoder_ctl(self._enc, OPUS_SET_VBR_REQUEST, ctypes.c_int(1 if vbr else 0))
        _lib.opus_encoder_ctl(self._enc, OPUS_SET_INBAND_FEC_REQUEST, ctypes.c_int(1 if fec else 0))
        _lib.opus_encoder_ctl(self._enc, OPUS_SET_PACKET_LOSS_PERC_REQUEST, ctypes.c_int(expected_loss_pct))
        _lib.opus_encoder_ctl(self._enc, OPUS_SET_SIGNAL_REQUEST, ctypes.c_int(OPUS_SIGNAL_VOICE))

        self._max_packet = 4000

    def encode(self, pcm_int16: bytes, frame_size: int) -> bytes:
        """pcm_int16: raw little-endian int16 mono samples for exactly one frame."""
        pcm_array = (ctypes.c_int16 * frame_size).from_buffer_copy(pcm_int16)
        out_buf = (ctypes.c_ubyte * self._max_packet)()
        n = _lib.opus_encode(self._enc, pcm_array, frame_size, out_buf, self._max_packet)
        if n < 0:
            raise RuntimeError(f"opus_encode failed: error {n}")
        return bytes(out_buf[:n])

    def close(self):
        if getattr(self, "_enc", None):
            _lib.opus_encoder_destroy(self._enc)
            self._enc = None

    def __del__(self):
        self.close()


class OpusDecoder:
    def __init__(self, sample_rate: int, channels: int):
        err = ctypes.c_int()
        self._dec = _lib.opus_decoder_create(sample_rate, channels, ctypes.byref(err))
        if err.value != 0 or not self._dec:
            raise RuntimeError(f"opus_decoder_create failed: error {err.value}")
        self.channels = channels

    def decode(self, payload: bytes | None, frame_size: int) -> bytes:
        """
        payload=None triggers Opus's built-in packet-loss concealment
        (it synthesizes a plausible continuation instead of silence).
        """
        out_pcm = (ctypes.c_int16 * (frame_size * self.channels))()
        if payload is None:
            n = _lib.opus_decode(self._dec, None, 0, out_pcm, frame_size, 0)
        else:
            buf = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
            n = _lib.opus_decode(self._dec, buf, len(payload), out_pcm, frame_size, 0)
        if n < 0:
            raise RuntimeError(f"opus_decode failed: error {n}")
        return bytes(out_pcm)[: n * 2 * self.channels]

    def close(self):
        if getattr(self, "_dec", None):
            _lib.opus_decoder_destroy(self._dec)
            self._dec = None

    def __del__(self):
        self.close()
