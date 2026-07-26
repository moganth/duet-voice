"""
Minimal system tray icon so the app can run in the background without a
visible window stealing focus from the game. Left-click doesn't do
anything special; right-click (or the default click on Windows) opens
the menu built below.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
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
    def __init__(self, session: VoiceSession, on_quit: Callable[[], None],
                 config_path: Optional[Path] = None):
        self._session = session
        self._on_quit = on_quit
        self._config_path = config_path
        self._icon = pystray.Icon(
            "duet-voice", _ICON_IDLE, "Duet Voice", menu=self._build_menu()
        )
        # Keep the icon in sync when VoiceSession's link health changes.
        session.on_state_change = self._refresh
        # If the configured devices fail to open and start_audio() falls
        # back to system default, persist that so we don't keep retrying -
        # and failing - the same broken combo on every future launch.
        session.on_device_fallback = self._on_device_fallback
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
            pystray.MenuItem("Audio Device", pystray.Menu(lambda: self._device_menu_items())),
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

    def _device_menu_items(self):
        """Generator called fresh every time the Audio Device submenu opens.
        get_audio_device_groups() queries Windows directly (see
        services/audio_service.py) so hot-plugged devices appear
        immediately without an app restart or manual refresh - even mid-call.

        Each entry represents one physical device, not one PortAudio
        endpoint: a Bluetooth headset's separate mic (HFP) and speaker
        (A2DP) endpoints are merged into a single entry so picking it
        switches both directions together - matching Windows' own sound
        picker instead of showing two confusingly-named entries for the
        same earbuds."""
        from services.audio_service import (
            _device_group_key, get_audio_device_groups, get_current_default_names,
        )

        # "System default" used to always show that literal label, even
        # while it silently resolved to a specific device - which didn't
        # match what Windows' own Sound Settings showed as the active
        # device, and looked like nothing was actually selected. Show the
        # real, currently-active device name when it's unambiguous (mic
        # and speaker are the same physical device). When Windows' mic and
        # speaker defaults are two different physical devices (a perfectly
        # normal Windows configuration - e.g. a Bluetooth headset as the
        # communication speaker with a laptop's own mic), just say "System
        # Default": spelling out both names side by side reads like a bug
        # report ("why is Bluetooth AND the laptop mic selected?") rather
        # than the accurate-but-unremarkable fact that it is.
        default_label = "System Default"
        try:
            default_in, default_out = get_current_default_names()
            if default_in and default_out and _device_group_key(default_in) == _device_group_key(default_out):
                default_label = f"System Default ({_device_group_key(default_in)})"
            elif default_in and not default_out:
                default_label = f"System Default ({default_in})"
            elif default_out and not default_in:
                default_label = f"System Default ({default_out})"
        except Exception as exc:
            log.debug("Could not resolve live default device names: %s", exc)

        yield pystray.MenuItem(
            default_label,
            self._make_device_action(None, None),
            checked=lambda item: (
                self._session.current_input_device is None
                and self._session.current_output_device is None
            ),
        )
        def _is_current(group: dict) -> bool:
            if group["input"] is not None and self._session.current_input_device != group["input"]:
                return False
            if group["output"] is not None and self._session.current_output_device != group["output"]:
                return False
            return True

        try:
            for group in get_audio_device_groups():
                yield pystray.MenuItem(
                    group["label"],
                    self._make_device_action(group["input"], group["output"]),
                    checked=lambda item, g=group: _is_current(g),
                )
        except Exception as exc:
            log.warning("Could not list audio devices: %s", exc)

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

    def _on_device_fallback(self) -> None:
        log.warning("Falling back to system-default audio devices; persisting this to config.")
        self._save_current_devices()
        self._icon.notify(
            "Your configured audio device(s) failed to open - switched to system default.",
            "Duet Voice",
        )
        self._refresh()

    def _save_current_devices(self) -> None:
        if self._config_path is None:
            return
        try:
            from utils.config_io import save_device_selection
            save_device_selection(
                self._config_path,
                self._session.current_input_device,
                self._session.current_output_device,
            )
        except Exception as exc:
            log.warning("Could not save device selection to config: %s", exc)

    def _make_device_action(self, input_device: Optional[str], output_device: Optional[str]):
        """Return a pystray action that switches both the microphone and
        speaker to the given physical device's endpoints in one atomic,
        rollback-on-failure step."""
        def _action(icon, item):
            def worker():
                try:
                    self._session.switch_devices(input_device, output_device)
                    self._save_current_devices()
                except Exception as exc:
                    log.error("Audio device switch failed: %s", exc)
                    self._icon.notify(f"Audio device switch failed: {exc}", "Duet Voice")
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
