"""Strategy Controller for Smarting HOME HEMS.

Maps each AutopilotStrategy to a set of control rules that are executed
on every coordinator tick (~30s).  When the user switches strategy, only
the relevant rule-set is active.

Safety layers (W0 Grid Import Guard, W3 SOC Safety, W4 Voltage Cascade)
run **always**, regardless of selected strategy.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .ai_advisor import AIAdvisor

from .const import (
    DOMAIN,
    AutopilotStrategy,
    AUTOPILOT_STRATEGY_LABELS,
    G13Zone,
    G13_PRICES,
    G13_WINTER_SCHEDULE,
    G13_SUMMER_SCHEDULE,
    WINTER_MONTHS,
    RCE_PRICE_THRESHOLDS,
    VOLTAGE_THRESHOLD_WARNING,
    VOLTAGE_THRESHOLD_HIGH,
    VOLTAGE_THRESHOLD_CRITICAL,
    VOLTAGE_THRESHOLD_RECOVERY,
    PV_SURPLUS_TIER1,
    PV_SURPLUS_TIER2,
    PV_SURPLUS_TIER3,
    PV_SURPLUS_OFF,
    PV_SURPLUS_MIN_SOC_TIER1,
    PV_SURPLUS_MIN_SOC_TIER2,
    PV_SURPLUS_MIN_SOC_TIER3,
    SOC_EMERGENCY,
    NIGHT_ARBITRAGE_MIN_FORECAST,
    DEFAULT_BATTERY_CAPACITY,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_BATTERY_SOC,
    SENSOR_PV_POWER,
    SENSOR_LOAD_TOTAL,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_RCE_PRICE,
    BINARY_RCE_CHEAPEST,
    BINARY_RCE_EXPENSIVE,
)
from .energy_manager import EnergyManager
from .inverter_agent import InverterAgent

_LOGGER = logging.getLogger(__name__)

# Throttle: minimum seconds between identical actions
ACTION_COOLDOWN = 60

# Strategy-specific SOC thresholds
_SOC_LIMITS: dict[AutopilotStrategy, dict[str, float]] = {
    AutopilotStrategy.MAX_SELF_CONSUMPTION: {"min": 10, "max": 100},
    AutopilotStrategy.MAX_PROFIT:           {"min": 15, "max": 100},
    AutopilotStrategy.BATTERY_PROTECTION:   {"min": 30, "max": 80},
    AutopilotStrategy.ZERO_EXPORT:          {"min": 10, "max": 100},
    AutopilotStrategy.WEATHER_ADAPTIVE:     {"min": 15, "max": 95},
    AutopilotStrategy.AI_FULL_AUTONOMY:     {"min": 10, "max": 100},
}

# Services that indicate an automation conflicts with StrategyController
HEMS_CONFLICT_SERVICES = {
    "goodwe.set_parameter",
    "smartinghome.force_charge",
    "smartinghome.force_discharge",
    "smartinghome.set_mode",
    "smartinghome.set_export_limit",
}

# Switches managed by StrategyController — automations toggling these conflict
from .const import SWITCH_BOILER, SWITCH_AC, SWITCH_SOCKET2
HEMS_MANAGED_SWITCHES = {SWITCH_BOILER, SWITCH_AC, SWITCH_SOCKET2}


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert to float safely."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _get_g13_zone(hour: int, month: int, weekday: int) -> G13Zone:
    """Determine current G13 tariff zone."""
    if weekday >= 5:
        return G13Zone.OFF_PEAK
    schedule = G13_WINTER_SCHEDULE if month in WINTER_MONTHS else G13_SUMMER_SCHEDULE
    for (start, end), zone in schedule.items():
        if start < end:
            if start <= hour < end:
                return zone
        else:
            if hour >= start or hour < end:
                return zone
    return G13Zone.OFF_PEAK


class StrategyController:
    """Executes control rules based on the active AutopilotStrategy.

    Called on every coordinator tick (~30s).  Each strategy maps to a
    combination of control layers:

    - W0: Grid Import Guard (always)
    - W1: G13 tariff schedule
    - W2: RCE dynamic pricing
    - W3: SOC safety (always)
    - W4: Voltage + PV Surplus cascade (always)
    - W5: Weather forecast adaptive
    """

    def __init__(
        self,
        hass: HomeAssistant,
        energy_manager: EnergyManager,
    ) -> None:
        self.hass = hass
        self._em = energy_manager
        self._active_strategy = AutopilotStrategy.MAX_SELF_CONSUMPTION
        self._enabled = False

        # Throttle tracking: action_name → last_execution_timestamp
        self._last_action: dict[str, float] = {}

        # State tracking to avoid redundant commands
        self._charging_enabled: bool | None = None
        self._voltage_cascade_active = False
        self._surplus_cascade_active = False

        # Decision log (last N actions for UI)
        self._decision_log: list[dict[str, Any]] = []
        self._max_log_entries = 50

        # Automation manager: tracks which automations were disabled
        self._disabled_automations: set[str] = set()
        self._automation_scan_done = False

        # AI Controller state
        self._ai: AIAdvisor | None = None
        self._ai_cached_commands: dict[str, Any] | None = None
        self._ai_commands_executed: bool = False  # True when cached commands have been executed
        self._ai_last_call: float = 0.0
        self._ai_call_interval: int = 300  # default 5 min, AI can override via next_check_minutes
        self._ai_dry_run: bool = False  # Set True to log-only without executing

        # InverterAgent — state-aware command executor
        self._inverter_agent = InverterAgent(
            hass, energy_manager, dry_run=self._ai_dry_run,
        )

    def set_ai_advisor(self, ai_advisor: AIAdvisor) -> None:
        """Inject AI advisor reference (called after services setup)."""
        self._ai = ai_advisor
        _LOGGER.info("AI Controller: advisor connected (dry_run=%s)", self._ai_dry_run)

    def set_inverter_brand(self, brand: str) -> None:
        """Set inverter brand for entity discovery."""
        self._inverter_agent._inverter_brand = brand
        _LOGGER.info("InverterAgent: brand set to %s", brand)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def active_strategy(self) -> AutopilotStrategy:
        return self._active_strategy

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def decision_log(self) -> list[dict[str, Any]]:
        """Return recent decision log entries."""
        return list(self._decision_log)

    async def activate_strategy(self, strategy: AutopilotStrategy) -> None:
        """Switch to a new strategy."""
        old = self._active_strategy
        self._active_strategy = strategy
        self._enabled = True
        self._last_action.clear()  # allow immediate actions after switch

        # Disable conflicting automations
        await self._disable_conflicting_automations()

        label = AUTOPILOT_STRATEGY_LABELS.get(strategy, strategy.value)
        self._log_decision(
            "strategy_change",
            f"Zmieniono strategię: {AUTOPILOT_STRATEGY_LABELS.get(old, old)} → {label}",
        )
        _LOGGER.info(
            "Strategy activated: %s → %s", old, strategy.value,
        )

        # Fire event for frontend
        self.hass.bus.async_fire(
            f"{DOMAIN}_strategy_changed",
            {
                "strategy": strategy.value,
                "label": label,
                "previous": old.value,
                "disabled_automations": list(self._disabled_automations),
            },
        )

    async def deactivate(self) -> None:
        """Deactivate the controller (manual mode)."""
        self._enabled = False

        # Restore previously disabled automations
        restored = await self._restore_automations()

        msg = f"Autopilot wyłączony — tryb manualny"
        if restored:
            msg += f" (przywrócono {len(restored)} automatyzacji)"
        self._log_decision("deactivated", msg)
        _LOGGER.info("Strategy controller deactivated, restored: %s", restored)

    async def execute_tick(self, data: dict[str, Any]) -> dict[str, Any]:
        """Execute one control cycle.  Called every coordinator tick.

        Args:
            data: merged raw + computed sensor data from coordinator.

        Returns:
            dict with actions taken, strategy info, decision log.
        """
        if not self._enabled:
            return {"enabled": False, "strategy": self._active_strategy.value}

        # Trigger entity discovery on first tick (HA state machine populated)
        if not self._inverter_agent.capabilities_discovered:
            try:
                await self._inverter_agent.discover_capabilities()
            except Exception as err:
                _LOGGER.warning("Entity discovery failed: %s", err)

        now = datetime.now()
        hour = now.hour
        month = now.month
        weekday = now.weekday()

        # Extract key sensor values
        soc = _safe_float(data.get(SENSOR_BATTERY_SOC))
        pv = _safe_float(data.get(SENSOR_PV_POWER))
        load = _safe_float(data.get(SENSOR_LOAD_TOTAL))
        grid = _safe_float(data.get(SENSOR_GRID_POWER_TOTAL))
        v_l1 = _safe_float(data.get(SENSOR_GRID_VOLTAGE_L1))
        v_l2 = _safe_float(data.get(SENSOR_GRID_VOLTAGE_L2))
        v_l3 = _safe_float(data.get(SENSOR_GRID_VOLTAGE_L3))
        rce_mwh = _safe_float(data.get(SENSOR_RCE_PRICE))
        surplus = max(pv - load, 0)
        forecast_tomorrow = _safe_float(data.get("pv_forecast_tomorrow_total"))

        g13_zone = _get_g13_zone(hour, month, weekday)
        g13_price = G13_PRICES.get(g13_zone, 0.63)

        actions_taken: list[str] = []
        strategy = self._active_strategy

        # ═══════════════════════════════════════════════════════
        # SAFETY LAYERS — always active
        # ═══════════════════════════════════════════════════════

        # W3: SOC Emergency (highest priority)
        if soc < SOC_EMERGENCY:
            if await self._throttled_action("soc_emergency"):
                await self._em.force_charge()
                actions_taken.append("W3: ⚠️ SOC emergency — wymuszono ładowanie")
                self._log_decision("soc_emergency", f"SOC={soc:.0f}% < {SOC_EMERGENCY}% — ładowanie awaryjne")
                self._charging_enabled = True

        # W0: Grid Import Guard
        w0_actions = await self._execute_w0_grid_import_guard(
            soc, pv, load, grid, g13_zone, g13_price, rce_mwh, hour,
        )
        actions_taken.extend(w0_actions)

        # W4: Voltage cascade (if daytime)
        if pv > 50:  # only during solar hours
            v_actions = await self._execute_w4_voltage_cascade(v_l1, v_l2, v_l3, soc)
            actions_taken.extend(v_actions)

        # W4: PV Surplus cascade
        surplus_actions = await self._execute_w4_pv_surplus_cascade(surplus, soc)
        actions_taken.extend(surplus_actions)

        # ═══════════════════════════════════════════════════════
        # STRATEGY-SPECIFIC LAYERS
        # ═══════════════════════════════════════════════════════

        if strategy == AutopilotStrategy.MAX_SELF_CONSUMPTION:
            s_actions = await self._strategy_max_self_consumption(
                soc, pv, load, surplus, g13_zone, g13_price, hour,
            )
            actions_taken.extend(s_actions)

        elif strategy == AutopilotStrategy.MAX_PROFIT:
            s_actions = await self._strategy_max_profit(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, data,
            )
            actions_taken.extend(s_actions)

        elif strategy == AutopilotStrategy.BATTERY_PROTECTION:
            s_actions = await self._strategy_battery_protection(
                soc, pv, load, surplus, g13_price, hour,
            )
            actions_taken.extend(s_actions)

        elif strategy == AutopilotStrategy.ZERO_EXPORT:
            s_actions = await self._strategy_zero_export(
                soc, pv, load, surplus, g13_price, hour,
            )
            actions_taken.extend(s_actions)

        elif strategy == AutopilotStrategy.WEATHER_ADAPTIVE:
            s_actions = await self._strategy_weather_adaptive(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, forecast_tomorrow, data,
            )
            actions_taken.extend(s_actions)

        elif strategy == AutopilotStrategy.AI_FULL_AUTONOMY:
            # AI Full Autonomy uses all layers
            s_actions = await self._strategy_ai_full_autonomy(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, forecast_tomorrow, data,
            )
            actions_taken.extend(s_actions)

        # AI reasoning (for frontend display)
        ai_reasoning = ""
        if self._ai_cached_commands and strategy == AutopilotStrategy.AI_FULL_AUTONOMY:
            ai_reasoning = self._ai_cached_commands.get("reasoning", "")

        return {
            "enabled": True,
            "strategy": strategy.value,
            "strategy_label": AUTOPILOT_STRATEGY_LABELS.get(strategy, ""),
            "actions": actions_taken,
            "soc": soc,
            "pv": pv,
            "load": load,
            "surplus": surplus,
            "g13_zone": g13_zone.value,
            "g13_price": g13_price,
            "rce_price_mwh": rce_mwh,
            "ai_reasoning": ai_reasoning,
            "timestamp": now.strftime("%H:%M:%S"),
        }

    # ------------------------------------------------------------------
    #  W0 — Grid Import Guard
    # ------------------------------------------------------------------

    async def _execute_w0_grid_import_guard(
        self,
        soc: float, pv: float, load: float, grid: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float,
        hour: int,
    ) -> list[str]:
        """W0: Block battery charging from grid during expensive hours.

        Exception: RCE < 100 PLN/MWh (arbitrage profitable).
        PV charging is always allowed.
        """
        actions: list[str] = []
        is_expensive = g13_zone in (G13Zone.MORNING_PEAK, G13Zone.AFTERNOON_PEAK)
        rce_cheap_exception = rce_mwh < RCE_PRICE_THRESHOLDS["very_cheap"]

        if is_expensive and not rce_cheap_exception:
            # Expensive tariff zone — block grid charging
            if grid > 200 and pv < load * 0.5:
                # Importing significantly from grid AND low PV
                if self._charging_enabled is not False:
                    if await self._throttled_action("w0_block_charge"):
                        await self._em.force_discharge()
                        self._charging_enabled = False
                        msg = f"W0: Grid Import Guard — blokada ładowania (G13={g13_zone.value}, {g13_price:.2f} PLN)"
                        actions.append(msg)
                        self._log_decision("w0_block", msg)
        elif is_expensive and rce_cheap_exception:
            # Expensive G13 but RCE is very cheap → allow
            if self._charging_enabled is False:
                if await self._throttled_action("w0_rce_exception"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    msg = f"W0: RCE wyjątek ({rce_mwh:.0f} PLN/MWh) — ładowanie dozwolone mimo drogiej taryfy"
                    actions.append(msg)
                    self._log_decision("w0_rce_exception", msg)

        return actions

    # ------------------------------------------------------------------
    #  W4 — Voltage Cascade
    # ------------------------------------------------------------------

    async def _execute_w4_voltage_cascade(
        self, v_l1: float, v_l2: float, v_l3: float, soc: float,
    ) -> list[str]:
        """W4: Voltage protection cascade."""
        actions: list[str] = []
        max_v = max(v_l1, v_l2, v_l3)

        if max_v > VOLTAGE_THRESHOLD_CRITICAL:
            if await self._throttled_action("v_cascade_t3"):
                result = await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = True
                msg = f"W4: ⚡ Napięcie krytyczne {max_v:.0f}V — kaskada T3 (bojler+AC+ładowanie)"
                actions.append(msg)
                self._log_decision("voltage_t3", msg)

        elif max_v > VOLTAGE_THRESHOLD_HIGH:
            if await self._throttled_action("v_cascade_t2"):
                result = await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = True
                msg = f"W4: ⚡ Napięcie wysokie {max_v:.0f}V — kaskada T2 (bojler+AC)"
                actions.append(msg)
                self._log_decision("voltage_t2", msg)

        elif max_v > VOLTAGE_THRESHOLD_WARNING:
            if await self._throttled_action("v_cascade_t1"):
                result = await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = True
                msg = f"W4: ⚡ Napięcie podwyższone {max_v:.0f}V — kaskada T1 (bojler)"
                actions.append(msg)
                self._log_decision("voltage_t1", msg)

        elif max_v < VOLTAGE_THRESHOLD_RECOVERY and self._voltage_cascade_active:
            if await self._throttled_action("v_cascade_recovery"):
                result = await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = False
                msg = f"W4: ✅ Napięcie znormalizowane {max_v:.0f}V — odzyskiwanie"
                actions.append(msg)
                self._log_decision("voltage_recovery", msg)

        return actions

    # ------------------------------------------------------------------
    #  W4 — PV Surplus Cascade
    # ------------------------------------------------------------------

    async def _execute_w4_pv_surplus_cascade(
        self, surplus: float, soc: float,
    ) -> list[str]:
        """W4: PV surplus load management cascade."""
        actions: list[str] = []

        if soc < 50 and self._surplus_cascade_active:
            if await self._throttled_action("surplus_emergency_off"):
                result = await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = False
                msg = f"W4: SOC={soc:.0f}% < 50% — wyłączenie odbiorników kaskadowych"
                actions.append(msg)
                self._log_decision("surplus_emergency", msg)
            return actions

        if surplus > PV_SURPLUS_TIER3 and soc >= PV_SURPLUS_MIN_SOC_TIER3:
            if await self._throttled_action("surplus_t3"):
                result = await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = True
                msg = f"W4: ☀️ Nadwyżka {surplus:.0f}W — T3: bojler+AC+gniazdko"
                actions.append(msg)
                self._log_decision("surplus_t3", msg)

        elif surplus > PV_SURPLUS_TIER2 and soc >= PV_SURPLUS_MIN_SOC_TIER2:
            if await self._throttled_action("surplus_t2"):
                result = await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = True
                msg = f"W4: ☀️ Nadwyżka {surplus:.0f}W — T2: bojler+AC"
                actions.append(msg)
                self._log_decision("surplus_t2", msg)

        elif surplus > PV_SURPLUS_TIER1 and soc >= PV_SURPLUS_MIN_SOC_TIER1:
            if await self._throttled_action("surplus_t1"):
                result = await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = True
                msg = f"W4: ☀️ Nadwyżka {surplus:.0f}W — T1: bojler"
                actions.append(msg)
                self._log_decision("surplus_t1", msg)

        elif surplus < PV_SURPLUS_OFF and self._surplus_cascade_active:
            if await self._throttled_action("surplus_off"):
                result = await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = False
                msg = f"W4: Nadwyżka spadła do {surplus:.0f}W — wyłączanie odbiorników"
                actions.append(msg)
                self._log_decision("surplus_off", msg)

        return actions

    # ==================================================================
    #  STRATEGY IMPLEMENTATIONS
    # ==================================================================

    async def _strategy_max_self_consumption(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_zone: G13Zone, g13_price: float, hour: int,
    ) -> list[str]:
        """🟢 Max Self-Consumption: PV→load→battery→grid."""
        actions: list[str] = []

        if surplus > 200 and soc < 95:
            # PV excess → charge battery
            if self._charging_enabled is not True:
                if await self._throttled_action("msc_charge"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    msg = f"🟢 MSC: PV nadwyżka {surplus:.0f}W → ładowanie baterii (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("msc_charge", msg)

        elif pv < load * 0.3 and soc > 15:
            # Low PV → discharge battery to cover load
            if self._charging_enabled is not False:
                if await self._throttled_action("msc_discharge"):
                    await self._em.force_discharge()
                    self._charging_enabled = False
                    msg = f"🟢 MSC: Niskie PV ({pv:.0f}W) → rozładowanie baterii (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("msc_discharge", msg)

        return actions

    async def _strategy_max_profit(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float,
        hour: int, data: dict,
    ) -> list[str]:
        """💰 Max Profit: Arbitrage-focused. Buy low, sell high."""
        actions: list[str] = []

        is_cheap = (
            g13_zone == G13Zone.OFF_PEAK
            or rce_mwh < RCE_PRICE_THRESHOLDS["cheap"]
        )
        is_expensive = (
            g13_zone == G13Zone.AFTERNOON_PEAK
            or rce_mwh > RCE_PRICE_THRESHOLDS["expensive"]
        )

        # W1: G13 Schedule + W2: RCE dynamic
        if is_cheap and soc < 90:
            # Cheap → charge battery (even from grid)
            if self._charging_enabled is not True:
                if await self._throttled_action("mp_cheap_charge"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    msg = f"💰 MP: Tania energia ({g13_price:.2f} PLN, RCE={rce_mwh:.0f}) → ładowanie (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("mp_charge", msg)

        elif is_expensive and soc > 20:
            # Expensive → discharge + export
            if self._charging_enabled is not False:
                if await self._throttled_action("mp_expensive_sell"):
                    await self._em.force_discharge()
                    self._charging_enabled = False
                    msg = f"💰 MP: Droga energia ({g13_price:.2f} PLN, RCE={rce_mwh:.0f}) → sprzedaż (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("mp_sell", msg)

        elif rce_mwh < 0:
            # Negative RCE → charge everything, enable all loads
            if await self._throttled_action("mp_negative_rce"):
                await self._em.force_charge()
                self._charging_enabled = True
                msg = f"💰 MP: Ujemna cena RCE ({rce_mwh:.0f}) — darmowa energia! Ładuj + wszystko ON"
                actions.append(msg)
                self._log_decision("mp_negative_rce", msg)

        else:
            # Normal time → self-consumption
            s_actions = await self._strategy_max_self_consumption(
                soc, pv, load, surplus, g13_zone, g13_price, hour,
            )
            actions.extend(s_actions)

        # Night arbitrage check (W1)
        if hour == 23 and soc < 50:
            forecast_tmr = _safe_float(data.get("pv_forecast_tomorrow_total"))
            if forecast_tmr < NIGHT_ARBITRAGE_MIN_FORECAST:
                if await self._throttled_action("mp_night_arb"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    profit = (DEFAULT_BATTERY_CAPACITY / 1000) * (
                        G13_PRICES[G13Zone.AFTERNOON_PEAK] - G13_PRICES[G13Zone.OFF_PEAK]
                    )
                    msg = (
                        f"💰 MP: Arbitraż nocny — ładowanie z sieci "
                        f"(prognoza jutro: {forecast_tmr:.1f}kWh, zysk: ~{profit:.2f} PLN)"
                    )
                    actions.append(msg)
                    self._log_decision("mp_night_arb", msg)

        # Pre-afternoon-peak smart charging (W5+)
        # If we're in off-peak (13:00-14:59) approaching afternoon peak (15:00),
        # PV is weak and battery low → grid-charge at 0.63 PLN/kWh to:
        # (a) avoid buying at 1.50 during peak
        # (b) sell if RCE spikes in afternoon
        # (c) self-consumption savings during 7h peak window
        if (
            hour in (13, 14)
            and g13_zone == G13Zone.OFF_PEAK
            and soc < 70
            and pv < load * 0.8  # PV can't cover load → no natural charge
        ):
            remaining_forecast = _safe_float(
                data.get("pv_forecast_remaining_today_total")
            )
            rce_next = _safe_float(data.get("sensor.rce_pse_cena_nastepnej_godziny"))
            # Charge if: PV forecast too low to fill naturally,
            # OR RCE is rising (arbitrage opportunity)
            should_charge = (
                remaining_forecast < 3.0  # Less than 3 kWh left today from PV
                or rce_next > rce_mwh * 1.2  # RCE rising 20%+ → sell later
                or soc < 40  # Critical SOC before 7h peak window
            )
            if should_charge:
                if await self._throttled_action("mp_prepeak_fill"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    margin = G13_PRICES[G13Zone.AFTERNOON_PEAK] - G13_PRICES[G13Zone.OFF_PEAK]
                    msg = (
                        f"💰 MP: Pre-peak fill → ładuj baterię z sieci po {G13_PRICES[G13Zone.OFF_PEAK]:.2f} PLN "
                        f"przed szczytem popołudniowym (1.50 PLN). "
                        f"SOC={soc:.0f}%, PV={pv:.0f}W, forecast remaining={remaining_forecast:.1f}kWh, "
                        f"marża arbitrażu: {margin:.2f} PLN/kWh"
                    )
                    actions.append(msg)
                    self._log_decision("mp_prepeak", msg)

        return actions

    async def _strategy_battery_protection(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_price: float, hour: int,
    ) -> list[str]:
        """🔋 Battery Protection: SOC 30-80%, gentle cycling."""
        actions: list[str] = []
        limits = _SOC_LIMITS[AutopilotStrategy.BATTERY_PROTECTION]

        if soc >= limits["max"]:
            # SOC too high — stop charging
            if self._charging_enabled is not False:
                if await self._throttled_action("bp_soc_high"):
                    await self._em.force_discharge()
                    self._charging_enabled = False
                    msg = f"🔋 BP: SOC={soc:.0f}% >= {limits['max']:.0f}% — stop ładowania (ochrona)"
                    actions.append(msg)
                    self._log_decision("bp_high", msg)

        elif soc <= limits["min"]:
            # SOC too low — charge
            if self._charging_enabled is not True:
                if await self._throttled_action("bp_soc_low"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    msg = f"🔋 BP: SOC={soc:.0f}% <= {limits['min']:.0f}% — ładowanie ochronne"
                    actions.append(msg)
                    self._log_decision("bp_low", msg)

        elif surplus > 300 and soc < limits["max"]:
            # PV surplus and room to charge
            if self._charging_enabled is not True:
                if await self._throttled_action("bp_pv_charge"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    msg = f"🔋 BP: PV nadwyżka {surplus:.0f}W → łagodne ładowanie (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("bp_gentle_charge", msg)

        return actions

    async def _strategy_zero_export(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_price: float, hour: int,
    ) -> list[str]:
        """⚡ Zero Export: Never export to grid, store everything."""
        actions: list[str] = []

        if surplus > 100 and soc < 98:
            # Any PV surplus → absorb into battery
            if self._charging_enabled is not True:
                if await self._throttled_action("ze_charge"):
                    await self._em.force_charge()
                    self._charging_enabled = True
                    # Also set zero export limit
                    await self._em.set_export_limit(0)
                    msg = f"⚡ ZE: Nadwyżka {surplus:.0f}W → ładuj baterię (zero eksport)"
                    actions.append(msg)
                    self._log_decision("ze_charge", msg)

        elif pv < load and soc > 15:
            # No surplus → use battery
            if self._charging_enabled is not False:
                if await self._throttled_action("ze_discharge"):
                    await self._em.force_discharge()
                    self._charging_enabled = False
                    msg = f"⚡ ZE: PV < Load → rozładowanie baterii (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("ze_discharge", msg)

        return actions

    async def _strategy_weather_adaptive(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float,
        hour: int, forecast_tomorrow: float, data: dict,
    ) -> list[str]:
        """🌧️ Weather Adaptive: Forecast-driven decisions."""
        actions: list[str] = []
        forecast_today_remaining = _safe_float(data.get("pv_forecast_remaining_today_total"))

        # If good forecast — optimize for profit
        if forecast_today_remaining > 5:
            s_actions = await self._strategy_max_profit(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, data,
            )
            actions.extend(s_actions)
            if s_actions:
                actions.append(f"🌧️ WA: Dobra prognoza ({forecast_today_remaining:.1f}kWh) → tryb Max Zysk")

        elif forecast_today_remaining < 2:
            # Poor forecast — conserve battery
            s_actions = await self._strategy_battery_protection(
                soc, pv, load, surplus, g13_price, hour,
            )
            actions.extend(s_actions)
            if s_actions:
                actions.append(f"🌧️ WA: Słaba prognoza ({forecast_today_remaining:.1f}kWh) → tryb Ochrona")

        else:
            # Moderate forecast — self-consumption
            s_actions = await self._strategy_max_self_consumption(
                soc, pv, load, surplus, g13_zone, g13_price, hour,
            )
            actions.extend(s_actions)

        # Pre-peak battery fill check (W5 logic)
        if hour in (11, 12) and soc < 60 and forecast_today_remaining < 3:
            if await self._throttled_action("wa_prepeak_fill"):
                await self._em.force_charge()
                self._charging_enabled = True
                msg = f"🌧️ WA: Pre-peak fill (SOC={soc:.0f}%, prognoza={forecast_today_remaining:.1f}kWh)"
                actions.append(msg)
                self._log_decision("wa_prepeak", msg)

        return actions

    async def _strategy_ai_full_autonomy(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float,
        hour: int, forecast_tomorrow: float, data: dict,
    ) -> list[str]:
        """🧠 AI Full Autonomy: AI-driven control via JSON toolcalling.

        Every N minutes (default 5), queries AI for structured JSON commands.
        Between AI calls, uses cached commands. Falls back to weather-adaptive
        if AI is unavailable.
        """
        actions: list[str] = []
        now = time.time()

        # Check if AI advisor is available
        if self._ai is None:
            # Fallback: use weather-adaptive strategy
            w_actions = await self._strategy_weather_adaptive(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, forecast_tomorrow, data,
            )
            actions.extend(w_actions)
            return actions

        # Throttle: only call AI every N minutes
        elapsed = now - self._ai_last_call
        if elapsed >= self._ai_call_interval:
            try:
                from .autopilot_engine import build_ai_controller_prompt

                # Get device status from InverterAgent for AI prompt
                device_status_text = self._inverter_agent.format_status_for_prompt()
                prompt = build_ai_controller_prompt(data, device_status_text)
                ai_result = await self._ai.ask_controller(prompt)

                self._ai_last_call = now
                self._ai_cached_commands = ai_result
                self._ai_commands_executed = False  # new commands, not yet executed

                # Update interval from AI response
                next_min = ai_result.get("next_check_minutes", 5)
                self._ai_call_interval = max(60, min(next_min * 60, 1800))  # 1-30 min

                _LOGGER.info(
                    "AI Controller: received %d commands (next check in %d min): %s",
                    len(ai_result.get("commands", [])),
                    next_min,
                    ai_result.get("reasoning", "?"),
                )
            except Exception as err:
                _LOGGER.error("AI Controller call failed: %s", err)
                # Keep using cached commands if available

        # Execute cached AI commands — only ONCE (InverterAgent handles dedup)
        if self._ai_cached_commands and not self._ai_commands_executed:
            reasoning = self._ai_cached_commands.get("reasoning", "")
            commands = self._ai_cached_commands.get("commands", [])

            for cmd in commands:
                tool = cmd.get("tool", "no_action")
                params = cmd.get("params", {})

                # Use InverterAgent for state-aware execution
                result = await self._inverter_agent.execute(tool, params)

                if result.executed:
                    actions.append(result.message)
                    self._log_decision("ai_cmd", result.message)
                elif result.skipped and result.reason:
                    # Log skip only at debug level to avoid spam
                    _LOGGER.debug(
                        "AI CTRL: %s → SKIP (%s)", tool, result.reason
                    )
                    # Still track the decision for UI (but mark as skip)
                    if tool != "no_action":
                        actions.append(f"🧠 AI CTRL: {tool} → ✅ (already active)")
                    else:
                        actions.append(f"🧠 AI CTRL: {result.reason}")

            # Mark as executed — don't re-execute same commands every tick
            self._ai_commands_executed = True

            # Log reasoning as decision
            if reasoning:
                self._log_decision("ai_ctrl", f"🧠 {reasoning}")
        elif not self._ai_cached_commands:
            # No cached commands yet — fallback to weather-adaptive
            w_actions = await self._strategy_weather_adaptive(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, forecast_tomorrow, data,
            )
            actions.extend(w_actions)

        return actions

    async def _execute_ai_command(self, tool: str, params: dict) -> str | None:
        """Execute a single AI command. Returns action description or None."""
        prefix = "🧠 AI CTRL" if not self._ai_dry_run else "🧠 AI DRY-RUN"

        try:
            if tool == "force_charge":
                if not self._ai_dry_run:
                    await self._em.force_charge()
                    self._charging_enabled = True
                msg = f"{prefix}: force_charge → ładowanie baterii"
                self._log_decision("ai_cmd", msg)
                return msg

            elif tool == "force_discharge":
                if not self._ai_dry_run:
                    await self._em.force_discharge()
                    self._charging_enabled = False
                msg = f"{prefix}: force_discharge → rozładowanie baterii"
                self._log_decision("ai_cmd", msg)
                return msg

            elif tool == "set_dod":
                dod = int(params.get("dod", 80))
                dod = max(0, min(dod, 95))  # clamp to GoodWe max (95%)
                if not self._ai_dry_run:
                    await self._em._set_dod(dod)
                msg = f"{prefix}: set_dod({dod}%) → głębokość rozładowania"
                self._log_decision("ai_cmd", msg)
                return msg

            elif tool == "set_export_limit":
                limit = int(params.get("limit", 0))
                limit = max(0, min(limit, 10000))  # clamp
                if not self._ai_dry_run:
                    await self._em.set_export_limit(limit)
                msg = f"{prefix}: set_export_limit({limit}W)"
                self._log_decision("ai_cmd", msg)
                return msg

            elif tool == "switch_on":
                entity = params.get("entity", "")
                entity_id = self._resolve_entity(entity)
                if entity_id:
                    if not self._ai_dry_run:
                        await self._em._switch_on(entity_id)
                    msg = f"{prefix}: switch_on({entity}) → włączono"
                    self._log_decision("ai_cmd", msg)
                    return msg

            elif tool == "switch_off":
                entity = params.get("entity", "")
                entity_id = self._resolve_entity(entity)
                if entity_id:
                    if not self._ai_dry_run:
                        await self._em._switch_off(entity_id)
                    msg = f"{prefix}: switch_off({entity}) → wyłączono"
                    self._log_decision("ai_cmd", msg)
                    return msg

            else:
                _LOGGER.warning("AI Controller: unknown tool '%s'", tool)

        except Exception as err:
            _LOGGER.error("AI Controller: failed to execute %s: %s", tool, err)
            return f"{prefix}: ❌ {tool} failed: {err}"

        return None

    @staticmethod
    def _resolve_entity(name: str) -> str | None:
        """Map friendly AI entity name to HA entity_id."""
        _MAP = {
            "boiler": SWITCH_BOILER,
            "bojler": SWITCH_BOILER,
            "ac": SWITCH_AC,
            "klimatyzacja": SWITCH_AC,
            "socket2": SWITCH_SOCKET2,
            "gniazdko": SWITCH_SOCKET2,
        }
        return _MAP.get(name.lower().strip())

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    async def _throttled_action(self, action_name: str) -> bool:
        """Return True if the action can execute (not throttled)."""
        now = time.time()
        last = self._last_action.get(action_name, 0)
        if now - last < ACTION_COOLDOWN:
            return False
        self._last_action[action_name] = now
        return True

    def _log_decision(self, action: str, message: str) -> None:
        """Add to decision log and fire event."""
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "message": message,
            "strategy": self._active_strategy.value,
        }
        self._decision_log.append(entry)
        if len(self._decision_log) > self._max_log_entries:
            self._decision_log = self._decision_log[-self._max_log_entries:]

        # Fire event for live frontend updates
        self.hass.bus.async_fire(f"{DOMAIN}_autopilot_action", entry)
        _LOGGER.info("Autopilot: %s", message)

    # ------------------------------------------------------------------
    #  Automation Manager
    # ------------------------------------------------------------------

    async def _scan_hems_automations(self) -> list[str]:
        """Scan all automation.* entities for HEMS-conflicting ones.

        Detection criteria:
        - Automation config contains service calls to goodwe.*, smartinghome.*
        - Automation config references managed switches (boiler, AC, socket)
        - Automation name/id contains 'hems', 'smartinghome', 'goodwe'

        Returns list of entity_ids that conflict.
        """
        conflicting: list[str] = []

        for state in self.hass.states.async_all("automation"):
            entity_id = state.entity_id
            name = (state.attributes.get("friendly_name") or "").lower()

            # Method 1: Check name for known HEMS keywords
            hems_keywords = (
                "hems", "smartinghome", "smarting home",
                "goodwe", "arbitraż", "arbitrage",
                "voltage protection", "voltage cascade",
                "napięcie", "soc emergency", "soc safety",
                "pv surplus", "nadwyżka pv",
                "morning sell", "midday charge", "night arbitrage",
                "rce cheapest", "rce expensive", "rce cheap",
            )
            if any(kw in name for kw in hems_keywords):
                conflicting.append(entity_id)
                continue

            # Method 2: Check automation config for conflicting services
            try:
                config = await self._get_automation_config(entity_id)
                if config and self._config_has_conflict(config):
                    conflicting.append(entity_id)
            except Exception:
                pass  # skip automations we can't inspect

        return conflicting

    async def _get_automation_config(self, entity_id: str) -> dict | None:
        """Try to get the config/action data for an automation."""
        # HA stores automation configs in its entity registry
        # We can access the raw config by looking at platform data
        try:
            component = self.hass.data.get("automation")
            if not component:
                return None
            # Walk through automation entities
            for entity in component.entities:
                if entity.entity_id == entity_id:
                    raw = entity.action_script
                    if hasattr(raw, "raw_config"):
                        return raw.raw_config
                    # Fallback: check referenced entities
                    if hasattr(entity, "referenced_entities"):
                        return {"_refs": list(entity.referenced_entities)}
            return None
        except Exception:
            return None

    def _config_has_conflict(self, config: dict | list | str) -> bool:
        """Recursively check if an automation config references HEMS services/entities."""
        if isinstance(config, str):
            # Check for conflict service names
            for svc in HEMS_CONFLICT_SERVICES:
                if svc in config:
                    return True
            # Check for managed switch entity IDs
            for sw in HEMS_MANAGED_SWITCHES:
                if sw in config:
                    return True
            return False

        if isinstance(config, list):
            return any(self._config_has_conflict(item) for item in config)

        if isinstance(config, dict):
            for key, val in config.items():
                if self._config_has_conflict(str(key)):
                    return True
                if self._config_has_conflict(val):
                    return True
            return False

        # Check stringified form as fallback
        return self._config_has_conflict(str(config))

    async def _disable_conflicting_automations(self) -> list[str]:
        """Scan and disable HEMS-conflicting automations, remembering which
        ones were active so we can restore later.

        Returns list of entity_ids that were disabled.
        """
        # First restore any previously disabled (in case of strategy switch)
        if self._disabled_automations:
            await self._restore_automations()

        conflicting = await self._scan_hems_automations()
        newly_disabled: list[str] = []

        for entity_id in conflicting:
            state = self.hass.states.get(entity_id)
            if state and state.state == "on":
                # This automation is currently active → disable it
                try:
                    await self.hass.services.async_call(
                        "automation", "turn_off",
                        {"entity_id": entity_id},
                        blocking=True,
                    )
                    newly_disabled.append(entity_id)
                    self._disabled_automations.add(entity_id)
                    name = state.attributes.get("friendly_name", entity_id)
                    _LOGGER.info(
                        "Disabled conflicting automation: %s (%s)",
                        name, entity_id,
                    )
                except Exception as err:
                    _LOGGER.warning(
                        "Failed to disable automation %s: %s",
                        entity_id, err,
                    )

        if newly_disabled:
            self._log_decision(
                "automations_disabled",
                f"Wyłączono {len(newly_disabled)} automatyzacji HEMS: "
                + ", ".join(newly_disabled),
            )

        self._automation_scan_done = True
        return newly_disabled

    async def _restore_automations(self) -> list[str]:
        """Restore all previously disabled automations."""
        restored: list[str] = []

        for entity_id in list(self._disabled_automations):
            try:
                await self.hass.services.async_call(
                    "automation", "turn_on",
                    {"entity_id": entity_id},
                    blocking=True,
                )
                restored.append(entity_id)
                name = (
                    self.hass.states.get(entity_id)
                    and self.hass.states.get(entity_id).attributes.get(
                        "friendly_name", entity_id
                    )
                ) or entity_id
                _LOGGER.info(
                    "Restored automation: %s (%s)", name, entity_id,
                )
            except Exception as err:
                _LOGGER.warning(
                    "Failed to restore automation %s: %s",
                    entity_id, err,
                )

        self._disabled_automations.clear()

        if restored:
            self._log_decision(
                "automations_restored",
                f"Przywrócono {len(restored)} automatyzacji: "
                + ", ".join(restored),
            )

        return restored

    @property
    def disabled_automations(self) -> set[str]:
        """Return entity IDs of currently disabled automations."""
        return set(self._disabled_automations)
