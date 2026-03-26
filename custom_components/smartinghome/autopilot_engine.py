"""Autopilot Engine for Smarting HOME.

AI-powered strategy evaluation and hourly energy plan estimation.
Supports 6 strategies: Max Self-Consumption, Max Profit, Battery Protection,
Zero Export, Weather Adaptive, AI Full Autonomy.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .const import (
    AutopilotStrategy,
    AUTOPILOT_STRATEGY_LABELS,
    AUTOPILOT_STRATEGY_DESCRIPTIONS,
    G13Zone,
    G13_PRICES,
    G13_WINTER_SCHEDULE,
    G13_SUMMER_SCHEDULE,
    WINTER_MONTHS,
    DEFAULT_BATTERY_CAPACITY,
    RCE_PROSUMER_COEFFICIENT,
)

_LOGGER = logging.getLogger(__name__)

# Typical household load profile (W) per hour — Polish average
_TYPICAL_LOAD_PROFILE = [
    300, 250, 200, 200, 200, 250,   # 00-05: night
    400, 600, 800, 700, 600, 500,   # 06-11: morning
    500, 450, 400, 500, 600, 800,   # 12-17: midday
    1000, 1200, 1000, 800, 600, 400, # 18-23: evening peak
]

# PV generation profile factors (fraction of peak capacity per hour)
# Spring/autumn solar curve for Poland ~50°N
_PV_PROFILE_FACTORS = [
    0, 0, 0, 0, 0, 0.02,            # 00-05
    0.08, 0.18, 0.35, 0.55, 0.72, 0.85,  # 06-11
    0.90, 0.88, 0.78, 0.62, 0.42, 0.22,  # 12-17
    0.08, 0.01, 0, 0, 0, 0,         # 18-23
]


def _get_g13_zone(hour: int, month: int, weekday: int) -> G13Zone:
    """Determine G13 tariff zone for given hour/month/weekday."""
    if weekday >= 5:  # weekend = off-peak
        return G13Zone.OFF_PEAK

    schedule = G13_WINTER_SCHEDULE if month in WINTER_MONTHS else G13_SUMMER_SCHEDULE
    for (start, end), zone in schedule.items():
        if start < end:
            if start <= hour < end:
                return zone
        else:  # wraps midnight
            if hour >= start or hour < end:
                return zone
    return G13Zone.OFF_PEAK


def _get_g13_price(zone: G13Zone) -> float:
    """Get G13 price for a zone."""
    return G13_PRICES.get(zone, 0.63)


class HourlyAction:
    """Represents a single hour's energy action plan."""

    __slots__ = (
        "hour", "action", "reason", "estimated_pv", "estimated_load",
        "battery_delta", "grid_delta", "soc_start", "soc_end",
        "cost", "revenue", "savings",
    )

    def __init__(self, hour: int):
        self.hour = hour
        self.action = "hold"  # charge|discharge|sell|hold|load_on|load_off
        self.reason = ""
        self.estimated_pv = 0.0
        self.estimated_load = 0.0
        self.battery_delta = 0.0  # positive = charge, negative = discharge
        self.grid_delta = 0.0     # positive = import, negative = export
        self.soc_start = 0.0
        self.soc_end = 0.0
        self.cost = 0.0
        self.revenue = 0.0
        self.savings = 0.0

    def to_dict(self) -> dict:
        return {
            "hour": self.hour,
            "action": self.action,
            "reason": self.reason,
            "pv": round(self.estimated_pv),
            "load": round(self.estimated_load),
            "battery": round(self.battery_delta),
            "grid": round(self.grid_delta),
            "soc_start": round(self.soc_start, 1),
            "soc_end": round(self.soc_end, 1),
            "cost": round(self.cost, 2),
            "revenue": round(self.revenue, 2),
            "savings": round(self.savings, 2),
        }


class AutopilotEngine:
    """AI-powered autopilot for energy management.

    Estimates hourly plans for each strategy and computes financial outcomes.
    """

    def __init__(
        self,
        battery_capacity_wh: float = DEFAULT_BATTERY_CAPACITY,
        pv_peak_w: float = 0.0,
    ) -> None:
        self._battery_cap = battery_capacity_wh
        self._pv_peak = pv_peak_w

    def estimate_strategy(
        self,
        strategy: AutopilotStrategy,
        current_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Estimate a 24h plan for a given strategy.

        Returns:
            {
                "strategy": str,
                "label": str,
                "description": str,
                "hourly_plan": [HourlyAction.to_dict(), ...],
                "total_savings": float,
                "total_cost": float,
                "total_revenue": float,
                "total_import_kwh": float,
                "total_export_kwh": float,
                "total_self_consumption_kwh": float,
                "vs_no_management": float,  # savings vs no strategy
            }
        """
        now = datetime.now()
        month = now.month
        weekday = now.weekday()
        current_hour = now.hour

        # Extract current data
        soc = float(current_data.get("battery_soc") or 50)
        pv_forecast = float(current_data.get("forecast_today") or 0)
        rce_price = float(current_data.get("rce_price") or 250)  # PLN/MWh
        load_now = float(current_data.get("load") or 500)
        pv_now = float(current_data.get("pv_power") or 0)

        # Estimate PV peak from forecast or current
        pv_peak = self._pv_peak or max(pv_now * 1.5, 3000)
        if pv_forecast > 0:
            # Scale PV profile to match forecast
            total_factor = sum(_PV_PROFILE_FACTORS)
            pv_peak = (pv_forecast * 1000) / total_factor if total_factor > 0 else 3000

        bat_cap_kwh = self._battery_cap / 1000
        max_charge_rate = bat_cap_kwh * 0.5  # 0.5C max charge (kW)
        max_discharge_rate = bat_cap_kwh * 0.5

        # Build hourly plan — start from current hour for remaining day
        plan: list[HourlyAction] = []
        running_soc = soc
        total_cost = 0.0
        total_revenue = 0.0
        total_savings = 0.0
        total_import = 0.0
        total_export = 0.0
        total_self_consumption = 0.0

        # Also compute "no management" baseline (simple: PV→load, rest→grid)
        baseline_cost = 0.0

        # Generate ordered hour list: current_hour → 23, then 0 → current_hour-1 (next day)
        hours = list(range(current_hour, 24)) + list(range(0, current_hour))

        for h in hours:
            ha = HourlyAction(h)
            ha.soc_start = running_soc

            # Current hour → use actual readings; future hours → profile estimates
            if h == current_hour:
                ha.estimated_pv = pv_now
                ha.estimated_load = load_now
            else:
                ha.estimated_pv = pv_peak * _PV_PROFILE_FACTORS[h]
                # Scale load profile by current load ratio
                load_scale = load_now / 500 if load_now > 0 else 1.0
                ha.estimated_load = _TYPICAL_LOAD_PROFILE[h] * load_scale

            pv_w = ha.estimated_pv
            load_w = ha.estimated_load
            surplus = pv_w - load_w  # positive = excess PV

            g13_zone = _get_g13_zone(h, month, weekday)
            g13_price = _get_g13_price(g13_zone)
            rce_sell = (rce_price / 1000) * RCE_PROSUMER_COEFFICIENT

            # Baseline: no battery management, excess PV → grid
            if surplus > 0:
                baseline_cost -= (surplus / 1000) * rce_sell  # export revenue
            else:
                baseline_cost += (abs(surplus) / 1000) * g13_price  # import cost

            # Apply strategy logic
            if strategy == AutopilotStrategy.MAX_SELF_CONSUMPTION:
                ha = self._apply_max_self_consumption(
                    ha, surplus, running_soc, bat_cap_kwh,
                    max_charge_rate, max_discharge_rate, g13_price, rce_sell,
                )
            elif strategy == AutopilotStrategy.MAX_PROFIT:
                ha = self._apply_max_profit(
                    ha, surplus, running_soc, bat_cap_kwh,
                    max_charge_rate, max_discharge_rate, g13_zone, g13_price,
                    rce_price, rce_sell, h,
                )
            elif strategy == AutopilotStrategy.BATTERY_PROTECTION:
                ha = self._apply_battery_protection(
                    ha, surplus, running_soc, bat_cap_kwh,
                    max_charge_rate, max_discharge_rate, g13_price, rce_sell, h,
                )
            elif strategy == AutopilotStrategy.ZERO_EXPORT:
                ha = self._apply_zero_export(
                    ha, surplus, running_soc, bat_cap_kwh,
                    max_charge_rate, max_discharge_rate, g13_price,
                )
            elif strategy in (
                AutopilotStrategy.WEATHER_ADAPTIVE,
                AutopilotStrategy.AI_FULL_AUTONOMY,
            ):
                # These strategies defer to AI — use balanced fallback for estimation
                ha = self._apply_max_self_consumption(
                    ha, surplus, running_soc, bat_cap_kwh,
                    max_charge_rate, max_discharge_rate, g13_price, rce_sell,
                )
                ha.reason = "AI decyduje w czasie rzeczywistym"

            running_soc = max(5, min(100, ha.soc_end))
            total_cost += ha.cost
            total_revenue += ha.revenue
            total_savings += ha.savings
            if ha.grid_delta > 0:
                total_import += ha.grid_delta / 1000
            else:
                total_export += abs(ha.grid_delta) / 1000
            # Self-consumption = min(PV, load) when PV > 0
            total_self_consumption += min(pv_w, load_w) / 1000

            plan.append(ha)

        net_savings = total_revenue + total_savings - total_cost
        vs_baseline = net_savings - (-baseline_cost)  # positive = autopilot is better

        return {
            "strategy": strategy.value,
            "label": AUTOPILOT_STRATEGY_LABELS.get(strategy, strategy.value),
            "description": AUTOPILOT_STRATEGY_DESCRIPTIONS.get(strategy, ""),
            "hourly_plan": [a.to_dict() for a in plan],
            "total_savings": round(total_savings, 2),
            "total_cost": round(total_cost, 2),
            "total_revenue": round(total_revenue, 2),
            "net_savings": round(net_savings, 2),
            "total_import_kwh": round(total_import, 1),
            "total_export_kwh": round(total_export, 1),
            "total_self_consumption_kwh": round(total_self_consumption, 1),
            "vs_no_management": round(vs_baseline, 2),
            "timestamp": datetime.now().strftime("%H:%M"),
        }

    def evaluate_all_strategies(
        self, current_data: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Run estimation for all 6 strategies."""
        results = []
        for strategy in AutopilotStrategy:
            try:
                result = self.estimate_strategy(strategy, current_data)
                results.append(result)
            except Exception as err:
                _LOGGER.error("Strategy estimation failed for %s: %s", strategy, err)
                results.append({
                    "strategy": strategy.value,
                    "label": AUTOPILOT_STRATEGY_LABELS.get(strategy, strategy.value),
                    "error": str(err),
                })
        # Sort by net_savings descending
        results.sort(key=lambda r: r.get("net_savings", -9999), reverse=True)
        return results

    # =========================================================================
    # Strategy implementations
    # =========================================================================

    def _apply_max_self_consumption(
        self, ha: HourlyAction, surplus: float, soc: float,
        bat_cap: float, max_charge: float, max_discharge: float,
        g13_price: float, rce_sell: float,
    ) -> HourlyAction:
        """Max self-consumption: use PV for load, store excess, discharge at night."""
        if surplus > 0:
            # Excess PV → charge battery
            charge_kw = min(surplus / 1000, max_charge, (100 - soc) / 100 * bat_cap)
            ha.battery_delta = charge_kw * 1000
            remaining_surplus = surplus - ha.battery_delta
            if remaining_surplus > 0:
                ha.grid_delta = -remaining_surplus  # export
                ha.revenue = (remaining_surplus / 1000) * rce_sell
            ha.action = "charge" if charge_kw > 0.1 else "hold"
            ha.reason = "PV nadwyżka → ładuj baterię"
            ha.savings = (min(surplus, ha.estimated_load) / 1000) * g13_price
        else:
            # Deficit → discharge battery
            deficit = abs(surplus)
            discharge_kw = min(deficit / 1000, max_discharge, (soc - 5) / 100 * bat_cap)
            ha.battery_delta = -discharge_kw * 1000
            remaining_deficit = deficit - discharge_kw * 1000
            if remaining_deficit > 0:
                ha.grid_delta = remaining_deficit  # import
                ha.cost = (remaining_deficit / 1000) * g13_price
            ha.action = "discharge" if discharge_kw > 0.1 else "hold"
            ha.reason = "Deficyt → rozładuj baterię"
            if ha.estimated_pv > 0:
                ha.savings = (ha.estimated_pv / 1000) * g13_price

        ha.soc_end = soc + (ha.battery_delta / 1000) / bat_cap * 100
        return ha

    def _apply_max_profit(
        self, ha: HourlyAction, surplus: float, soc: float,
        bat_cap: float, max_charge: float, max_discharge: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float, rce_sell: float,
        hour: int,
    ) -> HourlyAction:
        """Max profit: buy low, sell high. Arbitrage-focused."""
        is_cheap = g13_zone == G13Zone.OFF_PEAK or rce_mwh < 150
        is_expensive = g13_zone == G13Zone.AFTERNOON_PEAK or rce_mwh > 400

        if is_cheap and soc < 90:
            # Cheap time → charge battery (even from grid)
            charge_kw = min(max_charge, (90 - soc) / 100 * bat_cap)
            total_charge = surplus + charge_kw * 1000 if surplus > 0 else charge_kw * 1000
            ha.battery_delta = min(total_charge, max_charge * 1000)
            grid_needed = max(0, ha.battery_delta + ha.estimated_load - ha.estimated_pv)
            ha.grid_delta = grid_needed
            ha.cost = (grid_needed / 1000) * g13_price
            ha.action = "charge"
            ha.reason = f"Tania energia ({g13_price:.2f} zł) → ładuj!"
        elif is_expensive and soc > 20:
            # Expensive time → discharge + export
            discharge_kw = min(max_discharge, (soc - 20) / 100 * bat_cap)
            ha.battery_delta = -discharge_kw * 1000
            available = ha.estimated_pv + discharge_kw * 1000
            self_use = min(available, ha.estimated_load)
            export = available - self_use
            ha.grid_delta = -export if export > 0 else (ha.estimated_load - available)
            ha.revenue = (export / 1000) * rce_sell if export > 0 else 0
            ha.savings = (self_use / 1000) * g13_price
            if ha.grid_delta > 0:
                ha.cost = (ha.grid_delta / 1000) * g13_price
            ha.action = "sell"
            ha.reason = f"Droga energia ({g13_price:.2f} zł) → sprzedawaj!"
        else:
            # Normal time → self-consumption
            return self._apply_max_self_consumption(
                ha, surplus, soc, bat_cap, max_charge, max_discharge,
                g13_price, rce_sell,
            )

        ha.soc_end = soc + (ha.battery_delta / 1000) / bat_cap * 100
        return ha

    def _apply_battery_protection(
        self, ha: HourlyAction, surplus: float, soc: float,
        bat_cap: float, max_charge: float, max_discharge: float,
        g13_price: float, rce_sell: float, hour: int,
    ) -> HourlyAction:
        """Battery protection: SOC 30-80%, gentle cycling."""
        min_soc = 30
        max_soc = 80
        gentle_rate = max_charge * 0.5  # half rate for longevity

        if surplus > 0:
            if soc < max_soc:
                charge_kw = min(surplus / 1000, gentle_rate, (max_soc - soc) / 100 * bat_cap)
                ha.battery_delta = charge_kw * 1000
                remaining = surplus - ha.battery_delta
                if remaining > 0:
                    ha.grid_delta = -remaining
                    ha.revenue = (remaining / 1000) * rce_sell
                ha.action = "charge"
                ha.reason = "Łagodne ładowanie (ochrona baterii)"
            else:
                ha.grid_delta = -surplus
                ha.revenue = (surplus / 1000) * rce_sell
                ha.action = "hold"
                ha.reason = "SOC max (80%) — eksport nadwyżki"
            ha.savings = (min(surplus, ha.estimated_load) / 1000) * g13_price
        else:
            deficit = abs(surplus)
            if soc > min_soc:
                discharge_kw = min(deficit / 1000, gentle_rate, (soc - min_soc) / 100 * bat_cap)
                ha.battery_delta = -discharge_kw * 1000
                remaining = deficit - discharge_kw * 1000
                if remaining > 0:
                    ha.grid_delta = remaining
                    ha.cost = (remaining / 1000) * g13_price
                ha.action = "discharge"
                ha.reason = "Łagodne rozładowanie (ochrona baterii)"
            else:
                ha.grid_delta = deficit
                ha.cost = (deficit / 1000) * g13_price
                ha.action = "hold"
                ha.reason = "SOC min (30%) — import z sieci"
            if ha.estimated_pv > 0:
                ha.savings = (ha.estimated_pv / 1000) * g13_price

        ha.soc_end = soc + (ha.battery_delta / 1000) / bat_cap * 100
        return ha

    def _apply_zero_export(
        self, ha: HourlyAction, surplus: float, soc: float,
        bat_cap: float, max_charge: float, max_discharge: float,
        g13_price: float,
    ) -> HourlyAction:
        """Zero export: never export to grid, store everything."""
        if surplus > 0:
            # All PV → battery or curtail
            charge_kw = min(surplus / 1000, max_charge, (100 - soc) / 100 * bat_cap)
            ha.battery_delta = charge_kw * 1000
            # Zero export — curtail remainder
            ha.grid_delta = 0
            ha.action = "charge"
            ha.reason = "Zero eksport → ładuj baterię"
            ha.savings = (min(ha.estimated_pv, ha.estimated_load) / 1000) * g13_price
        else:
            deficit = abs(surplus)
            discharge_kw = min(deficit / 1000, max_discharge, (soc - 5) / 100 * bat_cap)
            ha.battery_delta = -discharge_kw * 1000
            remaining = deficit - discharge_kw * 1000
            if remaining > 0:
                ha.grid_delta = remaining
                ha.cost = (remaining / 1000) * g13_price
            ha.action = "discharge" if discharge_kw > 0.1 else "hold"
            ha.reason = "Zero eksport → rozładuj baterię"
            if ha.estimated_pv > 0:
                ha.savings = (ha.estimated_pv / 1000) * g13_price

        ha.soc_end = soc + (ha.battery_delta / 1000) / bat_cap * 100
        return ha


def _render_ecowitt(data: dict[str, Any]) -> str:
    """Render Ecowitt local station data for AI prompt (if available)."""
    ecowitt = data.get("ecowitt")
    if not ecowitt:
        return ""

    lines = ["\nLocal Weather Station (Ecowitt WH90):"]
    field_labels = {
        "solar_radiation": ("Solar Radiation", "W/m²"),
        "solar_lux": ("Solar Lux", "lx"),
        "uv_index": ("UV Index", ""),
        "temperature": ("Temperature", "°C"),
        "humidity": ("Humidity", "%"),
        "wind_speed": ("Wind Speed", "km/h"),
        "wind_gust": ("Wind Gust", "km/h"),
        "wind_direction": ("Wind Direction", "°"),
        "rain_rate": ("Rain Rate", "mm/h"),
        "daily_rain": ("Daily Rain", "mm"),
        "pressure": ("Pressure", "hPa"),
        "feels_like": ("Feels Like", "°C"),
    }
    for key, (label, unit) in field_labels.items():
        val = ecowitt.get(key)
        if val is not None:
            lines.append(f"- {label}: {val} {unit}".rstrip())

    return chr(10).join(lines) + chr(10)


def _render_forecast(data: dict[str, Any]) -> str:
    """Render multi-day weather forecast for AI prompt (if available)."""
    forecast = data.get("weather_forecast")
    if not forecast:
        return ""

    lines = ["Multi-day forecast:"]
    for day in forecast:
        date = day.get("date", "?")[:10]
        cond = day.get("condition", "?")
        hi = day.get("temp_high", "?")
        lo = day.get("temp_low", "?")
        precip = day.get("precipitation", 0)
        prob = day.get("precipitation_probability", "?")
        clouds = day.get("cloud_coverage", "?")
        lines.append(
            f"  {date}: {cond}, {lo}–{hi}°C, "
            f"clouds {clouds}%, precip {precip}mm ({prob}%)"
        )

    return chr(10).join(lines) + chr(10)


def _render_energy_history(data: dict[str, Any]) -> str:
    """Render 3-day energy consumption history table for AI prompt."""
    history = data.get("energy_history_3d")
    if not history:
        return "  Brak danych historycznych (Recorder niedostępny)"

    lines = []
    totals: dict[str, float] = {"load": 0, "pv": 0, "imp": 0, "exp": 0}
    count = 0
    for day in history:
        d = day.get("date", "?")
        load = float(day.get("load_kwh", 0))
        pv = float(day.get("pv_kwh", 0))
        imp = float(day.get("import_kwh", 0))
        exp = float(day.get("export_kwh", 0))
        lines.append(
            f"  {d} | {load:6.1f} kWh | {pv:6.1f} kWh | {imp:6.1f} kWh | {exp:6.1f} kWh"
        )
        totals["load"] += load
        totals["pv"] += pv
        totals["imp"] += imp
        totals["exp"] += exp
        count += 1

    if count > 0:
        lines.append(
            f"  Średnia:    | {totals['load']/count:6.1f} kWh | {totals['pv']/count:6.1f} kWh "
            f"| {totals['imp']/count:6.1f} kWh | {totals['exp']/count:6.1f} kWh"
        )

    header = "  Data       | Zużycie dom | Produkcja PV | Import sieć  | Eksport"
    return header + chr(10) + chr(10).join(lines)


def _render_weather_detailed(data: dict[str, Any]) -> str:
    """Render detailed weather section for AI prompt (local station + forecast)."""
    lines = []

    # Local station data (Ecowitt)
    local_temp = data.get("local_temp") or data.get("weather_temp")
    local_humidity = data.get("local_humidity") or data.get("weather_humidity")
    local_wind = data.get("local_wind_speed") or data.get("weather_wind_speed")
    local_rain = data.get("local_rain_rate")
    local_radiation = data.get("local_solar_radiation")
    local_uv = data.get("local_uv_index") or data.get("weather_uv_index")
    local_pressure = data.get("local_pressure") or data.get("weather_pressure")
    local_clouds = data.get("weather_clouds") or data.get("local_cloud_cover")
    condition = data.get("weather_condition", "N/A")

    lines.append(f"  TERAZ: {condition}, {local_temp or 'N/A'}°C, wilgotność {local_humidity or 'N/A'}%")
    lines.append(f"  Wiatr: {local_wind or 'N/A'} km/h | Ciśnienie: {local_pressure or 'N/A'} hPa")
    lines.append(f"  Chmury: {local_clouds or 'N/A'}% | UV: {local_uv or 'N/A'} | Promieniowanie: {local_radiation or 'N/A'} W/m²")
    if local_rain and float(local_rain or 0) > 0:
        lines.append(f"  Deszcz: {local_rain} mm/h")

    # Multi-day forecast (if available)
    forecast = data.get("weather_forecast")
    if forecast:
        lines.append("")
        lines.append("  PROGNOZA:")
        for day in forecast[:2]:  # today + tomorrow
            date = day.get("date", "?")[:10]
            cond = day.get("condition", "?")
            hi = day.get("temp_high", "?")
            lo = day.get("temp_low", "?")
            clouds_f = day.get("cloud_coverage", "?")
            prob = day.get("precipitation_probability", "?")
            precip = day.get("precipitation", 0)
            lines.append(
                f"  {date}: {cond}, {lo}–{hi}°C, chmury {clouds_f}%, opad {precip}mm ({prob}%)"
            )

    return chr(10).join(lines)


def _render_night_charging_logic(data: dict[str, Any], bat_cap_kwh: float) -> str:
    """Calculate and render night charging optimization (PV space vs battery).

    Key insight: if tomorrow is sunny and PV will exceed battery capacity,
    DON'T charge to 100% — leave room for free PV energy.
    """
    forecast_tomorrow = float(data.get("forecast_tomorrow", 0))
    soc = float(data.get("battery_soc", 0))
    usable_kwh = bat_cap_kwh * 0.9  # 90% usable (DOD 95% - min 5%)

    # Estimate average daily consumption from history
    history = data.get("energy_history_3d", [])
    if history:
        avg_load = sum(float(d.get("load_kwh", 0)) for d in history) / len(history)
    else:
        avg_load = 15.0  # default estimate

    # Solar hours: assume ~10h of production (07:00-17:00)
    # Load during solar hours is roughly 60% of daily
    load_during_solar = avg_load * 0.6
    pv_surplus_tomorrow = max(0.0, forecast_tomorrow - load_during_solar)

    # How much battery space to leave for PV surplus
    if forecast_tomorrow >= 8.0 and pv_surplus_tomorrow > 2.0:
        # Good sun — leave space for PV
        space_pct = min(50.0, (pv_surplus_tomorrow / usable_kwh) * 100)
        optimal_soc = max(50, int(100 - space_pct))
        recommendation = f"DOBRA POGODA → ładuj do {optimal_soc}% (zostaw ~{space_pct:.0f}% na darmowe PV)"
    elif forecast_tomorrow >= 5.0:
        optimal_soc = 80
        recommendation = f"UMIARKOWANA POGODA → ładuj do {optimal_soc}% (kompromis: bezpieczeństwo + miejsce na PV)"
    else:
        optimal_soc = 100
        recommendation = "ZŁA POGODA → ładuj do 100% (brak PV do ładowania)"

    lines = [
        f"  Pojemność baterii: {bat_cap_kwh:.1f} kWh (usable: {usable_kwh:.1f} kWh)",
        f"  Aktualny SOC: {soc:.0f}%",
        f"  Prognoza PV jutro: {forecast_tomorrow:.1f} kWh",
        f"  Średnie zużycie dzienne: {avg_load:.1f} kWh",
        f"  Zużycie w godzinach słonecznych: ~{load_during_solar:.1f} kWh",
        f"  Szacowana nadwyżka PV jutro: ~{pv_surplus_tomorrow:.1f} kWh",
        "",
        "  ⚠️ UWAGA: Nadwyżka PV powyżej pojemności baterii = DARMOWY eksport do sieci!",
        "  Darmowe PV > nocny prąd za 0.63 PLN/kWh — zostaw miejsce w baterii!",
        "",
        f"  🎯 REKOMENDACJA: {recommendation}",
        f"  Optymalny SOC na rano: {optimal_soc}%",
    ]
    return chr(10).join(lines)


def build_autopilot_ai_prompt(
    strategy: AutopilotStrategy,
    current_data: dict[str, Any],
    estimation: dict[str, Any],
) -> str:
    """Build a specialized prompt for AI autopilot analysis."""
    now = datetime.now()
    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
    month = now.month
    weekday = now.weekday()

    # Build current G13 zone info
    current_zone = _get_g13_zone(now.hour, month, weekday)
    current_price = _get_g13_price(current_zone)

    # Build G13 schedule for today
    schedule = G13_WINTER_SCHEDULE if month in WINTER_MONTHS else G13_SUMMER_SCHEDULE
    g13_lines = []
    for (start, end), zone in schedule.items():
        price = G13_PRICES.get(zone, 0.63)
        g13_lines.append(f"  {start:02d}:00-{end:02d}:00 → {zone.value} ({price:.2f} PLN/kWh)")

    # Build hourly plan summary
    plan_lines = []
    for h in estimation.get("hourly_plan", []):
        zone = _get_g13_zone(h['hour'], month, weekday)
        plan_lines.append(
            f"  {h['hour']:02d}:00 — {h['action'].upper():10s} | "
            f"PV:{h['pv']:5.0f}W | Load:{h['load']:5.0f}W | "
            f"Bat:{h['battery']:+6.0f}W | Grid:{h['grid']:+6.0f}W | "
            f"SOC:{h['soc_start']:.0f}→{h['soc_end']:.0f}% | "
            f"G13:{zone.value}"
        )

    # RCE sell price calculation
    rce_mwh = float(current_data.get('rce_price') or 250)
    rce_sell = float(current_data.get('rce_sell') or (rce_mwh / 1000) * RCE_PROSUMER_COEFFICIENT)

    # Battery capacity
    bat_cap = current_data.get('battery_capacity') or DEFAULT_BATTERY_CAPACITY
    bat_cap_kwh = float(bat_cap or DEFAULT_BATTERY_CAPACITY) / 1000

    prompt = f"""You are an expert energy management AI for a home solar+battery system in Poland with G13 multi-zone tariff.

TASK: Analyze the 24-hour energy plan for strategy "{AUTOPILOT_STRATEGY_LABELS.get(strategy, strategy.value)}" and provide a CONCISE analysis.

CRITICAL: Keep your response under 3000 words. Be direct and actionable, avoid lengthy explanations.

Provide EXACTLY 3 sections:
1. ## 📊 Analiza strategii — what's wrong with the current plan (max 5 bullet points)
2. ## 🎯 Zoptymalizowany plan — key corrections per time block (morning/afternoon/evening/night), NOT hourly JSON
3. ## 💰 Oszczędności — estimated savings table (current vs optimized) and top 3 HEMS automation recommendations

═══ SYSTEM CONFIGURATION ═══
- Battery Capacity: {bat_cap_kwh:.1f} kWh
- Max Charge Rate: {bat_cap_kwh * 0.5:.1f} kW (0.5C)
- Max Discharge Rate: {bat_cap_kwh * 0.5:.1f} kW (0.5C)
- Inverter: GoodWe (hybrid, 3-phase)
- Managed Loads: Boiler 3.8kW, AC, Smart Socket

═══ CURRENT STATE ({now.strftime('%Y-%m-%d')} {day_names[weekday]} {now.strftime('%H:%M')}) ═══
- PV Power: {current_data.get('pv_power', 0)} W
- Load: {current_data.get('load', 0)} W
- Battery SOC: {current_data.get('battery_soc', 0)}%
- Grid Power: {current_data.get('grid_power', 0)} W (positive=import)
- PV Surplus: {current_data.get('pv_surplus', 0)} W
- Grid Voltage: L1={current_data.get('voltage_l1', 'N/A')}V

═══ TARIFF G13 — TODAY'S SCHEDULE ═══
{'weekend (all off-peak 0.63 PLN/kWh)' if weekday >= 5 else chr(10).join(g13_lines)}
Current zone: {current_zone.value} ({current_price:.2f} PLN/kWh)

═══ PRICING ═══
- G13 Buy Prices: Off-Peak=0.63, Morning Peak=0.91, Afternoon Peak=1.50 PLN/kWh
- RCE Price: {rce_mwh:.1f} PLN/MWh ({rce_mwh/1000:.4f} PLN/kWh)
- RCE Sell Price: {rce_sell:.4f} PLN/kWh (prosumer coefficient applied)
- IMPORTANT: At current RCE, selling to grid earns {rce_sell:.4f} PLN/kWh vs buying at {current_price:.2f} PLN/kWh
- Arbitrage potential: Buy at 0.63, sell self-consumption at 1.50 = {1.50 - 0.63:.2f} PLN/kWh margin

═══ RCE PRICE FORECAST (next hours) ═══
- Next hour (+1h): {current_data.get('rce_next_hour', 'N/A')} PLN/MWh (sell: {current_data.get('rce_sell_next_hour', 'N/A')} PLN/kWh)
- +2 hours: {current_data.get('rce_2h', 'N/A')} PLN/MWh (sell: {current_data.get('rce_sell_2h', 'N/A')} PLN/kWh)
- +3 hours: {current_data.get('rce_3h', 'N/A')} PLN/MWh (sell: {current_data.get('rce_sell_3h', 'N/A')} PLN/kWh)
- Today's RCE avg: {current_data.get('rce_avg_today', 'N/A')} PLN/kWh
- Today's RCE min: {current_data.get('rce_min_today', 'N/A')} PLN/kWh
- Today's RCE max: {current_data.get('rce_max_today', 'N/A')} PLN/kWh
- RCE Trend: {current_data.get('rce_trend', 'N/A')}
- CRITICAL: If RCE is rising and afternoon peak (15:00-22:00) is approaching, grid-charge battery NOW at off-peak (0.63) to sell at high RCE + avoid buying at peak (1.50)!

═══ WEATHER & FORECAST ═══
Current conditions:
- Weather: {current_data.get('weather_condition', 'N/A')}, {current_data.get('weather_temp', 'N/A')}°C
- Clouds: {current_data.get('weather_clouds', 'N/A')}%, Humidity: {current_data.get('weather_humidity', 'N/A')}%
- Wind: {current_data.get('weather_wind_speed', 'N/A')} km/h, Pressure: {current_data.get('weather_pressure', 'N/A')} hPa
PV Forecast:
- Today total: {current_data.get('forecast_today', 0)} kWh
- Remaining today: {current_data.get('forecast_remaining', 0)} kWh
- Tomorrow: {current_data.get('forecast_tomorrow', 0)} kWh
{_render_ecowitt(current_data)}{_render_forecast(current_data)}

═══ CURRENT ESTIMATION (MATHEMATICAL MODEL) ═══
{chr(10).join(plan_lines)}

TOTALS:
- Import: {estimation.get('total_import_kwh', 0)} kWh (cost: {estimation.get('total_cost', 0):.2f} PLN)
- Export: {estimation.get('total_export_kwh', 0)} kWh (revenue: {estimation.get('total_revenue', 0):.2f} PLN)
- Self-consumption: {estimation.get('total_self_consumption_kwh', 0)} kWh
- Net savings: {estimation.get('net_savings', 0):.2f} PLN
- vs No Management: {estimation.get('vs_no_management', 0):.2f} PLN

═══ ACTIVE HEMS AUTOMATION LAYERS ═══
- W0: Grid Import Guard — STOP battery grid-charging in expensive G13 zones.
  Exception: RCE < 100 PLN/MWh (arbitrage). PV charging always allowed.
- W1: G13 Schedule — 07:00 sell mode, 13:00 charge mode (off-peak), night arbitrage 23:00.
- W2: RCE Dynamic — cheapest/most expensive windows, thresholds 150/300/500 PLN/MWh.
- W3: SOC Safety — tariff-aware: expensive hours PV-only, cheap hours normal. Emergency SOC <5%.
- W4: Voltage Cascade (252/253/254V) + PV Surplus Cascade (2/3/4kW → boiler/AC/socket).
- W5: Smart Pre-Peak — weather forecast-driven pre-charging before peaks.

KEY CONSTRAINTS:
1. When RCE sell price is very low ({rce_sell:.4f} PLN/kWh), exporting is worthless. Focus on self-consumption.
2. G13 arbitrage (grid-charge at 0.63, discharge at 1.50) yields {1.50 - 0.63:.2f} PLN/kWh — THIS IS THE PRIMARY PROFIT LEVER.
3. Battery has {bat_cap_kwh:.1f} kWh capacity × {1.50 - 0.63:.2f} PLN/kWh = ~{bat_cap_kwh * (1.50 - 0.63):.2f} PLN per full arbitrage cycle.
4. Night charge (23:00-07:00) at 0.63 → discharge 07:00-13:00/15:00-22:00 at 0.91/1.50 is the core strategy.

RESPOND IN POLISH. Format as structured markdown with:
## 📊 Analiza strategii
## 🕐 Zoptymalizowany plan godzinowy (JSON)
## 💰 Estymacja oszczędności
## ⚠️ Ryzyka i zalecenia
## 🎯 Rekomendacja

Use tables for comparisons. Be concise but comprehensive. Focus on actionable insights."""

    return prompt


# ═══════════════════════════════════════════════════════════════
# AI CONTROLLER PROMPT — structured JSON toolcalling
# ═══════════════════════════════════════════════════════════════

# Available tools the AI can call — these map to EnergyManager methods
AI_CONTROLLER_TOOLS = {
    "force_charge": "Force battery charge from grid via Eco Mode (eco_charge, soc=100%, power=100%, current=18.5A)",
    "force_discharge": "⚠️ Force battery discharge TO GRID (SPRZEDAŻ!) via eco_discharge. Użyj TYLKO gdy chcesz SPRZEDAWAĆ energię do sieci!",
    "set_general": "Przełącz na tryb General — bateria zasila dom (AUTOKONSUMPCJA). Użyj gdy chcesz rozładować baterię przez zasilanie domu, BEZ sprzedaży do sieci.",
    "stop_force_charge": "STOP forced charging — restore general mode",
    "stop_force_discharge": "STOP forced discharge — restore general mode",
    "emergency_stop": "EMERGENCY STOP all force operations — reset everything to general mode immediately",
    "set_dod": "Set max depth of discharge (params: dod: int 0-95). Higher = more capacity available. Max is 95.",
    "set_export_limit": "Set grid export limit in watts (params: limit: int). 0 = no export",
    "switch_on": "Turn on managed load (params: entity: 'boiler'|'ac'|'socket2')",
    "switch_off": "Turn off managed load (params: entity: 'boiler'|'ac'|'socket2')",
    "no_action": "Do nothing this tick (params: reason: string). Use when current state is optimal",
}

# Per-strategy behavioral instructions for AI prompt injection
STRATEGY_AI_INSTRUCTIONS: dict[str, str] = {
    "max_self_consumption": (
        "STRATEGIA: 🟢 Max Autokonsumpcja\n"
        "  Priorytet: zużyj CAŁE PV w domu + baterii. MIN import z sieci, MIN eksport.\n"
        "  NIE sprzedawaj do sieci (chyba że SOC=100% i nadwyżka PV).\n"
        "  NIE ładuj z sieci (chyba że emergency SOC < 5%).\n"
        "  SOC limits: min=10%, max=100%."
    ),
    "max_profit": (
        "STRATEGIA: 💰 Max Zysk (Arbitraż Cenowy)\n"
        "  Priorytet: kupuj tanio, sprzedawaj drogo. Arbitraż G13 + RCE.\n"
        "  Off-peak (0.63): force_charge z sieci → naładuj baterię do 95-100%.\n"
        "  Afternoon_peak (1.50): set_general → bateria zasila dom (autokonsumpcja).\n"
        "  Jeśli RCE sell > 0.63: force_discharge → sprzedaj nadwyżkę do sieci.\n"
        "  SPRZEDAWAJ TYLKO gdy RCE sell > 0.63 PLN/kWh (koszt zakupu). Inaczej zachowaj.\n"
        "  Zostaw min 20% SOC na przetrwanie szczytu po rozładowaniu.\n"
        "  SOC limits: min=15%, max=100%."
    ),
    "battery_protection": (
        "STRATEGIA: 🔋 Ochrona Baterii\n"
        "  Priorytet: minimalne obciążenie baterii, łagodne cykle.\n"
        "  SOC ZAWSZE w zakresie 30-80%. NIGDY poniżej 30%, NIGDY powyżej 80%.\n"
        "  set_dod(70). Unikaj force_charge/force_discharge.\n"
        "  Preferuj PV → dom. Bateria tylko jako bufor.\n"
        "  SOC limits: min=30%, max=80%."
    ),
    "zero_export": (
        "STRATEGIA: ⚡ Zero Export\n"
        "  Priorytet: ZERO eksportu do sieci. Cała energia w domu + bateria.\n"
        "  set_export_limit(0) — nie pozwól na żaden eksport.\n"
        "  PV → dom → bateria → obciążenia zarządzane (bojler, klima).\n"
        "  NIE używaj force_discharge. NIE sprzedawaj.\n"
        "  SOC limits: min=10%, max=100%."
    ),
    "weather_adaptive": (
        "STRATEGIA: 🌧️ Pogodowy Adaptacyjny\n"
        "  Priorytet: dynamiczna adaptacja do pogody godzina po godzinie.\n"
        "  Zła pogoda (PV forecast < 5kWh) + tania strefa → force_charge z sieci.\n"
        "  Dobra pogoda + dużo PV → PV pokryje zapotrzebowanie, oszczędzaj siatkę.\n"
        "  Przed szczytem: jeśli SOC < 80% i słaba prognoza → force_charge.\n"
        "  Po szczycie: stop_force, wróć do general.\n"
        "  SOC limits: min=15%, max=95%."
    ),
    "ai_full_autonomy": (
        "STRATEGIA: 🧠 AI Pełna Autonomia\n"
        "  Masz pełną swobodę decyzji. Mieszaj techniki per godzina.\n"
        "  Możesz: arbitraż, autokonsumpcja, sprzedaż, ładowanie z sieci.\n"
        "  PAMIĘTAJ o stop_force po zakończeniu wymuszania!\n"
        "  NIGDY nie zostawiaj falownika w trybie wymuszonym na dłużej niż 6h.\n"
        "  SOC limits: min=10%, max=100%."
    ),
}


def build_action_catalog_text(
    action_states: dict[str, Any] | None = None,
) -> str:
    """Build a concise action catalog for AI prompts.

    One-liner per action: status + id + description.
    Keeps total size small to avoid prompt truncation.
    """
    from .autopilot_actions import (
        build_all_actions,
        CATEGORY_LABELS,
        CATEGORY_ORDER,
    )

    actions = build_all_actions()
    states = action_states or {}

    lines: list[str] = []
    lines.append("═ ACTIONS (use {\"action\":\"id\"} to trigger) ═")

    for cat in CATEGORY_ORDER:
        cat_actions = [a for a in actions if a.category == cat]
        if not cat_actions:
            continue

        label = CATEGORY_LABELS.get(cat, str(cat))
        lines.append(f"[{label}]")

        for a in cat_actions:
            st = states.get(a.id, "idle")
            icon = {"active": "●", "waiting": "◐", "idle": "○", "disabled": "✗"}.get(st, "○")
            alw = "!" if a.always_active else ""
            lines.append(f" {icon}{alw} {a.id}")

    # Active summary
    if states:
        active = [aid for aid, s in states.items() if s in ("active", "waiting")]
        if active:
            lines.append(f"ACTIVE: {','.join(active)}")

    return "\n".join(lines)


def _format_decision_history(
    decision_history: list[dict[str, Any]] | None = None,
) -> str:
    """Format recent decision log entries for AI prompt context.

    Returns a compact section showing the last actions taken so the AI
    can avoid duplicate commands and learn from its own decisions.
    """
    if not decision_history:
        return ""

    # Take last 10 entries
    recent = decision_history[-10:]
    lines = ["═══ RECENT ACTIONS (your past decisions) ═══"]
    for entry in recent:
        t = entry.get("time", "??:??")
        action = entry.get("action", "?")
        msg = entry.get("message", "")
        # Truncate long messages
        if len(msg) > 80:
            msg = msg[:77] + "..."
        lines.append(f"  {t} [{action}] {msg}")

    lines.append("DO NOT repeat actions that are already in progress. Check before acting.")
    return "\n".join(lines)


def _format_financial_context(current_data: dict[str, Any]) -> str:
    """Format daily financial balance and RCE yesterday range for AI prompt.

    Shows the AI its realized P&L: how much energy was imported/exported,
    what it cost, and yesterday's RCE range for forward planning.
    """
    balance = current_data.get("daily_balance")
    rce_yday = current_data.get("rce_yesterday")

    parts: list[str] = []

    if balance and any(v > 0 for v in balance.values()):
        imp_kwh = balance.get("import_kwh", 0)
        imp_cost = balance.get("import_cost", 0)
        exp_kwh = balance.get("export_kwh", 0)
        exp_rev = balance.get("export_revenue", 0)
        net = exp_rev - imp_cost
        parts.append("═══ TODAY'S FINANCIAL BALANCE ═══")
        parts.append(f"  Import: {imp_kwh:.1f} kWh = -{imp_cost:.2f} PLN")
        parts.append(f"  Export: {exp_kwh:.1f} kWh = +{exp_rev:.2f} PLN")
        parts.append(f"  NET P&L: {'+' if net >= 0 else ''}{net:.2f} PLN")
        parts.append(f"  Daily energy: PV={current_data.get('pv_today_kwh', 0):.1f}kWh, "
                     f"Import={current_data.get('grid_import_today_kwh', 0):.1f}kWh, "
                     f"Export={current_data.get('grid_export_today_kwh', 0):.1f}kWh")
        parts.append("Minimize import cost, maximize export revenue. Negative NET = losing money.")

    if rce_yday and rce_yday.get("avg", 0) > 0:
        parts.append("═══ RCE YESTERDAY (reference for today's planning) ═══")
        parts.append(f"  Min: {rce_yday['min']:.2f} PLN/MWh → {rce_yday['min']/1000:.4f} PLN/kWh")
        parts.append(f"  Avg: {rce_yday['avg']:.2f} PLN/MWh → {rce_yday['avg']/1000:.4f} PLN/kWh")
        parts.append(f"  Max: {rce_yday['max']:.2f} PLN/MWh → {rce_yday['max']/1000:.4f} PLN/kWh")
        parts.append("Use yesterday's range as reference: buy when RCE < yesterday avg, sell when RCE > yesterday avg.")

    return "\n".join(parts)

def build_ai_controller_prompt(
    current_data: dict[str, Any],
    device_status_text: str = "",
    action_states: dict[str, Any] | None = None,
    active_strategy: str = "ai_full_autonomy",
    decision_history: list[dict[str, Any]] | None = None,
) -> str:
    """Build a concise prompt for AI to return structured JSON commands.

    Unlike the advisory prompt (build_autopilot_ai_prompt), this one:
    - Does NOT request analysis text or markdown
    - Requires ONLY valid JSON response
    - Lists available tools the AI can call
    - Includes ACTION CATALOG with all 35 autopilot actions
    - Focuses on immediate action (this tick), not 24h plans
    - Includes DEVICE STATUS from InverterAgent (state awareness)
    """
    now = datetime.now()
    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
    month = now.month
    weekday = now.weekday()
    hour = now.hour

    # G13 zone
    current_zone = _get_g13_zone(hour, month, weekday)
    current_price = _get_g13_price(current_zone)

    # Next zone info — what's coming in the next hours
    next_zones = []
    for h_offset in range(1, 4):
        fh = (hour + h_offset) % 24
        fz = _get_g13_zone(fh, month, weekday)
        fp = _get_g13_price(fz)
        next_zones.append(f"+{h_offset}h ({fh:02d}:00): {fz.value} ({fp:.2f} PLN/kWh)")

    # RCE data
    rce_mwh = float(current_data.get('rce_price') or 250)
    rce_sell = float(current_data.get('rce_sell') or (rce_mwh / 1000) * RCE_PROSUMER_COEFFICIENT)

    # Battery
    bat_cap = current_data.get('battery_capacity') or DEFAULT_BATTERY_CAPACITY
    bat_cap_kwh = float(bat_cap) / 1000

    # Transform raw sensor values to explicit human-readable format
    # GoodWe convention: battery_power negative=charging, positive=discharging
    # grid_power: negative=export, positive=import
    raw_bat = float(current_data.get('battery_power', 0))
    raw_grid = float(current_data.get('grid_power', 0))
    bat_state = f"ŁADOWANIE {abs(raw_bat):.0f}W (do baterii z sieci/PV)" if raw_bat < -50 else f"ROZŁADOWYWANIE {abs(raw_bat):.0f}W (z baterii do domu)" if raw_bat > 50 else "BEZCZYNNA (idle)"
    grid_state = f"IMPORT {abs(raw_grid):.0f}W (pobór z sieci)" if raw_grid > 50 else f"EKSPORT {abs(raw_grid):.0f}W (sprzedaż do sieci)" if raw_grid < -50 else "ZERO (brak przepływu)"

    # Tools description
    tools_desc = "\n".join(
        f'  - "{name}": {desc}' for name, desc in AI_CONTROLLER_TOOLS.items()
    )

    # Action catalog
    action_catalog = build_action_catalog_text(action_states)

    # Device status section (from InverterAgent)
    device_section = ""
    if device_status_text:
        device_section = f"\n{device_status_text}\n"

    prompt = f"""Jesteś autonomicznym kontrolerem energii dla domowego systemu solar+bateria w Polsce (taryfa G13).
Odpowiadaj ZAWSZE po polsku. Wszystkie opisy, reasoning, analysis — po polsku.

TWOJE ZADANIE: Zdecyduj jaką akcję podjąć TERAZ na podstawie bieżącego stanu systemu.
Masz BIBLIOTEKĘ 35 nazwanych akcji (patrz KATALOG AKCJI poniżej). PREFERUJ aktywowanie akcji po ID.
Odpowiedz WYŁĄCZNIE poprawnym JSON.

═══ AVAILABLE TOOLS (low-level) ═══
{tools_desc}

{action_catalog}
═══ MANAGED ENTITIES ═══
  - "boiler": Water heater 3.8kW (switch.bojler_3800)
  - "ac": Air conditioning (switch.klimatyzacja_socket_1)
  - "socket2": Smart socket (switch.drugie_gniazdko)

═══ CURRENT STATE ({now.strftime('%Y-%m-%d')} {day_names[weekday]} {now.strftime('%H:%M')}) ═══
  PV Power: {current_data.get('pv_power', 0)} W
  Load (zużycie domu): {current_data.get('load', 0)} W
  Battery SOC: {current_data.get('battery_soc', 0)}%
  Battery: {bat_state}
  Grid (sieć): {grid_state}
  PV Surplus: {current_data.get('pv_surplus', 0)} W
  Battery Capacity: {bat_cap_kwh:.1f} kWh
{device_section}
═══ TARIFF & PRICING ═══
  Current zone: {current_zone.value} ({current_price:.2f} PLN/kWh)
  Upcoming zones:
    {chr(10) + "    ".join(next_zones)}
  G13 prices: off_peak=0.63, morning_peak=0.91, afternoon_peak=1.50 PLN/kWh
  RCE: {rce_mwh:.1f} PLN/MWh (sell: {rce_sell:.4f} PLN/kWh)
  RCE next hour: {current_data.get('rce_next_hour', 'N/A')} PLN/MWh
  RCE +2h: {current_data.get('rce_2h', 'N/A')} PLN/MWh
  RCE +3h: {current_data.get('rce_3h', 'N/A')} PLN/MWh

═══ WEATHER & FORECAST ═══
{_render_weather_detailed(current_data)}
  PV forecast remaining today: {current_data.get('forecast_remaining', 0)} kWh
  PV forecast tomorrow: {current_data.get('forecast_tomorrow', 0)} kWh

═══ HISTORIA ZUŻYCIA (3 DNI) ═══
{_render_energy_history(current_data)}

═══ NOCNE ŁADOWANIE — OPTYMALIZACJA ═══
{_render_night_charging_logic(current_data, bat_cap_kwh)}

═══ ACTIVE STRATEGY ═══
{STRATEGY_AI_INSTRUCTIONS.get(active_strategy, STRATEGY_AI_INSTRUCTIONS['ai_full_autonomy'])}

{_format_decision_history(decision_history)}
{_format_financial_context(current_data)}
═══ KEY RULES ═══
1. ARBITRAGE: off_peak(0.63)→afternoon_peak(1.50)=0.87 margin. Charge cheap, discharge expensive.
2. If approaching afternoon_peak AND SOC < 90% → force_charge NOW
3. During afternoon_peak → set_general (bateria zasila dom) LUB force_discharge (sprzedaż do sieci gdy RCE > 0.63)
4. Night 22-06: ładuj baterię ale SPRAWDŹ sekcję NOCNE ŁADOWANIE — nie ładuj do 100% jeśli jutro dobra pogoda!
5. PV surplus > 2kW + SOC > 80% → switch_on boiler
6. AUTO-STOP CHARGE: when SOC >= 95% → call stop_force_charge
7. AUTO-STOP DISCHARGE: when SOC <= 20% → call stop_force_discharge
8. EMERGENCY: SOC < 5% → call emergency_stop, then force_charge
9. ZONE TRADING:
   - Tania strefa (off_peak 0.63) + zła pogoda (PV forecast < 5kWh) → force_charge z sieci
   - Droga strefa (afternoon_peak 1.50) + SOC > 30% → set_general (zasil dom z baterii)
   - Droga strefa + RCE sell > 0.63 + SOC > 50% → force_discharge (sprzedaj nadwyżkę)
   - Zostaw min 20% SOC na przetrwanie szczytu
10. SELL PROFITABILITY: sprzedawaj TYLKO gdy RCE sell ({rce_sell:.4f}) > G13 buy (0.63 PLN/kWh)
   - Jeśli RCE sell < 0.63 → nie opłaca się, zostaw w baterii
11. ALWAYS call stop_force after force operations end. NEVER leave inverter in forced state.
12. CHECK DEVICE STATUS — if already active, use "no_action"!

═══ FORMAT ODPOWIEDZI ═══
TYLKO poprawny JSON. Bez tekstu, bez markdown, bez wyjaśnień poza JSON.
ZABRONIONE klucze: "analysis", "explanation", "plan". Używaj TYLKO: "reasoning", "commands", "next_check_minutes".
"reasoning" = 1-2 ZDANIA po polsku. Opisz DLACZEGO podejmujesz tę decyzję i co chcesz osiągnąć.
{{
  "reasoning": "Rozpoczynam rozładowanie baterii — drogi szczyt popołudniowy (1.50 PLN/kWh), SOC 54% wystarczy na zasil domu. Unikam importu z sieci.",
  "commands": [{{"action": "evening_peak"}}],
  "next_check_minutes": 5
}}
Używaj "action" z ID katalogu. Maks 2 komendy. Jeśli nic nie trzeba: {{"tool":"no_action","params":{{"reason":"ok"}}}}"""

    return prompt


# ═══════════════════════════════════════════════════════════════
# AI STRATEGIST PROMPT — 24h strategic plan with time_blocks
# ═══════════════════════════════════════════════════════════════


def build_ai_strategist_prompt(
    current_data: dict[str, Any],
    estimation: dict[str, Any],
    device_status_text: str = "",
    action_states: dict[str, Any] | None = None,
    active_strategy: str = "ai_full_autonomy",
    decision_history: list[dict[str, Any]] | None = None,
) -> str:
    """Build prompt for AI Strategist — deep 24h strategic plan.

    Unlike build_ai_controller_prompt (quick JSON for immediate action),
    this prompt requests a FULL 24H PLAN with time_blocks, each containing
    specific commands per tariff zone.

    The Strategist runs on cron (every 15-60 min) and produces a plan
    that the Executor follows tick by tick without needing AI API calls.
    """
    now = datetime.now()
    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']
    month = now.month
    weekday = now.weekday()
    hour = now.hour

    # G13 zone
    current_zone = _get_g13_zone(hour, month, weekday)
    _get_g13_price(current_zone)  # side-effect: validates zone

    # Build G13 schedule for today
    schedule = G13_WINTER_SCHEDULE if month in WINTER_MONTHS else G13_SUMMER_SCHEDULE
    g13_lines = []
    for (start, end), zone in schedule.items():
        price = G13_PRICES.get(zone, 0.63)
        marker = " ← TERAZ" if _get_g13_zone(hour, month, weekday) == zone else ""
        g13_lines.append(f"  {start:02d}:00-{end:02d}:00 → {zone.value} ({price:.2f} PLN/kWh){marker}")

    # RCE data
    rce_mwh = float(current_data.get('rce_price') or 250)
    rce_sell = float(current_data.get('rce_sell') or (rce_mwh / 1000) * RCE_PROSUMER_COEFFICIENT)

    # Battery
    bat_cap = current_data.get('battery_capacity') or DEFAULT_BATTERY_CAPACITY
    bat_cap_kwh = float(bat_cap) / 1000

    # Transform raw sensor values to explicit human-readable format
    # GoodWe convention: battery_power negative=charging, positive=discharging
    # grid_power: negative=export, positive=import
    raw_bat = float(current_data.get('battery_power', 0))
    raw_grid = float(current_data.get('grid_power', 0))
    bat_state = f"ŁADOWANIE {abs(raw_bat):.0f}W (do baterii z sieci/PV)" if raw_bat < -50 else f"ROZŁADOWYWANIE {abs(raw_bat):.0f}W (z baterii do domu)" if raw_bat > 50 else "BEZCZYNNA (idle)"
    grid_state = f"IMPORT {abs(raw_grid):.0f}W (pobór z sieci)" if raw_grid > 50 else f"EKSPORT {abs(raw_grid):.0f}W (sprzedaż do sieci)" if raw_grid < -50 else "ZERO (brak przepływu)"

    # Hourly plan from mathematical model
    plan_lines = []
    for h in estimation.get("hourly_plan", []):
        zone = _get_g13_zone(h['hour'], month, weekday)
        plan_lines.append(
            f"  {h['hour']:02d}:00 | PV:{h['pv']:5.0f}W | Load:{h['load']:5.0f}W | "
            f"Bat:{h['battery']:+6.0f}W | Grid:{h['grid']:+6.0f}W | "
            f"SOC:{h['soc_start']:.0f}→{h['soc_end']:.0f}% | {zone.value}"
        )

    # Action catalog
    action_catalog = build_action_catalog_text(action_states)

    # Device status section
    device_section = ""
    if device_status_text:
        device_section = f"\n{device_status_text}\n"

    # Tools description
    tools_desc = "\n".join(
        f'  - "{name}": {desc}' for name, desc in AI_CONTROLLER_TOOLS.items()
    )

    prompt = f"""Jesteś ekspertem od strategii energetycznej AI dla domowego systemu solar+bateria w Polsce (taryfa G13).
Odpowiadaj ZAWSZE po polsku. Wszystkie opisy, reasoning, analysis — po polsku.

TWOJE ZADANIE: Stwórz STRATEGICZNY PLAN 24H określający jakie AKCJE aktywować w każdym bloku czasowym.
Masz BIBLIOTEKĘ 35 nazwanych akcji (patrz KATALOG AKCJI poniżej). PREFERUJ odwoływanie się do akcji po ID.
This plan will be executed automatically by the InverterAgent. Respond ONLY with valid JSON.

═══ SYSTEM ═══
  Battery: {bat_cap_kwh:.1f} kWh, Max charge/discharge: {bat_cap_kwh * 0.5:.1f} kW
  Inverter: hybrid 3-phase
  Managed Loads: boiler (3.8kW), ac, socket2
{device_section}
═══ AVAILABLE TOOLS (low-level) ═══
{tools_desc}

{action_catalog}

═══ CURRENT STATE ({now.strftime('%Y-%m-%d')} {day_names[weekday]} {now.strftime('%H:%M')}) ═══
  PV: {current_data.get('pv_power', 0)} W | Load (zużycie domu): {current_data.get('load', 0)} W
  Battery SOC: {current_data.get('battery_soc', 0)}% | {bat_state}
  Siec: {grid_state} | PV Surplus: {current_data.get('pv_surplus', 0)} W

═══ TARIFF G13 — SCHEDULE ═══
{'Weekend (all off-peak 0.63 PLN/kWh)' if weekday >= 5 else chr(10).join(g13_lines)}
  Prices: off_peak=0.63, morning_peak=0.91, afternoon_peak=1.50 PLN/kWh
  Arbitrage potential: charge@0.63 → discharge@1.50 = 0.87 PLN/kWh × {bat_cap_kwh:.1f} kWh = {bat_cap_kwh * 0.87:.2f} PLN/cycle

═══ RCE PRICES ═══
  Current: {rce_mwh:.1f} PLN/MWh (sell: {rce_sell:.4f} PLN/kWh)
  +1h: {current_data.get('rce_next_hour', 'N/A')} | +2h: {current_data.get('rce_2h', 'N/A')} | +3h: {current_data.get('rce_3h', 'N/A')} PLN/MWh
  Today avg: {current_data.get('rce_avg_today', 'N/A')} | min: {current_data.get('rce_min_today', 'N/A')} | max: {current_data.get('rce_max_today', 'N/A')} PLN/MWh

═══ WEATHER ═══
  {current_data.get('weather_condition', 'N/A')}, {current_data.get('weather_temp', 'N/A')}°C, clouds {current_data.get('weather_clouds', 'N/A')}%
  PV remaining today: {current_data.get('forecast_remaining', 0)} kWh | Tomorrow: {current_data.get('forecast_tomorrow', 0)} kWh

═══ HISTORIA ZUŻYCIA (3 DNI) ═══
{_render_energy_history(current_data)}

═══ POGODA SZCZEGÓŁOWO ═══
{_render_weather_detailed(current_data)}

═══ PROGNOZA PRODUKCJI PV ═══
  Forecast.Solar — dziś (pozostało): {current_data.get('forecast_remaining', 0)} kWh
  Forecast.Solar — jutro: {current_data.get('forecast_tomorrow', 0)} kWh
  Forecast.Solar — dziś (total): {current_data.get('forecast_today', 0)} kWh

═══ NOCNE ŁADOWANIE — OPTYMALIZACJA MIEJSCA NA PV ═══
{_render_night_charging_logic(current_data, bat_cap_kwh)}

═══ MATHEMATICAL MODEL (current estimation) ═══
{chr(10).join(plan_lines) if plan_lines else '  No hourly plan available'}

TOTALS: Import {estimation.get('total_import_kwh', 0)} kWh ({estimation.get('total_cost', 0):.2f} PLN) | \
Export {estimation.get('total_export_kwh', 0)} kWh ({estimation.get('total_revenue', 0):.2f} PLN) | \
Net savings: {estimation.get('net_savings', 0):.2f} PLN

═══ ACTIVE STRATEGY ═══
{STRATEGY_AI_INSTRUCTIONS.get(active_strategy, STRATEGY_AI_INSTRUCTIONS['ai_full_autonomy'])}

{_format_decision_history(decision_history)}
{_format_financial_context(current_data)}
═══ STRATEGIC RULES ═══
1. ARBITRAGE IS KING: charge battery at off_peak (0.63) → discharge at afternoon_peak (1.50) = 0.87 PLN/kWh
2. Before EVERY peak: battery MUST be at 95-100% SOC. Plan charging accordingly.
3. Night charge (21:00-07:00 off_peak at 0.63) is CRITICAL for morning peak coverage.
4. During peaks: ZERO grid import. Battery + PV must cover all load.
5. Managed loads (boiler, AC) should run ONLY during off-peak or PV surplus > 2kW.
6. AUTO-STOP CHARGE: plan stop_force_charge when SOC reaches 95%. Include in commands.
7. AUTO-STOP DISCHARGE: plan stop_force_discharge when SOC reaches 20%.
8. ZONE TRADING:
   - Tania strefa (off_peak 0.63) + zła pogoda (PV forecast < 5kWh) → force_charge z sieci
   - Droga strefa (afternoon_peak 1.50) + SOC > 30% → set_general (zasil dom z baterii)
   - Droga strefa + RCE sell > 0.63 + SOC > 50% → force_discharge (sprzedaj nadwyżkę do sieci)
   - Zostaw zawsze min 20% SOC na przetrwanie szczytu po rozładowaniu
9. SELL PROFITABILITY: sprzedawaj TYLKO gdy RCE sell ({rce_sell:.4f}) > G13 buy (0.63 PLN/kWh)
   - Jeśli RCE sell < 0.63 → nie opłaca się, zostaw w baterii  
10. NEVER discharge below 10% SOC.
11. NOCNE ŁADOWANIE vs PV: Sprawdź sekcję "NOCNE ŁADOWANIE — OPTYMALIZACJA MIEJSCA NA PV":
   - Dobra pogoda jutro (PV > 8kWh) → NIE ładuj do 100%, zostaw miejsce na darmowe PV!
   - Zła pogoda (PV < 5kWh) → ładuj do 100%
   - Darmowe PV z paneli jest ZAWSZE lepsze niż nocny prąd za 0.63 PLN/kWh
   - Nadwyżka PV ponad pojemność baterii = STRATA (oddana za darmo do sieci)
12. CHECK DEVICE STATUS — don't send commands the system is already executing.
13. ALWAYS include stop_force commands when forced state should end (zone transition).
14. HISTORIA: Analizuj średnie zużycie z ostatnich 3 dni aby precyzyjnie planować ładowanie.

═══ FORMAT ODPOWIEDZI ═══
KRYTYCZNE: Odpowiedz WYŁĄCZNIE poprawnym JSON. Bez markdown, bez tekstu przed/po.
Pisz po POLSKU. Wszystkie opisy po polsku.
time_blocks MUSZĄ być PRZED analysis.
PREFERUJ używanie "actions" (lista ID akcji z katalogu) zamiast surowych "commands".

{{
  "time_blocks": [
    {{
      "start": "HH:MM",
      "end": "HH:MM",
      "zone": "off_peak|morning_peak|afternoon_peak",
      "price": 0.63,
      "strategy": "aggressive_charge|discharge_self_consume|night_charge|pv_optimize|no_action",
      "actions": ["action_id_1"],
      "commands": [
        {{"tool": "force_charge", "params": {{}}}}
      ],
      "reasoning": "1-2 zdania po polsku — dlaczego ten blok, co chcesz osiągnąć"
    }}
  ],
  "analysis": "2-3 zdania po polsku. Opisz swoją strategię, DLACZEGO tak planujesz, jakie oszczędności przewidujesz.",
  "savings_estimate": {{
    "optimized_cost_pln": 7.56,
    "net_savings_pln": 8.74
  }},
  "next_analysis_minutes": 30
}}

RULES FOR time_blocks:
- Start from NOW ({now.strftime('%H:%M')}) and cover next 24h
- Each block aligns with G13 tariff zone transitions
- Max 6 blocks (group similar periods)
- Max 3 commands per block
- Order blocks chronologically
- "actions": list of action IDs from ACTION CATALOG that should be active in this block
- "commands": can use EITHER {{"action": "id"}} or {{"tool": "name", "params": {{}}}}
- Keep reasoning to 1-2 short sentences PER BLOCK in Polish
- analysis MUST be in Polish and explain your overall strategy"""

    return prompt
