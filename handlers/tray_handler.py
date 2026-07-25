"""
Minimal system tray icon so the app can run in the background without a
visible window stealing focus from the game. Left-click doesn't do
anything special; right-click (or the default click on Windows) opens
the menu built below.
"""
from __future__ import annotations

import threading
from typing import Callable

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


class TrayApp:
    def __init__(self, session: VoiceSession, on_quit: Callable[[], None]):
        self._session = session
        self._on_quit = on_quit
        self._icon = pystray.Icon(
            "duet-voice", _ICON_IDLE, "Duet Voice", menu=self._build_menu()
        )

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
            pystray.MenuItem("Quit", self._on_quit_clicked),
        )

    def _status_text(self, item) -> str:
        if not self._session.is_connected:
            return "Status: Not connected"
        return f"Status: Connected{' (muted)' if self._session.is_muted else ''}"

    def _refresh(self) -> None:
        if self._session.is_muted:
            self._icon.icon = _ICON_MUTED
        elif self._session.is_connected:
            self._icon.icon = _ICON_CONNECTED
        else:
            self._icon.icon = _ICON_IDLE
        self._icon.menu = self._build_menu()

    def _on_connect(self, icon, item) -> None:
        def worker():
            try:
                self._session.connect()
                self._session.start_audio()
            except Exception as exc:
                log.error("Connect failed: %s", exc)
                self._icon.notify(f"Connect failed: {exc}", "Duet Voice")
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

    def run(self) -> None:
        """Blocks - call this from the main thread (pystray requirement
        on some backends, notably macOS)."""
        self._icon.run()
