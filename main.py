"""
Duet Voice - ultra-light P2P voice chat for co-op gaming sessions.

Usage:
    python main.py                  # normal run: tray icon, auto-connect if configured
    python main.py --config PATH    # use an alternate settings YAML file
    python main.py --list-devices   # print audio device names/indices and exit
    python main.py --no-autoconnect # start in tray, connect manually from the menu
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from handlers.tray_handler import TrayApp
from handlers.voice_handler import VoiceSession
from schemas.config_schema import AppConfig
from utils.logger import setup_logging, get_logger

CONFIG_DIR = Path(__file__).resolve().parent / "config"
CONFIG_PATH = CONFIG_DIR / "settings.yaml"
EXAMPLE_PATH = CONFIG_DIR / "settings.example.yaml"


def load_config(config_path: Path) -> AppConfig:
    if not config_path.exists():
        if config_path == CONFIG_PATH and EXAMPLE_PATH.exists():
            print(f"No config/settings.yaml found - copying {EXAMPLE_PATH.name} as a starting point.")
            CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            print(f"ERROR: {config_path} is missing.")
            sys.exit(1)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig(**raw)


def register_hotkeys(session: VoiceSession, config: AppConfig) -> None:
    try:
        import keyboard  # optional dependency; hotkeys degrade gracefully if unavailable
    except ImportError:
        get_logger(__name__).warning(
            "The 'keyboard' package isn't installed - hotkeys disabled. "
            "Use the tray menu instead, or `pip install keyboard`."
        )
        return

    if config.hotkeys.mute_toggle:
        keyboard.add_hotkey(config.hotkeys.mute_toggle, lambda: session.toggle_mute())

    if config.hotkeys.push_to_talk:
        key = config.hotkeys.push_to_talk
        keyboard.on_press_key(key, lambda _: session.set_muted(False))
        keyboard.on_release_key(key, lambda _: session.set_muted(True))
        session.set_muted(True)  # PTT starts muted until the key is held


def main() -> None:
    parser = argparse.ArgumentParser(description="Duet Voice")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="Path to the settings YAML file")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument("--no-autoconnect", action="store_true", help="Don't connect automatically on launch")
    args = parser.parse_args()

    if args.list_devices:
        from services.audio_service import list_devices
        print(list_devices())
        return

    config = load_config(args.config)
    setup_logging(config.log_level)
    log = get_logger("main")
    log.info("Duet Voice starting as '%s'", config.display_name)

    session = VoiceSession(config)
    register_hotkeys(session, config)

    def on_quit():
        return None

    tray = TrayApp(session, on_quit, config_path=args.config)

    if not args.no_autoconnect:
        import threading

        def autoconnect():
            try:
                session.connect()
                session.start_audio()
                tray._refresh()  # noqa: SLF001 - internal refresh, same package
            except Exception as exc:
                log.error("Auto-connect failed: %s", exc)

        threading.Thread(target=autoconnect, daemon=True).start()

    tray.run()  # blocks until Quit is clicked


if __name__ == "__main__":
    main()
