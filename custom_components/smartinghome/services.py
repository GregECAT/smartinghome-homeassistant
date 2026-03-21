"""Service handlers for Smarting HOME."""
from __future__ import annotations

import logging

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

        # Build AI context from coordinator data
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

        if provider == "gemini" or (provider == "auto" and ai_advisor.gemini_available):
            response = await ai_advisor.ask_gemini(question, ai_data)
        elif provider == "anthropic" or (provider == "auto" and ai_advisor.anthropic_available):
            response = await ai_advisor.ask_anthropic(question, ai_data)
        else:
            response = "No AI provider available."

        # Fire an event with the response
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

        hass.bus.async_fire(
            f"{DOMAIN}_daily_report",
            {"report": report},
        )

    # Register services
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

    _LOGGER.info("Registered %d Smarting HOME services", 6)


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Smarting HOME services."""
    for service in [
        SERVICE_SET_MODE,
        SERVICE_FORCE_CHARGE,
        SERVICE_FORCE_DISCHARGE,
        SERVICE_SET_EXPORT_LIMIT,
        SERVICE_ASK_AI,
        SERVICE_GENERATE_REPORT,
    ]:
        hass.services.async_remove(DOMAIN, service)
