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

        # Build hourly plan
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

        for h in range(24):
            ha = HourlyAction(h)
            ha.soc_start = running_soc

            # Estimate PV and load for this hour
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


def build_autopilot_ai_prompt(
    strategy: AutopilotStrategy,
    current_data: dict[str, Any],
    estimation: dict[str, Any],
) -> str:
    """Build a specialized prompt for AI autopilot analysis."""
    now = datetime.now()
    day_names = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']

    # Build hourly plan summary
    plan_lines = []
    for h in estimation.get("hourly_plan", []):
        plan_lines.append(
            f"  {h['hour']:02d}:00 — {h['action'].upper():10s} | "
            f"PV:{h['pv']:5.0f}W | Load:{h['load']:5.0f}W | "
            f"Bat:{h['battery']:+6.0f}W | Grid:{h['grid']:+6.0f}W | "
            f"SOC:{h['soc_start']:.0f}→{h['soc_end']:.0f}%"
        )

    prompt = f"""You are an expert energy management AI for a home solar+battery system in Poland.

TASK: Analyze the following 24-hour energy plan for strategy "{AUTOPILOT_STRATEGY_LABELS.get(strategy, strategy.value)}" and provide:
1. A refined hourly action plan in JSON format
2. Key optimization opportunities
3. Risk assessment
4. Expected savings vs no management

CURRENT STATE ({now.strftime('%Y-%m-%d')} {day_names[now.weekday()]} {now.strftime('%H:%M')}):
- PV Power: {current_data.get('pv_power', 0)} W
- Load: {current_data.get('load', 0)} W
- Battery SOC: {current_data.get('battery_soc', 0)}%
- Grid Power: {current_data.get('grid_power', 0)} W
- RCE Price: {current_data.get('rce_price', 250)} PLN/MWh
- G13 Zone: {current_data.get('g13_zone', 'unknown')}
- Weather: {current_data.get('weather_condition', 'N/A')}, {current_data.get('weather_temp', 'N/A')}°C, chmury: {current_data.get('weather_clouds', 'N/A')}%
- PV Forecast Today: {current_data.get('forecast_today', 0)} kWh
- PV Forecast Tomorrow: {current_data.get('forecast_tomorrow', 0)} kWh

CURRENT ESTIMATION:
{chr(10).join(plan_lines)}

TOTALS:
- Import: {estimation.get('total_import_kwh', 0)} kWh
- Export: {estimation.get('total_export_kwh', 0)} kWh
- Self-consumption: {estimation.get('total_self_consumption_kwh', 0)} kWh
- Cost: {estimation.get('total_cost', 0)} PLN
- Revenue: {estimation.get('total_revenue', 0)} PLN
- Net savings: {estimation.get('net_savings', 0)} PLN
- vs No Management: {estimation.get('vs_no_management', 0)} PLN

RESPOND IN POLISH. Format as structured markdown with:
## 📊 Analiza strategii
## 🕐 Zoptymalizowany plan godzinowy
## 💰 Estymacja oszczędności
## ⚠️ Ryzyka i zalecenia
## 🎯 Rekomendacja

Use tables for the hourly plan. Be concise but comprehensive."""

    return prompt
