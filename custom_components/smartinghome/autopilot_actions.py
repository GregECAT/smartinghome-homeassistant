"""Autopilot Actions Registry for Smarting HOME.

Each automation rule is an independent Action with:
- Sensor slot mappings (auto-matched, user-overridable)
- Conditions for activation (evaluated every tick)
- Commands to execute when active
- Status tracking (idle/waiting/active/disabled)

Strategies (presets) map to sets of active actions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

from .const import (
    AutopilotStrategy,
    SENSOR_BATTERY_SOC,
    SENSOR_BATTERY_POWER,
    SENSOR_PV_POWER,
    SENSOR_LOAD_TOTAL,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_RCE_PRICE,
    SENSOR_RCE_SELL_PROSUMER,
    BINARY_RCE_CHEAPEST,
    BINARY_RCE_EXPENSIVE,
    SWITCH_BOILER,
    SWITCH_AC,
    SWITCH_SOCKET2,
    G13Zone,
    G13_PRICES,
    RCE_PRICE_THRESHOLDS,
    VOLTAGE_THRESHOLD_WARNING,
    VOLTAGE_THRESHOLD_HIGH,
    VOLTAGE_THRESHOLD_CRITICAL,
    PV_SURPLUS_TIER1,
    PV_SURPLUS_TIER2,
    PV_SURPLUS_TIER3,
    PV_SURPLUS_MIN_SOC_TIER1,
    PV_SURPLUS_MIN_SOC_TIER2,
    PV_SURPLUS_MIN_SOC_TIER3,
    SOC_EMERGENCY,
    SOC_CHECK_11_THRESHOLD,
    SOC_CHECK_12_THRESHOLD,
    NIGHT_ARBITRAGE_MIN_FORECAST,
)

_LOGGER = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Action Status
# ═══════════════════════════════════════════════════════════════

class ActionStatus(StrEnum):
    """Status of an autopilot action."""

    IDLE = "idle"           # ○ Conditions not met, not relevant now
    WAITING = "waiting"     # ◐ Conditions partially met, monitoring
    ACTIVE = "active"       # ● Conditions met, action executing
    DISABLED = "disabled"   # ✗ Manually disabled by user


# ═══════════════════════════════════════════════════════════════
# Action Categories (W-layers)
# ═══════════════════════════════════════════════════════════════

class ActionCategory(StrEnum):
    """Action categories corresponding to HEMS layers."""

    W0_SAFETY = "w0_safety"
    W1_G13 = "w1_g13"
    W2_RCE = "w2_rce"
    W3_SOC = "w3_soc"
    W4_VOLTAGE = "w4_voltage"
    W4_SURPLUS = "w4_surplus"
    W5_WEATHER = "w5_weather"


CATEGORY_LABELS: Final = {
    ActionCategory.W0_SAFETY: "🛡️ W0: Safety Guard",
    ActionCategory.W1_G13: "⚡ W1: Harmonogram taryfowy",
    ActionCategory.W2_RCE: "💰 W2: RCE Dynamic Pricing",
    ActionCategory.W3_SOC: "🔋 W3: SOC Safety",
    ActionCategory.W4_VOLTAGE: "⚡ W4: Napięcie",
    ActionCategory.W4_SURPLUS: "☀️ W4: Nadwyżka PV",
    ActionCategory.W5_WEATHER: "🌧️ W5: Pogoda + Pre-peak",
}

CATEGORY_ORDER: Final = [
    ActionCategory.W0_SAFETY,
    ActionCategory.W1_G13,
    ActionCategory.W2_RCE,
    ActionCategory.W3_SOC,
    ActionCategory.W4_VOLTAGE,
    ActionCategory.W4_SURPLUS,
    ActionCategory.W5_WEATHER,
]


# ═══════════════════════════════════════════════════════════════
# Sensor Slot — a named slot that maps to an HA entity
# ═══════════════════════════════════════════════════════════════

@dataclass
class SensorSlot:
    """A sensor slot for an action's display/evaluation."""

    key: str              # "grid_power", "battery_soc", etc.
    label: str            # "Grid", "SOC", "RCE"
    unit: str             # "kW", "%", "zł"
    default_entity: str   # Default HA entity ID
    format_fn: str = ""   # "kw" | "pct" | "pln" | "pln_mwh" | "v" | "w" | "kwh" | "mm"


# ═══════════════════════════════════════════════════════════════
# AutopilotAction — core dataclass
# ═══════════════════════════════════════════════════════════════

@dataclass
class AutopilotAction:
    """A single autopilot action (automation rule)."""

    id: str
    name: str
    icon: str
    description: str
    category: ActionCategory
    sensor_slots: list[SensorSlot] = field(default_factory=list)
    default_params: dict[str, Any] = field(default_factory=dict)
    always_active: bool = False  # True = runs regardless of strategy (safety layers)
    commands: list[dict[str, Any]] = field(default_factory=list)  # [{"tool": "force_charge", "params": {}}]

    # Runtime state (not serialized in definition)
    status: ActionStatus = ActionStatus.IDLE
    last_triggered: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for frontend."""
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "description": self.description,
            "category": self.category.value,
            "sensor_slots": [
                {
                    "key": s.key,
                    "label": s.label,
                    "unit": s.unit,
                    "default_entity": s.default_entity,
                    "format_fn": s.format_fn,
                }
                for s in self.sensor_slots
            ],
            "default_params": self.default_params,
            "always_active": self.always_active,
            "status": self.status.value,
            "commands": self.commands,
        }


# ═══════════════════════════════════════════════════════════════
# Shared sensor slot presets
# ═══════════════════════════════════════════════════════════════

def _slot_grid() -> SensorSlot:
    return SensorSlot("grid_power", "Grid", "kW", SENSOR_GRID_POWER_TOTAL, "kw")

def _slot_soc() -> SensorSlot:
    return SensorSlot("battery_soc", "SOC", "%", SENSOR_BATTERY_SOC, "pct")

def _slot_bat_power() -> SensorSlot:
    return SensorSlot("battery_power", "Bat.", "W", SENSOR_BATTERY_POWER, "w")

def _slot_pv() -> SensorSlot:
    return SensorSlot("pv_power", "PV", "kW", SENSOR_PV_POWER, "kw")

def _slot_rce() -> SensorSlot:
    return SensorSlot("rce_price", "RCE", "zł", SENSOR_RCE_PRICE, "pln_mwh")

def _slot_rce_mwh() -> SensorSlot:
    return SensorSlot("rce_price", "RCE (MWh)", "PLN", SENSOR_RCE_PRICE, "pln_mwh")

def _slot_voltage() -> SensorSlot:
    return SensorSlot("voltage_l1", "V max", "V", SENSOR_GRID_VOLTAGE_L1, "v")

def _slot_g13() -> SensorSlot:
    return SensorSlot("g13_zone", "G13", "", "", "")

def _slot_surplus() -> SensorSlot:
    return SensorSlot("pv_surplus", "Nadwyżka", "W", "", "w")

def _slot_boiler() -> SensorSlot:
    return SensorSlot("boiler", "Bojler", "", SWITCH_BOILER, "")

def _slot_klima() -> SensorSlot:
    return SensorSlot("ac", "Klima", "", SWITCH_AC, "")

def _slot_forecast_tomorrow() -> SensorSlot:
    return SensorSlot("forecast_tomorrow", "Prognoza", "kWh", "sensor.energy_production_tomorrow", "kwh")

def _slot_forecast_remaining() -> SensorSlot:
    return SensorSlot("forecast_remaining", "PV rem.", "kWh", "sensor.energy_production_today_remaining", "kwh")

def _slot_radiation() -> SensorSlot:
    return SensorSlot("solar_radiation", "Radiacja", "W/m²", "", "w")

def _slot_rain() -> SensorSlot:
    return SensorSlot("rain_rate", "Deszcz", "mm/h", "", "mm")

def _slot_cheapest_window() -> SensorSlot:
    return SensorSlot("rce_cheapest", "Okno", "", BINARY_RCE_CHEAPEST, "")

def _slot_rce_trend() -> SensorSlot:
    return SensorSlot("rce_trend", "Trend", "", "", "")

def _slot_dod() -> SensorSlot:
    return SensorSlot("dod", "DOD", "%", "number.goodwe_depth_of_discharge_on_grid", "pct")


# ═══════════════════════════════════════════════════════════════
# ALL ACTIONS REGISTRY
# ═══════════════════════════════════════════════════════════════

def build_all_actions() -> list[AutopilotAction]:
    """Build the complete list of autopilot actions."""
    return [
        # ═══ W0: SAFETY GUARD ═══════════════════════════════════

        AutopilotAction(
            id="grid_import_guard",
            name="Grid Import Guard",
            icon="🛡️",
            description="STOP ładowania baterii z sieci w drogich godzinach. Wyjątek: RCE < 100 PLN/MWh.",
            category=ActionCategory.W0_SAFETY,
            sensor_slots=[_slot_grid(), _slot_bat_power()],
            default_params={"rce_exception_threshold": 100},
            always_active=True,
            commands=[{"tool": "force_discharge", "params": {}}],
        ),
        AutopilotAction(
            id="pv_surplus_charge",
            name="PV Surplus → ładuj baterię",
            icon="☀️🔋",
            description="Nadwyżka PV (export >300W) w drogich godz. → ładuj baterię. Guard czuwa nad importem.",
            category=ActionCategory.W0_SAFETY,
            sensor_slots=[_slot_grid(), _slot_soc()],
            default_params={"min_export_w": 300},
            always_active=True,
            commands=[{"tool": "force_charge", "params": {}}],
        ),

        # ═══ W1: G13 SCHEDULE ═══════════════════════════════════

        AutopilotAction(
            id="sell_07",
            name="Sprzedaż (07:00)",
            icon="☀️",
            description="G13 szczyt poranny (0.91 zł). Sprzedawaj 7-13 Pn-Pt.",
            category=ActionCategory.W1_G13,
            sensor_slots=[_slot_rce(), _slot_soc()],
            default_params={"start_hour": 7, "end_hour": 13, "weekday_only": True},
            commands=[{"tool": "force_discharge", "params": {}}],
        ),
        AutopilotAction(
            id="charge_13",
            name="Ładowanie (13:00)",
            icon="🔋",
            description="Off-peak (0.63 zł). Ładuj baterię 13:00-szczyt Pn-Pt.",
            category=ActionCategory.W1_G13,
            sensor_slots=[_slot_rce(), _slot_soc()],
            default_params={"start_hour": 13, "end_hour": 16, "weekday_only": True},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="evening_peak",
            name="Szczyt wieczorny",
            icon="💰",
            description="G13 szczyt (1.50 zł). Bateria zasila dom.",
            category=ActionCategory.W1_G13,
            sensor_slots=[_slot_g13(), _slot_soc()],
            default_params={"zone": "afternoon_peak"},
            commands=[{"tool": "force_discharge", "params": {}}],
        ),
        AutopilotAction(
            id="weekend",
            name="Weekend",
            icon="🏖️",
            description="Off-peak cały dzień → autokonsumpcja.",
            category=ActionCategory.W1_G13,
            sensor_slots=[_slot_rce(), SensorSlot("cheapest", "Najtaniej", "", "", "")],
            default_params={"weekend_only": True},
            commands=[{"tool": "no_action", "params": {"reason": "Weekend — autokonsumpcja"}}],
        ),

        # ═══ W2: RCE DYNAMIC PRICING ════════════════════════════

        AutopilotAction(
            id="night_arbitrage",
            name="Arbitraż nocny",
            icon="🌙",
            description="Nocne ładowanie z sieci (0.63 zł → 1.50 zł) gdy słaba prognoza PV.",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_forecast_tomorrow(), _slot_soc()],
            default_params={"min_forecast_kwh": NIGHT_ARBITRAGE_MIN_FORECAST, "max_soc": 50, "start_hour": 23},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="cheapest_window",
            name="Najtańsze okno → ładuj",
            icon="🟢",
            description="Binary sensor PSE: najtańsze okno aktywne → ładuj baterię.",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_cheapest_window(), _slot_soc()],
            default_params={},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="most_expensive_window",
            name="Najdroższe okno → alert",
            icon="🔴",
            description="Najdroższe okno aktywne → bateria zasila dom, max oszczędność.",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_rce_mwh(), _slot_g13()],
            default_params={},
            commands=[{"tool": "force_discharge", "params": {}}],
        ),
        AutopilotAction(
            id="low_price_charge",
            name="Niska cena → ładuj",
            icon="📉",
            description="RCE < 150 PLN/MWh → nie opłaca się sprzedawać. Ładuj baterię.",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_rce_mwh(), _slot_rce_trend()],
            default_params={"threshold_mwh": RCE_PRICE_THRESHOLDS["cheap"]},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="high_price_sell",
            name="Cena wzrosła → sprzedaj",
            icon="📈",
            description="RCE > 300 PLN/MWh → opłaca się sprzedawać.",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_rce_mwh(), _slot_rce_trend()],
            default_params={"threshold_mwh": RCE_PRICE_THRESHOLDS["expensive"]},
            commands=[{"tool": "force_discharge", "params": {}}],
        ),
        AutopilotAction(
            id="rce_peak_g13",
            name="RCE Peak + G13 Szczyt",
            icon="💰💰",
            description="RCE > 500 PLN/MWh wieczorem + G13 szczyt → max zysk!",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_rce_mwh(), _slot_soc()],
            default_params={"rce_threshold": RCE_PRICE_THRESHOLDS["very_expensive"]},
            commands=[{"tool": "force_discharge", "params": {}}],
        ),
        AutopilotAction(
            id="negative_price",
            name="Ujemna cena → DARMOWA!",
            icon="🤑",
            description="RCE ujemna → darmowa energia! Ładuj baterię + bojler ON.",
            category=ActionCategory.W2_RCE,
            sensor_slots=[_slot_rce_mwh(), _slot_boiler()],
            default_params={},
            commands=[
                {"tool": "force_charge", "params": {}},
                {"tool": "switch_on", "params": {"entity": "boiler"}},
            ],
        ),

        # ═══ W3: SOC SAFETY ═════════════════════════════════════

        AutopilotAction(
            id="soc_check_11",
            name="SOC check 11:00 (PV-only)",
            icon="⚠️",
            description="SOC < 50% o 11:00 → ładuj z nadwyżki PV. NIE z sieci w szczycie!",
            category=ActionCategory.W3_SOC,
            sensor_slots=[
                _slot_soc(),
                SensorSlot("threshold", "Próg", "%", "", ""),
            ],
            default_params={"hour": 11, "threshold": SOC_CHECK_11_THRESHOLD},
            always_active=True,
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="soc_check_12",
            name="SOC check 12:00 (ostatnia szansa)",
            icon="⚠️",
            description="SOC < 70% o 12:00. Nadwyżka PV → bateria. Brak → czekaj na 13:00 off-peak.",
            category=ActionCategory.W3_SOC,
            sensor_slots=[
                _slot_soc(),
                SensorSlot("threshold", "Próg", "%", "", ""),
            ],
            default_params={"hour": 12, "threshold": SOC_CHECK_12_THRESHOLD},
            always_active=True,
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="smart_soc_protection",
            name="Smart SOC Protection (tariff-aware)",
            icon="🔋",
            description="SOC < 20%: drogie godz. → PV-only, tanie godz. → ładuj normalnie.",
            category=ActionCategory.W3_SOC,
            sensor_slots=[
                _slot_soc(),
                SensorSlot("threshold", "Próg", "%", "", ""),
            ],
            default_params={"low_soc_threshold": 20},
            always_active=True,
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="soc_emergency",
            name="EMERGENCY SOC < 5%",
            icon="🚨",
            description="Ładuj awaryjnie NIEZALEŻNIE od taryfy do 15%! Bateria bliska shutdown.",
            category=ActionCategory.W3_SOC,
            sensor_slots=[
                _slot_soc(),
                SensorSlot("threshold", "Próg", "%", "", ""),
            ],
            default_params={"threshold": SOC_EMERGENCY},
            always_active=True,
            commands=[{"tool": "force_charge", "params": {}}],
        ),

        # ═══ W4: VOLTAGE ════════════════════════════════════════

        AutopilotAction(
            id="voltage_boiler",
            name="Napięcie → Bojler",
            icon="⚡",
            description=f">{VOLTAGE_THRESHOLD_WARNING}V → Bojler ON (zagospodarowanie nadwyżki).",
            category=ActionCategory.W4_VOLTAGE,
            sensor_slots=[_slot_voltage(), _slot_boiler()],
            default_params={"threshold_v": VOLTAGE_THRESHOLD_WARNING},
            always_active=True,
            commands=[{"tool": "switch_on", "params": {"entity": "boiler"}}],
        ),
        AutopilotAction(
            id="voltage_klima",
            name="Napięcie → Klima",
            icon="⚡⚡",
            description=f">{VOLTAGE_THRESHOLD_HIGH}V → Klima ON (bojler już działa).",
            category=ActionCategory.W4_VOLTAGE,
            sensor_slots=[_slot_voltage(), _slot_klima()],
            default_params={"threshold_v": VOLTAGE_THRESHOLD_HIGH},
            always_active=True,
            commands=[
                {"tool": "switch_on", "params": {"entity": "boiler"}},
                {"tool": "switch_on", "params": {"entity": "ac"}},
            ],
        ),
        AutopilotAction(
            id="voltage_critical",
            name="Krytyczne napięcie",
            icon="🔴",
            description=f">{VOLTAGE_THRESHOLD_CRITICAL}V → Ładuj baterię natychmiast!",
            category=ActionCategory.W4_VOLTAGE,
            sensor_slots=[_slot_voltage(), _slot_soc()],
            default_params={"threshold_v": VOLTAGE_THRESHOLD_CRITICAL},
            always_active=True,
            commands=[
                {"tool": "force_charge", "params": {}},
                {"tool": "switch_on", "params": {"entity": "boiler"}},
                {"tool": "switch_on", "params": {"entity": "ac"}},
            ],
        ),

        # ═══ W4: PV SURPLUS ═════════════════════════════════════

        AutopilotAction(
            id="surplus_boiler",
            name="Nadwyżka → Bojler",
            icon="☀️",
            description=f">{PV_SURPLUS_TIER1 / 1000:.0f}kW nadwyżki + SOC >{PV_SURPLUS_MIN_SOC_TIER1}% → Bojler ON.",
            category=ActionCategory.W4_SURPLUS,
            sensor_slots=[_slot_surplus(), _slot_soc()],
            default_params={"surplus_w": PV_SURPLUS_TIER1, "min_soc": PV_SURPLUS_MIN_SOC_TIER1},
            always_active=True,
            commands=[{"tool": "switch_on", "params": {"entity": "boiler"}}],
        ),
        AutopilotAction(
            id="surplus_klima",
            name="Nadwyżka → Klima",
            icon="❄️",
            description=f">{PV_SURPLUS_TIER2 / 1000:.0f}kW nadwyżki + SOC >{PV_SURPLUS_MIN_SOC_TIER2}% + bojler ON → Klima ON.",
            category=ActionCategory.W4_SURPLUS,
            sensor_slots=[_slot_surplus(), _slot_soc()],
            default_params={"surplus_w": PV_SURPLUS_TIER2, "min_soc": PV_SURPLUS_MIN_SOC_TIER2},
            always_active=True,
            commands=[
                {"tool": "switch_on", "params": {"entity": "boiler"}},
                {"tool": "switch_on", "params": {"entity": "ac"}},
            ],
        ),
        AutopilotAction(
            id="surplus_gniazdko",
            name="Nadwyżka → Gniazdko 2",
            icon="🔌",
            description=f">{PV_SURPLUS_TIER3 / 1000:.0f}kW nadwyżki + SOC >{PV_SURPLUS_MIN_SOC_TIER3}% + bojler + klima → Gniazdko ON.",
            category=ActionCategory.W4_SURPLUS,
            sensor_slots=[_slot_surplus(), _slot_soc()],
            default_params={"surplus_w": PV_SURPLUS_TIER3, "min_soc": PV_SURPLUS_MIN_SOC_TIER3},
            always_active=True,
            commands=[
                {"tool": "switch_on", "params": {"entity": "boiler"}},
                {"tool": "switch_on", "params": {"entity": "ac"}},
                {"tool": "switch_on", "params": {"entity": "socket2"}},
            ],
        ),
        AutopilotAction(
            id="surplus_emergency_off",
            name="Awaryjne OFF",
            icon="🚨",
            description="SOC < 50% → wyłącz wszystkie obciążenia.",
            category=ActionCategory.W4_SURPLUS,
            sensor_slots=[
                _slot_soc(),
                SensorSlot("threshold", "Próg", "%", "", ""),
            ],
            default_params={"soc_threshold": 50},
            always_active=True,
            commands=[
                {"tool": "switch_off", "params": {"entity": "boiler"}},
                {"tool": "switch_off", "params": {"entity": "ac"}},
                {"tool": "switch_off", "params": {"entity": "socket2"}},
            ],
        ),

        # ═══ W5: WEATHER + PRE-PEAK ═════════════════════════════

        AutopilotAction(
            id="morning_check_0530",
            name="05:30 poranny check",
            icon="🌧️⚡",
            description="SOC <80% + PV <10kWh → ładuj z sieci (0.63 zł) do 07:00.",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[
                SensorSlot("forecast_today", "PV prognoza", "kWh", "sensor.energy_production_today", "kwh"),
                _slot_soc(),
            ],
            default_params={"hour": 5, "minute": 30, "soc_threshold": 80, "pv_threshold": 10},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="ecowitt_check_1000",
            name="10:00 weryfikacja Ecowitt",
            icon="🌥️",
            description="SOC <60% + niska radiacja → priorytet PV→bateria.",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[
                _slot_radiation(),
                SensorSlot("uv_index", "UV", "", "", ""),
            ],
            default_params={"hour": 10, "soc_threshold": 60},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="last_chance_1330",
            name="13:30 ostatnia szansa (zima)",
            icon="⚠️🔋",
            description="SOC <80% + PV rem <5kWh → ładuj! Szczyt za 2.5h.",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[_slot_forecast_remaining(), _slot_soc()],
            default_params={"hour": 13, "minute": 30, "soc_threshold": 80, "pv_remaining_threshold": 5},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="prepeak_summer_1800",
            name="18:00 pre-peak lato",
            icon="☀️⚠️",
            description="SOC <70% + słaba radiacja → ładuj! Szczyt o 19:00 (lato).",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[_slot_radiation(), _slot_soc()],
            default_params={"hour": 18, "soc_threshold": 70},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="sudden_clouds",
            name="Nagłe zachmurzenie",
            icon="☁️",
            description="Radiacja <50 W/m² + SOC <70% → priorytet bateria.",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[_slot_radiation(), _slot_pv()],
            default_params={"radiation_threshold": 50, "soc_threshold": 70},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="rain_priority",
            name="Deszcz → priorytet bateria",
            icon="🌧️",
            description="Opady >0.5mm/h + SOC <70% → cały PV → bateria.",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[_slot_rain(), _slot_pv()],
            default_params={"rain_threshold": 0.5, "soc_threshold": 70},
            commands=[{"tool": "force_charge", "params": {}}],
        ),
        AutopilotAction(
            id="weak_forecast_dod",
            name="Słaba prognoza → DOD",
            icon="🌧️",
            description="Jutro < 5 kWh → zachowaj baterię (DOD → 70%).",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[
                _slot_forecast_tomorrow(),
                _slot_dod(),
            ],
            default_params={"forecast_threshold": 5.0, "reduced_dod": 70},
            commands=[{"tool": "set_dod", "params": {"dod": 70}}],
        ),
        AutopilotAction(
            id="restore_dod",
            name="Przywróć DOD",
            icon="☀️",
            description="PV > 500W przez 10 min → DOD z powrotem na 95%.",
            category=ActionCategory.W5_WEATHER,
            sensor_slots=[
                _slot_pv(),
                SensorSlot("threshold", "Próg", "W", "", "w"),
            ],
            default_params={"pv_threshold": 500, "duration_min": 10, "restored_dod": 95},
            always_active=True,
            commands=[{"tool": "set_dod", "params": {"dod": 95}}],
        ),
    ]


# ═══════════════════════════════════════════════════════════════
# Strategy → Action Mapping (presets)
# ═══════════════════════════════════════════════════════════════

# Which NON-always_active actions are enabled per strategy
STRATEGY_ACTION_MAP: Final[dict[AutopilotStrategy, list[str]]] = {
    AutopilotStrategy.MAX_SELF_CONSUMPTION: [
        # No G13 schedule trading, just PV self-consumption
        "weekend",
    ],
    AutopilotStrategy.MAX_PROFIT: [
        "sell_07", "charge_13", "evening_peak", "weekend",
        "night_arbitrage", "cheapest_window", "most_expensive_window",
        "low_price_charge", "high_price_sell", "rce_peak_g13", "negative_price",
        "morning_check_0530", "ecowitt_check_1000", "last_chance_1330",
        "prepeak_summer_1800", "sudden_clouds", "rain_priority",
        "weak_forecast_dod",
    ],
    AutopilotStrategy.BATTERY_PROTECTION: [
        # Conservative: no grid charging, gentle cycling
        "weekend",
        "weak_forecast_dod",
    ],
    AutopilotStrategy.ZERO_EXPORT: [
        # No export, store everything
        "weekend",
    ],
    AutopilotStrategy.WEATHER_ADAPTIVE: [
        "sell_07", "charge_13", "evening_peak", "weekend",
        "night_arbitrage", "low_price_charge", "high_price_sell",
        "morning_check_0530", "ecowitt_check_1000", "last_chance_1330",
        "prepeak_summer_1800", "sudden_clouds", "rain_priority",
        "weak_forecast_dod",
    ],
    AutopilotStrategy.AI_FULL_AUTONOMY: [
        # AI decides — enable all actions, AI selects which to trigger
        "sell_07", "charge_13", "evening_peak", "weekend",
        "night_arbitrage", "cheapest_window", "most_expensive_window",
        "low_price_charge", "high_price_sell", "rce_peak_g13", "negative_price",
        "morning_check_0530", "ecowitt_check_1000", "last_chance_1330",
        "prepeak_summer_1800", "sudden_clouds", "rain_priority",
        "weak_forecast_dod",
    ],
}


def get_active_action_ids(strategy: AutopilotStrategy) -> set[str]:
    """Get IDs of actions that should be active for a given strategy.

    Always-active actions are always included, plus strategy-specific ones.
    """
    all_actions = build_all_actions()
    always = {a.id for a in all_actions if a.always_active}
    strategy_specific = set(STRATEGY_ACTION_MAP.get(strategy, []))
    return always | strategy_specific


def get_actions_by_category() -> dict[str, list[AutopilotAction]]:
    """Get all actions grouped by category, in display order."""
    actions = build_all_actions()
    grouped: dict[str, list[AutopilotAction]] = {}
    for cat in CATEGORY_ORDER:
        grouped[str(cat)] = []
    for a in actions:
        key = str(a.category)
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(a)
    return grouped
