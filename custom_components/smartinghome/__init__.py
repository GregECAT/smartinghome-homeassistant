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

PANEL_FRONTEND_PATH = "/smartinghome_frontend"
PANEL_TITLE = "Smarting HOME"
PANEL_ICON = "mdi:solar-power-variant"


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> bool:
    """Set up Smarting HOME from a config entry."""
    _LOGGER.info("Setting up Smarting HOME Energy Management v1.3.0")

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
        _async_register_panel(hass)
    except Exception as err:
        _LOGGER.warning("Could not register sidebar panel: %s", err)

    # Listen for options updates
    entry.async_on_unload(
        entry.add_update_listener(_async_update_listener)
    )

    _LOGGER.info("Smarting HOME setup complete (tier=%s)", license_mgr.tier)
    return True


def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the Smarting HOME panel in the sidebar."""
    frontend_dir = Path(__file__).parent / "frontend"
    panel_file = frontend_dir / "panel.js"

    if not panel_file.exists():
        _LOGGER.error(
            "Panel JS not found at %s — sidebar panel will NOT appear.",
            panel_file,
        )
        return

    _LOGGER.info(
        "Panel JS found: %s (%d bytes)", panel_file, panel_file.stat().st_size
    )

    # Register the entire frontend directory as a static path
    try:
        hass.http.register_static_path(
            PANEL_FRONTEND_PATH, str(frontend_dir), cache_headers=False
        )
        _LOGGER.info("Registered static path: %s → %s", PANEL_FRONTEND_PATH, frontend_dir)
    except Exception:
        _LOGGER.debug("Static path %s already registered", PANEL_FRONTEND_PATH)

    # Register the panel in the sidebar
    from homeassistant.components.frontend import async_register_built_in_panel

    module_url = f"{PANEL_FRONTEND_PATH}/panel.js"

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
        _LOGGER.warning("Panel registration issue: %s", err)


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
