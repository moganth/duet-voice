"""
Small, surgical helpers for updating specific values in settings.yaml
in-place, preserving the rest of the file (comments included).

A full parse-with-PyYAML-then-dump round-trip would silently strip every
comment in settings.yaml (it has several explaining each option) every
time a value is saved - so instead we patch just the one line we need via
regex, leaving everything else byte-for-byte untouched.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml


def _yaml_scalar(value: Optional[str]) -> str:
    """Render `value` the way PyYAML would as a mapping value (handles
    quoting/escaping for null, and any characters that need it)."""
    dumped = yaml.safe_dump({"v": value}).strip()
    return dumped.split(": ", 1)[1]


def _patch_scalar_line(text: str, key: str, value: Optional[str]) -> str:
    """Rewrite a single `key: value` line in raw YAML text, preserving its
    indentation and any trailing inline comment."""
    new_scalar = _yaml_scalar(value)
    pattern = re.compile(
        rf"(?m)^(?P<indent>[ \t]*){re.escape(key)}:[ \t]*[^\n#]*(?P<comment>[ \t]+#.*)?$"
    )

    def _replace(m: re.Match) -> str:
        comment = m.group("comment") or ""
        return f"{m.group('indent')}{key}: {new_scalar}{comment}"

    new_text, count = pattern.subn(_replace, text, count=1)
    if count == 0:
        raise ValueError(f"Could not find a '{key}:' line to update in the config file.")
    return new_text


def save_device_selection(
    config_path: Path, input_device: Optional[str], output_device: Optional[str]
) -> None:
    """Persist the current microphone/speaker selection to settings.yaml so
    it's used as the app's own default on the next launch, instead of
    always resetting to the system default."""
    text = config_path.read_text(encoding="utf-8")
    text = _patch_scalar_line(text, "input_device", input_device)
    text = _patch_scalar_line(text, "output_device", output_device)
    config_path.write_text(text, encoding="utf-8")
