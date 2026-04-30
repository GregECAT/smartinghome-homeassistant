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
    SERVICE_FORCE_CUSTOM,
    SERVICE_STOP_FORCE_CHARGE,
    SERVICE_STOP_FORCE_DISCHARGE,
    SERVICE_EMERGENCY_STOP,
    SERVICE_SET_EXPORT_LIMIT,
    SERVICE_ASK_AI,
    SERVICE_GENERATE_REPORT,
    SERVICE_RUN_AUTOPILOT,
    SERVICE_SAVE_SCHEDULE,
    SERVICE_GET_SCHEDULE_STATUS,
    SERVICE_APPLY_MANUAL_MODE,
    HEMSMode,
    AutopilotStrategy,
    ManualMode,
    CONF_GEMINI_API_KEY,
    CONF_ANTHROPIC_API_KEY,
    CONF_ECOWITT_ENABLED,
    CONF_SENSOR_MAP,
    AUTOPILOT_PLAN_KEY,
    AUTOPILOT_SETTINGS_KEY,
)
from .coordinator import SmartingHomeCoordinator
from .energy_manager import EnergyManager
from .ai_advisor import AIAdvisor
from .license import LicenseManager
from .cron_scheduler import AICronScheduler
from .autopilot_engine import AutopilotEngine, build_autopilot_ai_prompt, DEFAULT_BATTERY_CAPACITY
from .strategy_controller import StrategyController
from .settings_io import (
    read_sync as _read_settings_io,
    write_async as _update_settings_file_io,
    get_path as _get_settings_path_io,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_UPLOAD_IMAGE = "upload_inverter_image"
SERVICE_SAVE_SETTINGS = "save_settings"
SERVICE_TEST_API_KEY = "test_api_key"
SERVICE_SAVE_PANEL_SETTINGS = "save_panel_settings"
SERVICE_SET_AUTOPILOT_STRATEGY = "set_autopilot_strategy"
SERVICE_DEACTIVATE_AUTOPILOT = "deactivate_autopilot"
SERVICE_TRIGGER_AUTOPILOT_ACTION = "trigger_autopilot_action"
SERVICE_TOGGLE_AUTOPILOT_ACTION = "toggle_autopilot_action"
SERVICE_SYNC_ECOWITT_STATE = "sync_ecowitt_state"
SERVICE_UPDATE_SENSOR_MAP = "update_sensor_map"
SERVICE_SEND_ALERT_NOTIFICATION = "send_alert_notification"
SERVICE_SET_PEAK_SELL_PERCENT = "set_peak_sell_percent"
SERVICE_GET_WIND_CALENDAR = "get_wind_calendar"
SERVICE_GET_WIND_SUMMARY = "get_wind_summary"
SERVICE_RECALCULATE_WIND_CALENDAR = "recalculate_wind_calendar"

ALERT_WEBHOOK_URL = "https://a.gregciupek.com/webhook/1161573e-6e16-4884-97f9-e98d7f6d04e2"

FORCE_CUSTOM_SCHEMA = vol.Schema(
    {
        vol.Optional("work_mode"): cv.string,
        vol.Optional("modbus_47511", default=-1): vol.All(
            vol.Coerce(int), vol.Range(min=-1, max=10)
        ),
        vol.Optional("charge_current"): cv.string,
        vol.Optional("export_limit"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=16000)
        ),
        vol.Optional("eco_mode_power"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional("eco_mode_soc"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
    }
)

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

RUN_AUTOPILOT_SCHEMA = vol.Schema(
    {
        vol.Optional("strategy", default="max_self_consumption"): vol.In(
            [s.value for s in AutopilotStrategy]
        ),
        vol.Optional("provider", default="auto"): vol.In(
            ["auto", "gemini", "anthropic"]
        ),
        vol.Optional("with_ai", default=True): cv.boolean,
    }
)

SET_AUTOPILOT_STRATEGY_SCHEMA = vol.Schema(
    {
        vol.Required("strategy"): vol.In(
            [s.value for s in AutopilotStrategy]
        ),
    }
)

TRIGGER_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("action_id"): cv.string,
    }
)

TOGGLE_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("action_id"): cv.string,
        vol.Required("enabled"): cv.boolean,
    }
)

SYNC_ECOWITT_STATE_SCHEMA = vol.Schema(
    {
        vol.Required("enabled"): cv.boolean,
    }
)

UPDATE_SENSOR_MAP_SCHEMA = vol.Schema(
    {
        vol.Required("sensor_key"): cv.string,
        vol.Required("entity_id"): cv.string,
    }
)

SEND_ALERT_SCHEMA = vol.Schema(
    {
        vol.Required("alert_id"): cv.string,
        vol.Required("level"): vol.In(["critical", "warning", "info"]),
        vol.Required("source"): cv.string,
        vol.Required("title"): cv.string,
        vol.Required("message"): cv.string,
        vol.Optional("diag_action", default=""): cv.string,
    }
)

SET_PEAK_SELL_SCHEMA = vol.Schema(
    {
        vol.Required("percent"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=80)
        ),
    }
)

SAVE_SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional("schedule_weekday"): cv.string,
        vol.Optional("schedule_weekend"): cv.string,
        vol.Optional("schedule_enabled"): cv.boolean,
        vol.Optional("schedule_mode"): vol.In(["weekday_weekend", "single"]),
    }
)

APPLY_MANUAL_MODE_SCHEMA = vol.Schema(
    {
        vol.Required("mode"): vol.In([m.value for m in ManualMode]),
    }
)

GET_WIND_CALENDAR_SCHEMA = vol.Schema(
    {
        vol.Optional("start_date", default=""): cv.string,
        vol.Optional("end_date", default=""): cv.string,
    }
)

GET_WIND_SUMMARY_SCHEMA = vol.Schema(
    {
        vol.Optional("start_date", default=""): cv.string,
        vol.Optional("end_date", default=""): cv.string,
    }
)


async def async_setup_services(
    hass: HomeAssistant,
    coordinator: SmartingHomeCoordinator,
    license_mgr: LicenseManager,
    strategy_controller: StrategyController | None = None,
    schedule_manager=None,
) -> AICronScheduler:
    """Register Smarting HOME services. Returns the AI cron scheduler."""
    entry = coordinator.entry
    device_id = entry.data.get("device_id", "")

    # Helper functions for settings.json — delegated to centralized settings_io
    def _get_settings_path(h: HomeAssistant) -> Path:
        return _get_settings_path_io(h)

    def _read_settings(h: HomeAssistant) -> dict:
        return _read_settings_io(h)

    async def _update_settings_file(h: HomeAssistant, updates: dict) -> None:
        await _update_settings_file_io(h, updates)

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

    # Wire AI advisor to strategy controller for AI Full Autonomy mode
    if strategy_controller is not None:
        strategy_controller.set_ai_advisor(ai_advisor)

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

    async def handle_stop_force_charge(call: ServiceCall) -> None:
        """Handle stop_force_charge service."""
        await energy_mgr.stop_force_charge()

    async def handle_stop_force_discharge(call: ServiceCall) -> None:
        """Handle stop_force_discharge service."""
        await energy_mgr.stop_force_discharge()

    async def handle_emergency_stop(call: ServiceCall) -> None:
        """Handle emergency_stop service."""
        await energy_mgr.emergency_stop()

    async def handle_force_custom(call: ServiceCall) -> None:
        """Handle force_custom service — configurable force command."""
        work_mode = call.data.get("work_mode")
        modbus_val = call.data.get("modbus_47511", -1)
        charge_current = call.data.get("charge_current")
        export_limit = call.data.get("export_limit")
        eco_mode_power = call.data.get("eco_mode_power")
        eco_mode_soc = call.data.get("eco_mode_soc")

        result = await energy_mgr.force_custom(
            work_mode=work_mode,
            modbus_47511=modbus_val if modbus_val >= 0 else None,
            charge_current=charge_current,
            export_limit=export_limit,
            eco_mode_power=eco_mode_power,
            eco_mode_soc=eco_mode_soc,
        )

        # Fire event for frontend feedback
        hass.bus.async_fire(
            f"{DOMAIN}_force_custom_result",
            result,
        )
        _LOGGER.info("Force custom: %s", result)

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
            # GoodWe raw: +export/-import → invert to AI convention: +import/-export
            "grid_power": -1 * float(data.get("sensor.meter_active_power_total") or 0),
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
            # Phase 1: Battery health & diagnostics
            "battery_soh": data.get("battery_soh"),
            "battery_health": data.get("battery_health_score"),
            "inverter_thermal": data.get("inverter_thermal_status"),
            "grid_power_factor": data.get("grid_power_factor"),
            "has_errors": data.get("has_active_errors"),
            "ems_mode": data.get("ems_mode"),
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
        """Update API keys + model settings — store in settings.json ONLY.

        IMPORTANT: Do NOT call async_update_entry here!
        It triggers _async_update_listener → async_reload → full integration
        restart, which destroys the ai_advisor before settings.json is written.
        """
        gemini_key = call.data.get("gemini_api_key")
        anthropic_key = call.data.get("anthropic_api_key")
        gemini_model = call.data.get("gemini_model")
        anthropic_model = call.data.get("anthropic_model")
        default_provider = call.data.get("default_ai_provider")

        updates = {}

        if gemini_key is not None and gemini_key:
            ai_advisor._gemini_key = gemini_key
            updates["gemini_api_key"] = gemini_key
            updates["gemini_key_status"] = "saved"
            updates["gemini_key_masked"] = gemini_key[:6] + "***" + gemini_key[-4:] if len(gemini_key) > 10 else "***"
        if anthropic_key is not None and anthropic_key:
            ai_advisor._anthropic_key = anthropic_key
            updates["anthropic_api_key"] = anthropic_key
            updates["anthropic_key_status"] = "saved"
            updates["anthropic_key_masked"] = anthropic_key[:7] + "***" + anthropic_key[-4:] if len(anthropic_key) > 11 else "***"

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

        if updates:
            await _update_settings_file(hass, updates)

        _LOGGER.info("API keys/models updated via panel (updates=%s)", list(updates.keys()))

    async def handle_test_api_key(call: ServiceCall) -> None:
        """Test if an API key is valid by making a minimal request."""
        provider = call.data["provider"]
        test_key = call.data.get("api_key", "")

        # If no key provided in the call, try reading from stored settings
        if not test_key:
            stored = _read_settings(hass)
            def _clean(k: str) -> str:
                """Return empty string for masked or empty keys."""
                return "" if not k or "***" in k else k

            if provider == "gemini":
                test_key = (
                    _clean(stored.get("gemini_api_key", ""))
                    or _clean(entry.data.get(CONF_GEMINI_API_KEY, ""))
                    or _clean(ai_advisor._gemini_key)
                )
            else:
                test_key = (
                    _clean(stored.get("anthropic_api_key", ""))
                    or _clean(entry.data.get(CONF_ANTHROPIC_API_KEY, ""))
                    or _clean(ai_advisor._anthropic_key)
                )
            _LOGGER.info(
                "Test %s: key from settings (len=%d, prefix=%s)",
                provider, len(test_key), test_key[:8] + "..." if len(test_key) > 8 else test_key
            )

        # Also refresh model from settings (user may have changed it in panel)
        stored_for_model = _read_settings(hass)
        gm = stored_for_model.get("gemini_model", "")
        am = stored_for_model.get("anthropic_model", "")
        if gm:
            ai_advisor._gemini_model = gm
        if am:
            # Normalize old model IDs to clean format
            import re
            am = re.sub(r"claude-sonnet-4[\.\-]6.*", "claude-sonnet-4-6", am)
            am = re.sub(r"claude-opus-4[\.\-]6.*", "claude-opus-4-6", am)
            am = re.sub(r"claude-haiku-[34][\.\-]5.*", "claude-3-5-haiku", am)
            ai_advisor._anthropic_model = am
        _LOGGER.info(
            "Test %s: model=%s",
            provider, ai_advisor._gemini_model if provider == "gemini" else ai_advisor._anthropic_model
        )

        if not test_key:
            hass.bus.async_fire(
                f"{DOMAIN}_api_key_test",
                {"provider": provider, "status": "invalid"},
            )
            _LOGGER.warning("Test %s: no key found anywhere!", provider)
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
        await _update_settings_file(hass, {f"{provider}_key_status": status})
        _LOGGER.info("API key test for %s: %s", provider, status)

    async def handle_save_panel_settings(call: ServiceCall) -> None:
        """Save arbitrary panel settings to settings.json."""
        raw = call.data["settings"]
        try:
            incoming = json.loads(raw)
        except Exception as err:
            _LOGGER.error("Invalid JSON in save_panel_settings: %s", err)
            return
        # SAFETY: never let panel settings overwrite API keys
        for danger_key in ("gemini_api_key", "anthropic_api_key"):
            incoming.pop(danger_key, None)
        await _update_settings_file(hass, incoming)
        _LOGGER.info("Panel settings saved: %s", list(incoming.keys()))

    # Autopilot engine — instantiated per-request with actual data
    # (no longer using hardcoded defaults)

    async def handle_run_autopilot(call: ServiceCall) -> None:
        """Handle run_autopilot service — AI-powered strategy estimation."""
        if not license_mgr.is_pro:
            _LOGGER.warning("Autopilot requires PRO or ENTERPRISE license")
            hass.bus.async_fire(
                f"{DOMAIN}_autopilot_result",
                {"error": "Autopilot wymaga licencji PRO lub ENTERPRISE."},
            )
            return

        strategy_str = call.data.get("strategy", "max_self_consumption")
        provider = call.data.get("provider", "auto")
        with_ai = call.data.get("with_ai", True)

        try:
            strategy = AutopilotStrategy(strategy_str)
        except ValueError:
            strategy = AutopilotStrategy.MAX_SELF_CONSUMPTION

        data = coordinator.data or {}

        # Instantiate AutopilotEngine with actual system values
        battery_cap = float(
            data.get("sensor.battery_capacity")
            or data.get("battery_capacity_wh")
            or DEFAULT_BATTERY_CAPACITY
        )
        pv_peak = float(
            data.get("sensor.pv_rated_power")
            or data.get("pv_peak_w")
            or 0
        )
        autopilot_engine = AutopilotEngine(
            battery_capacity_wh=battery_cap,
            pv_peak_w=pv_peak,
        )

        ai_data = {
            "pv_power": data.get("sensor.pv_power"),
            # GoodWe raw: +export/-import → invert to AI convention: +import/-export
            "grid_power": -1 * float(data.get("sensor.meter_active_power_total") or 0),
            "battery_soc": data.get("sensor.battery_state_of_charge"),
            "battery_power": data.get("sensor.battery_power"),
            "load": data.get("sensor.load"),
            "pv_surplus": data.get("hems_pv_surplus_power"),
            "g13_zone": data.get("g13_current_zone"),
            "g13_price": data.get("g13_buy_price"),
            "rce_price": data.get("sensor.rce_pse_cena"),
            "rce_sell": data.get("rce_sell_price"),
            "rce_trend": data.get("rce_price_trend"),
            "forecast_today": data.get("pv_forecast_today_total"),
            "forecast_remaining": data.get("pv_forecast_remaining_today_total"),
            "forecast_tomorrow": data.get("pv_forecast_tomorrow_total"),
            "voltage_l1": data.get("sensor.grid_voltage_l1"),
            "battery_capacity": data.get("sensor.battery_capacity",
                                         data.get("battery_capacity_wh", 10000)),
            # RCE future prices for arbitrage decisions (v2 keys)
            "rce_next_period": data.get("rce_sell_price_next_hour"),
            "rce_2h": data.get("rce_sell_price_2h"),
            "rce_3h": data.get("rce_sell_price_3h"),
            "rce_avg_today": data.get("rce_average_today"),
            "rce_min_today": data.get("rce_min_today"),
            "rce_max_today": data.get("rce_max_today"),
            "rce_median_today": data.get("rce_median_today"),
            # v2: Energy Compass (PDGSZ) — PSE grid demand signal
            "rce_compass": data.get("rce_compass", "unknown"),
            # v2: Tomorrow awareness
            "rce_tomorrow_price": data.get("rce_tomorrow_price"),
            "rce_avg_tomorrow": data.get("rce_avg_tomorrow"),
            "rce_tomorrow_vs_today_pct": data.get("rce_tomorrow_vs_today_pct"),
            # v2: Window averages & arbitrage margin
            "rce_cheap_window_avg": data.get("rce_cheap_window_avg"),
            "rce_expensive_window_avg": data.get("rce_expensive_window_avg"),
            "rce_window_arbitrage_margin": data.get("rce_window_arbitrage_margin"),
            # Tariff config (for dynamic prompt rendering)
            "tariff_type": data.get("tariff_type", "g13"),
            "energy_provider": data.get("energy_provider", "tauron"),
            # Phase 1: Battery health & diagnostics
            "battery_soh": data.get("battery_soh"),
            "battery_health": data.get("battery_health_score"),
            "battery_charge_limit_a": data.get("battery_charge_limit_a"),
            "battery_discharge_limit_a": data.get("battery_discharge_limit_a"),
            "inverter_thermal": data.get("inverter_thermal_status"),
            "inverter_temp_radiator": data.get("inverter_temp_radiator"),
            "grid_power_factor": data.get("grid_power_factor"),
            "grid_quality": data.get("grid_quality"),
            "backup_load_w": data.get("backup_load_w"),
            "ups_load_pct": data.get("ups_load_pct"),
            "has_errors": data.get("has_active_errors"),
            "diag_status_code": data.get("diag_status_code"),
            "ems_mode": data.get("ems_mode"),
        }

        # Try to get weather data (AccuWeather / weather.dom)
        try:
            weather_entity = hass.states.get("weather.dom")
            if not weather_entity:
                # Fallback priority
                for wid in ("weather.home", "weather.accuweather", "weather.forecast_home"):
                    weather_entity = hass.states.get(wid)
                    if weather_entity:
                        break

            if weather_entity:
                ai_data["weather_temp"] = weather_entity.attributes.get("temperature")
                ai_data["weather_clouds"] = weather_entity.attributes.get("cloud_coverage")
                ai_data["weather_condition"] = weather_entity.state
                ai_data["weather_humidity"] = weather_entity.attributes.get("humidity")
                ai_data["weather_pressure"] = weather_entity.attributes.get("pressure")
                ai_data["weather_wind_speed"] = weather_entity.attributes.get("wind_speed")
                ai_data["weather_wind_bearing"] = weather_entity.attributes.get("wind_bearing")

                # Get multi-day forecast (AccuWeather provides this)
                forecast = weather_entity.attributes.get("forecast", [])
                if forecast:
                    ai_data["weather_forecast"] = [
                        {
                            "date": f.get("datetime", ""),
                            "condition": f.get("condition", ""),
                            "temp_high": f.get("temperature", ""),
                            "temp_low": f.get("templow", ""),
                            "precipitation": f.get("precipitation", 0),
                            "precipitation_probability": f.get("precipitation_probability", ""),
                            "wind_speed": f.get("wind_speed", ""),
                            "cloud_coverage": f.get("cloud_coverage", ""),
                        }
                        for f in forecast[:5]  # Next 5 days
                    ]
        except Exception:
            pass

        # Add Ecowitt local weather station data (if available)
        try:
            if data.get("ecowitt_enabled"):
                ecowitt = {
                    "solar_radiation": data.get("ecowitt_solar_radiation"),
                    "solar_lux": data.get("ecowitt_solar_lux"),
                    "uv_index": data.get("ecowitt_uv_index"),
                    "temperature": data.get("ecowitt_temp"),
                    "humidity": data.get("ecowitt_humidity"),
                    "wind_speed": data.get("ecowitt_wind_speed"),
                    "wind_gust": data.get("ecowitt_wind_gust"),
                    "wind_direction": data.get("ecowitt_wind_direction"),
                    "rain_rate": data.get("ecowitt_rain_rate"),
                    "daily_rain": data.get("ecowitt_daily_rain"),
                    "pressure": data.get("ecowitt_pressure"),
                    "feels_like": data.get("ecowitt_feels_like"),
                }
                # Only include non-None values
                ai_data["ecowitt"] = {k: v for k, v in ecowitt.items() if v is not None}
        except Exception:
            pass

        # Run mathematical estimation
        estimation = autopilot_engine.estimate_strategy(strategy, ai_data)

        # Optionally enhance with AI analysis
        ai_analysis = ""
        if with_ai and ai_advisor.any_available:
            try:
                # Resolve provider from settings if auto
                if provider == "auto":
                    stored = _read_settings(hass)
                    provider = stored.get("default_ai_provider", "gemini")

                prompt = build_autopilot_ai_prompt(strategy, ai_data, estimation)
                ai_analysis = await ai_advisor.ask_autopilot(prompt, ai_data, provider)
            except Exception as err:
                _LOGGER.error("Autopilot AI analysis failed: %s", err)
                ai_analysis = f"AI analysis error: {err}"

        # Store result
        result = {
            **estimation,
            "ai_analysis": ai_analysis,
            "provider": provider,
        }
        await _update_settings_file(hass, {AUTOPILOT_PLAN_KEY: result})

        # Fire event for live frontend update
        hass.bus.async_fire(
            f"{DOMAIN}_autopilot_result",
            result,
        )
        _LOGGER.info(
            "Autopilot estimation for '%s': net_savings=%.2f PLN, vs_baseline=%.2f PLN",
            strategy_str, estimation.get("net_savings", 0), estimation.get("vs_no_management", 0),
        )

    # ── Set Autopilot Strategy service ──

    async def handle_set_autopilot_strategy(call: ServiceCall) -> None:
        """Handle set_autopilot_strategy — activate a strategy on the controller."""
        if not strategy_controller:
            _LOGGER.warning("Strategy controller not available")
            return

        strategy_str = call.data["strategy"]
        try:
            strategy = AutopilotStrategy(strategy_str)
        except ValueError:
            _LOGGER.error("Unknown strategy: %s", strategy_str)
            return

        await strategy_controller.activate_strategy(strategy)

        # Persist to settings.json
        await _update_settings_file(hass, {
            AUTOPILOT_SETTINGS_KEY: strategy.value,
        })

        _LOGGER.info("Autopilot strategy set to: %s", strategy.value)

    # ── Deactivate Autopilot service ──

    async def handle_deactivate_autopilot(call: ServiceCall) -> None:
        """Deactivate autopilot — restore automations and go manual."""
        if not strategy_controller:
            _LOGGER.warning("Strategy controller not available")
            return

        await strategy_controller.deactivate()

        # Clear saved strategy
        await _update_settings_file(hass, {
            AUTOPILOT_SETTINGS_KEY: "",
        })

        _LOGGER.info("Autopilot deactivated, automations restored")

    # ── Trigger Autopilot Action service ──

    async def handle_trigger_autopilot_action(call: ServiceCall) -> None:
        """Handle trigger_autopilot_action — manually fire an action."""
        if not strategy_controller:
            _LOGGER.warning("Strategy controller not available")
            return

        action_id = call.data["action_id"]
        result = await strategy_controller.trigger_action(action_id)

        hass.bus.async_fire(
            f"{DOMAIN}_action_triggered",
            result,
        )
        _LOGGER.info("Action triggered: %s → %s", action_id, result)

    # ── Toggle Autopilot Action service ──

    async def handle_toggle_autopilot_action(call: ServiceCall) -> None:
        """Handle toggle_autopilot_action — enable/disable an action."""
        if not strategy_controller:
            _LOGGER.warning("Strategy controller not available")
            return

        action_id = call.data["action_id"]
        enabled = call.data["enabled"]
        ok = strategy_controller.toggle_action(action_id, enabled)

        if ok:
            hass.bus.async_fire(
                f"{DOMAIN}_action_toggled",
                {"action_id": action_id, "enabled": enabled},
            )
        _LOGGER.info("Action toggled: %s → %s", action_id, "ON" if enabled else "OFF")

    async def handle_sync_ecowitt_state(call: ServiceCall) -> None:
        """Sync Ecowitt enabled state to config_entry.data."""
        enabled = call.data["enabled"]
        current = entry.data
        new_data = {**current, CONF_ECOWITT_ENABLED: enabled, "_keys_only_update": True}
        hass.config_entries.async_update_entry(entry, data=new_data)
        # Also update coordinator's cached flag immediately
        coordinator_ref = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")
        if coordinator_ref:
            coordinator_ref._ecowitt_enabled = enabled
        _LOGGER.info("Ecowitt state synced to config entry: %s", enabled)

    async def handle_update_sensor_map(call: ServiceCall) -> None:
        """Update a single sensor mapping in config_entry + coordinator.

        Called by the frontend entity picker modal to save sensor mappings
        without a full integration restart.
        """
        sensor_key = call.data["sensor_key"]
        entity_id = call.data["entity_id"]

        # Validate sensor_key is known
        from .const import SENSOR_MAP_KEYS
        if sensor_key not in SENSOR_MAP_KEYS:
            _LOGGER.warning("Unknown sensor_key: %s", sensor_key)
            return

        # Update config_entry.data
        current = entry.data
        sensor_map = dict(current.get(CONF_SENSOR_MAP, {}))
        sensor_map[sensor_key] = entity_id
        new_data = {**current, CONF_SENSOR_MAP: sensor_map, "_keys_only_update": True}
        hass.config_entries.async_update_entry(entry, data=new_data)

        # Update coordinator in-memory (no restart needed)
        coordinator_ref = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")
        if coordinator_ref:
            coordinator_ref.update_sensor_map(sensor_key, entity_id)

        _LOGGER.info("Sensor map updated: %s → %s", sensor_key, entity_id)

    # ── Set Peak Sell Percent service ──

    async def handle_set_peak_sell_percent(call: ServiceCall) -> None:
        """Handle set_peak_sell_percent — update peak sell SOC percentage."""
        if not strategy_controller:
            _LOGGER.warning("Strategy controller not available")
            return

        percent = call.data["percent"]
        await strategy_controller.set_peak_sell_soc_percent(percent)
        _LOGGER.info("Peak sell SOC percent set to: %d%%", percent)

    # ── Schedule Manager services ──

    async def handle_save_schedule(call: ServiceCall) -> None:
        """Handle save_schedule — save hourly schedule to settings.json."""
        if not schedule_manager:
            _LOGGER.warning("Schedule manager not available")
            return

        weekday_raw = call.data.get("schedule_weekday")
        weekend_raw = call.data.get("schedule_weekend")
        enabled = call.data.get("schedule_enabled")
        mode = call.data.get("schedule_mode")

        weekday_schedule = None
        weekend_schedule = None

        if weekday_raw:
            try:
                weekday_schedule = json.loads(weekday_raw)
            except Exception as err:
                _LOGGER.error("Invalid weekday schedule JSON: %s", err)
                return

        if weekend_raw:
            try:
                weekend_schedule = json.loads(weekend_raw)
            except Exception as err:
                _LOGGER.error("Invalid weekend schedule JSON: %s", err)
                return

        await schedule_manager.save_schedule(
            weekday_schedule=weekday_schedule,
            weekend_schedule=weekend_schedule,
            enabled=enabled,
            schedule_mode=mode,
        )

        hass.bus.async_fire(
            f"{DOMAIN}_schedule_saved",
            {"status": "ok"},
        )
        _LOGGER.info("Schedule saved via service")

    async def handle_get_schedule_status(call: ServiceCall) -> None:
        """Handle get_schedule_status — fire event with current status."""
        if not schedule_manager:
            _LOGGER.warning("Schedule manager not available")
            return

        status = schedule_manager.get_status()
        hass.bus.async_fire(
            f"{DOMAIN}_schedule_status",
            status,
        )

    async def handle_apply_manual_mode(call: ServiceCall) -> None:
        """Handle apply_manual_mode — instantly apply a manual mode."""
        if not schedule_manager:
            _LOGGER.warning("Schedule manager not available")
            return

        mode_str = call.data["mode"]
        try:
            mode = ManualMode(mode_str)
        except ValueError:
            _LOGGER.error("Unknown manual mode: %s", mode_str)
            return

        result = await schedule_manager.apply_manual_override(mode)
        hass.bus.async_fire(
            f"{DOMAIN}_manual_mode_applied",
            result,
        )
        _LOGGER.info("Manual mode applied: %s", mode_str)

    # ── Wind Calendar services ──

    async def handle_get_wind_calendar(call: ServiceCall) -> None:
        """Return wind calendar data for the requested date range."""
        wind_cal = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("wind_calendar")
        if not wind_cal:
            _LOGGER.warning("Wind calendar not available")
            hass.bus.async_fire(
                f"{DOMAIN}_wind_calendar_data",
                {"error": "Wind calendar not initialized"},
            )
            return

        start = call.data.get("start_date", "") or None
        end = call.data.get("end_date", "") or None

        data = wind_cal.get_calendar_data(start, end)
        hass.bus.async_fire(f"{DOMAIN}_wind_calendar_data", data)
        _LOGGER.debug(
            "Wind calendar data returned: %d days (%s → %s)",
            len(data.get("days", {})), start or "all", end or "all",
        )

    async def handle_get_wind_summary(call: ServiceCall) -> None:
        """Return aggregated wind summary for a period."""
        wind_cal = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("wind_calendar")
        if not wind_cal:
            _LOGGER.warning("Wind calendar not available")
            hass.bus.async_fire(
                f"{DOMAIN}_wind_summary",
                {"error": "Wind calendar not initialized"},
            )
            return

        start = call.data.get("start_date", "") or None
        end = call.data.get("end_date", "") or None

        data = wind_cal.get_calendar_data(start, end)
        # Return only summary (without all day records) for lighter payload
        hass.bus.async_fire(
            f"{DOMAIN}_wind_summary",
            {"summary": data.get("summary", {}), "meta": data.get("meta", {})},
        )

    async def handle_recalculate_wind_calendar(call: ServiceCall) -> None:
        """Recalculate all wind calendar production with current turbine params."""
        wind_cal = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("wind_calendar")
        if not wind_cal:
            _LOGGER.warning("Wind calendar not available")
            return

        count = await wind_cal.recalculate_all()
        hass.bus.async_fire(
            f"{DOMAIN}_wind_calendar_recalculated",
            {"days_recalculated": count},
        )
        _LOGGER.info("Wind calendar recalculated: %d days", count)

    # ── Analyze ROI — AI Interpreter for tariff comparison ──

    SERVICE_ANALYZE_ROI = "analyze_roi"
    ANALYZE_ROI_SCHEMA = vol.Schema({
        vol.Required("roi_data"): cv.string,  # JSON string with ROI results
    })

    async def handle_analyze_roi(call: ServiceCall) -> None:
        """Handle analyze_roi — AI explains ROI simulation results in plain language."""
        if not license_mgr.is_pro:
            _LOGGER.warning("ROI AI Analysis requires PRO license")
            hass.bus.async_fire(
                f"{DOMAIN}_roi_analysis",
                {"error": "Analiza AI wymaga licencji PRO."},
            )
            return

        if not ai_advisor.any_available:
            hass.bus.async_fire(
                f"{DOMAIN}_roi_analysis",
                {"error": "Brak skonfigurowanego dostawcy AI. Dodaj klucz API Gemini lub Anthropic w ustawieniach."},
            )
            return

        try:
            roi_data = json.loads(call.data["roi_data"])
        except Exception as err:
            _LOGGER.error("Invalid ROI data JSON: %s", err)
            hass.bus.async_fire(
                f"{DOMAIN}_roi_analysis",
                {"error": f"Błąd danych: {err}"},
            )
            return

        question = f"""Jesteś ekspertem od fotowoltaiki i zarządzania energią w domu.
Użytkownik ma system PV + bateria z Home Assistant i integrację Smarting HOME.

Poniżej są wyniki symulacji ROI (zwrotu z inwestycji) porównujące 3 taryfy energetyczne.
Wytłumacz te dane PROSTYM JĘZYKIEM dla osoby bez wiedzy technicznej o fotowoltaice.

WAŻNE WYTYCZNE:
1. Pisz po polsku, prostym językiem — jak tłumaczysz sąsiadowi
2. Unikaj żargonu — zamiast "autokonsumpcja" → "energia zużyta na własne potrzeby"
3. Podaj KONKRETNE kwoty w złotówkach
4. Wyjaśnij CO TO ZNACZY dla portfela użytkownika
5. Wyjaśnij co robi bateria i automatyka HEMS i ILE złotych to daje
6. Podaj zwrot z inwestycji — kiedy się "zwróci" i ile zarobi po 25 latach
7. Użyj analogii z życia codziennego
8. NIE oceniaj żadnej taryfy jako "najgorsza" — mów "w tej symulacji najmniej korzystna finansowo"

KLUCZOWA ZASADA — DWA RÓŻNE PYTANIA:
Użytkownik chce wiedzieć JEDNĄ KLUCZOWĄ RZECZ:
"Ile zaoszczędzę rocznie jeśli zainstaluję PV + baterię i przejdę na tę taryfę?"

Odpowiedź jest w polu "realSaving" = g11BaselineCost - annualEnergyCost.
To jest REALNA oszczędność: porównanie kosztu na G11 BEZ PV → koszt na tej taryfie Z PV.

Dodatkowe metryki:
- "annualEnergyCost" = ile user zapłaci rocznie za prąd z systemem
- "realPayback" = realSaving → za ile lat się zwróci inwestycja
- "realProfit25" = zysk w 25 lat (realSaving × 25 - inwestycja)
- "benefit" = korzyść PV wewnątrz tej samej taryfy (mniej istotne)

WAŻNA LOGIKA DO WYJAŚNIENIA:
- Taryfa z TAŃSZYM prądem (np. dynamiczna) + PV daje NAJWIĘKSZĄ realną oszczędność
  bo: (1) sam prąd jest tańszy + (2) PV daje oszczędności = podwójna korzyść
- Taryfa z DROŻSZYM prądem (G11) daje MNIEJSZĄ realną oszczędność
  bo: PV oszczędza dużo per kWh, ale baseline jest drogi → rachunek też drogi
- To jest fundamentalna różnica vs stare podejście (gdzie G11 "wygrywała" bo PV było warte więcej per kWh)

Pole "automationGainHEMS" w _systemContext pokazuje ile złotych rocznie daje automatyka Smarting HOME.

WYMAGANA STRUKTURA ODPOWIEDZI:
1. Krótkie wprowadzenie z analogią (2-3 zdania)
2. Porównanie taryf — dla każdej podaj: roczny koszt energii, REALNĄ oszczędność, zwrot inwestycji
3. Wyjaśnienie roli baterii i HEMS (ile złotych daje automatyka)
4. OBOWIĄZKOWA TABELA na końcu (przed rekomendacjami):

| Kryterium | Najlepsza taryfa |
|---|---|
| 🚀 Największa realna oszczędność (ROI) | ... (realSaving + realPayback) |
| 💰 Najniższy roczny koszt energii | ... (annualEnergyCost) |
| 💎 Największa wartość 1 kWh z PV | ... |
| 🤖 Korzyść z automatyki HEMS | ... |

5. 1-2 KONKRETNE rekomendacje — ZAWSZE rekomenduj taryfę z najkrótszym realPayback

WAŻNE OGRANICZENIA:
- NIE omawiaj bieżącego stanu systemu (aktualna produkcja PV, stan baterii, pobór domu)
- Skup się WYŁĄCZNIE na danych ROI i porównaniu taryf
- To jest symulacja ROCZNA — nie analizuj danych "tu i teraz"
- NIE spekuluj o konfiguracji HEMS ani o błędach — dane są prawidłowe

DANE SYMULACJI ROI:
{json.dumps(roi_data, indent=2, ensure_ascii=False)}

Odpowiedz w formacie markdown z sekcjami (##), listami, **bold** i tabelą |...|.
Długość: 400-600 słów."""

        try:
            stored = _read_settings(hass)
            provider = stored.get("default_ai_provider", "gemini")

            # Direct API call without system context (no _build_context)
            # to avoid polluting ROI analysis with live system status
            if provider == "anthropic" and ai_advisor.anthropic_available:
                response = await ai_advisor._direct_ask_anthropic(question)
            elif ai_advisor.gemini_available:
                response = await ai_advisor._direct_ask_gemini(question)
                provider = "gemini"
            elif ai_advisor.anthropic_available:
                response = await ai_advisor._direct_ask_anthropic(question)
                provider = "anthropic"
            else:
                response = "Brak dostępnego dostawcy AI."
                provider = "none"

            from datetime import datetime
            now_str = datetime.now().strftime("%H:%M")
            result = {
                "text": response,
                "provider": provider,
                "timestamp": now_str,
            }
            # Save to settings for persistence
            await _update_settings_file(hass, {"ai_roi_analysis": result})
            # Fire event for live frontend update
            hass.bus.async_fire(f"{DOMAIN}_roi_analysis", result)
            _LOGGER.info("ROI AI analysis complete (%s, %d chars)", provider, len(response))

        except Exception as err:
            _LOGGER.error("ROI AI analysis error: %s", err)
            hass.bus.async_fire(
                f"{DOMAIN}_roi_analysis",
                {"error": f"Błąd analizy AI: {err}"},
            )

    # ── Send Alert Notification service ──

    async def handle_send_alert_notification(call: ServiceCall) -> None:
        """Dispatch alert notification to configured channels."""
        alert_id = call.data["alert_id"]
        level = call.data["level"]
        source = call.data["source"]
        title = call.data["title"]
        message = call.data["message"]
        diag_action = call.data.get("diag_action", "")

        settings = _read_settings(hass)
        notif_cfg = settings.get("notification_config", {})

        if not notif_cfg.get("enabled", False):
            _LOGGER.debug("Alert notification skipped — disabled")
            return

        # Test notifications bypass all filters
        is_test = alert_id == "TEST_NOTIFICATION"

        # Cooldown check
        cooldown_min = int(notif_cfg.get("cooldown", 15))
        notif_log = settings.get("notification_log", [])
        from datetime import datetime, timedelta
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
        cutoff = now - timedelta(minutes=cooldown_min)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

        if not is_test:
            recent_same = [
                e for e in notif_log
                if e.get("alert_id") == alert_id and e.get("ts", "") > cutoff_str
            ]
            if recent_same:
                _LOGGER.debug(
                    "Alert notification '%s' skipped — cooldown (%d min)",
                    alert_id, cooldown_min,
                )
                return

        # Quiet hours check (skip non-critical during quiet hours)
        quiet_start = notif_cfg.get("quiet_start", "")
        quiet_end = notif_cfg.get("quiet_end", "")
        if quiet_start and quiet_end and level != "critical" and not is_test:
            try:
                h_now = now.hour * 100 + now.minute
                qs = int(quiet_start.replace(":", ""))
                qe = int(quiet_end.replace(":", ""))
                if qs > qe:  # e.g. 22:00 — 07:00
                    in_quiet = h_now >= qs or h_now < qe
                else:
                    in_quiet = qs <= h_now < qe
                if in_quiet:
                    _LOGGER.debug(
                        "Alert '%s' skipped — quiet hours (%s-%s)",
                        alert_id, quiet_start, quiet_end,
                    )
                    return
            except Exception:
                pass

        # Level filter
        allowed_levels = notif_cfg.get("levels", ["critical", "warning"])
        if not is_test and level not in allowed_levels:
            _LOGGER.debug("Alert '%s' skipped — level '%s' not in filter", alert_id, level)
            return

        channels = notif_cfg.get("channels", {})
        sent_channels = []
        level_emoji = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(level, "ℹ️")
        notif_title = f"{level_emoji} Smarting HOME — {title}"
        notif_body = f"{message}"
        if diag_action:
            notif_body += f"\n🛠️ {diag_action}"

        # Channel 1: HA Companion Push (multi-device)
        if channels.get("ha_push"):
            entities = notif_cfg.get("ha_push_entities", [])
            # Backward compat: single entity fallback
            if not entities:
                single = notif_cfg.get("ha_push_entity", "")
                if single:
                    entities = [single]
            push_ok = False
            for entity in entities:
                if not entity:
                    continue
                try:
                    await hass.services.async_call(
                        "notify", entity.replace("notify.", ""),
                        {
                            "title": notif_title,
                            "message": notif_body,
                            "data": {
                                "tag": f"smartinghome_{alert_id}",
                                "importance": "high" if level == "critical" else "default",
                                "channel": "smartinghome_alerts",
                            },
                        },
                    )
                    push_ok = True
                    _LOGGER.info("HA push sent to %s", entity)
                except Exception as err:
                    _LOGGER.error("HA push notification failed for %s: %s", entity, err)
            if push_ok:
                sent_channels.append("ha_push")

        # Channel 2: Persistent Notification
        if channels.get("persistent"):
            try:
                await hass.services.async_call(
                    "persistent_notification", "create",
                    {
                        "title": notif_title,
                        "message": notif_body,
                        "notification_id": f"smartinghome_{alert_id}",
                    },
                )
                sent_channels.append("persistent")
            except Exception as err:
                _LOGGER.error("Persistent notification failed: %s", err)

        # Channel 3: Webhook SMS
        if channels.get("sms"):
            phone = notif_cfg.get("phone", "")
            if phone:
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        await session.post(
                            ALERT_WEBHOOK_URL,
                            json={
                                "channel": "sms",
                                "phone": phone,
                                "subject": notif_title,
                                "message": notif_body,
                                "level": level,
                                "alert_id": alert_id,
                                "source": source,
                                "timestamp": now_str,
                            },
                            timeout=aiohttp.ClientTimeout(total=10),
                        )
                    sent_channels.append("sms")
                except Exception as err:
                    _LOGGER.error("Webhook SMS failed: %s", err)

        # Channel 4: Webhook Email
        if channels.get("email"):
            email = notif_cfg.get("email", "")
            if email:
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        await session.post(
                            ALERT_WEBHOOK_URL,
                            json={
                                "channel": "email",
                                "email": email,
                                "subject": notif_title,
                                "message": notif_body,
                                "level": level,
                                "alert_id": alert_id,
                                "source": source,
                                "timestamp": now_str,
                            },
                            timeout=aiohttp.ClientTimeout(total=10),
                        )
                    sent_channels.append("email")
                except Exception as err:
                    _LOGGER.error("Webhook email failed: %s", err)

        if sent_channels:
            # Log to notification_log
            log_entry = {
                "alert_id": alert_id,
                "level": level,
                "title": title,
                "channels": sent_channels,
                "ts": now_str,
            }
            notif_log.append(log_entry)
            # Trim to last 30
            if len(notif_log) > 30:
                notif_log = notif_log[-30:]
            await _update_settings_file(hass, {"notification_log": notif_log})

            _LOGGER.info(
                "Alert notification sent: '%s' [%s] → %s",
                alert_id, level, ", ".join(sent_channels),
            )
        else:
            _LOGGER.debug("Alert '%s': no channels dispatched", alert_id)

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
        DOMAIN, SERVICE_FORCE_CUSTOM, handle_force_custom,
        schema=FORCE_CUSTOM_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_FORCE_CHARGE, handle_stop_force_charge
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_FORCE_DISCHARGE, handle_stop_force_discharge
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EMERGENCY_STOP, handle_emergency_stop
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
    hass.services.async_register(
        DOMAIN, SERVICE_RUN_AUTOPILOT, handle_run_autopilot,
        schema=RUN_AUTOPILOT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_AUTOPILOT_STRATEGY, handle_set_autopilot_strategy,
        schema=SET_AUTOPILOT_STRATEGY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DEACTIVATE_AUTOPILOT, handle_deactivate_autopilot,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TRIGGER_AUTOPILOT_ACTION, handle_trigger_autopilot_action,
        schema=TRIGGER_ACTION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_TOGGLE_AUTOPILOT_ACTION, handle_toggle_autopilot_action,
        schema=TOGGLE_ACTION_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_ECOWITT_STATE, handle_sync_ecowitt_state,
        schema=SYNC_ECOWITT_STATE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UPDATE_SENSOR_MAP, handle_update_sensor_map,
        schema=UPDATE_SENSOR_MAP_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ANALYZE_ROI, handle_analyze_roi,
        schema=ANALYZE_ROI_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_ALERT_NOTIFICATION, handle_send_alert_notification,
        schema=SEND_ALERT_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PEAK_SELL_PERCENT, handle_set_peak_sell_percent,
        schema=SET_PEAK_SELL_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_SCHEDULE, handle_save_schedule,
        schema=SAVE_SCHEDULE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_SCHEDULE_STATUS, handle_get_schedule_status,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_MANUAL_MODE, handle_apply_manual_mode,
        schema=APPLY_MANUAL_MODE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_WIND_CALENDAR, handle_get_wind_calendar,
        schema=GET_WIND_CALENDAR_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_GET_WIND_SUMMARY, handle_get_wind_summary,
        schema=GET_WIND_SUMMARY_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RECALCULATE_WIND_CALENDAR, handle_recalculate_wind_calendar,
    )

    _LOGGER.info("Registered %d Smarting HOME services", 27)

    # Start AI Cron Scheduler
    cron = AICronScheduler(
        hass,
        ai_advisor,
        get_coordinator_data=lambda: coordinator.data or {},
        strategy_controller=strategy_controller,
    )
    await cron.async_start()
    return cron


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unload Smarting HOME services."""
    for service in [
        SERVICE_SET_MODE,
        SERVICE_FORCE_CHARGE,
        SERVICE_FORCE_DISCHARGE,
        SERVICE_FORCE_CUSTOM,
        SERVICE_STOP_FORCE_CHARGE,
        SERVICE_STOP_FORCE_DISCHARGE,
        SERVICE_EMERGENCY_STOP,
        SERVICE_SET_EXPORT_LIMIT,
        SERVICE_ASK_AI,
        SERVICE_GENERATE_REPORT,
        SERVICE_UPLOAD_IMAGE,
        SERVICE_SAVE_SETTINGS,
        SERVICE_TEST_API_KEY,
        SERVICE_SAVE_PANEL_SETTINGS,
        SERVICE_RUN_AUTOPILOT,
        SERVICE_SET_AUTOPILOT_STRATEGY,
        SERVICE_DEACTIVATE_AUTOPILOT,
        SERVICE_TRIGGER_AUTOPILOT_ACTION,
        SERVICE_TOGGLE_AUTOPILOT_ACTION,
        SERVICE_SYNC_ECOWITT_STATE,
        SERVICE_UPDATE_SENSOR_MAP,
        "analyze_roi",
        SERVICE_SEND_ALERT_NOTIFICATION,
        SERVICE_SET_PEAK_SELL_PERCENT,
        SERVICE_SAVE_SCHEDULE,
        SERVICE_GET_SCHEDULE_STATUS,
        SERVICE_APPLY_MANUAL_MODE,
        SERVICE_GET_WIND_CALENDAR,
        SERVICE_GET_WIND_SUMMARY,
        SERVICE_RECALCULATE_WIND_CALENDAR,
    ]:
        hass.services.async_remove(DOMAIN, service)
