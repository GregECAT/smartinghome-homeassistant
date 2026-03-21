"""Data update coordinator for Smarting HOME."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
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
    SENSOR_RCE_PRICE,
    SENSOR_RCE_PRICE_KWH,
    SENSOR_RCE_SELL_PROSUMER,
    SENSOR_RCE_NEXT_HOUR,
    SENSOR_RCE_2H,
    SENSOR_RCE_3H,
    SENSOR_RCE_AVG_TODAY,
    SENSOR_RCE_MIN_TODAY,
    SENSOR_RCE_MAX_TODAY,
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
    LicenseTier,
    DEMO_MAX_SENSORS,
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
    SENSOR_RCE_PRICE, SENSOR_RCE_PRICE_KWH, SENSOR_RCE_SELL_PROSUMER,
    SENSOR_RCE_NEXT_HOUR, SENSOR_RCE_2H, SENSOR_RCE_3H,
    SENSOR_RCE_AVG_TODAY, SENSOR_RCE_MIN_TODAY, SENSOR_RCE_MAX_TODAY,
    SENSOR_FORECAST_POWER_1, SENSOR_FORECAST_POWER_2,
    SENSOR_FORECAST_TODAY_1, SENSOR_FORECAST_TODAY_2,
    SENSOR_FORECAST_REMAINING_1, SENSOR_FORECAST_REMAINING_2,
    SENSOR_FORECAST_TOMORROW_1, SENSOR_FORECAST_TOMORROW_2,
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

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from HA state machine and compute HEMS values."""
        try:
            # Periodic license check
            await self.license_manager.periodic_check()

            # Read all source sensors
            raw = self._read_source_sensors()

            # Compute derived values
            computed = self._compute_derived(raw)

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
        battery_power = _safe_float(raw.get(SENSOR_BATTERY_POWER))
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
            max(loads) - min(loads) if all(l > 0 for l in loads) else 0
        )

        # —— Active loads count ——
        active = sum(1 for l in loads if l > 100)
        data["hems_active_loads"] = f"{active}/3"

        # —— G13 Tariff zone ——
        g13_data = self._compute_g13(now)
        data.update(g13_data)

        # —— RCE calculations ——
        if self._rce_enabled:
            rce_data = self._compute_rce(raw, g13_data)
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
        grid_import = data.get("grid_import_daily", 0.0)
        grid_export = data.get("grid_export_daily", 0.0)

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
            data["goodwe_autarky_today"] = 0.0
            data["goodwe_self_consumption_today"] = 0.0
            data["goodwe_home_consumption_from_pv_today"] = 0.0

        data["goodwe_net_grid_today"] = round(grid_import - grid_export, 2)

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

    def _compute_rce(
        self, raw: dict[str, Any], g13_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Compute RCE-derived values."""
        data: dict[str, Any] = {}

        rce_mwh = _safe_float(raw.get(SENSOR_RCE_PRICE))
        rce_kwh = rce_mwh / 1000 if rce_mwh else 0
        rce_sell = rce_kwh * RCE_PROSUMER_COEFFICIENT

        data["rce_sell_price"] = round(rce_sell, 4)

        # Next hours
        rce_next = _safe_float(raw.get(SENSOR_RCE_NEXT_HOUR)) / 1000
        data["rce_sell_price_next_hour"] = round(
            rce_next * RCE_PROSUMER_COEFFICIENT, 4
        )

        rce_2h = _safe_float(raw.get(SENSOR_RCE_2H)) / 1000
        data["rce_sell_price_2h"] = round(rce_2h * RCE_PROSUMER_COEFFICIENT, 4)

        rce_3h = _safe_float(raw.get(SENSOR_RCE_3H)) / 1000
        data["rce_sell_price_3h"] = round(rce_3h * RCE_PROSUMER_COEFFICIENT, 4)

        # Averages
        avg = _safe_float(raw.get(SENSOR_RCE_AVG_TODAY)) / 1000
        data["rce_average_today"] = round(avg * RCE_PROSUMER_COEFFICIENT, 4)

        min_rce = _safe_float(raw.get(SENSOR_RCE_MIN_TODAY)) / 1000
        data["rce_min_today"] = round(min_rce * RCE_PROSUMER_COEFFICIENT, 4)

        max_rce = _safe_float(raw.get(SENSOR_RCE_MAX_TODAY)) / 1000
        data["rce_max_today"] = round(max_rce * RCE_PROSUMER_COEFFICIENT, 4)

        # Spread: G13 buy - RCE sell
        g13_price = g13_data.get("g13_buy_price", 0.63)
        data["g13_rce_spread"] = round(g13_price - rce_sell, 4)

        # Trend
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

        # Good sell evaluation
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

        # Arbitrage potential
        peak_price = G13_PRICES[G13Zone.AFTERNOON_PEAK]
        off_peak_price = G13_PRICES[G13Zone.OFF_PEAK]
        capacity_kwh = DEFAULT_BATTERY_CAPACITY / 1000
        data["g13_battery_arbitrage_potential"] = round(
            capacity_kwh * (peak_price - off_peak_price), 2
        )

        return data

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
