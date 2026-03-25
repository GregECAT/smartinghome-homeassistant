"""Thread-safe settings.json I/O for Smarting HOME.

All components that need to read/write settings.json should import from
here to avoid race conditions when multiple writers operate concurrently.

Key features:
- threading.Lock ensures only one writer at a time
- Atomic write via tmp → rename prevents partial/corrupt reads
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_lock = threading.Lock()
SETTINGS_FILE = "settings.json"


def get_path(hass: HomeAssistant) -> Path:
    """Return path to settings.json, creating parent dirs if needed."""
    d = Path(hass.config.path("www")) / "smartinghome"
    d.mkdir(parents=True, exist_ok=True)
    return d / SETTINGS_FILE


def read_sync(hass: HomeAssistant) -> dict[str, Any]:
    """Read settings from JSON (sync — call via executor for async contexts)."""
    p = get_path(hass)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def write_sync(hass: HomeAssistant, updates: dict[str, Any]) -> None:
    """Merge updates into settings.json (thread-safe, atomic write).

    Uses a lock to prevent concurrent read-modify-write races, and
    writes to a .tmp file first then renames for filesystem atomicity.
    """
    with _lock:
        current = read_sync(hass)
        current.update(updates)
        p = get_path(hass)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False))
        tmp.replace(p)
    _LOGGER.debug("Settings updated: %s", list(updates.keys()))


async def write_async(hass: HomeAssistant, updates: dict[str, Any]) -> None:
    """Merge updates into settings.json (async-safe wrapper)."""
    await hass.async_add_executor_job(write_sync, hass, updates)


def read_and_write_sync(
    hass: HomeAssistant, key: str, value: Any
) -> dict[str, Any]:
    """Read current settings, update one key, write back. Returns full dict.

    Useful for coordinator-style writes that need the full dict for merging.
    """
    with _lock:
        current = read_sync(hass)
        current[key] = value
        p = get_path(hass)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False))
        tmp.replace(p)
    return current
