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
from .strategy_controller import StrategyController
from .energy_manager import EnergyManager
from .schedule_manager import ScheduleManager
from .const import AutopilotStrategy as AutopilotStrategy, CONF_DEVICE_ID, DEFAULT_GOODWE_DEVICE_ID, CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE

_LOGGER = logging.getLogger(__name__)

SmartingHomeConfigEntry = ConfigEntry

PANEL_TITLE = "Smarting HOME"
PANEL_ICON = "mdi:solar-power-variant"
PANEL_FILENAME = "panel.js"
PANEL_WWW_DIR = "community/smartinghome"


class SmartingHomeDashboardProxy:
    """Proxy so Smarting HOME appears in the default-panel dropdown.

    HA's "Pick default panel" dropdown reads from
    hass.data[LOVELACE_DATA].dashboards.  By injecting this lightweight
    proxy, fetchDashboards() returns our panel alongside real Lovelace
    dashboards — without changing how the panel actually renders.
    """

    def __init__(self, url_path: str, title: str, icon: str) -> None:
        self.config = {
            "id": url_path,
            "url_path": url_path,
            "title": title,
            "icon": icon,
            "show_in_sidebar": True,
            "require_admin": False,
            "mode": "storage",
        }


async def async_setup_entry(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> bool:
    """Set up Smarting HOME from a config entry."""
    _LOGGER.info("Setting up Smarting HOME Energy Management v1.15.0")

    hass.data.setdefault(DOMAIN, {})

    # Get device identity (HA instance UUID)
    try:
        from homeassistant.helpers.instance_id import async_get as async_get_instance_id
        device_id = await async_get_instance_id(hass)
    except Exception:
        device_id = str(hass.data.get("core.uuid", entry.entry_id))

    try:
        from homeassistant.const import __version__ as ha_ver
        ha_version = ha_ver
    except Exception:
        ha_version = "unknown"

    # Initialize API client with device identity
    session = async_get_clientsession(hass)
    license_key = entry.data.get(CONF_LICENSE_KEY, "")
    license_mode = entry.data.get(CONF_LICENSE_MODE, LICENSE_MODE_FREE)

    _LOGGER.info(
        "License config: mode=%s, key=%s..., device_id=%s, ha=%s",
        license_mode,
        license_key[:12] if license_key else "(none)",
        device_id[:12] if device_id else "(none)",
        ha_version,
    )

    api = SmartingHomeAPI(
        session,
        license_key,
        device_id=device_id,
        ha_version=ha_version,
        integration_version="1.15.0",
    )

    # Initialize license manager
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

    # Register device for telemetry (FREE and PRO)
    try:
        if license_mode == LICENSE_MODE_FREE:
            await api.register_free_device()
            _LOGGER.info("FREE device telemetry registered ✅")
    except Exception as err:
        _LOGGER.debug("FREE registration skipped: %s", err)

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

    # Create Strategy Controller for autonomous HEMS
    device_id_for_ems = entry.data.get(CONF_DEVICE_ID, "") or entry.data.get("device_id", DEFAULT_GOODWE_DEVICE_ID)
    inverter_brand = entry.data.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
    energy_mgr = EnergyManager(hass, device_id_for_ems, inverter_brand=inverter_brand)
    strategy_ctrl = StrategyController(hass, energy_mgr)
    strategy_ctrl.set_inverter_brand(inverter_brand)
    coordinator.set_strategy_controller(strategy_ctrl)

    # Persist inverter_brand to settings.json for panel.js image detection
    try:
        from .settings_io import write_async
        await write_async(hass, {"inverter_brand": inverter_brand})
    except Exception:
        pass

    # Create Schedule Manager
    schedule_mgr = ScheduleManager(hass, strategy_ctrl, energy_mgr)
    coordinator.set_schedule_manager(schedule_mgr)

    # Register services (returns AI cron scheduler)
    cron_scheduler = await async_setup_services(
        hass, coordinator, license_mgr, strategy_ctrl, schedule_mgr,
    )

    # Restore saved autopilot state from settings.json
    #   (strategy, enabled, action toggles, disabled actions)
    try:
        await strategy_ctrl.restore_state()
    except Exception as err:
        _LOGGER.debug("Could not restore autopilot state: %s", err)

    # Restore schedule state from settings.json
    try:
        await schedule_mgr.restore_schedule()
    except Exception as err:
        _LOGGER.debug("Could not restore schedule state: %s", err)

    # Store references for cleanup
    hass.data[DOMAIN][entry.entry_id]["cron_scheduler"] = cron_scheduler
    hass.data[DOMAIN][entry.entry_id]["strategy_controller"] = strategy_ctrl
    hass.data[DOMAIN][entry.entry_id]["schedule_manager"] = schedule_mgr

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
    import hashlib
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
        await hass.async_add_executor_job(
            shutil.copy2, str(source_file), str(dest_file)
        )
        _LOGGER.info("Copied panel.js → %s", dest_file)
    except Exception as err:
        _LOGGER.error("Failed to copy panel.js to www/: %s", err)
        return

    # Cache-busting: hash of file content as query param
    file_bytes = await hass.async_add_executor_job(dest_file.read_bytes)
    file_hash = hashlib.md5(file_bytes).hexdigest()[:8]
    module_url = f"/local/{PANEL_WWW_DIR}/{PANEL_FILENAME}?v={file_hash}"

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

    # Inject dashboard proxy so panel appears in default-panel dropdown
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is not None:
            proxy = SmartingHomeDashboardProxy(DOMAIN, PANEL_TITLE, PANEL_ICON)
            lovelace_data.dashboards[DOMAIN] = proxy
            _LOGGER.info("Injected dashboard proxy for default-panel dropdown ✅")
        else:
            _LOGGER.debug("Lovelace data not available yet, skipping proxy")
    except Exception as err:
        _LOGGER.debug("Could not inject dashboard proxy: %s", err)


async def async_unload_entry(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> bool:
    """Unload a Smarting HOME config entry."""
    _LOGGER.info("Unloading Smarting HOME")

    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )

    if unload_ok:
        # Stop AI cron scheduler
        cron = hass.data[DOMAIN].get(entry.entry_id, {}).get("cron_scheduler")
        if cron:
            await cron.async_stop()
        await async_unload_services(hass)
        hass.data[DOMAIN].pop(entry.entry_id)
        # Remove sidebar panel
        try:
            from homeassistant.components.frontend import async_remove_panel
            async_remove_panel(hass, DOMAIN)
        except Exception:
            _LOGGER.debug("Panel already removed or not registered")

        # Remove dashboard proxy
        try:
            from homeassistant.components.lovelace.const import LOVELACE_DATA

            lovelace_data = hass.data.get(LOVELACE_DATA)
            if lovelace_data and DOMAIN in lovelace_data.dashboards:
                del lovelace_data.dashboards[DOMAIN]
                _LOGGER.debug("Removed dashboard proxy")
        except Exception:
            _LOGGER.debug("Dashboard proxy already removed")

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: SmartingHomeConfigEntry
) -> None:
    """Handle options update.

    Skip full reload if only API keys were updated (_keys_only_update flag).
    This prevents destroying the ai_advisor when keys change.
    """
    if entry.data.get("_keys_only_update"):
        # Clear the flag and DON'T reload
        new_data = {**entry.data}
        new_data.pop("_keys_only_update", None)
        hass.config_entries.async_update_entry(entry, data=new_data)
        _LOGGER.info("API keys updated (no reload needed)")
        return

    _LOGGER.info("Options updated, reloading Smarting HOME")
    await hass.config_entries.async_reload(entry.entry_id)
