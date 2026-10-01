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
from .wind_calendar import WindCalendar
from .const import AutopilotStrategy as AutopilotStrategy, CONF_DEVICE_ID, DEFAULT_GOODWE_DEVICE_ID, CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE, is_grid_only

_LOGGER = logging.getLogger(__name__)

SmartingHomeConfigEntry = ConfigEntry

PANEL_TITLE = "Smarting HOME"
PANEL_ICON = "mdi:solar-power-variant"
PANEL_FILENAME = "panel.js"
PANEL_WWW_DIR = "community/smartinghome"

# Second panel: meter, tariff zones and costs — works with or without PV
METER_PANEL_URL = f"{DOMAIN}-energia"
METER_PANEL_TITLE = "Energia i koszty"
METER_PANEL_ICON = "mdi:meter-electric"
METER_PANEL_FILENAME = "meter.js"
METER_PANEL_ELEMENT = "smartinghome-meter-panel"

# Third panel: the home / office overview (people, cameras, security, rooms)
SITE_PANEL_URL = f"{DOMAIN}-obiekt"
SITE_PANEL_ICON = "mdi:home-city-outline"
SITE_PANEL_FILENAME = "site.js"
SITE_PANEL_ELEMENT = "smartinghome-site-panel"


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

    # ── Sofar sensor_map migration: sofar_ → sofarsolar_ ──
    # Users who configured before v1.54.0 have wrong entity prefix.
    inverter_brand = entry.data.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
    if inverter_brand in ("sofar", "sofarsolar"):
        from .const import CONF_SENSOR_MAP, INVERTER_BRAND_SOFAR
        sensor_map = dict(entry.data.get(CONF_SENSOR_MAP, {}))
        migrated = False
        for key, entity_id in sensor_map.items():
            if entity_id and "sensor.sofar_" in entity_id and "sofarsolar_" not in entity_id:
                sensor_map[key] = entity_id.replace("sensor.sofar_", "sensor.sofarsolar_")
                migrated = True
        if migrated:
            new_data = {**entry.data, CONF_SENSOR_MAP: sensor_map}
            hass.config_entries.async_update_entry(entry, data=new_data)
            _LOGGER.warning(
                "Migrated Sofar sensor_map: sofar_ → sofarsolar_ (%d keys updated)",
                sum(1 for v in sensor_map.values() if v and "sofarsolar_" in v),
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

    # Create Strategy Controller for autonomous HEMS
    device_id_for_ems = entry.data.get(CONF_DEVICE_ID, "") or entry.data.get("device_id", DEFAULT_GOODWE_DEVICE_ID)
    inverter_brand = entry.data.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
    energy_mgr = EnergyManager(hass, device_id_for_ems, inverter_brand=inverter_brand)
    strategy_ctrl = StrategyController(hass, energy_mgr)
    strategy_ctrl.set_inverter_brand(inverter_brand)
    strategy_ctrl.set_forecasters(coordinator.pv_forecaster, coordinator.load_forecaster)
    grid_only = is_grid_only(entry.data)
    if not grid_only:
        # Without an inverter the controller exists (services reference it)
        # but is never ticked — nothing ever writes to an inverter.
        coordinator.set_strategy_controller(strategy_ctrl)

    # Persist inverter_brand to settings.json for panel.js image detection
    try:
        from .settings_io import write_async
        await write_async(hass, {"inverter_brand": inverter_brand})
    except Exception:
        pass

    # Create Schedule Manager
    schedule_mgr = ScheduleManager(hass, strategy_ctrl, energy_mgr)
    if not grid_only:
        coordinator.set_schedule_manager(schedule_mgr)

    # Register services (returns AI cron scheduler)
    cron_scheduler = await async_setup_services(
        hass, coordinator, license_mgr, strategy_ctrl, schedule_mgr,
    )

    # WebSocket API for the panel (AI providers, dynamic model lists)
    from .ws_api import async_register as _register_ws_api
    _register_ws_api(hass)

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

    # Initialize Wind Calendar for daily wind energy tracking
    wind_calendar = WindCalendar(hass)
    await wind_calendar.async_load()
    coordinator.set_wind_calendar(wind_calendar)
    hass.data[DOMAIN][entry.entry_id]["wind_calendar"] = wind_calendar

    # Bootstrap wind history from Recorder (async, non-blocking)
    async def _bootstrap_wind():
        try:
            count = await wind_calendar.bootstrap_from_recorder()
            if count > 0:
                _LOGGER.info("Wind calendar bootstrapped: %d days", count)
        except Exception as err:
            _LOGGER.debug("Wind calendar bootstrap skipped: %s", err)

    hass.async_create_task(_bootstrap_wind())

    # Register custom panels in sidebar (no PV/battery panel without an inverter)
    try:
        await _async_register_panel(hass, main=not grid_only)
    except Exception as err:
        _LOGGER.warning("Could not register sidebar panel: %s", err)

    # Listen for options updates
    entry.async_on_unload(
        entry.add_update_listener(_async_update_listener)
    )

    _LOGGER.info("Smarting HOME setup complete (tier=%s)", license_mgr.tier)
    return True


async def _async_register_panel(hass: HomeAssistant, main: bool = True) -> None:
    """Register the Smarting HOME panels in the sidebar.

    Approach: copy the panel JS files to <config>/www/community/smartinghome/
    and register them with module_url /local/community/smartinghome/<file>.
    The /local/ path is HA's built-in static file server for www/.

    main: the PV/battery panel (skipped for installations without an inverter).
    The "Energia i koszty" meter panel is always registered.
    """
    import shutil

    frontend = Path(__file__).parent / "frontend"
    www_dir = Path(hass.config.path("www")) / PANEL_WWW_DIR
    www_dir.mkdir(parents=True, exist_ok=True)

    img_source = frontend / "img"
    try:
        # Bundled graphics (inverters per brand, house, grid) — no external hosting
        if img_source.is_dir():
            await hass.async_add_executor_job(
                lambda: shutil.copytree(img_source, www_dir / "img", dirs_exist_ok=True)
            )
    except Exception as err:  # noqa: BLE001 — images are optional
        _LOGGER.warning("Failed to copy panel images to www/: %s", err)

    panels = [
        (SITE_PANEL_URL, await _async_site_title(hass), SITE_PANEL_ICON,
         SITE_PANEL_FILENAME, SITE_PANEL_ELEMENT),
        (METER_PANEL_URL, METER_PANEL_TITLE, METER_PANEL_ICON,
         METER_PANEL_FILENAME, METER_PANEL_ELEMENT),
    ]
    if main:
        panels.insert(0, (DOMAIN, PANEL_TITLE, PANEL_ICON, PANEL_FILENAME, "smartinghome-panel"))

    for url_path, title, icon, filename, element in panels:
        module_url = await _async_publish_js(hass, frontend / filename, www_dir)
        if module_url is None:
            continue
        _register_sidebar_panel(hass, url_path, title, icon, element, module_url)


async def _async_site_title(hass: HomeAssistant) -> str:
    """Sidebar name of the overview panel: custom title, else Biuro / Dom."""
    try:
        from .settings_io import read_async

        settings = await read_async(hass)
    except Exception:  # noqa: BLE001 — a missing settings file means defaults
        settings = {}
    site = settings.get("site") if isinstance(settings.get("site"), dict) else {}
    meter = settings.get("meter") if isinstance(settings.get("meter"), dict) else {}
    if site.get("title"):
        return str(site["title"])[:40]
    return "Biuro" if meter.get("site_kind") == "business" else "Dom"


async def _async_publish_js(hass: HomeAssistant, source: Path, www_dir: Path) -> str | None:
    """Copy a panel JS file to www/ and return its cache-busted /local/ URL."""
    import hashlib
    import shutil

    if not source.exists():
        _LOGGER.error("Panel JS not found: %s", source)
        return None
    dest = www_dir / source.name
    try:
        await hass.async_add_executor_job(shutil.copy2, str(source), str(dest))
        _LOGGER.info("Copied %s → %s", source.name, dest)
    except Exception as err:
        _LOGGER.error("Failed to copy %s to www/: %s", source.name, err)
        return None
    # Cache-busting: hash of file content as query param
    file_bytes = await hass.async_add_executor_job(dest.read_bytes)
    file_hash = hashlib.md5(file_bytes).hexdigest()[:8]
    return f"/local/{PANEL_WWW_DIR}/{source.name}?v={file_hash}"


def _register_sidebar_panel(
    hass: HomeAssistant, url_path: str, title: str, icon: str, element: str, module_url: str
) -> None:
    from homeassistant.components.frontend import async_register_built_in_panel

    try:
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title=title,
            sidebar_icon=icon,
            frontend_url_path=url_path,
            require_admin=False,
            config={
                "_panel_custom": {
                    "name": element,
                    "module_url": module_url,
                }
            },
        )
        _LOGGER.info("Registered sidebar panel %s → %s ✅", url_path, module_url)
    except Exception as err:
        _LOGGER.warning("Panel %s already registered or error: %s", url_path, err)

    # Inject dashboard proxy so the panel appears in the default-panel dropdown
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        lovelace_data = hass.data.get(LOVELACE_DATA)
        if lovelace_data is not None:
            lovelace_data.dashboards[url_path] = SmartingHomeDashboardProxy(url_path, title, icon)
            _LOGGER.info("Injected dashboard proxy for %s ✅", url_path)
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
        for url_path in (DOMAIN, METER_PANEL_URL, SITE_PANEL_URL):
            # Remove sidebar panel
            try:
                from homeassistant.components.frontend import async_remove_panel
                async_remove_panel(hass, url_path)
            except Exception:
                _LOGGER.debug("Panel %s already removed or not registered", url_path)

            # Remove dashboard proxy
            try:
                from homeassistant.components.lovelace.const import LOVELACE_DATA

                lovelace_data = hass.data.get(LOVELACE_DATA)
                if lovelace_data and url_path in lovelace_data.dashboards:
                    del lovelace_data.dashboards[url_path]
                    _LOGGER.debug("Removed dashboard proxy %s", url_path)
            except Exception:
                _LOGGER.debug("Dashboard proxy %s already removed", url_path)

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
