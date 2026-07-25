"""
Minimal system tray icon so the app can run in the background without a
visible window stealing focus from the game. Left-click doesn't do
anything special; right-click (or the default click on Windows) opens
the menu built below.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import pystray
from PIL import Image, ImageDraw

from handlers.voice_handler import VoiceSession
from utils.logger import get_logger

log = get_logger(__name__)


def _make_icon_image(color: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=color)
    return img


_ICON_CONNECTED = _make_icon_image("#3BA55D")   # green
_ICON_MUTED = _make_icon_image("#ED4245")       # red
_ICON_IDLE = _make_icon_image("#5865F2")        # blurple
_ICON_WARNING = _make_icon_image("#FAA61A")     # orange - connected but link may be down


class TrayApp:
    def __init__(self, session: VoiceSession, on_quit: Callable[[], None]):
        self._session = session
        self._on_quit = on_quit
        self._icon = pystray.Icon(
            "duet-voice", _ICON_IDLE, "Duet Voice", menu=self._build_menu()
        )
        # Keep the icon in sync when VoiceSession's link health changes.
        session.on_state_change = self._refresh
        # Detect headset plug/unplug without requiring a manual refresh.
        self._start_device_monitor()

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Connect", self._on_connect,
                              visible=lambda item: not self._session.is_connected),
            pystray.MenuItem("Disconnect", self._on_disconnect,
                              visible=lambda item: self._session.is_connected),
            pystray.MenuItem("Mute", self._on_toggle_mute, checked=lambda item: self._session.is_muted),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Microphone", pystray.Menu(lambda: self._device_menu_items("input"))),
            pystray.MenuItem("Speaker", pystray.Menu(lambda: self._device_menu_items("output"))),
            pystray.MenuItem("Mic Boost", self._build_gain_submenu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit_clicked),
        )

    def _status_text(self, item) -> str:
        if not self._session.is_connected:
            return "Status: Not connected"
        if not self._session.is_audio_flowing:
            return "Status: Connected (link down?)"
        return f"Status: Connected{' (muted)' if self._session.is_muted else ''}"

    def _refresh(self) -> None:
        if not self._session.is_connected:
            self._icon.icon = _ICON_IDLE
        elif self._session.is_muted:
            self._icon.icon = _ICON_MUTED
        elif not self._session.is_audio_flowing:
            self._icon.icon = _ICON_WARNING
        else:
            self._icon.icon = _ICON_CONNECTED
        self._icon.menu = self._build_menu()

    def _on_connect(self, icon, item) -> None:
        def worker():
            try:
                self._session.connect()
                self._session.start_audio()
            except Exception as exc:
                log.error("Connect failed: %s", exc)
                self._icon.notify(f"Connect failed: {exc}", "Duet Voice")
                # start_audio() can fail after connect() already succeeded
                # (e.g. an ambiguous/unplugged device) - without this the
                # session is left "connected" with no audio pipeline at all,
                # which looks like total communication failure. Back all the
                # way out so the tray shows Disconnected and the user can
                # retry cleanly.
                self._session.disconnect()
            self._refresh()
        threading.Thread(target=worker, daemon=True).start()

    def _on_disconnect(self, icon, item) -> None:
        def worker():
            self._session.disconnect()
            self._refresh()
        threading.Thread(target=worker, daemon=True).start()

    def _on_toggle_mute(self, icon, item) -> None:
        self._session.toggle_mute()
        self._refresh()

    def _on_quit_clicked(self, icon, item) -> None:
        self._session.disconnect()
        icon.stop()
        self._on_quit()

    # ---- audio device & gain controls --------------------------------------

    def _device_menu_items(self, kind: str):
        """Generator called fresh every time the Microphone/Speaker submenu
        opens. get_audio_devices() queries Windows directly (see
        services/audio_service.py) so hot-plugged devices appear
        immediately without an app restart or manual refresh - even mid-call.

        Each submenu is filtered to devices that actually support that
        direction, so e.g. a Bluetooth headset's output-only "Headphones"
        (A2DP) endpoint only ever shows up under Speaker, while its
        "Headset" (HFP, has a mic) endpoint can show under both - instead of
        one flat, confusing list with no way to tell which is which."""
        from services.audio_service import get_audio_devices

        def _current() -> Optional[str]:
            return self._session.current_input_device if kind == "input" else self._session.current_output_device

        yield pystray.MenuItem(
            "(system default)",
            self._make_device_action(kind, None),
            checked=lambda item: _current() is None,
        )
        try:
            for name in get_audio_devices(kind):
                yield pystray.MenuItem(
                    name,
                    self._make_device_action(kind, name),
                    checked=lambda item, n=name: _current() == n,
                )
        except Exception as exc:
            log.warning("Could not list %s devices: %s", kind, exc)

    def _build_gain_submenu(self) -> pystray.Menu:
        """Build the mic gain preset submenu."""
        presets = [("Normal (1×)", 1.0), ("2×", 2.0), ("3×", 3.0), ("4×", 4.0)]
        return pystray.Menu(*[
            pystray.MenuItem(
                label,
                self._make_gain_action(value),
                checked=lambda item, v=value: abs(
                    self._session.config.audio.mic_gain - v) < 0.01,
            )
            for label, value in presets
        ])

    def _make_device_action(self, kind: str, device: Optional[str]):
        """Return a pystray action that switches the microphone or speaker
        device (kind: "input" or "output")."""
        def _action(icon, item):
            def worker():
                try:
                    if kind == "input":
                        self._session.switch_input_device(device)
                    else:
                        self._session.switch_output_device(device)
                except Exception as exc:
                    label = "Microphone" if kind == "input" else "Speaker"
                    log.error("%s switch failed: %s", label, exc)
                    self._icon.notify(f"{label} switch failed: {exc}", "Duet Voice")
                self._refresh()
            threading.Thread(target=worker, daemon=True).start()
        return _action

    def _make_gain_action(self, gain: float):
        """Return a pystray action that sets mic gain."""
        def _action(icon, item):
            self._session.set_mic_gain(gain)
            self._refresh()
        return _action

    def _start_device_monitor(self) -> None:
        """Poll the live microphone/speaker device lists every 2 s and rebuild
        the tray menu on change.

        pystray's Win32 backend caches its native menu handle and won't
        notice new/removed items on its own, so this forces a rebuild via
        _refresh() when the device list actually changes.

        get_audio_devices() queries Windows directly (see
        services/audio_service.py), not PortAudio, so - unlike the earlier
        sd._terminate()/_initialize() approach - this never touches the
        active MicCapture/SpeakerPlayback streams and is safe to run
        continuously, including during an active call."""
        from services.audio_service import get_audio_devices

        def _snapshot() -> tuple[tuple[str, ...], tuple[str, ...]]:
            return (tuple(get_audio_devices("input")), tuple(get_audio_devices("output")))

        def _monitor() -> None:
            try:
                last = _snapshot()
            except Exception:
                last = ((), ())
            while True:
                time.sleep(2.0)
                try:
                    current = _snapshot()
                    if current != last:
                        last = current
                        self._refresh()
                except Exception:
                    pass

        threading.Thread(target=_monitor, daemon=True, name="device-monitor").start()

    def run(self) -> None:
        """Blocks - call this from the main thread (pystray requirement
        on some backends, notably macOS)."""
        self._icon.run()
