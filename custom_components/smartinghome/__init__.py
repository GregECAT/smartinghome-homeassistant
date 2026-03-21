"""Smarting HOME — Autonomous Energy Management System for Home Assistant.

HACS Integration by Smarting HOME (smartinghome.pl)
Licensed under Smarting HOME Commercial License.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartingHomeAPI
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_LICENSE_KEY,
    CONF_LICENSE_MODE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    LICENSE_MODE_FREE,
)
from .coordinator import SmartingHomeCoordinator
from .license import LicenseManager
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

SmartingHomeConfigEntry = ConfigEntry

PANEL_TITLE = "Smarting HOME"
PANEL_ICON = "mdi:solar-power-variant"
PANEL_FILENAME = "panel.js"
PANEL_WWW_DIR = "community/smartinghome"


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> bool:
    """Set up Smarting HOME from a config entry."""
    _LOGGER.info("Setting up Smarting HOME Energy Management v1.3.1")

    hass.data.setdefault(DOMAIN, {})

    # Initialize API client
    session = async_get_clientsession(hass)
    license_key = entry.data.get(CONF_LICENSE_KEY, "")
    api = SmartingHomeAPI(session, license_key)

    # Initialize license manager
    license_mode = entry.data.get(CONF_LICENSE_MODE, LICENSE_MODE_FREE)
    license_mgr = LicenseManager(hass, api, license_mode=license_mode)

    # Validate license on startup
    try:
        license_info = await license_mgr.validate()
        if license_info.valid:
            _LOGGER.info(
                "License active: tier=%s, expires=%s",
                license_info.tier,
                license_info.expires,
            )
        else:
            _LOGGER.warning(
                "Running in DEMO mode: %s",
                license_info.message or "License not valid",
            )
    except Exception as err:
        _LOGGER.warning(
            "License validation failed on startup, using DEMO mode: %s", err
        )

    # Initialize data update coordinator
    update_interval = entry.data.get(
        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
    )
    coordinator = SmartingHomeCoordinator(
        hass=hass,
        entry=entry,
        license_manager=license_mgr,
        update_interval=timedelta(seconds=update_interval),
    )

    # Perform initial data fetch
    await coordinator.async_config_entry_first_refresh()

    # Store references
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "license_manager": license_mgr,
        "api": api,
    }

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await async_setup_services(hass, coordinator, license_mgr)

    # Register custom panel in sidebar
    try:
        await _async_register_panel(hass)
    except Exception as err:
        _LOGGER.warning("Could not register sidebar panel: %s", err)

    # Listen for options updates
    entry.async_on_unload(
        entry.add_update_listener(_async_update_listener)
    )

    _LOGGER.info("Smarting HOME setup complete (tier=%s)", license_mgr.tier)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the Smarting HOME panel in the sidebar.

    Approach: copy panel.js to <config>/www/community/smartinghome/
    and register with module_url /local/community/smartinghome/panel.js.
    The /local/ path is HA's built-in static file server for www/.
    """
    import shutil

    source_file = Path(__file__).parent / "frontend" / PANEL_FILENAME
    if not source_file.exists():
        _LOGGER.error("Panel JS not found: %s", source_file)
        return

    # Copy panel.js to <config>/www/community/smartinghome/
    www_dir = Path(hass.config.path("www")) / PANEL_WWW_DIR
    www_dir.mkdir(parents=True, exist_ok=True)
    dest_file = www_dir / PANEL_FILENAME

    try:
        shutil.copy2(str(source_file), str(dest_file))
        _LOGGER.info("Copied panel.js → %s", dest_file)
    except Exception as err:
        _LOGGER.error("Failed to copy panel.js to www/: %s", err)
        return

    # module_url via /local/ — HA's built-in static path for www/
    module_url = f"/local/{PANEL_WWW_DIR}/{PANEL_FILENAME}"

    # Register the panel in the sidebar
    from homeassistant.components.frontend import async_register_built_in_panel

    try:
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            frontend_url_path=DOMAIN,
            require_admin=False,
            config={
                "_panel_custom": {
                    "name": "smartinghome-panel",
                    "module_url": module_url,
                }
            },
        )
        _LOGGER.info("Registered sidebar panel → %s ✅", module_url)
    except Exception as err:
        _LOGGER.warning("Panel already registered or error: %s", err)


async def async_unload_entry(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> bool:
    """Unload a Smarting HOME config entry."""
    _LOGGER.info("Unloading Smarting HOME")

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )

    if unload_ok:
        await async_unload_services(hass)
        hass.data[DOMAIN].pop(entry.entry_id)
        # Remove sidebar panel
        try:
            from homeassistant.components.frontend import async_remove_panel
            async_remove_panel(hass, DOMAIN)
        except Exception:
            _LOGGER.debug("Panel already removed or not registered")

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> None:
    """Handle options update."""
    _LOGGER.info("Options updated, reloading Smarting HOME")
    await hass.config_entries.async_reload(entry.entry_id)
