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
    "force_charge": "Force battery charging from grid+PV (call when battery needs charging)",
    "force_discharge": "Force battery discharge (sell/self-consume, call when selling makes sense)",
    "set_dod": "Set max depth of discharge (params: dod: int 0-95). Higher = more capacity available. Max is 95.",
    "set_export_limit": "Set grid export limit in watts (params: limit: int). 0 = no export",
    "switch_on": "Turn on managed load (params: entity: 'boiler'|'ac'|'socket2')",
    "switch_off": "Turn off managed load (params: entity: 'boiler'|'ac'|'socket2')",
    "no_action": "Do nothing this tick (params: reason: string). Use when current state is optimal",
}


def build_ai_controller_prompt(
    current_data: dict[str, Any],
    device_status_text: str = "",
) -> str:
    """Build a concise prompt for AI to return structured JSON commands.

    Unlike the advisory prompt (build_autopilot_ai_prompt), this one:
    - Does NOT request analysis text or markdown
    - Requires ONLY valid JSON response
    - Lists available tools the AI can call
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

    # Tools description
    tools_desc = "\n".join(
        f'  - "{name}": {desc}' for name, desc in AI_CONTROLLER_TOOLS.items()
    )

    # Device status section (from InverterAgent)
    device_section = ""
    if device_status_text:
        device_section = f"\n{device_status_text}\n"

    prompt = f"""You are an autonomous energy controller for a home solar+battery system in Poland (G13 tariff).

YOUR JOB: Decide what action to take RIGHT NOW based on current system state. Respond ONLY with valid JSON.

═══ AVAILABLE TOOLS ═══
{tools_desc}

═══ MANAGED ENTITIES ═══
  - "boiler": Water heater 3.8kW (switch.bojler_3800)
  - "ac": Air conditioning (switch.klimatyzacja_socket_1)
  - "socket2": Smart socket (switch.drugie_gniazdko)

═══ CURRENT STATE ({now.strftime('%Y-%m-%d')} {day_names[weekday]} {now.strftime('%H:%M')}) ═══
  PV Power: {current_data.get('pv_power', 0)} W
  Load: {current_data.get('load', 0)} W
  Battery SOC: {current_data.get('battery_soc', 0)}%
  Battery Power: {current_data.get('battery_power', 0)} W (positive=charging)
  Grid Power: {current_data.get('grid_power', 0)} W (positive=import)
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

═══ WEATHER ═══
  Conditions: {current_data.get('weather_condition', 'N/A')}, {current_data.get('weather_temp', 'N/A')}°C
  Clouds: {current_data.get('weather_clouds', 'N/A')}%
  PV forecast remaining today: {current_data.get('forecast_remaining', 0)} kWh
  PV forecast tomorrow: {current_data.get('forecast_tomorrow', 0)} kWh

═══ KEY RULES ═══
1. ARBITRAGE IS KING: Buy at off_peak (0.63), self-consume at afternoon_peak (1.50) = 0.87 PLN/kWh margin
2. If approaching afternoon_peak AND SOC < 90% → force_charge NOW (even from grid at 0.63)
3. During afternoon_peak → force_discharge to avoid importing at 1.50
4. Pre-peak (13:00-15:59): charge battery + heat water (boiler) using cheap electricity
5. Night (22:00-06:00): charge battery from grid if tomorrow's PV forecast < 5 kWh
6. If PV surplus > 2000W and SOC > 80% → switch_on boiler to use free energy
7. If RCE > 500 PLN/MWh and SOC > 30% → force_discharge (sell at high price)
8. NEVER discharge below 10% SOC (safety layer handles this externally)
9. CHECK DEVICE STATUS above — if charging is ALREADY active, use "no_action" instead of "force_charge"!
10. If a switch is ALREADY ON, do NOT send switch_on again — use "no_action"!

═══ RESPONSE FORMAT ═══
CRITICAL: Respond with ONLY valid JSON. No markdown, no explanations, no text before/after JSON.
Keep "reasoning" to MAX 2 sentences in Polish. Be extremely concise.
{{
  "reasoning": "Krótkie uzasadnienie decyzji (max 2 zdania)",
  "commands": [
    {{"tool": "tool_name", "params": {{}}}}
  ],
  "next_check_minutes": 5
}}

If no action needed, use: {{"tool": "no_action", "params": {{"reason": "..."}}}}
Maximum 3 commands per response. Order by priority (most important first)."""

    return prompt


# ═══════════════════════════════════════════════════════════════
# AI STRATEGIST PROMPT — 24h strategic plan with time_blocks
# ═══════════════════════════════════════════════════════════════


def build_ai_strategist_prompt(
    current_data: dict[str, Any],
    estimation: dict[str, Any],
    device_status_text: str = "",
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
    current_price = _get_g13_price(current_zone)

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

    # Hourly plan from mathematical model
    plan_lines = []
    for h in estimation.get("hourly_plan", []):
        zone = _get_g13_zone(h['hour'], month, weekday)
        plan_lines.append(
            f"  {h['hour']:02d}:00 | PV:{h['pv']:5.0f}W | Load:{h['load']:5.0f}W | "
            f"Bat:{h['battery']:+6.0f}W | Grid:{h['grid']:+6.0f}W | "
            f"SOC:{h['soc_start']:.0f}→{h['soc_end']:.0f}% | {zone.value}"
        )

    # Tools description
    tools_desc = "\n".join(
        f'  - "{name}": {desc}' for name, desc in AI_CONTROLLER_TOOLS.items()
    )

    # Device status section
    device_section = ""
    if device_status_text:
        device_section = f"\n{device_status_text}\n"

    prompt = f"""You are an expert energy strategist AI for a home solar+battery system in Poland (G13 tariff).

YOUR JOB: Create a STRATEGIC 24H PLAN with specific commands for each time block.
This plan will be executed automatically by the InverterAgent. Respond ONLY with valid JSON.

═══ SYSTEM ═══
  Battery: {bat_cap_kwh:.1f} kWh, Max charge/discharge: {bat_cap_kwh * 0.5:.1f} kW
  Inverter: hybrid 3-phase
  Managed Loads: boiler (3.8kW), ac, socket2
{device_section}
═══ AVAILABLE TOOLS ═══
{tools_desc}

═══ CURRENT STATE ({now.strftime('%Y-%m-%d')} {day_names[weekday]} {now.strftime('%H:%M')}) ═══
  PV: {current_data.get('pv_power', 0)} W | Load: {current_data.get('load', 0)} W
  Battery SOC: {current_data.get('battery_soc', 0)}% | Power: {current_data.get('battery_power', 0)} W
  Grid: {current_data.get('grid_power', 0)} W (+=import) | Surplus: {current_data.get('pv_surplus', 0)} W

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

═══ MATHEMATICAL MODEL (current estimation) ═══
{chr(10).join(plan_lines) if plan_lines else '  No hourly plan available'}

TOTALS: Import {estimation.get('total_import_kwh', 0)} kWh ({estimation.get('total_cost', 0):.2f} PLN) | \
Export {estimation.get('total_export_kwh', 0)} kWh ({estimation.get('total_revenue', 0):.2f} PLN) | \
Net savings: {estimation.get('net_savings', 0):.2f} PLN

═══ STRATEGIC RULES ═══
1. ARBITRAGE IS KING: charge battery at off_peak (0.63) → discharge at afternoon_peak (1.50) = 0.87 PLN/kWh
2. Before EVERY peak: battery MUST be at 95-100% SOC. Plan charging accordingly.
3. Night charge (21:00-07:00 off_peak at 0.63) is CRITICAL for morning peak coverage.
4. During peaks: ZERO grid import. Battery + PV must cover all load.
5. Managed loads (boiler, AC) should run ONLY during off-peak or PV surplus > 2kW.
6. If RCE sell > current G13 buy AND battery is full → consider export.
7. NEVER discharge below 10% SOC.
8. Consider weather: cloudy tomorrow → charge more tonight.
9. CHECK DEVICE STATUS — don't send commands the system is already executing.

═══ RESPONSE FORMAT ═══
CRITICAL: Respond with ONLY valid JSON. No markdown, no text before/after.

{{
  "analysis": "Krótka analiza obecnej sytuacji (max 3 zdania, po polsku)",
  "time_blocks": [
    {{
      "start": "HH:MM",
      "end": "HH:MM",
      "zone": "off_peak|morning_peak|afternoon_peak",
      "price": 0.63,
      "strategy": "aggressive_charge|discharge_self_consume|night_charge|pv_optimize|no_action",
      "commands": [
        {{"tool": "force_charge", "params": {{}}}},
        {{"tool": "switch_on", "params": {{"entity": "boiler"}}}}
      ],
      "reasoning": "Uzasadnienie dla tego bloku (1 zdanie)"
    }}
  ],
  "savings_estimate": {{
    "optimized_cost_pln": 7.56,
    "net_savings_pln": 8.74,
    "vs_current_plan_pln": 6.27
  }},
  "next_analysis_minutes": 30
}}

RULES FOR time_blocks:
- Start from NOW ({now.strftime('%H:%M')}) and cover next 24h
- Each block aligns with G13 tariff zone transitions
- Max 8 blocks (group similar periods)
- Max 4 commands per block
- Order blocks chronologically
- "commands" uses same tools as ═══ AVAILABLE TOOLS ═══ above"""

    return prompt
