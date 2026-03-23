"""Service handlers for Smarting HOME."""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_SET_MODE,
    SERVICE_FORCE_CHARGE,
    SERVICE_FORCE_DISCHARGE,
    SERVICE_SET_EXPORT_LIMIT,
    SERVICE_ASK_AI,
    SERVICE_GENERATE_REPORT,
    HEMSMode,
    CONF_GEMINI_API_KEY,
    CONF_ANTHROPIC_API_KEY,
)
from .coordinator import SmartingHomeCoordinator
from .energy_manager import EnergyManager
from .ai_advisor import AIAdvisor
from .license import LicenseManager
from .cron_scheduler import AICronScheduler

_LOGGER = logging.getLogger(__name__)

SERVICE_UPLOAD_IMAGE = "upload_inverter_image"
SERVICE_SAVE_SETTINGS = "save_settings"
SERVICE_TEST_API_KEY = "test_api_key"
SERVICE_SAVE_PANEL_SETTINGS = "save_panel_settings"

SETTINGS_FILE = "settings.json"

SET_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In([m.value for m in HEMSMode]),
    }
)

SET_EXPORT_LIMIT_SCHEMA = vol.Schema(
    {
        vol.Required("limit"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=16000)
        ),
    }
)

ASK_AI_SCHEMA = vol.Schema(
    {
        vol.Required("question"): cv.string,
        vol.Optional("provider", default="auto"): vol.In(
            ["auto", "gemini", "anthropic"]
        ),
    }
)

UPLOAD_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("filename"): cv.string,
        vol.Required("data"): cv.string,
    }
)

SAVE_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Optional("gemini_api_key"): cv.string,
        vol.Optional("anthropic_api_key"): cv.string,
        vol.Optional("gemini_model"): cv.string,
        vol.Optional("anthropic_model"): cv.string,
        vol.Optional("default_ai_provider"): cv.string,
        vol.Optional("gemini_key_status"): cv.string,
        vol.Optional("anthropic_key_status"): cv.string,
    }
)

TEST_API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required("provider"): vol.In(["gemini", "anthropic"]),
        vol.Optional("api_key", default=""): cv.string,
    }
)

SAVE_PANEL_SETTINGS_SCHEMA = vol.Schema(
    {
        vol.Required("settings"): cv.string,
    }
)


async def async_setup_services(
    hass: HomeAssistant,
    coordinator: SmartingHomeCoordinator,
    license_mgr: LicenseManager,
) -> AICronScheduler:
    """Register Smarting HOME services. Returns the AI cron scheduler."""
    entry = coordinator.entry
    device_id = entry.data.get("device_id", "")

    # Helper functions for settings.json (defined first so they can be used below)
    def _get_settings_path(h: HomeAssistant) -> Path:
        """Return path to settings.json."""
        d = Path(h.config.path("www")) / "smartinghome"
        d.mkdir(parents=True, exist_ok=True)
        return d / SETTINGS_FILE

    def _read_settings(h: HomeAssistant) -> dict:
        """Read settings from JSON."""
        p = _get_settings_path(h)
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return {}
        return {}

    def _update_settings_file(h: HomeAssistant, updates: dict) -> None:
        """Merge updates into settings.json."""
        current = _read_settings(h)
        current.update(updates)
        p = _get_settings_path(h)
        p.write_text(json.dumps(current, indent=2, ensure_ascii=False))
        _LOGGER.debug("Settings updated: %s", list(updates.keys()))

    energy_mgr = EnergyManager(hass, device_id)

    # Try to load keys from settings.json first (more reliable than config_entry)
    _settings_keys = _read_settings(hass)
    _gemini_key_init = entry.data.get(CONF_GEMINI_API_KEY, "") or _settings_keys.get("gemini_api_key", "")
    _anthropic_key_init = entry.data.get(CONF_ANTHROPIC_API_KEY, "") or _settings_keys.get("anthropic_api_key", "")

    ai_advisor = AIAdvisor(
        hass,
        gemini_api_key=_gemini_key_init,
        anthropic_api_key=_anthropic_key_init,
    )

    async def handle_set_mode(call: ServiceCall) -> None:
        """Handle set_mode service."""
        mode = HEMSMode(call.data["mode"])
        await energy_mgr.set_mode(mode)

    async def handle_force_charge(call: ServiceCall) -> None:
        """Handle force_charge service."""
        await energy_mgr.force_charge()

    async def handle_force_discharge(call: ServiceCall) -> None:
        """Handle force_discharge service."""
        await energy_mgr.force_discharge()

    async def handle_set_export_limit(call: ServiceCall) -> None:
        """Handle set_export_limit service."""
        limit = call.data["limit"]
        await energy_mgr.set_export_limit(limit)

    async def handle_ask_ai(call: ServiceCall) -> None:
        """Handle ask_ai_advisor service."""
        if not license_mgr.is_pro:
            _LOGGER.warning("AI advisor requires PRO license")
            return

        question = call.data["question"]
        provider = call.data.get("provider", "auto")
        data = coordinator.data or {}

        ai_data = {
            "pv_power": data.get("sensor.pv_power"),
            "grid_power": data.get("sensor.meter_active_power_total"),
            "battery_soc": data.get("sensor.battery_state_of_charge"),
            "battery_power": data.get("sensor.battery_power"),
            "load": data.get("sensor.load"),
            "pv_surplus": data.get("hems_pv_surplus_power"),
            "g13_zone": data.get("g13_current_zone"),
            "g13_price": data.get("g13_buy_price"),
            "rce_price": data.get("sensor.rce_pse_cena"),
            "rce_sell": data.get("rce_sell_price"),
            "rce_trend": data.get("rce_price_trend"),
            "rce_level": data.get("rce_good_sell"),
            "battery_available": data.get("goodwe_battery_energy_available"),
            "battery_runtime": data.get("goodwe_battery_runtime"),
            "forecast_today": data.get("pv_forecast_today_total"),
            "forecast_remaining": data.get("pv_forecast_remaining_today_total"),
            "forecast_tomorrow": data.get("pv_forecast_tomorrow_total"),
            "autarky": data.get("goodwe_autarky_today"),
        }

        if provider == "gemini" or (
            provider == "auto" and ai_advisor.gemini_available
        ):
            response = await ai_advisor.ask_gemini(question, ai_data)
        elif provider == "anthropic" or (
            provider == "auto" and ai_advisor.anthropic_available
        ):
            response = await ai_advisor.ask_anthropic(question, ai_data)
        else:
            response = "No AI provider available."

        hass.bus.async_fire(
            f"{DOMAIN}_ai_response",
            {"question": question, "response": response, "provider": provider},
        )
        _LOGGER.info("AI response: %s", response[:200])

    async def handle_generate_report(call: ServiceCall) -> None:
        """Handle generate_report service."""
        if not license_mgr.is_pro:
            _LOGGER.warning("Report generation requires PRO license")
            return

        data = coordinator.data or {}
        ai_data = {
            "pv_power": data.get("sensor.pv_power"),
            "battery_soc": data.get("sensor.battery_state_of_charge"),
            "load": data.get("sensor.load"),
            "g13_zone": data.get("g13_current_zone"),
            "forecast_today": data.get("pv_forecast_today_total"),
            "forecast_tomorrow": data.get("pv_forecast_tomorrow_total"),
            "autarky": data.get("goodwe_autarky_today"),
        }

        report = await ai_advisor.generate_daily_report(ai_data)
        hass.bus.async_fire(f"{DOMAIN}_daily_report", {"report": report})

    # ── New services: upload, save_settings, test_api_key ──

    async def handle_upload_inverter_image(call: ServiceCall) -> None:
        """Decode base64 image and save to www/smartinghome/."""
        filename = call.data["filename"]
        data_b64 = call.data["data"]

        # Use the provided filename directly (e.g. home.png, inverter.png)
        safe_name = Path(filename).name  # strip any path components
        if not safe_name:
            safe_name = "inverter.png"

        www_dir = Path(hass.config.path("www")) / "smartinghome"
        www_dir.mkdir(parents=True, exist_ok=True)
        dest = www_dir / safe_name

        try:
            img_bytes = base64.b64decode(data_b64)
            dest.write_bytes(img_bytes)
            size_kb = len(img_bytes) / 1024
            _LOGGER.info(
                "Image saved: %s (%.1f KB)", dest, size_kb
            )
        except Exception as err:
            _LOGGER.error("Failed to save image: %s", err)

    async def handle_save_settings(call: ServiceCall) -> None:
        """Update API keys + model settings — store in config_entry AND settings.json."""
        gemini_key = call.data.get("gemini_api_key")
        anthropic_key = call.data.get("anthropic_api_key")
        gemini_model = call.data.get("gemini_model")
        anthropic_model = call.data.get("anthropic_model")
        default_provider = call.data.get("default_ai_provider")

        new_data = {**entry.data}
        updates = {}
        changed = False

        if gemini_key is not None and gemini_key:
            new_data[CONF_GEMINI_API_KEY] = gemini_key
            ai_advisor._gemini_key = gemini_key
            updates["gemini_api_key"] = gemini_key
            updates["gemini_key_status"] = "saved"
            updates["gemini_key_masked"] = gemini_key[:6] + "***" + gemini_key[-4:] if len(gemini_key) > 10 else "***"
            changed = True
        if anthropic_key is not None and anthropic_key:
            new_data[CONF_ANTHROPIC_API_KEY] = anthropic_key
            ai_advisor._anthropic_key = anthropic_key
            updates["anthropic_api_key"] = anthropic_key
            updates["anthropic_key_status"] = "saved"
            updates["anthropic_key_masked"] = anthropic_key[:7] + "***" + anthropic_key[-4:] if len(anthropic_key) > 11 else "***"
            changed = True

        # Update model selections on advisor
        if gemini_model:
            ai_advisor._gemini_model = gemini_model
            updates["gemini_model"] = gemini_model
        if anthropic_model:
            ai_advisor._anthropic_model = anthropic_model
            updates["anthropic_model"] = anthropic_model
        if default_provider:
            updates["default_ai_provider"] = default_provider

        # Pass through status fields from frontend
        for status_key in ("gemini_key_status", "anthropic_key_status"):
            val = call.data.get(status_key)
            if val is not None:
                updates[status_key] = val

        if changed:
            hass.config_entries.async_update_entry(entry, data=new_data)
        if updates:
            _update_settings_file(hass, updates)

        _LOGGER.info("API keys/models updated via panel (changed=%s, updates=%s)", changed, list(updates.keys()))

    async def handle_test_api_key(call: ServiceCall) -> None:
        """Test if an API key is valid by making a minimal request."""
        provider = call.data["provider"]
        test_key = call.data.get("api_key", "")

        # If no key provided in the call, try reading from stored settings
        if not test_key:
            stored = _read_settings(hass)
            if provider == "gemini":
                test_key = stored.get("gemini_api_key", "") or ai_advisor._gemini_key
            else:
                test_key = stored.get("anthropic_api_key", "") or ai_advisor._anthropic_key

        # Also refresh model from settings (user may have changed it in panel)
        stored_for_model = _read_settings(hass)
        gm = stored_for_model.get("gemini_model", "")
        am = stored_for_model.get("anthropic_model", "")
        if gm:
            ai_advisor._gemini_model = gm
        if am:
            ai_advisor._anthropic_model = am

        if not test_key:
            hass.bus.async_fire(
                f"{DOMAIN}_api_key_test",
                {"provider": provider, "status": "invalid"},
            )
            return

        try:
            # Save old key to restore on failure
            old_key = ai_advisor._gemini_key if provider == "gemini" else ai_advisor._anthropic_key
            if provider == "gemini":
                ai_advisor._gemini_key = test_key
                valid = await ai_advisor.test_gemini_key()
                if not valid:
                    ai_advisor._gemini_key = old_key  # restore working key
            else:
                ai_advisor._anthropic_key = test_key
                valid = await ai_advisor.test_anthropic_key()
                if not valid:
                    ai_advisor._anthropic_key = old_key  # restore working key
            status = "valid" if valid else "invalid"
        except Exception:
            status = "invalid"

        hass.bus.async_fire(
            f"{DOMAIN}_api_key_test",
            {"provider": provider, "status": status},
        )
        # Also save to settings.json
        _update_settings_file(hass, {f"{provider}_key_status": status})
        _LOGGER.info("API key test for %s: %s", provider, status)

    async def handle_save_panel_settings(call: ServiceCall) -> None:
        """Save arbitrary panel settings to settings.json."""
        raw = call.data["settings"]
        try:
            incoming = json.loads(raw)
        except Exception as err:
            _LOGGER.error("Invalid JSON in save_panel_settings: %s", err)
            return
        _update_settings_file(hass, incoming)
        _LOGGER.info("Panel settings saved: %s", list(incoming.keys()))

    # Register all services
    hass.services.async_register(
        DOMAIN, SERVICE_SET_MODE, handle_set_mode, schema=SET_MODE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_CHARGE, handle_force_charge
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_DISCHARGE, handle_force_discharge
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_EXPORT_LIMIT, handle_set_export_limit,
        schema=SET_EXPORT_LIMIT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ASK_AI, handle_ask_ai, schema=ASK_AI_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_REPORT, handle_generate_report
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UPLOAD_IMAGE, handle_upload_inverter_image,
        schema=UPLOAD_IMAGE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_SETTINGS, handle_save_settings,
        schema=SAVE_SETTINGS_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TEST_API_KEY, handle_test_api_key,
        schema=TEST_API_KEY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_PANEL_SETTINGS, handle_save_panel_settings,
        schema=SAVE_PANEL_SETTINGS_SCHEMA,
    )

    _LOGGER.info("Registered %d Smarting HOME services", 10)

    # Start AI Cron Scheduler
    cron = AICronScheduler(
        hass,
        ai_advisor,
        get_coordinator_data=lambda: coordinator.data or {},
    )
    await cron.async_start()
    return cron


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Smarting HOME services."""
    for service in [
        SERVICE_SET_MODE,
        SERVICE_FORCE_CHARGE,
        SERVICE_FORCE_DISCHARGE,
        SERVICE_SET_EXPORT_LIMIT,
        SERVICE_ASK_AI,
        SERVICE_GENERATE_REPORT,
        SERVICE_UPLOAD_IMAGE,
        SERVICE_SAVE_SETTINGS,
        SERVICE_TEST_API_KEY,
        SERVICE_SAVE_PANEL_SETTINGS,
    ]:
        hass.services.async_remove(DOMAIN, service)
