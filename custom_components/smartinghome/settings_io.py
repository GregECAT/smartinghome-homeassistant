"""Thread-safe settings I/O for Smarting HOME.

All components that need to read/write settings should import from
here to avoid race conditions when multiple writers operate concurrently.

Key features:
- threading.Lock ensures only one writer at a time
- Atomic write via tmp → rename prevents partial/corrupt reads
- Private location: <config>/.storage/smartinghome.settings.json.
  Before v1.58.0 the file lived in <config>/www/smartinghome/settings.json,
  which HA serves under /local/ WITHOUT authentication. It is migrated
  (and removed from www/) on first access. The panel reads/writes through
  the authenticated WebSocket API (ws_api.py: smartinghome/settings/*).
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)
_lock = threading.RLock()
SETTINGS_FILE = "smartinghome.settings.json"
LEGACY_SETTINGS_FILE = "settings.json"

# Never returned to / accepted from the panel (keys live in AISecrets)
SECRET_KEYS = ("gemini_api_key", "anthropic_api_key", "openrouter_api_key")

_migrated = False


def legacy_path(hass: HomeAssistant) -> Path:
    """Public pre-v1.58.0 location (served under /local/ without auth)."""
    return Path(hass.config.path("www")) / "smartinghome" / LEGACY_SETTINGS_FILE


def _migrate_legacy(hass: HomeAssistant, private: Path) -> None:
    """Move www/smartinghome/settings.json into private storage (once per run).

    If both files exist (e.g. after a downgrade + upgrade), the newer one wins.
    The public copy is deleted afterwards; uploaded images stay in www/.
    """
    global _migrated
    if _migrated:
        return
    legacy = legacy_path(hass)
    try:
        if not legacy.exists():
            return
        if not private.exists() or legacy.stat().st_mtime > private.stat().st_mtime:
            data = json.loads(legacy.read_text())
            if not isinstance(data, dict):
                data = {}
            tmp = private.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            tmp.replace(private)
            _LOGGER.warning(
                "Moved settings from public %s to private %s (%d keys)",
                legacy, private, len(data),
            )
        legacy.unlink()
        legacy.with_suffix(".tmp").unlink(missing_ok=True)
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Could not migrate %s to private storage: %s", legacy, err)
    finally:
        # Set only once done (also on error — retrying every read would spam
        # the log), so get_path() callers skip the lock only after the private
        # file is in place instead of reading {} mid-migration.
        _migrated = True


def get_path(hass: HomeAssistant) -> Path:
    """Return path to the private settings file, migrating the legacy one."""
    d = Path(hass.config.path(".storage"))
    d.mkdir(parents=True, exist_ok=True)
    p = d / SETTINGS_FILE
    if not _migrated:
        with _lock:
            _migrate_legacy(hass, p)
    return p


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
    """Merge updates into settings (thread-safe, atomic write).

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


async def read_async(hass: HomeAssistant) -> dict[str, Any]:
    """Read settings from JSON (async-safe wrapper)."""
    return await hass.async_add_executor_job(read_sync, hass)


async def write_async(hass: HomeAssistant, updates: dict[str, Any]) -> None:
    """Merge updates into settings (async-safe wrapper)."""
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
