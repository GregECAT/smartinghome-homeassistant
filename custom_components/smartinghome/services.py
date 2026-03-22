"""Service handlers for Smarting HOME."""
from __future__ import annotations

import base64
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

_LOGGER = logging.getLogger(__name__)

SERVICE_UPLOAD_IMAGE = "upload_inverter_image"
SERVICE_SAVE_SETTINGS = "save_settings"
SERVICE_TEST_API_KEY = "test_api_key"

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
        vol.Optional("gemini_api_key", default=""): cv.string,
        vol.Optional("anthropic_api_key", default=""): cv.string,
    }
)

TEST_API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required("provider"): vol.In(["gemini", "anthropic"]),
    }
)


async def async_setup_services(
    hass: HomeAssistant,
    coordinator: SmartingHomeCoordinator,
    license_mgr: LicenseManager,
) -> None:
    """Register Smarting HOME services."""
    entry = coordinator.entry
    device_id = entry.data.get("device_id", "")

    energy_mgr = EnergyManager(hass, device_id)
    ai_advisor = AIAdvisor(
        hass,
        gemini_api_key=entry.data.get(CONF_GEMINI_API_KEY, ""),
        anthropic_api_key=entry.data.get(CONF_ANTHROPIC_API_KEY, ""),
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
        """Decode base64 image and save to www/smartinghome/inverter.png."""
        filename = call.data["filename"]
        data_b64 = call.data["data"]

        ext = Path(filename).suffix.lower() or ".png"
        out_name = f"inverter{ext}"

        www_dir = Path(hass.config.path("www")) / "smartinghome"
        www_dir.mkdir(parents=True, exist_ok=True)
        dest = www_dir / out_name

        try:
            img_bytes = base64.b64decode(data_b64)
            dest.write_bytes(img_bytes)
            size_kb = len(img_bytes) / 1024
            _LOGGER.info(
                "Inverter image saved: %s (%.1f KB)", dest, size_kb
            )
        except Exception as err:
            _LOGGER.error("Failed to save inverter image: %s", err)

    async def handle_save_settings(call: ServiceCall) -> None:
        """Update config entry with new API keys."""
        gemini_key = call.data.get("gemini_api_key", "")
        anthropic_key = call.data.get("anthropic_api_key", "")

        new_data = {**entry.data}
        if gemini_key:
            new_data[CONF_GEMINI_API_KEY] = gemini_key
        if anthropic_key:
            new_data[CONF_ANTHROPIC_API_KEY] = anthropic_key

        hass.config_entries.async_update_entry(entry, data=new_data)

        if gemini_key:
            ai_advisor._gemini_api_key = gemini_key
        if anthropic_key:
            ai_advisor._anthropic_api_key = anthropic_key

        _LOGGER.info("API keys updated via panel settings")

    async def handle_test_api_key(call: ServiceCall) -> None:
        """Test if an API key is valid by making a minimal request."""
        provider = call.data["provider"]
        try:
            if provider == "gemini":
                valid = await ai_advisor.test_gemini_key()
            else:
                valid = await ai_advisor.test_anthropic_key()
            status = "valid" if valid else "invalid"
        except Exception:
            status = "invalid"

        hass.bus.async_fire(
            f"{DOMAIN}_api_key_test",
            {"provider": provider, "status": status},
        )
        _LOGGER.info("API key test for %s: %s", provider, status)

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

    _LOGGER.info("Registered %d Smarting HOME services", 9)


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
    ]:
        hass.services.async_remove(DOMAIN, service)
