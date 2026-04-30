"""Data update coordinator for Smarting HOME."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DOMAIN,
    SENSOR_PV_POWER,
    SENSOR_PV1_POWER,
    SENSOR_PV2_POWER,
    SENSOR_PV_GENERATION_TODAY,
    SENSOR_PV_GENERATION_TOTAL,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_GRID_POWER_L1,
    SENSOR_GRID_POWER_L2,
    SENSOR_GRID_POWER_L3,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_GRID_FREQUENCY_L1,
    SENSOR_GRID_FREQUENCY_L2,
    SENSOR_GRID_FREQUENCY_L3,
    SENSOR_BATTERY_SOC,
    SENSOR_BATTERY_POWER,
    SENSOR_BATTERY_VOLTAGE,
    SENSOR_BATTERY_CURRENT,
    SENSOR_BATTERY_TEMPERATURE,
    SENSOR_BATTERY_MODE,
    SENSOR_BATTERY_CHARGE_TODAY,
    SENSOR_BATTERY_DISCHARGE_TODAY,
    SENSOR_LOAD_TOTAL,
    SENSOR_LOAD_L1,
    SENSOR_LOAD_L2,
    SENSOR_LOAD_L3,
    SENSOR_LOAD_TODAY,
    SENSOR_WORK_MODE,
    SENSOR_INVERTER_TEMP,
    SENSOR_BATTERY_SOH,
    SENSOR_BATTERY_CHARGE_LIMIT,
    SENSOR_BATTERY_DISCHARGE_LIMIT,
    SENSOR_INVERTER_TEMP_RADIATOR,
    SENSOR_DIAG_STATUS_CODE,
    SENSOR_METER_POWER_FACTOR,
    SENSOR_BACKUP_LOAD,
    SENSOR_UPS_LOAD,
    SENSOR_EMS_MODE,
    SENSOR_RCE_PRICE,
    SENSOR_RCE_SELL_PROSUMER,
    SENSOR_RCE_NEXT_PERIOD,
    SENSOR_RCE_PREV_PERIOD,
    SENSOR_RCE_AVG_TODAY,
    SENSOR_RCE_MIN_TODAY,
    SENSOR_RCE_MAX_TODAY,
    SENSOR_RCE_MEDIAN_TODAY,
    SENSOR_RCE_PRICE_TOMORROW,
    SENSOR_RCE_AVG_TOMORROW,
    SENSOR_RCE_TOMORROW_VS_TODAY,
    SENSOR_RCE_COMPASS_TODAY,
    SENSOR_RCE_CHEAP_WINDOW_AVG,
    SENSOR_RCE_EXPENSIVE_WINDOW_AVG,
    SENSOR_FORECAST_POWER_1,
    SENSOR_FORECAST_POWER_2,
    SENSOR_FORECAST_TODAY_1,
    SENSOR_FORECAST_TODAY_2,
    SENSOR_FORECAST_REMAINING_1,
    SENSOR_FORECAST_REMAINING_2,
    SENSOR_FORECAST_TOMORROW_1,
    SENSOR_FORECAST_TOMORROW_2,
    CONF_TARIFF,
    CONF_RCE_ENABLED,
    TariffType,
    G13_PRICES,
    G13Zone,
    G13_WINTER_SCHEDULE,
    G13_SUMMER_SCHEDULE,
    WINTER_MONTHS,
    RCE_PROSUMER_COEFFICIENT,
    RCE_PRICE_THRESHOLDS,
    RCEPriceLevel,
    RCETrend,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_BATTERY_MIN_SOC,
    CONF_ECOWITT_ENABLED,
    CONF_SENSOR_MAP,
    DEFAULT_SENSOR_MAP,
    CONF_ENERGY_PROVIDER,
    DEFAULT_ENERGY_PROVIDER,
    DYNAMIC_PRICE_THRESHOLDS,
    SENSOR_ENTSOE_PRICE_NOW,
    SENSOR_ENTSOE_ALLIN_NOW,
    SENSOR_ENTSOE_ALLIN_NEXT,
    SENSOR_ENTSOE_ALLIN_MIN,
    SENSOR_ENTSOE_ALLIN_MAX,
    SENSOR_ENTSOE_AVG_TODAY,
    SENSOR_ENTSOE_RANK,
    SENSOR_ENTSOE_PERCENTILE,
)
from .license import LicenseManager

_LOGGER = logging.getLogger(__name__)

# Source sensors to read from HA state machine
SOURCE_SENSORS: list[str] = [
    SENSOR_PV_POWER, SENSOR_PV1_POWER, SENSOR_PV2_POWER,
    SENSOR_PV_GENERATION_TODAY, SENSOR_PV_GENERATION_TOTAL,
    SENSOR_GRID_POWER_TOTAL, SENSOR_GRID_POWER_L1,
    SENSOR_GRID_POWER_L2, SENSOR_GRID_POWER_L3,
    SENSOR_GRID_VOLTAGE_L1, SENSOR_GRID_VOLTAGE_L2, SENSOR_GRID_VOLTAGE_L3,
    SENSOR_GRID_FREQUENCY_L1, SENSOR_GRID_FREQUENCY_L2, SENSOR_GRID_FREQUENCY_L3,
    SENSOR_BATTERY_SOC, SENSOR_BATTERY_POWER,
    SENSOR_BATTERY_VOLTAGE, SENSOR_BATTERY_CURRENT,
    SENSOR_BATTERY_TEMPERATURE, SENSOR_BATTERY_MODE,
    SENSOR_BATTERY_CHARGE_TODAY, SENSOR_BATTERY_DISCHARGE_TODAY,
    SENSOR_LOAD_TOTAL, SENSOR_LOAD_L1, SENSOR_LOAD_L2, SENSOR_LOAD_L3,
    SENSOR_LOAD_TODAY,
    SENSOR_WORK_MODE, SENSOR_INVERTER_TEMP,
    # GoodWe extended (Phase 1 — health & diagnostics)
    SENSOR_BATTERY_SOH, SENSOR_BATTERY_CHARGE_LIMIT, SENSOR_BATTERY_DISCHARGE_LIMIT,
    SENSOR_INVERTER_TEMP_RADIATOR, SENSOR_DIAG_STATUS_CODE,
    SENSOR_METER_POWER_FACTOR, SENSOR_BACKUP_LOAD, SENSOR_UPS_LOAD,
    SENSOR_EMS_MODE,
    SENSOR_RCE_PRICE, SENSOR_RCE_SELL_PROSUMER,
    SENSOR_RCE_NEXT_PERIOD, SENSOR_RCE_PREV_PERIOD,
    SENSOR_RCE_AVG_TODAY, SENSOR_RCE_MIN_TODAY, SENSOR_RCE_MAX_TODAY,
    SENSOR_RCE_MEDIAN_TODAY,
    SENSOR_RCE_PRICE_TOMORROW, SENSOR_RCE_AVG_TOMORROW,
    SENSOR_RCE_TOMORROW_VS_TODAY,
    SENSOR_RCE_COMPASS_TODAY,
    SENSOR_RCE_CHEAP_WINDOW_AVG, SENSOR_RCE_EXPENSIVE_WINDOW_AVG,
    SENSOR_FORECAST_POWER_1, SENSOR_FORECAST_POWER_2,
    SENSOR_FORECAST_TODAY_1, SENSOR_FORECAST_TODAY_2,
    SENSOR_FORECAST_REMAINING_1, SENSOR_FORECAST_REMAINING_2,
    SENSOR_FORECAST_TOMORROW_1, SENSOR_FORECAST_TOMORROW_2,
    # ENTSO-E dynamic pricing sensors
    SENSOR_ENTSOE_PRICE_NOW, SENSOR_ENTSOE_ALLIN_NOW,
    SENSOR_ENTSOE_ALLIN_NEXT, SENSOR_ENTSOE_ALLIN_MIN,
    SENSOR_ENTSOE_ALLIN_MAX, SENSOR_ENTSOE_AVG_TODAY,
    SENSOR_ENTSOE_RANK, SENSOR_ENTSOE_PERCENTILE,
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert a value to float safely."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


class SmartingHomeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Smarting HOME data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        license_manager: LicenseManager,
        update_interval: timedelta,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=update_interval,
        )
        self.entry = entry
        self.license_manager = license_manager
        self._tariff = entry.data.get(CONF_TARIFF, TariffType.G13)
        self._rce_enabled = entry.data.get(CONF_RCE_ENABLED, True)
        self._ecowitt_enabled = entry.data.get(CONF_ECOWITT_ENABLED, False)
        self._sensor_map = entry.data.get(CONF_SENSOR_MAP, {})
        self._strategy_controller = None
        self._schedule_manager = None
        self._wind_calendar = None

        # ── Day/Night energy accumulation (server-side, 24/7) ──
        self._load_day_ws: float = 0.0    # watt-seconds during daytime
        self._load_night_ws: float = 0.0  # watt-seconds during nighttime
        self._last_load_ts: float | None = None  # monotonic timestamp
        self._accum_date: str = ""        # YYYY-MM-DD for midnight reset

        # ── Grid daily midnight reset (GoodWe "daily" sensors reset at sunrise, not midnight) ──
        self._grid_baseline_date: str = ""  # date for which baselines are valid
        self._grid_import_baseline: float = 0.0  # snapshot at midnight
        self._grid_export_baseline: float = 0.0  # snapshot at midnight

    def set_strategy_controller(self, controller) -> None:
        """Set the strategy controller for autonomous HEMS control."""
        self._strategy_controller = controller

    def set_schedule_manager(self, manager) -> None:
        """Set the schedule manager for hourly autopilot/manual orchestration."""
        self._schedule_manager = manager

    def set_wind_calendar(self, calendar) -> None:
        """Set the wind calendar for daily wind energy tracking."""
        self._wind_calendar = calendar

    def update_sensor_map(self, key: str, entity_id: str) -> None:
        """Update a single sensor mapping in-memory (no restart needed)."""
        self._sensor_map[key] = entity_id

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from HA state machine and compute HEMS values."""
        try:
            # Periodic license check
            await self.license_manager.periodic_check()

            # Read all source sensors
            raw = self._read_source_sensors()

            # Compute derived values
            computed = self._compute_derived(raw)

            # Read Ecowitt sensors if enabled
            if self._ecowitt_enabled:
                ecowitt = self._read_ecowitt_sensors()
                computed.update(ecowitt)

                # Feed wind data to calendar accumulator
                if self._wind_calendar:
                    self._wind_calendar.accumulate_sample(
                        ecowitt.get("ecowitt_wind_speed"),
                        ecowitt.get("ecowitt_wind_gust"),
                    )
                    computed["wind_calendar_today"] = (
                        self._wind_calendar.get_today_status()
                    )

            # Evaluate schedule manager (if enabled)
            schedule_result = {}
            if self._schedule_manager:
                try:
                    merged_for_schedule = {**raw, **computed}
                    schedule_result = await self._schedule_manager.evaluate_tick(
                        merged_for_schedule
                    )
                    computed["schedule_status"] = schedule_result
                except Exception as sched_err:
                    _LOGGER.error(
                        "Schedule manager tick failed: %s", sched_err
                    )
                    schedule_result = {}
                    computed["schedule_status"] = {"error": str(sched_err)}

            # Execute strategy controller tick (autonomous HEMS control)
            # Schedule manager controls whether full strategy or safety-only runs
            autopilot_should_run = schedule_result.get(
                "autopilot_should_run", self._strategy_controller.enabled if self._strategy_controller else False
            )
            safety_only = schedule_result.get("safety_only", False)

            if self._strategy_controller:
                try:
                    merged = {**raw, **computed}
                    if safety_only:
                        # Manual mode — only safety layers (W0/W3/W4)
                        ctrl_result = await self._strategy_controller.execute_safety_only(merged)
                    elif autopilot_should_run:
                        # Autopilot mode — full strategy execution
                        ctrl_result = await self._strategy_controller.execute_tick(merged)
                    else:
                        # Autopilot disabled, no schedule — skip
                        ctrl_result = {
                            "enabled": False,
                            "strategy": self._strategy_controller.active_strategy.value,
                        }

                    computed["autopilot_status"] = ctrl_result
                    computed["autopilot_active_strategy"] = (
                        self._strategy_controller.active_strategy.value
                    )
                    computed["autopilot_enabled"] = (
                        self._strategy_controller.enabled
                    )
                    computed["autopilot_decision_log"] = (
                        self._strategy_controller.decision_log[-10:]
                    )
                    computed["autopilot_disabled_automations"] = list(
                        self._strategy_controller.disabled_automations
                    )

                    # Persist live autopilot data for frontend polling
                    await self._write_autopilot_live(ctrl_result)

                except Exception as ctrl_err:
                    _LOGGER.error(
                        "Strategy controller tick failed: %s", ctrl_err
                    )
                    computed["autopilot_status"] = {"error": str(ctrl_err)}

            return {**raw, **computed}

        except Exception as err:
            raise UpdateFailed(f"Error updating Smarting HOME data: {err}") from err

    def _read_source_sensors(self) -> dict[str, Any]:
        """Read current values from HA state machine."""
        data: dict[str, Any] = {}
        for entity_id in SOURCE_SENSORS:
            state = self.hass.states.get(entity_id)
            if state is not None and state.state not in ("unknown", "unavailable"):
                data[entity_id] = state.state
            else:
                data[entity_id] = None
        return data

    def _read_ecowitt_sensors(self) -> dict[str, Any]:
        """Read Ecowitt local weather sensors via sensor_map."""
        data: dict[str, Any] = {"ecowitt_enabled": True}

        # Map of local_* sensor_map keys → ecowitt_* output keys
        ecowitt_keys = {
            "local_temp": "ecowitt_temp",
            "local_humidity": "ecowitt_humidity",
            "local_dewpoint": "ecowitt_dewpoint",
            "local_wind_speed": "ecowitt_wind_speed",
            "local_wind_gust": "ecowitt_wind_gust",
            "local_wind_direction": "ecowitt_wind_direction",
            "local_rain_rate": "ecowitt_rain_rate",
            "local_daily_rain": "ecowitt_daily_rain",
            "local_solar_radiation": "ecowitt_solar_radiation",
            "local_solar_lux": "ecowitt_solar_lux",
            "local_uv_index": "ecowitt_uv_index",
            "local_pressure": "ecowitt_pressure",
            "local_feels_like": "ecowitt_feels_like",
        }

        for map_key, out_key in ecowitt_keys.items():
            entity_id = self._sensor_map.get(map_key, "")
            if entity_id:
                state = self.hass.states.get(entity_id)
                if state and state.state not in ("unknown", "unavailable"):
                    data[out_key] = _safe_float(state.state)
                else:
                    data[out_key] = None
            else:
                data[out_key] = None

        return data

    def _compute_derived(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Compute all HEMS derived sensors."""
        now = datetime.now()
        data: dict[str, Any] = {}

        # —— Grid directional power ——
        meter = _safe_float(raw.get(SENSOR_GRID_POWER_TOTAL))
        data["grid_import_power"] = max(meter, 0)
        data["grid_export_power"] = max(-meter, 0)

        # —— PV surplus ——
        pv_power = _safe_float(raw.get(SENSOR_PV_POWER))
        load = _safe_float(raw.get(SENSOR_LOAD_TOTAL))
        data["hems_pv_surplus_power"] = max(pv_power - load, 0)

        # —— Battery calculations ——
        soc = _safe_float(raw.get(SENSOR_BATTERY_SOC))
        _battery_power = _safe_float(raw.get(SENSOR_BATTERY_POWER))  # noqa: F841
        data["goodwe_battery_energy_available"] = round(
            (soc - DEFAULT_BATTERY_MIN_SOC) / 100 * DEFAULT_BATTERY_CAPACITY / 1000, 2
        )
        data["goodwe_battery_runtime"] = (
            round(data["goodwe_battery_energy_available"] / (load / 1000), 1)
            if load > 0 else 0.0
        )

        # —— Grid frequency average ——
        f1 = _safe_float(raw.get(SENSOR_GRID_FREQUENCY_L1))
        f2 = _safe_float(raw.get(SENSOR_GRID_FREQUENCY_L2))
        f3 = _safe_float(raw.get(SENSOR_GRID_FREQUENCY_L3))
        freqs = [f for f in [f1, f2, f3] if f > 0]
        data["goodwe_grid_frequency_average"] = (
            round(sum(freqs) / len(freqs), 2) if freqs else 0.0
        )

        # —— Load balance ——
        l1 = _safe_float(raw.get(SENSOR_LOAD_L1))
        l2 = _safe_float(raw.get(SENSOR_LOAD_L2))
        l3 = _safe_float(raw.get(SENSOR_LOAD_L3))
        loads = [l1, l2, l3]
        data["goodwe_load_balance_difference"] = (
            max(loads) - min(loads) if all(load_val > 0 for load_val in loads) else 0
        )

        # —— Active loads count ——
        active = sum(1 for load_val in loads if load_val > 100)
        data["hems_active_loads"] = f"{active}/3"

        # —— Tariff zone ——
        if self._tariff == TariffType.DYNAMIC:
            tariff_data = self._compute_dynamic_tariff(raw)
        else:
            tariff_data = self._compute_g13(now)
        data.update(tariff_data)

        # —— Tariff config for AI prompts ——
        data["tariff_type"] = self._tariff
        data["energy_provider"] = self.entry.data.get(
            CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER
        )

        # —— RCE calculations ——
        if self._rce_enabled:
            rce_data = self._compute_rce(raw, tariff_data)
            data.update(rce_data)

        # —— Forecast totals ——
        fp1 = _safe_float(raw.get(SENSOR_FORECAST_POWER_1))
        fp2 = _safe_float(raw.get(SENSOR_FORECAST_POWER_2))
        data["pv_forecast_power_now_total"] = fp1 + fp2

        ft1 = _safe_float(raw.get(SENSOR_FORECAST_TODAY_1))
        ft2 = _safe_float(raw.get(SENSOR_FORECAST_TODAY_2))
        data["pv_forecast_today_total"] = round(ft1 + ft2, 2)

        fr1 = _safe_float(raw.get(SENSOR_FORECAST_REMAINING_1))
        fr2 = _safe_float(raw.get(SENSOR_FORECAST_REMAINING_2))
        data["pv_forecast_remaining_today_total"] = round(fr1 + fr2, 2)

        fm1 = _safe_float(raw.get(SENSOR_FORECAST_TOMORROW_1))
        fm2 = _safe_float(raw.get(SENSOR_FORECAST_TOMORROW_2))
        data["pv_forecast_tomorrow_total"] = round(fm1 + fm2, 2)

        # —— Forecast accuracy ——
        pv_gen = _safe_float(raw.get(SENSOR_PV_GENERATION_TODAY))
        forecast_today = data["pv_forecast_today_total"]
        if forecast_today > 0 and pv_gen > 0:
            data["pv_forecast_accuracy_today"] = round(
                min(pv_gen / forecast_today * 100, 100), 1
            )
        else:
            data["pv_forecast_accuracy_today"] = 0.0

        # —— Autarky & self-consumption ——
        load_today = _safe_float(raw.get(SENSOR_LOAD_TODAY))

        # Grid import/export daily — read from sensor_map (inverter-specific)
        grid_import_entity = (
            self._sensor_map.get("grid_import_today")
            or DEFAULT_SENSOR_MAP.get("grid_import_today", "")
        )
        grid_export_entity = (
            self._sensor_map.get("grid_export_today")
            or DEFAULT_SENSOR_MAP.get("grid_export_today", "")
        )

        def _read_entity(entity_id: str) -> float:
            if not entity_id:
                return 0.0
            state = self.hass.states.get(entity_id)
            if state and state.state not in ("unknown", "unavailable"):
                return _safe_float(state.state)
            return 0.0

        grid_import_raw = _read_entity(grid_import_entity)
        grid_export_raw = _read_entity(grid_export_entity)

        # ── Midnight correction for GoodWe "daily" sensors ──
        # GoodWe resets daily sensors at inverter wake-up (sunrise), not midnight.
        # We snapshot values at midnight and subtract to get true "today" values.
        today_str = now.strftime("%Y-%m-%d")
        if self._grid_baseline_date and self._grid_baseline_date != today_str:
            # New day detected — set baselines to last known raw values
            # (these are yesterday's end-of-day totals still reported by GoodWe)
            self._grid_import_baseline = grid_import_raw
            self._grid_export_baseline = grid_export_raw
            self._grid_baseline_date = today_str
            _LOGGER.debug(
                "Grid midnight baseline set: import=%.1f, export=%.1f",
                self._grid_import_baseline, self._grid_export_baseline,
            )
        elif not self._grid_baseline_date:
            self._grid_baseline_date = today_str
            # First run — no baseline, assume raw values are correct for today
            self._grid_import_baseline = 0.0
            self._grid_export_baseline = 0.0

        # Detect GoodWe natural reset: raw value drops significantly below baseline
        if grid_import_raw < self._grid_import_baseline * 0.5:
            self._grid_import_baseline = 0.0
        if grid_export_raw < self._grid_export_baseline * 0.5:
            self._grid_export_baseline = 0.0

        # Apply correction: today = raw - baseline
        grid_import = max(0.0, grid_import_raw - self._grid_import_baseline)
        grid_export = max(0.0, grid_export_raw - self._grid_export_baseline)
        data["grid_import_daily"] = round(grid_import, 2)
        data["grid_export_daily"] = round(grid_export, 2)

        if load_today > 0:
            home_from_pv = max(pv_gen - grid_export, 0) if pv_gen > 0 else 0
            data["goodwe_autarky_today"] = round(
                max(0, (1 - grid_import / load_today)) * 100, 1
            ) if load_today > 0 else 0.0
            data["goodwe_self_consumption_today"] = round(
                (home_from_pv / pv_gen * 100) if pv_gen > 0 else 0, 1
            )
            data["goodwe_home_consumption_from_pv_today"] = round(home_from_pv, 2)
        else:
            home_from_pv = 0.0
            data["goodwe_autarky_today"] = 0.0
            data["goodwe_self_consumption_today"] = 0.0
            data["goodwe_home_consumption_from_pv_today"] = 0.0

        data["goodwe_net_grid_today"] = round(grid_import - grid_export, 2)

        # —— Day/Night energy accumulation (server-side) ——
        day_night = self._accumulate_day_night(load, load_today, home_from_pv)
        data.update(day_night)

        # —— Battery health (SOH + limits) ——
        soh = _safe_float(raw.get(SENSOR_BATTERY_SOH), 100.0)
        charge_limit = _safe_float(raw.get(SENSOR_BATTERY_CHARGE_LIMIT))
        discharge_limit = _safe_float(raw.get(SENSOR_BATTERY_DISCHARGE_LIMIT))
        data["battery_soh"] = soh
        data["battery_charge_limit_a"] = charge_limit
        data["battery_discharge_limit_a"] = discharge_limit
        if soh >= 90:
            data["battery_health_score"] = "good"
        elif soh >= 70:
            data["battery_health_score"] = "warning"
        else:
            data["battery_health_score"] = "critical"

        # —— Inverter thermal (dual sensor) ——
        temp_air = _safe_float(raw.get(SENSOR_INVERTER_TEMP))
        temp_radiator = _safe_float(raw.get(SENSOR_INVERTER_TEMP_RADIATOR))
        data["inverter_temp_radiator"] = temp_radiator
        max_temp = max(temp_air, temp_radiator)
        if max_temp >= 65:
            data["inverter_thermal_status"] = "critical"
        elif max_temp >= 55:
            data["inverter_thermal_status"] = "hot"
        elif max_temp >= 45:
            data["inverter_thermal_status"] = "warm"
        else:
            data["inverter_thermal_status"] = "normal"

        # —— Grid power factor ——
        pf = _safe_float(raw.get(SENSOR_METER_POWER_FACTOR))
        data["grid_power_factor"] = round(pf, 3) if pf > 0 else None
        if pf >= 0.95:
            data["grid_quality"] = "excellent"
        elif pf >= 0.90:
            data["grid_quality"] = "good"
        elif pf > 0:
            data["grid_quality"] = "poor"
        else:
            data["grid_quality"] = "unknown"

        # —— Backup / UPS status ——
        backup_load_w = _safe_float(raw.get(SENSOR_BACKUP_LOAD))
        ups_pct = _safe_float(raw.get(SENSOR_UPS_LOAD))
        data["backup_load_w"] = backup_load_w
        data["ups_load_pct"] = ups_pct

        # —— Diagnostics ——
        diag_code = int(_safe_float(raw.get(SENSOR_DIAG_STATUS_CODE)))
        data["diag_status_code"] = diag_code
        data["has_active_errors"] = diag_code != 0

        # —— EMS Mode (pass-through) ——
        ems_raw = raw.get(SENSOR_EMS_MODE)
        data["ems_mode"] = ems_raw if ems_raw else "unknown"

        # —— System status ——
        data["goodwe_system_status"] = self._compute_system_status(raw, soc)

        # —— HEMS recommendation ——
        data["hems_rce_recommendation"] = self._compute_recommendation(
            raw, data, soc, now
        )

        # —— License info ——
        data["license_valid"] = self.license_manager.is_valid
        data["license_tier"] = self.license_manager.tier.value

        return data

    def _accumulate_day_night(
        self,
        load_w: float,
        load_today_kwh: float,
        pv_to_home_kwh: float,
    ) -> dict[str, Any]:
        """Accumulate load energy into day/night buckets (server-side, 24/7).

        Cycle: midnight → midnight (24h).
        Day   = sunrise → sunset  (sun.sun above_horizon)
        Night = (00:00 → sunrise) + (sunset → 24:00)

        Both accumulators reset at midnight. Night is the SUM of the
        morning segment (00:00→sunrise) and evening segment (sunset→24:00)
        within the same calendar day.

        Calibrates against load_today from inverter for accuracy.
        """
        now_ts = time.monotonic()
        today_str = datetime.now().strftime("%Y-%m-%d")

        # ── Midnight reset ──
        # Reset BOTH accumulators at midnight — new 24h cycle begins.
        if self._accum_date and self._accum_date != today_str:
            _LOGGER.debug(
                "Day/night midnight reset: day=%.0f Ws, night=%.0f Ws",
                self._load_day_ws,
                self._load_night_ws,
            )
            self._load_day_ws = 0.0
            self._load_night_ws = 0.0
            self._accum_date = today_str
            # Reset timestamp to avoid a huge delta after date change
            self._last_load_ts = now_ts

        if not self._accum_date:
            self._accum_date = today_str

        # ── Determine if it's daytime ──
        sun_state = self.hass.states.get("sun.sun")
        is_day = sun_state is not None and sun_state.state == "above_horizon"

        # ── Accumulate energy ──
        # Night accumulates in two segments within the same day:
        #   1. Morning: 00:00 → sunrise (sun below horizon)
        #   2. Evening: sunset → 24:00 (sun below horizon)
        if self._last_load_ts is not None and load_w > 0:
            dt_sec = now_ts - self._last_load_ts
            if 0 < dt_sec < 120:  # Cap at 2 min to avoid spikes after sleep
                watt_sec = load_w * dt_sec
                if is_day:
                    self._load_day_ws += watt_sec
                else:
                    self._load_night_ws += watt_sec
        self._last_load_ts = now_ts

        # ── Calibrate against load_today from inverter ──
        # The inverter's load_today is the ground truth. Our accumulators
        # provide the day/night RATIO, which we apply to the inverter value.
        total_ws = self._load_day_ws + self._load_night_ws
        day_kwh = 0.0
        night_kwh = 0.0

        if total_ws > 0 and load_today_kwh > 0:
            day_ratio = self._load_day_ws / total_ws
            day_kwh = round(load_today_kwh * day_ratio, 2)
            night_kwh = round(load_today_kwh * (1 - day_ratio), 2)
        elif total_ws > 0:
            # No load_today yet — use raw accumulator values
            day_kwh = round(self._load_day_ws / 3_600_000, 2)
            night_kwh = round(self._load_night_ws / 3_600_000, 2)

        # ── PV-based fallback ──
        # If accumulators missed daytime (e.g. HA restart after sunset),
        # PV self-consumption is the physical minimum for day consumption
        if day_kwh == 0 and pv_to_home_kwh > 0 and load_today_kwh > 0:
            day_kwh = round(min(load_today_kwh, pv_to_home_kwh), 2)
            night_kwh = round(max(0, load_today_kwh - day_kwh), 2)

        # ── PV to home (server-side, for frontend) ──
        pv_to_home = round(pv_to_home_kwh, 2) if pv_to_home_kwh > 0 else 0.0

        return {
            "load_day_kwh": day_kwh,
            "load_night_kwh": night_kwh,
            "load_pv_to_home_kwh": pv_to_home,
        }

    def _compute_g13(self, now: datetime) -> dict[str, Any]:
        """Compute G13 tariff data."""
        data: dict[str, Any] = {}
        is_weekend = now.weekday() >= 5
        hour = now.hour
        is_winter = now.month in WINTER_MONTHS

        if is_weekend:
            data["g13_current_zone"] = G13Zone.OFF_PEAK
            data["g13_buy_price"] = G13_PRICES[G13Zone.OFF_PEAK]
            data["g13_is_afternoon_peak"] = False
            data["g13_is_off_peak"] = True
        else:
            schedule = G13_WINTER_SCHEDULE if is_winter else G13_SUMMER_SCHEDULE
            zone = G13Zone.OFF_PEAK  # default

            for (start, end), z in schedule.items():
                if start < end:
                    if start <= hour < end:
                        zone = z
                        break
                else:  # wraps midnight
                    if hour >= start or hour < end:
                        zone = z
                        break

            data["g13_current_zone"] = zone
            data["g13_buy_price"] = G13_PRICES[zone]
            data["g13_is_afternoon_peak"] = zone == G13Zone.AFTERNOON_PEAK
            data["g13_is_off_peak"] = zone == G13Zone.OFF_PEAK

        return data

    def _compute_dynamic_tariff(
        self, raw: dict[str, Any]
    ) -> dict[str, Any]:
        """Compute dynamic (ENTSO-E) tariff data."""
        data: dict[str, Any] = {}

        # Current all-in price
        allin_now = _safe_float(raw.get(SENSOR_ENTSOE_ALLIN_NOW))
        allin_next = _safe_float(raw.get(SENSOR_ENTSOE_ALLIN_NEXT))
        allin_min = _safe_float(raw.get(SENSOR_ENTSOE_ALLIN_MIN))
        allin_max = _safe_float(raw.get(SENSOR_ENTSOE_ALLIN_MAX))
        avg_today = _safe_float(raw.get(SENSOR_ENTSOE_AVG_TODAY))
        rank = _safe_float(raw.get(SENSOR_ENTSOE_RANK))
        percentile = _safe_float(raw.get(SENSOR_ENTSOE_PERCENTILE))
        market_price = _safe_float(raw.get(SENSOR_ENTSOE_PRICE_NOW))

        # Determine dynamic zone based on percentile
        thresholds = DYNAMIC_PRICE_THRESHOLDS
        if allin_now <= thresholds["very_cheap"]:
            dynamic_zone = "very_cheap"
        elif allin_now <= thresholds["cheap"]:
            dynamic_zone = "cheap"
        elif allin_now <= thresholds["normal"]:
            dynamic_zone = "normal"
        elif allin_now <= thresholds["expensive"]:
            dynamic_zone = "expensive"
        else:
            dynamic_zone = "very_expensive"

        data["dynamic_buy_price"] = round(allin_now, 4)
        data["dynamic_next_price"] = round(allin_next, 4)
        data["dynamic_min_today"] = round(allin_min, 4)
        data["dynamic_max_today"] = round(allin_max, 4)
        data["dynamic_avg_today"] = round(avg_today, 4)
        data["dynamic_market_price"] = round(market_price, 4)
        data["dynamic_rank"] = int(rank)
        data["dynamic_percentile"] = round(percentile, 1)
        data["dynamic_zone"] = dynamic_zone

        # Map to G13-compatible keys for backward compat with strategy controller
        zone_to_g13 = {
            "very_cheap": G13Zone.OFF_PEAK,
            "cheap": G13Zone.OFF_PEAK,
            "normal": G13Zone.MORNING_PEAK,
            "expensive": G13Zone.AFTERNOON_PEAK,
            "very_expensive": G13Zone.AFTERNOON_PEAK,
        }
        data["g13_current_zone"] = zone_to_g13.get(dynamic_zone, G13Zone.OFF_PEAK)
        data["g13_buy_price"] = round(allin_now, 4)
        data["g13_is_afternoon_peak"] = dynamic_zone in ("expensive", "very_expensive")
        data["g13_is_off_peak"] = dynamic_zone in ("very_cheap", "cheap")

        # Read prices_today attribute for frontend 24h chart
        entsoe_state = self.hass.states.get(SENSOR_ENTSOE_PRICE_NOW)
        if entsoe_state and entsoe_state.attributes:
            prices_today = entsoe_state.attributes.get("prices_today", [])
            data["dynamic_prices_today"] = prices_today
        else:
            data["dynamic_prices_today"] = []

        return data

    def _get_price_lookahead(self, hours_ahead: int = 3) -> dict[str, float]:
        """Extract future prices from the 96-point prices attribute (v2).

        RCE PSE v2 provides 15-min resolution data in the 'prices' attribute
        of sensor.rce_pse_cena (96 entries per day). We use this to compute
        +1h, +2h, +3h lookahead — replacing the removed v1 sensors.
        """
        state = self.hass.states.get(SENSOR_RCE_PRICE)
        if not state or not state.attributes:
            _LOGGER.debug("RCE lookahead: sensor %s has no state/attributes", SENSOR_RCE_PRICE)
            return {}

        # Try multiple attribute names (v2 may use 'prices', 'forecast', etc.)
        prices = (
            state.attributes.get("prices")
            or state.attributes.get("forecast")
            or state.attributes.get("price_list")
            or []
        )
        if not prices:
            _LOGGER.debug(
                "RCE lookahead: no prices attribute found. Available attrs: %s",
                list(state.attributes.keys()),
            )
            return {}

        # Use timezone-aware now to match RCE PSE v2 datetime format
        now = datetime.now(tz=timezone.utc)
        result: dict[str, float] = {}

        for offset in range(1, hours_ahead + 1):
            target_time = now + timedelta(hours=offset)
            # Find the price entry covering the target time
            for entry in prices:
                try:
                    dtime_str = entry.get("dtime", "") or entry.get("period", "")
                    entry_time = datetime.fromisoformat(str(dtime_str))
                    # If entry_time is naive, assume local timezone
                    if entry_time.tzinfo is None:
                        entry_time = entry_time.astimezone()
                    # Normalize target_time to same tz for comparison
                    if target_time.tzinfo != entry_time.tzinfo:
                        target_time_cmp = target_time.astimezone(entry_time.tzinfo)
                    else:
                        target_time_cmp = target_time
                    if entry_time >= target_time_cmp:
                        result[f"rce_lookahead_{offset}h_mwh"] = float(
                            entry.get("rce_pln", 0)
                        )
                        break
                except (ValueError, TypeError) as exc:
                    _LOGGER.debug("RCE lookahead parse error: %s (entry=%s)", exc, entry)
                    continue

        if not result:
            _LOGGER.debug(
                "RCE lookahead: no future prices found (prices entries: %d, now: %s)",
                len(prices), now.isoformat(),
            )
        return result

    def _compute_rce(
        self, raw: dict[str, Any], g13_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Compute RCE-derived values (v2 — 15-min resolution)."""
        data: dict[str, Any] = {}

        rce_mwh = _safe_float(raw.get(SENSOR_RCE_PRICE))
        rce_kwh = rce_mwh / 1000 if rce_mwh else 0
        rce_sell = rce_kwh * RCE_PROSUMER_COEFFICIENT

        data["rce_sell_price"] = round(rce_sell, 4)

        # —— Next period price (v2: 15min or 1h depending on config) ——
        rce_next = _safe_float(raw.get(SENSOR_RCE_NEXT_PERIOD)) / 1000
        data["rce_sell_price_next_hour"] = round(
            rce_next * RCE_PROSUMER_COEFFICIENT, 4
        )

        # —— Lookahead prices (computed from 96-point attribute) ——
        lookahead = self._get_price_lookahead(hours_ahead=3)
        rce_2h_mwh = lookahead.get("rce_lookahead_2h_mwh", rce_mwh)
        rce_3h_mwh = lookahead.get("rce_lookahead_3h_mwh", rce_mwh)
        rce_2h = rce_2h_mwh / 1000
        rce_3h = rce_3h_mwh / 1000

        data["rce_sell_price_2h"] = round(rce_2h * RCE_PROSUMER_COEFFICIENT, 4)
        data["rce_sell_price_3h"] = round(rce_3h * RCE_PROSUMER_COEFFICIENT, 4)

        # —— Statistics ——
        avg = _safe_float(raw.get(SENSOR_RCE_AVG_TODAY)) / 1000
        data["rce_average_today"] = round(avg * RCE_PROSUMER_COEFFICIENT, 4)

        min_rce = _safe_float(raw.get(SENSOR_RCE_MIN_TODAY)) / 1000
        data["rce_min_today"] = round(min_rce * RCE_PROSUMER_COEFFICIENT, 4)

        max_rce = _safe_float(raw.get(SENSOR_RCE_MAX_TODAY)) / 1000
        data["rce_max_today"] = round(max_rce * RCE_PROSUMER_COEFFICIENT, 4)

        # v2: Median — more robust than average for skewed distributions
        median_mwh = _safe_float(raw.get(SENSOR_RCE_MEDIAN_TODAY))
        data["rce_median_today"] = round(
            median_mwh / 1000 * RCE_PROSUMER_COEFFICIENT, 4
        ) if median_mwh else 0.0

        # —— Tomorrow awareness (available after ~14:00) ——
        tomorrow_mwh = _safe_float(raw.get(SENSOR_RCE_PRICE_TOMORROW))
        data["rce_tomorrow_price"] = round(tomorrow_mwh / 1000, 4) if tomorrow_mwh else None
        tomorrow_avg = _safe_float(raw.get(SENSOR_RCE_AVG_TOMORROW))
        data["rce_avg_tomorrow"] = round(
            tomorrow_avg / 1000 * RCE_PROSUMER_COEFFICIENT, 4
        ) if tomorrow_avg else None
        data["rce_tomorrow_vs_today_pct"] = _safe_float(
            raw.get(SENSOR_RCE_TOMORROW_VS_TODAY)
        )

        # —— Energy Compass (PDGSZ) — PSE grid demand signal ——
        compass_state = self.hass.states.get(SENSOR_RCE_COMPASS_TODAY)
        if compass_state and compass_state.state not in ("unknown", "unavailable"):
            data["rce_compass"] = compass_state.state
        else:
            data["rce_compass"] = "unknown"

        # —— Configurable window averages ——
        cheap_window_avg = _safe_float(raw.get(SENSOR_RCE_CHEAP_WINDOW_AVG))
        expensive_window_avg = _safe_float(raw.get(SENSOR_RCE_EXPENSIVE_WINDOW_AVG))
        data["rce_cheap_window_avg"] = round(cheap_window_avg / 1000, 4) if cheap_window_avg else 0.0
        data["rce_expensive_window_avg"] = round(expensive_window_avg / 1000, 4) if expensive_window_avg else 0.0
        # Real arbitrage margin from actual window data
        if cheap_window_avg > 0 and expensive_window_avg > 0:
            data["rce_window_arbitrage_margin"] = round(
                (expensive_window_avg - cheap_window_avg) / 1000, 4
            )
        else:
            data["rce_window_arbitrage_margin"] = 0.0

        # —— Spread: G13 buy - RCE sell ——
        g13_price = g13_data.get("g13_buy_price", 0.63)
        data["g13_rce_spread"] = round(g13_price - rce_sell, 4)

        # —— Trend ——
        if rce_kwh > 0 and rce_next > 0:
            change_pct = (rce_next - rce_kwh) / rce_kwh * 100
            if change_pct > 10:
                data["rce_price_trend"] = RCETrend.RISING
            elif change_pct < -10:
                data["rce_price_trend"] = RCETrend.FALLING
            else:
                data["rce_price_trend"] = RCETrend.STABLE
        else:
            data["rce_price_trend"] = RCETrend.STABLE

        # —— Good sell evaluation ——
        thresholds = RCE_PRICE_THRESHOLDS
        if rce_mwh >= thresholds["very_expensive"]:
            data["rce_good_sell"] = RCEPriceLevel.EXCELLENT
        elif rce_mwh >= thresholds["expensive"]:
            data["rce_good_sell"] = RCEPriceLevel.GOOD
        elif rce_mwh >= thresholds["normal"]:
            data["rce_good_sell"] = RCEPriceLevel.NORMAL
        elif rce_mwh >= thresholds["cheap"]:
            data["rce_good_sell"] = RCEPriceLevel.POOR
        else:
            data["rce_good_sell"] = RCEPriceLevel.TERRIBLE

        # Arbitrage potential — dynamic, SOC-aware
        peak_price = G13_PRICES[G13Zone.AFTERNOON_PEAK]
        off_peak_price = G13_PRICES[G13Zone.OFF_PEAK]
        capacity_kwh = DEFAULT_BATTERY_CAPACITY / 1000
        soc_val = _safe_float(raw.get(SENSOR_BATTERY_SOC))
        min_soc_pct = DEFAULT_BATTERY_MIN_SOC
        rt_efficiency = 0.92  # round-trip efficiency

        # Static potential (full cycle) — backward compat
        data["g13_battery_arbitrage_potential"] = round(
            capacity_kwh * (peak_price - off_peak_price), 2
        )

        # Dynamic potential — available RIGHT NOW based on SOC
        available_kwh = max(0, (soc_val - min_soc_pct) / 100 * capacity_kwh)
        arbitrage_margin = peak_price - off_peak_price
        data["arbitrage_potential_now"] = round(
            available_kwh * arbitrage_margin * rt_efficiency, 2
        )
        data["arbitrage_margin_per_kwh"] = round(arbitrage_margin, 4)
        data["arbitrage_buy_price"] = off_peak_price
        data["arbitrage_sell_price"] = peak_price
        data["arbitrage_available_kwh"] = round(available_kwh, 2)

        # If dynamic tariff — override with actual ENTSO-E prices
        if self._tariff == TariffType.DYNAMIC:
            entsoe_min = _safe_float(raw.get(SENSOR_ENTSOE_ALLIN_MIN))
            entsoe_max = _safe_float(raw.get(SENSOR_ENTSOE_ALLIN_MAX))
            if entsoe_min > 0 and entsoe_max > 0:
                dyn_margin = entsoe_max - entsoe_min
                data["arbitrage_margin_per_kwh"] = round(dyn_margin, 4)
                data["arbitrage_buy_price"] = round(entsoe_min, 4)
                data["arbitrage_sell_price"] = round(entsoe_max, 4)
                data["arbitrage_potential_now"] = round(
                    available_kwh * dyn_margin * rt_efficiency, 2
                )

        # Arbitrage profit today — estimated from battery throughput
        charge_today = _safe_float(raw.get(SENSOR_BATTERY_CHARGE_TODAY))
        discharge_today = _safe_float(raw.get(SENSOR_BATTERY_DISCHARGE_TODAY))
        # Estimated arbitrage profit: min(charge, discharge) × margin × efficiency
        arb_cycles_kwh = min(charge_today, discharge_today)
        data["arbitrage_profit_today"] = round(
            arb_cycles_kwh * data["arbitrage_margin_per_kwh"] * rt_efficiency, 2
        )

        # Next arbitrage action — context-aware recommendation
        g13_zone = g13_data.get("g13_current_zone", G13Zone.OFF_PEAK)
        now = datetime.now()
        data["arbitrage_next_action"] = self._compute_next_arb_action(
            g13_zone, soc_val, now
        )

        return data

    def _compute_next_arb_action(
        self, zone: str, soc: float, now: datetime
    ) -> str:
        """Compute the next recommended arbitrage action."""
        hour = now.hour
        is_weekend = now.weekday() >= 5

        if is_weekend:
            if soc < 50:
                return "🔋 Weekend off-peak — ładuj baterię (0.63 zł/kWh)"
            return "✅ Weekend — autokonsumpcja, bateria w standby"

        if zone == G13Zone.OFF_PEAK:
            if soc < 80:
                return "🔋 Off-peak — ładuj baterię tanio (0.63 zł/kWh)"
            return "✅ Off-peak — bateria naładowana, czekaj na szczyt"

        if zone == G13Zone.MORNING_PEAK:
            if soc > 30:
                return "💰 Szczyt poranny — bateria zasila dom (0.91 zł/kWh)"
            return "⚠️ Szczyt poranny — SOC niski, oszczędzaj na popołudnie"

        if zone == G13Zone.AFTERNOON_PEAK:
            if soc > 20:
                return "🔥 Szczyt popołudniowy — MAX rozładowanie! (1.50 zł/kWh)"
            return "⚠️ Bateria wyczerpana — import z sieci w szczycie"

        # Dynamic tariff
        if self._tariff == TariffType.DYNAMIC:
            if hour < 6:
                return "🌙 Noc — tanio, ładuj baterię z sieci"
            if 16 <= hour <= 21:
                return "🔥 Peak wieczorny — rozładowuj baterię"
            return "📊 Dynamiczna — AI analizuje ceny RCE"

        return "✅ System w trybie auto"

    def _compute_system_status(
        self, raw: dict[str, Any], soc: float
    ) -> str:
        """Compute system status text."""
        pv = _safe_float(raw.get(SENSOR_PV_POWER))
        battery_power = _safe_float(raw.get(SENSOR_BATTERY_POWER))
        meter = _safe_float(raw.get(SENSOR_GRID_POWER_TOTAL))

        statuses = []
        if pv > 100:
            statuses.append(f"PV: {pv:.0f}W")
        if battery_power > 50:
            statuses.append(f"Charging: {battery_power:.0f}W")
        elif battery_power < -50:
            statuses.append(f"Discharging: {abs(battery_power):.0f}W")
        if meter > 100:
            statuses.append(f"Importing: {meter:.0f}W")
        elif meter < -100:
            statuses.append(f"Exporting: {abs(meter):.0f}W")

        statuses.append(f"SOC: {soc:.0f}%")
        return " | ".join(statuses) if statuses else "System idle"

    def _compute_recommendation(
        self,
        raw: dict[str, Any],
        data: dict[str, Any],
        soc: float,
        now: datetime,
    ) -> str:
        """Compute HEMS recommendation text."""
        rce_mwh = _safe_float(raw.get(SENSOR_RCE_PRICE))
        g13_zone = data.get("g13_current_zone", G13Zone.OFF_PEAK)
        hour = now.hour

        # Emergency
        if soc < 20:
            return "⚠️ EMERGENCY: SOC < 20% — Force charging battery NOW"

        # Negative RCE — always charge
        if rce_mwh < 0:
            return "💰 RCE negative — Charge battery + enable all loads (free energy!)"

        # Very expensive RCE + evening = max export
        if rce_mwh > 500 and hour >= 16:
            return "🔥 RCE very high — Maximum export, disable all non-essential loads"

        # Afternoon peak — use battery
        if g13_zone == G13Zone.AFTERNOON_PEAK:
            return "💰 G13 afternoon peak (1.50 PLN/kWh) — Battery powers home, minimize import"

        # Morning peak — sell PV
        if g13_zone == G13Zone.MORNING_PEAK:
            pv = _safe_float(raw.get(SENSOR_PV_POWER))
            if pv > 500:
                return "☀️ G13 morning peak — Block charging, export PV to grid"
            return "⏰ G13 morning peak — Low PV, charge when RCE drops"

        # Off-peak — charge battery
        if g13_zone == G13Zone.OFF_PEAK:
            if soc < 50:
                return "🔋 Off-peak — Charge battery (SOC low)"
            return "🌙 Off-peak — Auto-consumption mode, battery standby"

        return "✅ System operating in auto mode"

    async def _write_autopilot_live(self, ctrl_result: dict[str, Any]) -> None:
        """Persist live autopilot tick data into settings.json for frontend polling."""
        from .settings_io import write_sync

        # Include current arbitrage data from coordinator
        arb_data = {}
        if self.data:
            arb_data = {
                "arbitrage_potential_now": self.data.get("arbitrage_potential_now", 0),
                "arbitrage_margin": self.data.get("arbitrage_margin_per_kwh", 0),
                "arbitrage_profit_today": self.data.get("arbitrage_profit_today", 0),
                "arbitrage_next_action": self.data.get("arbitrage_next_action", ""),
            }

        updates: dict[str, Any] = {
            "autopilot_live": {
                "enabled": ctrl_result.get("enabled", False),
                "strategy": ctrl_result.get("strategy", ""),
                "strategy_label": ctrl_result.get("strategy_label", ""),
                "actions": ctrl_result.get("actions", []),
                "soc": ctrl_result.get("soc"),
                "pv": ctrl_result.get("pv"),
                "load": ctrl_result.get("load"),
                "surplus": ctrl_result.get("surplus"),
                "g13_zone": ctrl_result.get("g13_zone"),
                "g13_price": ctrl_result.get("g13_price"),
                "rce_price_mwh": ctrl_result.get("rce_price_mwh"),
                "ai_reasoning": ctrl_result.get("ai_reasoning", ""),
                "timestamp": ctrl_result.get("timestamp"),
                **arb_data,
            },
        }

        # Also persist decision log (last 15 entries)
        if self._strategy_controller:
            updates["autopilot_decision_log"] = (
                self._strategy_controller.decision_log[-15:]
            )

        def _do_write() -> None:
            try:
                write_sync(self.hass, updates)
            except Exception:
                pass  # Non-critical — don't break coordinator

        await self.hass.async_add_executor_job(_do_write)

