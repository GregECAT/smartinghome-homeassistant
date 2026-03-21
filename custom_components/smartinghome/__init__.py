"""Smarting HOME — Autonomous Energy Management System for Home Assistant.

HACS Integration by Smarting HOME (smartinghome.pl)
Licensed under Smarting HOME Commercial License.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartingHomeAPI
from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_LICENSE_KEY,
    CONF_DEVICE_ID,
    CONF_TARIFF,
    CONF_RCE_ENABLED,
    CONF_GEMINI_API_KEY,
    CONF_ANTHROPIC_API_KEY,
    CONF_AI_ENABLED,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    SERVICE_SET_MODE,
    SERVICE_FORCE_CHARGE,
    SERVICE_FORCE_DISCHARGE,
    SERVICE_SET_EXPORT_LIMIT,
    SERVICE_ASK_AI,
    SERVICE_GENERATE_REPORT,
    LicenseTier,
    HEMSMode,
)
from .coordinator import SmartingHomeCoordinator
from .license import LicenseManager
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

SmartingHomeConfigEntry = ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> bool:
    """Set up Smarting HOME from a config entry."""
    _LOGGER.info("Setting up Smarting HOME Energy Management v1.0.0")

    hass.data.setdefault(DOMAIN, {})

    # Initialize API client
    session = async_get_clientsession(hass)
    license_key = entry.data.get(CONF_LICENSE_KEY, "")
    api = SmartingHomeAPI(session, license_key)

    # Initialize license manager
    license_mgr = LicenseManager(hass, api)

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

    # Listen for options updates
    entry.async_on_unload(
        entry.add_update_listener(_async_update_listener)
    )

    _LOGGER.info("Smarting HOME setup complete (tier=%s)", license_mgr.tier)
    return True


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

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> None:
    """Handle options update."""
    _LOGGER.info("Options updated, reloading Smarting HOME")
    await hass.config_entries.async_reload(entry.entry_id)
