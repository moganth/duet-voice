"""
Central logging setup. Everything routes through here so log level is
controlled from one place (config/settings.yaml -> log_level) and, when
packaged as a background service, logs land in a rotating file instead
of a console nobody will see.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path.home() / "AppData" / "Local" / "DuetVoice" / "logs" \
    if sys.platform == "win32" else Path.home() / ".duet-voice" / "logs"

_configured = False


def setup_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / "duet-voice.log"

    root = logging.getLogger()
    root.setLevel(level.upper())

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    root.addHandler(stream_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
