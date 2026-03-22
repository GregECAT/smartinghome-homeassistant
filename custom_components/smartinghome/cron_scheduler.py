"""AI Cron Scheduler for Smarting HOME.

Periodically runs AI Advisor functions (HEMS optimization, daily report,
anomaly detection) at user-configurable intervals. Results are stored in
settings.json and fired as HA bus events for live panel updates.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .ai_advisor import AIAdvisor
from .const import DOMAIN

SETTINGS_FILE = "settings.json"

_LOGGER = logging.getLogger(__name__)

# Default intervals in minutes
DEFAULT_HEMS_INTERVAL = 30
DEFAULT_REPORT_INTERVAL = 360  # 6 hours
DEFAULT_ANOMALY_INTERVAL = 60

# Settings keys
CRON_HEMS_INTERVAL = "cron_hems_interval"
CRON_REPORT_INTERVAL = "cron_report_interval"
CRON_ANOMALY_INTERVAL = "cron_anomaly_interval"
CRON_HEMS_ENABLED = "cron_hems_enabled"
CRON_REPORT_ENABLED = "cron_report_enabled"
CRON_ANOMALY_ENABLED = "cron_anomaly_enabled"

# Result keys in settings.json
AI_HEMS_ADVICE = "ai_hems_advice"
AI_DAILY_REPORT = "ai_daily_report"
AI_ANOMALY_REPORT = "ai_anomaly_report"


class AICronScheduler:
    """Cron-style scheduler for AI Advisor functions."""

    def __init__(
        self,
        hass: HomeAssistant,
        ai_advisor: AIAdvisor,
        get_coordinator_data: Any,
    ) -> None:
        """Initialize the scheduler."""
        self.hass = hass
        self._ai = ai_advisor
        self._get_data = get_coordinator_data
        self._tasks: list[asyncio.Task] = []
        self._running = False

    def _map_data(self, raw: dict) -> dict:
        """Map coordinator data keys to AI-expected simple keys."""
        # Try to get weather data from HA states
        weather_data = {}
        try:
            weather_entity = self.hass.states.get("weather.dom")
            if weather_entity:
                weather_data["weather_temp"] = weather_entity.attributes.get("temperature")
                weather_data["weather_clouds"] = weather_entity.attributes.get("cloud_coverage")
                weather_data["weather_condition"] = weather_entity.state
            realfeel = self.hass.states.get("sensor.dom_temperatura_realfeel")
            if realfeel:
                weather_data["weather_realfeel"] = realfeel.state
            sun_hours = self.hass.states.get("sensor.dom_godziny_sloneczne_dzien_0")
            if sun_hours:
                weather_data["sun_hours_today"] = sun_hours.state
            uv = self.hass.states.get("sensor.dom_indeks_uv_dzien_0")
            if uv:
                weather_data["uv_index"] = uv.state
        except Exception:
            pass

        return {
            "pv_power": raw.get("sensor.pv_power"),
            "grid_power": raw.get("sensor.meter_active_power_total"),
            "battery_soc": raw.get("sensor.battery_state_of_charge"),
            "battery_power": raw.get("sensor.battery_power"),
            "load": raw.get("sensor.load"),
            "pv_surplus": raw.get("hems_pv_surplus_power"),
            "g13_zone": raw.get("g13_current_zone"),
            "g13_price": raw.get("g13_buy_price"),
            "rce_price": raw.get("sensor.rce_pse_cena"),
            "rce_sell": raw.get("rce_sell_price"),
            "rce_trend": raw.get("rce_price_trend"),
            "rce_level": raw.get("rce_good_sell"),
            "battery_available": raw.get("goodwe_battery_energy_available"),
            "battery_runtime": raw.get("goodwe_battery_runtime"),
            "forecast_today": raw.get("pv_forecast_today_total"),
            "forecast_remaining": raw.get("pv_forecast_remaining_today_total"),
            "forecast_tomorrow": raw.get("pv_forecast_tomorrow_total"),
            "autarky": raw.get("goodwe_autarky_today"),
            "import_cost": raw.get("g13_import_cost_today"),
            "export_revenue": raw.get("g13_export_revenue_today"),
            "savings": raw.get("g13_self_consumption_savings_today"),
            **weather_data,
        }

    def _get_settings_path(self) -> Path:
        """Return path to settings.json."""
        d = Path(self.hass.config.path("www")) / "smartinghome"
        d.mkdir(parents=True, exist_ok=True)
        return d / SETTINGS_FILE

    def _read_settings(self) -> dict:
        """Read settings from JSON."""
        p = self._get_settings_path()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                return {}
        return {}

    def _update_settings(self, updates: dict) -> None:
        """Merge updates into settings.json."""
        current = self._read_settings()
        current.update(updates)
        p = self._get_settings_path()
        p.write_text(json.dumps(current, indent=2, ensure_ascii=False))

    async def async_start(self) -> None:
        """Start all enabled cron jobs."""
        self._running = True
        settings = self._read_settings()

        # HEMS Optimization
        if settings.get(CRON_HEMS_ENABLED, True):
            interval = int(settings.get(CRON_HEMS_INTERVAL, DEFAULT_HEMS_INTERVAL))
            self._tasks.append(
                asyncio.create_task(self._run_loop("hems", interval))
            )

        # Daily Report
        if settings.get(CRON_REPORT_ENABLED, True):
            interval = int(settings.get(CRON_REPORT_INTERVAL, DEFAULT_REPORT_INTERVAL))
            self._tasks.append(
                asyncio.create_task(self._run_loop("report", interval))
            )

        # Anomaly Detection
        if settings.get(CRON_ANOMALY_ENABLED, True):
            interval = int(settings.get(CRON_ANOMALY_INTERVAL, DEFAULT_ANOMALY_INTERVAL))
            self._tasks.append(
                asyncio.create_task(self._run_loop("anomaly", interval))
            )

        _LOGGER.info(
            "AI Cron started: %d jobs active", len(self._tasks)
        )

    async def async_stop(self) -> None:
        """Stop all cron jobs."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        _LOGGER.info("AI Cron stopped")

    async def async_restart(self) -> None:
        """Restart all cron jobs (after settings change)."""
        await self.async_stop()
        await self.async_start()

    async def _run_loop(self, job_type: str, interval_min: int) -> None:
        """Run a cron job loop with the given interval."""
        # Wait initial delay (2 min for first run, give HA time to stabilize)
        await asyncio.sleep(120)

        while self._running:
            try:
                if not self._ai.any_available:
                    _LOGGER.debug(
                        "AI Cron '%s' skipped — no AI provider configured", job_type
                    )
                    await asyncio.sleep(interval_min * 60)
                    continue

                raw_data = self._get_data()
                if not raw_data:
                    _LOGGER.debug("AI Cron '%s' skipped — no data", job_type)
                    await asyncio.sleep(interval_min * 60)
                    continue

                data = self._map_data(raw_data)

                _LOGGER.info("AI Cron running: %s", job_type)

                if job_type == "hems":
                    result = await self._ai.get_optimization_advice(data)
                    result_key = AI_HEMS_ADVICE
                elif job_type == "report":
                    result = await self._ai.generate_daily_report(data)
                    result_key = AI_DAILY_REPORT
                elif job_type == "anomaly":
                    result = await self._ai.detect_anomalies(data)
                    result_key = AI_ANOMALY_REPORT
                else:
                    result = ""
                    result_key = ""

                if result and result_key:
                    now_str = datetime.now().strftime("%H:%M")
                    now_date = datetime.now().strftime("%Y-%m-%d")
                    settings = self._read_settings()
                    default_prov = settings.get("default_ai_provider", "gemini")
                    if default_prov == "anthropic" and self._ai.anthropic_available:
                        provider = "anthropic"
                    elif self._ai.gemini_available:
                        provider = "gemini"
                    elif self._ai.anthropic_available:
                        provider = "anthropic"
                    else:
                        provider = "unknown"

                    # Determine status
                    is_error = result.startswith("Gemini error") or result.startswith("Anthropic error") or result.startswith("No response")
                    is_truncated = len(result) > 100 and not result.rstrip().endswith((".", "!", "?", ")", "]", "```"))
                    status = "error" if is_error else "truncated" if is_truncated else "ok"

                    entry = {
                        "text": result,
                        "timestamp": now_str,
                        "provider": provider,
                        "date": now_date,
                    }
                    self._update_settings({result_key: entry})

                    # --- AI Logs ---
                    log_entry = {
                        "date": now_date,
                        "time": now_str,
                        "job": job_type,
                        "provider": provider,
                        "chars": len(result),
                        "status": status,
                    }
                    logs = settings.get("ai_logs", [])
                    logs.append(log_entry)
                    # Auto-trim to last 50 entries
                    max_logs = int(settings.get("ai_logs_max", 50))
                    if len(logs) > max_logs:
                        logs = logs[-max_logs:]
                    self._update_settings({"ai_logs": logs})

                    # Fire HA bus event for live frontend update
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_ai_cron_update",
                        {"job": job_type, "result_key": result_key, **entry},
                    )
                    _LOGGER.info(
                        "AI Cron '%s' complete (%s, %d chars, status=%s)",
                        job_type, provider, len(result), status,
                    )

            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.error("AI Cron '%s' error: %s", job_type, err)
                # Log errors too
                try:
                    settings = self._read_settings()
                    logs = settings.get("ai_logs", [])
                    logs.append({
                        "date": datetime.now().strftime("%Y-%m-%d"),
                        "time": datetime.now().strftime("%H:%M"),
                        "job": job_type,
                        "provider": "—",
                        "chars": 0,
                        "status": "error",
                        "error": str(err)[:200],
                    })
                    max_logs = int(settings.get("ai_logs_max", 50))
                    if len(logs) > max_logs:
                        logs = logs[-max_logs:]
                    self._update_settings({"ai_logs": logs})
                except Exception:
                    pass

            await asyncio.sleep(interval_min * 60)

