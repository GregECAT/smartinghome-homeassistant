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
from datetime import datetime, timedelta
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
    SOC_CHECK_11_THRESHOLD,
    SOC_CHECK_12_THRESHOLD,
    NIGHT_ARBITRAGE_MIN_FORECAST,
    DEFAULT_BATTERY_CAPACITY,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_BATTERY_SOC,
    SENSOR_BATTERY_POWER,
    SENSOR_PV_POWER,
    SENSOR_LOAD_TOTAL,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_RCE_PRICE,
    SENSOR_RCE_NEXT_HOUR,
    SENSOR_RCE_2H,
    SENSOR_RCE_3H,
    SWITCH_BOILER,
    SWITCH_AC,
    SWITCH_SOCKET2,
)
from .energy_manager import EnergyManager
from .inverter_agent import InverterAgent
from .autopilot_actions import (
    AutopilotAction,
    ActionStatus,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    build_all_actions,
    get_active_action_ids,
)

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
    "modbus.write_register",       # Sofar (and other Modbus-based inverters)
    "smartinghome.force_charge",
    "smartinghome.force_discharge",
    "smartinghome.set_mode",
    "smartinghome.set_export_limit",
}

# Switches managed by StrategyController — automations toggling these conflict
HEMS_MANAGED_SWITCHES = {SWITCH_BOILER, SWITCH_AC, SWITCH_SOCKET2}


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert to float safely."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _build_ai_data(data: dict[str, Any]) -> dict[str, Any]:
    """Translate coordinator entity-ID keys to simplified prompt keys.

    The coordinator uses HA entity IDs (e.g., SENSOR_BATTERY_SOC = 'sensor.battery_state_of_charge')
    but prompt builders expect simplified keys (e.g., 'battery_soc').
    """
    return {
        # Core energy
        "pv_power": _safe_float(data.get(SENSOR_PV_POWER)),
        "load": _safe_float(data.get(SENSOR_LOAD_TOTAL)),
        "battery_soc": _safe_float(data.get(SENSOR_BATTERY_SOC)),
        "battery_power": _safe_float(data.get(SENSOR_BATTERY_POWER)),
        "grid_power": _safe_float(data.get(SENSOR_GRID_POWER_TOTAL)),
        "pv_surplus": _safe_float(data.get("hems_pv_surplus_power")),
        "battery_capacity": DEFAULT_BATTERY_CAPACITY,
        # RCE
        "rce_price": _safe_float(data.get(SENSOR_RCE_PRICE)),
        "rce_sell": _safe_float(data.get("rce_sell_price")),
        "rce_next_hour": data.get(SENSOR_RCE_NEXT_HOUR),
        "rce_2h": data.get(SENSOR_RCE_2H),
        "rce_3h": data.get(SENSOR_RCE_3H),
        "rce_avg_today": data.get("rce_average_today"),
        "rce_min_today": data.get("rce_min_today"),
        "rce_max_today": data.get("rce_max_today"),
        "rce_trend": str(data.get("rce_price_trend", "")),
        # Weather  (from HA weather entity or ecowitt)
        "weather_condition": data.get("weather_condition"),
        "weather_temp": data.get("weather_temp") or data.get("ecowitt_temp"),
        "weather_clouds": data.get("weather_clouds"),
        "weather_humidity": data.get("ecowitt_humidity"),
        "weather_wind_speed": data.get("ecowitt_wind_speed"),
        "weather_pressure": data.get("ecowitt_pressure"),
        # Forecasts
        "forecast_today": _safe_float(data.get("pv_forecast_today_total")),
        "forecast_remaining": _safe_float(data.get("pv_forecast_remaining_today_total")),
        "forecast_tomorrow": _safe_float(data.get("pv_forecast_tomorrow_total")),
        # Daily energy totals (actual kWh today)
        "grid_import_today_kwh": _safe_float(data.get("grid_import_today")),
        "grid_export_today_kwh": _safe_float(data.get("grid_export_today")),
        "pv_today_kwh": _safe_float(data.get("pv_today")),
        # Voltage
        "voltage_l1": _safe_float(data.get(SENSOR_GRID_VOLTAGE_L1)),
    }


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

        # Daily financial balance tracker (reset at midnight)
        self._daily_balance: dict[str, float] = {
            "import_cost": 0.0,
            "export_revenue": 0.0,
            "import_kwh": 0.0,
            "export_kwh": 0.0,
        }
        self._balance_date: str = ""  # YYYY-MM-DD tracking day
        self._last_import_kwh: float = 0.0  # For delta calculation
        self._last_export_kwh: float = 0.0

        # RCE price range: yesterday's data for planning context
        self._rce_yesterday: dict[str, float] = {
            "min": 0.0, "avg": 0.0, "max": 0.0,
        }

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

        # AI Strategist — 24h strategic plan (cron-based)
        self._strategic_plan: dict[str, Any] | None = None
        self._strategist_last_call: float = 0.0
        self._strategist_interval: int = 900  # default 15 min, AI can override
        self._current_block_key: str = ""  # "HH:MM-HH:MM" of currently executing block
        self._block_commands_executed: bool = False  # True when current block commands done

        # Energy history cache (refreshed hourly for AI prompts)
        self._energy_history_cache: list[dict[str, Any]] = []
        self._energy_history_last_fetch: float = 0.0
        self._energy_history_ttl: int = 3600  # cache for 1 hour

        # InverterAgent — state-aware command executor
        self._inverter_agent = InverterAgent(
            hass, energy_manager, dry_run=self._ai_dry_run,
        )

        # Action-based system
        self._all_actions: list[AutopilotAction] = build_all_actions()
        self._action_map: dict[str, AutopilotAction] = {
            a.id: a for a in self._all_actions
        }
        self._action_sensor_overrides: dict[str, dict[str, str]] = {}  # action_id → {slot_key: entity_id}
        self._active_action_ids: set[str] = set()  # currently active action IDs

    def set_ai_advisor(self, ai_advisor: AIAdvisor) -> None:
        """Inject AI advisor reference (called after services setup)."""
        self._ai = ai_advisor
        _LOGGER.info("AI Controller: advisor connected (dry_run=%s)", self._ai_dry_run)

    def set_inverter_brand(self, brand: str) -> None:
        """Set inverter brand for entity discovery."""
        self._inverter_agent._inverter_brand = brand
        _LOGGER.info("InverterAgent: brand set to %s", brand)

    async def _fetch_energy_history(self) -> list[dict[str, Any]]:
        """Fetch 3-day energy history from HA Recorder (cached hourly).

        Returns list of dicts: [{"date": "2026-03-26", "load_kwh": 15.2,
        "pv_kwh": 12.3, "import_kwh": 8.1, "export_kwh": 5.2}, ...]
        """
        now = time.time()
        if (
            self._energy_history_cache
            and (now - self._energy_history_last_fetch) < self._energy_history_ttl
        ):
            return self._energy_history_cache

        history: list[dict[str, Any]] = []
        try:
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
            from homeassistant.components.recorder import get_instance

            end = datetime.now()
            start = end - timedelta(days=3)

            # Entities to query — daily totals from Riemann sum / utility meter
            stat_ids = [
                "sensor.load",                    # Home consumption
                "sensor.today_s_pv_generation",    # PV production
                "sensor.grid_export_daily",        # Import (GoodWe naming inverted)
                "sensor.grid_import_daily",        # Export (GoodWe naming inverted)
            ]

            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                stat_ids,
                "day",    # period
                None,     # units
                {"sum"},  # types — we want cumulative sum
            )

            # Process: group by date, compute daily max (sum resets daily)
            daily: dict[str, dict[str, float]] = {}
            for entity_id, entries in stats.items():
                for entry in entries:
                    ts = entry.get("start")
                    if ts is None:
                        continue
                    if isinstance(ts, (int, float)):
                        date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                    else:
                        date_str = str(ts)[:10]

                    if date_str not in daily:
                        daily[date_str] = {
                            "load_kwh": 0, "pv_kwh": 0,
                            "import_kwh": 0, "export_kwh": 0,
                        }

                    val = entry.get("sum", 0) or 0
                    if "load" in entity_id:
                        daily[date_str]["load_kwh"] = max(daily[date_str]["load_kwh"], float(val))
                    elif "pv_generation" in entity_id:
                        daily[date_str]["pv_kwh"] = max(daily[date_str]["pv_kwh"], float(val))
                    elif "grid_export" in entity_id:
                        # GoodWe: grid_export_daily = your import
                        daily[date_str]["import_kwh"] = max(daily[date_str]["import_kwh"], float(val))
                    elif "grid_import" in entity_id:
                        # GoodWe: grid_import_daily = your export
                        daily[date_str]["export_kwh"] = max(daily[date_str]["export_kwh"], float(val))

            # Sort by date descending (most recent first)
            for date_str in sorted(daily.keys(), reverse=True):
                d = daily[date_str]
                d["date"] = date_str
                history.append(d)

            self._energy_history_cache = history[:3]  # Max 3 days
            self._energy_history_last_fetch = now
            _LOGGER.debug("Energy history fetched: %d days", len(self._energy_history_cache))

        except ImportError:
            _LOGGER.warning("HA Recorder not available — energy history will be empty")
        except Exception as exc:
            _LOGGER.warning("Failed to fetch energy history: %s", exc)

        return self._energy_history_cache

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

        # Update active action set based on strategy preset
        self._active_action_ids = get_active_action_ids(strategy)
        self._update_action_statuses()

        # Disable conflicting automations
        await self._disable_conflicting_automations()

        label = AUTOPILOT_STRATEGY_LABELS.get(strategy, strategy.value)
        self._log_decision(
            "strategy_change",
            f"Zmieniono strategię: {AUTOPILOT_STRATEGY_LABELS.get(old, old)} → {label}",
        )
        _LOGGER.info(
            "Strategy activated: %s → %s (actions: %d)",
            old, strategy.value, len(self._active_action_ids),
        )

        # Persist full autopilot state
        await self._persist_autopilot_state()

        # Fire event for frontend
        self.hass.bus.async_fire(
            f"{DOMAIN}_strategy_changed",
            {
                "strategy": strategy.value,
                "label": label,
                "previous": old.value,
                "disabled_automations": list(self._disabled_automations),
                "active_actions": list(self._active_action_ids),
            },
        )

    async def deactivate(self) -> None:
        """Deactivate the controller (manual mode)."""
        self._enabled = False

        # Restore previously disabled automations
        restored = await self._restore_automations()

        msg = "Autopilot wyłączony — tryb manualny"
        if restored:
            msg += f" (przywrócono {len(restored)} automatyzacji)"
        self._log_decision("deactivated", msg)
        _LOGGER.info("Strategy controller deactivated, restored: %s", restored)

        # Persist deactivated state
        await self._persist_autopilot_state()

    # ══════════════════════════════════════════════════════════════
    # Action-based system — public API
    # ══════════════════════════════════════════════════════════════

    def _update_action_statuses(self) -> None:
        """Update status of all actions based on active action IDs."""
        for action in self._all_actions:
            if action.id in self._active_action_ids:
                # Action is in the active set — set to waiting (will be set to active on tick)
                if action.status == ActionStatus.DISABLED:
                    continue  # User disabled it manually
                action.status = ActionStatus.WAITING
            else:
                if action.always_active:
                    action.status = ActionStatus.WAITING
                else:
                    action.status = ActionStatus.IDLE

    def get_all_actions_state(self) -> list[dict[str, Any]]:
        """Get serialized state of all actions for frontend.

        Returns list of action dicts grouped by category, with live sensor values.
        """
        result = []
        for action in self._all_actions:
            d = action.to_dict()
            # Add override info
            overrides = self._action_sensor_overrides.get(action.id, {})
            d["sensor_overrides"] = overrides
            d["is_active_in_strategy"] = (
                action.id in self._active_action_ids or action.always_active
            )
            result.append(d)
        return result

    def _get_action_states_for_ai(self) -> dict[str, str]:
        """Get simple action_id → status mapping for AI prompt injection."""
        return {
            action.id: action.status.value
            for action in self._all_actions
        }

    def get_actions_grouped(self) -> dict[str, Any]:
        """Get actions grouped by category with labels, for structured frontend rendering."""
        actions_state = self.get_all_actions_state()
        grouped: dict[str, Any] = {}
        for cat in CATEGORY_ORDER:
            cat_key = str(cat)
            cat_actions = [a for a in actions_state if a["category"] == cat_key]
            active_count = sum(1 for a in cat_actions if a["status"] == ActionStatus.ACTIVE)
            grouped[cat_key] = {
                "label": CATEGORY_LABELS.get(cat, cat_key),
                "actions": cat_actions,
                "total": len(cat_actions),
                "active": active_count,
            }
        return grouped

    async def trigger_action(
        self, action_id: str, source: str = "manual",
    ) -> dict[str, Any]:
        """Trigger a specific action.

        Args:
            action_id: The action to trigger.
            source: "manual" (UI click), "ai" (AI controller), "auto" (condition engine).

        Returns dict with result status and messages.
        """
        action = self._action_map.get(action_id)
        if not action:
            return {"success": False, "error": f"Unknown action: {action_id}"}

        source_labels = {
            "manual": "▶️ Ręczne wyzwolenie",
            "ai": "🧠 AI wyzwolenie",
            "auto": "⚙️ Auto wyzwolenie",
        }
        label = source_labels.get(source, f"🔧 {source}")

        _LOGGER.info("%s trigger: %s (%s)", source, action.name, action_id)
        self._log_decision(
            f"{source}_trigger_{action_id}",
            f"{label}: {action.icon} {action.name}",
        )

        results = []
        for cmd in action.commands:
            tool = cmd.get("tool", "")
            params = cmd.get("params", {})
            try:
                result = await self._execute_command(tool, params)
                results.append(f"✅ {tool}: {result}")
            except Exception as err:
                results.append(f"❌ {tool}: {err}")
                _LOGGER.error("Command %s failed: %s", tool, err)

        action.status = ActionStatus.ACTIVE
        import time
        action.last_triggered = time.time()

        return {"success": True, "action": action_id, "results": results, "status": "ok"}

    async def _execute_command(self, tool: str, params: dict) -> str:
        """Execute a single command from an action's command list."""
        em = self._em

        if tool == "force_charge":
            await em.force_charge()
            return "Force charge started"
        elif tool == "force_discharge":
            await em.force_discharge()
            return "Force discharge started"
        elif tool == "set_dod":
            dod = params.get("dod", 95)
            await em._set_dod(dod)
            return f"DOD set to {dod}%"
        elif tool == "switch_on":
            entity = params.get("entity", "")
            entity_map = {
                "boiler": SWITCH_BOILER,
                "ac": SWITCH_AC,
                "socket2": SWITCH_SOCKET2,
            }
            entity_id = entity_map.get(entity, entity)
            if entity_id:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": entity_id}
                )
            return f"Switch ON: {entity_id}"
        elif tool == "switch_off":
            entity = params.get("entity", "")
            entity_map = {
                "boiler": SWITCH_BOILER,
                "ac": SWITCH_AC,
                "socket2": SWITCH_SOCKET2,
            }
            entity_id = entity_map.get(entity, entity)
            if entity_id:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": entity_id}
                )
            return f"Switch OFF: {entity_id}"
        elif tool == "no_action":
            reason = params.get("reason", "No action needed")
            return reason
        elif tool == "stop_force_charge":
            await em.stop_force_charge()
            return "Stop force charge — general mode restored"
        elif tool == "stop_force_discharge":
            await em.stop_force_discharge()
            return "Stop force discharge — general mode restored"
        elif tool == "emergency_stop":
            await em.emergency_stop()
            return "EMERGENCY STOP — all force operations halted"
        else:
            return f"Unknown tool: {tool}"

    def update_action_sensor(
        self, action_id: str, slot_key: str, entity_id: str
    ) -> bool:
        """Override a sensor slot mapping for a specific action.

        Returns True if successful.
        """
        action = self._action_map.get(action_id)
        if not action:
            _LOGGER.warning("Cannot override sensor: unknown action %s", action_id)
            return False

        # Validate slot exists
        slot_exists = any(s.key == slot_key for s in action.sensor_slots)
        if not slot_exists:
            _LOGGER.warning(
                "Cannot override sensor: unknown slot %s in action %s",
                slot_key, action_id,
            )
            return False

        if action_id not in self._action_sensor_overrides:
            self._action_sensor_overrides[action_id] = {}
        self._action_sensor_overrides[action_id][slot_key] = entity_id

        _LOGGER.info(
            "Sensor override: %s.%s → %s", action_id, slot_key, entity_id,
        )
        return True

    def toggle_action(self, action_id: str, enabled: bool) -> bool:
        """Enable or disable a specific action."""
        action = self._action_map.get(action_id)
        if not action:
            return False

        if enabled:
            action.status = ActionStatus.WAITING
            self._active_action_ids.add(action_id)
        else:
            action.status = ActionStatus.DISABLED
            self._active_action_ids.discard(action_id)

        _LOGGER.info(
            "Action %s: %s", action_id, "enabled" if enabled else "disabled",
        )

        # Persist state change (fire-and-forget, sync via executor)
        self.hass.async_create_task(self._persist_autopilot_state())

        return True

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

        # Update financial tracker on each tick
        self._update_daily_balance(data)

        # ═══════════════════════════════════════════════════════
        # SAFETY LAYERS — always active
        # ═══════════════════════════════════════════════════════

        # W3: SOC Emergency (highest priority)
        if soc < SOC_EMERGENCY:
            # We must execute force_charge frequently (every 60s) to fight the integration auto-reset
            if await self._throttled_action("soc_emergency_execute", cooldown=60):
                await self._em.force_charge()
                self._charging_enabled = True
                actions_taken.append("W3: ⚠️ SOC emergency — wymuszono ładowanie")
                
                # But only log to the UI every 15 minutes to avoid spam
                if await self._throttled_action("soc_emergency_log", cooldown=900):
                    self._log_decision("soc_emergency", f"SOC={soc:.0f}% < {SOC_EMERGENCY}% — ładowanie awaryjne (Modbus BT)")

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

        # ═══════════════════════════════════════════════════════
        # EVALUATE ACTION CONDITIONS — update live statuses
        # ═══════════════════════════════════════════════════════
        self._evaluate_actions(
            soc=soc, pv=pv, load=load, surplus=surplus,
            g13_zone=g13_zone, g13_price=g13_price, rce_mwh=rce_mwh,
            hour=hour, v_l1=v_l1,
            forecast_tomorrow=forecast_tomorrow,
        )

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
            "action_states": self._get_action_states_for_ai(),
        }

    # ------------------------------------------------------------------
    #  Action Condition Evaluation Engine
    # ------------------------------------------------------------------

    def _evaluate_actions(
        self,
        soc: float, pv: float, load: float, surplus: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float,
        hour: int, v_l1: float,
        forecast_tomorrow: float = 0.0,
    ) -> None:
        """Evaluate conditions for all active actions and update statuses.

        Called every tick. Sets action.status to:
        - ACTIVE: conditions met right now
        - WAITING: action enabled but conditions not met
        - IDLE: action not in current preset
        - DISABLED: manually disabled by user
        """
        import time as _time
        from datetime import datetime

        weekday = datetime.now().weekday()
        is_weekend = weekday >= 5

        # Condition map: action_id → bool (True = conditions met)
        conditions: dict[str, bool] = {
            # W0: Safety
            "grid_import_guard": (
                g13_zone != G13Zone.OFF_PEAK
                and rce_mwh > 100
            ),
            "pv_surplus_charge": (
                surplus > 300  # export > 300W
                and g13_zone != G13Zone.OFF_PEAK
            ),

            # W1: G13 Schedule
            "sell_07": (
                g13_zone == G13Zone.MORNING_PEAK
                and soc > 20
                and not is_weekend
            ),
            "charge_13": (
                g13_zone == G13Zone.OFF_PEAK
                and 13 <= hour < 16
                and soc < 90
                and not is_weekend
            ),
            "evening_peak": (
                g13_zone == G13Zone.AFTERNOON_PEAK
                and soc > 15
            ),
            "weekend": is_weekend,

            # W2: RCE Dynamic
            "night_arbitrage": (
                22 <= hour or hour < 7
                and soc < 50
                and forecast_tomorrow < NIGHT_ARBITRAGE_MIN_FORECAST
            ),
            "cheapest_window": False,  # Requires binary sensor, evaluated externally
            "most_expensive_window": False,  # Requires binary sensor
            "low_price_charge": (
                rce_mwh < RCE_PRICE_THRESHOLDS["cheap"]
                and soc < 85
            ),
            "high_price_sell": (
                rce_mwh > RCE_PRICE_THRESHOLDS["expensive"]
                and soc > 20
            ),
            "rce_peak_g13": (
                rce_mwh > RCE_PRICE_THRESHOLDS.get("very_expensive", 500)
                and g13_zone == G13Zone.AFTERNOON_PEAK
                and soc > 20
            ),
            "negative_price": rce_mwh < 0,

            # W3: SOC Safety
            "soc_check_11": (hour == 11 and soc < SOC_CHECK_11_THRESHOLD),
            "soc_check_12": (hour == 12 and soc < SOC_CHECK_12_THRESHOLD),
            "smart_soc_protection": (soc < 20),
            "soc_emergency": (soc < SOC_EMERGENCY),

            # W4: Voltage
            "voltage_boiler": (v_l1 > VOLTAGE_THRESHOLD_WARNING),
            "voltage_klima": (v_l1 > VOLTAGE_THRESHOLD_HIGH),
            "voltage_critical": (v_l1 > VOLTAGE_THRESHOLD_CRITICAL),

            # W4: PV Surplus
            "surplus_boiler": (surplus > PV_SURPLUS_TIER1 and soc > PV_SURPLUS_MIN_SOC_TIER1),
            "surplus_klima": (surplus > PV_SURPLUS_TIER2 and soc > PV_SURPLUS_MIN_SOC_TIER2),
            "surplus_gniazdko": (surplus > PV_SURPLUS_TIER3 and soc > PV_SURPLUS_MIN_SOC_TIER3),
            "surplus_emergency_off": (soc < 50),

            # W5: Weather
            "morning_check_0530": (5 <= hour < 7 and soc < 80),
            "ecowitt_check_1000": (hour == 10 and soc < 60),
            "last_chance_1330": (hour == 13 and soc < 80),
            "prepeak_summer_1800": (hour == 18 and soc < 70),
            "sudden_clouds": (pv < 200 and soc < 70 and 8 <= hour <= 17),
            "rain_priority": False,  # Requires rain sensor
            "weak_forecast_dod": (forecast_tomorrow < 5.0),
            "restore_dod": (pv > 500),
        }

        now_ts = _time.time()

        for action in self._all_actions:
            if action.status == ActionStatus.DISABLED:
                continue

            is_in_preset = action.id in self._active_action_ids or action.always_active

            if not is_in_preset:
                if action.status != ActionStatus.IDLE:
                    action.status = ActionStatus.IDLE
                continue

            # Check condition
            cond_met = conditions.get(action.id, False)

            # Recently triggered? Keep ACTIVE for 4 minutes
            recently_triggered = (
                action.last_triggered > 0
                and (now_ts - action.last_triggered) < 240
            )

            if cond_met or recently_triggered:
                if action.status != ActionStatus.ACTIVE:
                    action.status = ActionStatus.ACTIVE
            else:
                if action.status != ActionStatus.WAITING:
                    action.status = ActionStatus.WAITING
                    _changed = True  # noqa: F841

        # Fire event for frontend (every tick — lightweight payload)
        self.hass.bus.async_fire(
            f"{DOMAIN}_action_states",
            {
                a.id: a.status.value
                for a in self._all_actions
            },
        )

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
                await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = True
                msg = f"W4: ⚡ Napięcie krytyczne {max_v:.0f}V — kaskada T3 (bojler+AC+ładowanie)"
                actions.append(msg)
                self._log_decision("voltage_t3", msg)

        elif max_v > VOLTAGE_THRESHOLD_HIGH:
            if await self._throttled_action("v_cascade_t2"):
                await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = True
                msg = f"W4: ⚡ Napięcie wysokie {max_v:.0f}V — kaskada T2 (bojler+AC)"
                actions.append(msg)
                self._log_decision("voltage_t2", msg)

        elif max_v > VOLTAGE_THRESHOLD_WARNING:
            if await self._throttled_action("v_cascade_t1"):
                await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
                self._voltage_cascade_active = True
                msg = f"W4: ⚡ Napięcie podwyższone {max_v:.0f}V — kaskada T1 (bojler)"
                actions.append(msg)
                self._log_decision("voltage_t1", msg)

        elif max_v < VOLTAGE_THRESHOLD_RECOVERY and self._voltage_cascade_active:
            if await self._throttled_action("v_cascade_recovery"):
                await self._em.check_voltage_protection(v_l1, v_l2, v_l3, soc)
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
                await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = False
                msg = f"W4: SOC={soc:.0f}% < 50% — wyłączenie odbiorników kaskadowych"
                actions.append(msg)
                self._log_decision("surplus_emergency", msg)
            return actions

        if surplus > PV_SURPLUS_TIER3 and soc >= PV_SURPLUS_MIN_SOC_TIER3:
            if await self._throttled_action("surplus_t3"):
                await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = True
                msg = f"W4: ☀️ Nadwyżka {surplus:.0f}W — T3: bojler+AC+gniazdko"
                actions.append(msg)
                self._log_decision("surplus_t3", msg)

        elif surplus > PV_SURPLUS_TIER2 and soc >= PV_SURPLUS_MIN_SOC_TIER2:
            if await self._throttled_action("surplus_t2"):
                await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = True
                msg = f"W4: ☀️ Nadwyżka {surplus:.0f}W — T2: bojler+AC"
                actions.append(msg)
                self._log_decision("surplus_t2", msg)

        elif surplus > PV_SURPLUS_TIER1 and soc >= PV_SURPLUS_MIN_SOC_TIER1:
            if await self._throttled_action("surplus_t1"):
                await self._em.check_pv_surplus(surplus, soc)
                self._surplus_cascade_active = True
                msg = f"W4: ☀️ Nadwyżka {surplus:.0f}W — T1: bojler"
                actions.append(msg)
                self._log_decision("surplus_t1", msg)

        elif surplus < PV_SURPLUS_OFF and self._surplus_cascade_active:
            if await self._throttled_action("surplus_off"):
                await self._em.check_pv_surplus(surplus, soc)
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
        """🧠 AI Full Autonomy — Strategist + Executor architecture.

        Strategist (cron): generates 24h plan with time_blocks every 15-60 min.
        Executor (tick):   reads cached plan, finds current block, dispatches
                           commands via InverterAgent (state-aware, no API call).

        Falls back to quick AI controller if plan is stale or unavailable.
        """
        actions: list[str] = []
        now = time.time()

        # Check if AI advisor is available
        if self._ai is None:
            w_actions = await self._strategy_weather_adaptive(
                soc, pv, load, surplus, g13_zone, g13_price,
                rce_mwh, hour, forecast_tomorrow, data,
            )
            actions.extend(w_actions)
            return actions

        # ── STRATEGIST CRON ──────────────────────────────────────
        # Run AI Strategist if interval elapsed
        strategist_elapsed = now - self._strategist_last_call
        if strategist_elapsed >= self._strategist_interval:
            await self._run_ai_strategist(data)

        # ── EXECUTOR ─────────────────────────────────────────────
        # Find current time block from cached strategic plan
        block = self._find_current_time_block()

        if block:
            block_key = f"{block['start']}-{block['end']}"

            # If we entered a new block, reset execution flag
            if block_key != self._current_block_key:
                self._current_block_key = block_key
                self._block_commands_executed = False
                _LOGGER.info(
                    "AI Executor: entered block %s (%s) — %s",
                    block_key,
                    block.get("strategy", "?"),
                    block.get("reasoning", "")[:80],
                )

            # Execute block commands ONCE per block
            if not self._block_commands_executed:
                commands = block.get("commands", [])
                for cmd in commands:
                    tool = cmd.get("tool", "no_action")
                    params = cmd.get("params", {})

                    result = await self._inverter_agent.execute(tool, params)

                    if result.executed:
                        actions.append(result.message)
                        self._log_decision("ai_exec", result.message)
                    elif result.skipped and result.reason:
                        _LOGGER.debug("AI Executor: %s → SKIP (%s)", tool, result.reason)
                        if tool != "no_action":
                            actions.append(f"🧠 AI Exec: {tool} → ✅ (already active)")
                        else:
                            actions.append(f"🧠 AI Exec: {result.reason}")

                self._block_commands_executed = True

                # Log block reasoning
                reasoning = block.get("reasoning", "")
                if reasoning:
                    self._log_decision("ai_exec", f"🧠 Block {block_key}: {reasoning}")

            else:
                # ── STATE-DRIFT DETECTION ────────────────────────
                # Check every tick if device state matches plan expectations
                # This catches manual overrides by user or external automations
                drift_cooldown = getattr(self, "_drift_last_reexec", 0)
                if now - drift_cooldown >= 120:  # Max once per 120s
                    strategy = block.get("strategy", "")
                    device_status = self._inverter_agent.get_device_status()
                    work_mode = device_status.get("work_mode", "")
                    bat_power = device_status.get("battery_power", 0)

                    # Detect drift: check if inverter work_mode matches plan expectations
                    # This catches manual overrides by user or external automations
                    # Strategy → expected work_mode mapping:
                    #   aggressive_charge/charge/night_charge → eco_charge
                    #   discharge_self_consume → general (battery powers house naturally)
                    #   discharge → eco_discharge (aggressive sell to grid)
                    _STRATEGY_EXPECTED_MODES = {
                        "aggressive_charge": ("eco_charge",),
                        "charge": ("eco_charge",),
                        "night_charge": ("eco_charge",),
                        "discharge_self_consume": ("general",),
                        "discharge": ("eco_discharge",),
                    }
                    drifted = False
                    expected_modes = _STRATEGY_EXPECTED_MODES.get(strategy)
                    if expected_modes and work_mode not in expected_modes and work_mode not in ("unknown", ""):
                        drifted = True
                        _LOGGER.warning(
                            "AI Executor: STATE DRIFT — plan=%s expects mode=%s but got mode=%s, bat=%dW → re-executing",
                            strategy, expected_modes, work_mode, bat_power,
                        )

                    if drifted:
                        self._drift_last_reexec = now
                        self._block_commands_executed = False  # Force re-execution on next tick
                        self._log_decision("drift", f"⚠️ State drift detected — re-applying block {block_key}")
                        actions.append(f"⚠️ Drift: re-applying {block_key}")

            # Show current block info
            actions.append(
                f"🧠 Plan: block {block_key} ({block.get('zone', '?')}, "
                f"{block.get('strategy', '?')})"
            )

        elif self._strategic_plan:
            # Plan exists but no matching block (edge case)
            actions.append("🧠 Plan: no matching time block for current time")
        else:
            # No strategic plan available — fallback to quick AI controller
            await self._fallback_quick_ai(data, actions, now)

        # AI reasoning for frontend
        if self._strategic_plan:
            analysis = self._strategic_plan.get("analysis", "")
            if analysis:
                self._ai_cached_commands = {"reasoning": analysis}

        return actions

    # ------------------------------------------------------------------
    #  AI STRATEGIST — cron-based deep 24h planning
    # ------------------------------------------------------------------

    async def _run_ai_strategist(self, data: dict) -> None:
        """Run AI Strategist — generates 24h strategic plan with time_blocks.

        Called on cron (every 15-60 min). Produces a structured plan that
        the Executor follows tick by tick without needing AI API calls.
        """
        if self._ai is None:
            return

        try:
            from .autopilot_engine import build_ai_strategist_prompt, AutopilotEngine

            # Fetch energy history (cached, max once per hour)
            energy_history = await self._fetch_energy_history()

            # ALWAYS compute fresh estimation for the Strategist
            ai_data = _build_ai_data(data)
            ai_data["daily_balance"] = self._daily_balance
            ai_data["rce_yesterday"] = self._rce_yesterday
            ai_data["energy_history_3d"] = energy_history

            bat_cap = float(data.get("battery_capacity_wh") or data.get("sensor.battery_capacity") or DEFAULT_BATTERY_CAPACITY)
            engine = AutopilotEngine(battery_capacity_wh=bat_cap)
            estimation = await self.hass.async_add_executor_job(
                engine.estimate_strategy, self._active_strategy, ai_data
            )
            _LOGGER.info(
                "Auto-estimation before Strategist: savings=%.2f, import=%.1fkWh, export=%.1fkWh",
                estimation.get("net_savings", 0),
                estimation.get("total_import_kwh", 0),
                estimation.get("total_export_kwh", 0),
            )

            device_status_text = self._inverter_agent.format_status_for_prompt()
            action_states = self._get_action_states_for_ai()

            prompt = build_ai_strategist_prompt(ai_data, estimation, device_status_text, action_states, self._active_strategy.value, self._decision_log)
            plan = await self._ai.ask_controller(prompt, raw_json=True, max_tokens=16384)

            if plan and plan.get("time_blocks"):
                self._strategic_plan = plan
                self._strategist_last_call = time.time()

                # Reset block execution tracking
                self._current_block_key = ""
                self._block_commands_executed = False

                # Update strategist interval from AI response
                next_min = plan.get("next_analysis_minutes", 15)
                self._strategist_interval = max(300, min(next_min * 60, 3600))

                # Persist plan to settings.json
                await self._persist_strategic_plan(plan)

                _LOGGER.info(
                    "AI Strategist: generated %d time-blocks (next in %d min): %s",
                    len(plan["time_blocks"]),
                    next_min,
                    plan.get("analysis", "?")[:100],
                )
                self._log_decision(
                    "ai_strategist",
                    f"🧠 Strategist: {len(plan['time_blocks'])} bloków — {plan.get('analysis', '')[:80]}",
                )
            else:
                _LOGGER.warning("AI Strategist: no time_blocks in response")
        except Exception as err:
            _LOGGER.error("AI Strategist call failed: %s", err)

    async def _persist_strategic_plan(self, plan: dict) -> None:
        """Save strategic plan to settings.json."""
        from .settings_io import write_sync

        def _do_persist() -> None:
            try:
                write_sync(self.hass, {"ai_strategic_plan": plan})
            except Exception as err:
                _LOGGER.warning("Failed to persist strategic plan: %s", err)

        await self.hass.async_add_executor_job(_do_persist)

    def _find_current_time_block(self) -> dict | None:
        """Find the time_block matching the current time."""
        if not self._strategic_plan or not self._strategic_plan.get("time_blocks"):
            return None

        now = datetime.now()
        now_minutes = now.hour * 60 + now.minute

        for block in self._strategic_plan["time_blocks"]:
            try:
                start_parts = block["start"].split(":")
                end_parts = block["end"].split(":")
                start_min = int(start_parts[0]) * 60 + int(start_parts[1])
                end_min = int(end_parts[0]) * 60 + int(end_parts[1])

                # Handle midnight wrap (e.g., 21:00 - 07:00)
                if end_min <= start_min:
                    if now_minutes >= start_min or now_minutes < end_min:
                        return block
                else:
                    if start_min <= now_minutes < end_min:
                        return block
            except (ValueError, KeyError, IndexError):
                continue

        return None

    async def _fallback_quick_ai(
        self, data: dict, actions: list[str], now: float,
    ) -> None:
        """Fallback: quick AI controller call if strategist plan unavailable."""
        elapsed = now - self._ai_last_call
        if elapsed >= self._ai_call_interval:
            try:
                from .autopilot_engine import build_ai_controller_prompt

                device_status_text = self._inverter_agent.format_status_for_prompt()
                ai_data = _build_ai_data(data)
                ai_data["daily_balance"] = self._daily_balance
                ai_data["rce_yesterday"] = self._rce_yesterday
                action_states = self._get_action_states_for_ai()
                prompt = build_ai_controller_prompt(ai_data, device_status_text, action_states, self._active_strategy.value, self._decision_log)
                ai_result = await self._ai.ask_controller(prompt)

                self._ai_last_call = now
                self._ai_cached_commands = ai_result
                self._ai_commands_executed = False

                next_min = ai_result.get("next_check_minutes", 5)
                self._ai_call_interval = max(60, min(next_min * 60, 1800))

                _LOGGER.info(
                    "AI Controller (fallback): %d commands (next in %d min)",
                    len(ai_result.get("commands", [])),
                    next_min,
                )
            except Exception as err:
                _LOGGER.error("AI Controller fallback failed: %s", err)

        # Execute cached quick commands
        if self._ai_cached_commands and not self._ai_commands_executed:
            commands = self._ai_cached_commands.get("commands", [])
            for cmd in commands:
                action_id = cmd.get("action")
                tool = cmd.get("tool", "no_action")
                params = cmd.get("params", {})

                if action_id:
                    result_msg = await self._execute_ai_command(tool, params, action=action_id)
                else:
                    result = await self._inverter_agent.execute(tool, params)
                    if result.executed:
                        result_msg = result.message
                        self._log_decision("ai_cmd", result.message)
                    elif result.skipped and tool != "no_action":
                        result_msg = f"🧠 AI CTRL: {tool} → ✅ (already active)"
                    else:
                        result_msg = None

                if result_msg:
                    actions.append(result_msg)

            self._ai_commands_executed = True

    async def _execute_ai_command(self, tool: str, params: dict, action: str | None = None) -> str | None:
        """Execute a single AI command. Returns action description or None.

        Supports both:
          - {"action": "action_id"} → find action in registry, run its commands
          - {"tool": "tool_name", "params": {...}} → raw tool call
        """
        prefix = "🧠 AI CTRL" if not self._ai_dry_run else "🧠 AI DRY-RUN"

        # Handle action-based commands
        if action:
            # Dedup: skip if action was already triggered recently (within 4 min)
            action_obj = self._action_map.get(action)
            if action_obj and action_obj.last_triggered:
                import time
                elapsed = time.time() - action_obj.last_triggered
                if elapsed < 240:  # 4 min cooldown
                    _LOGGER.debug(
                        "AI Controller: action '%s' skipped (triggered %.0fs ago)",
                        action, elapsed,
                    )
                    return None

            try:
                result = await self.trigger_action(action, source="ai")
                msg = f"{prefix}: action({action}) → {result.get('status', 'ok')}"
                self._log_decision("ai_action", msg)
                return msg
            except Exception as err:
                _LOGGER.error("AI Controller: action '%s' failed: %s", action, err)
                return f"{prefix}: ❌ action({action}) failed: {err}"

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

    async def _throttled_action(self, action_name: str, cooldown: int | None = None) -> bool:
        """Return True if the action can execute (not throttled).

        Args:
            action_name: unique key for this action (for dedup).
            cooldown: optional per-action cooldown in seconds.
                      Falls back to ACTION_COOLDOWN (60s) if not specified.
        """
        now = time.time()
        last = self._last_action.get(action_name, 0)
        cd = cooldown if cooldown is not None else ACTION_COOLDOWN
        if now - last < cd:
            return False
        self._last_action[action_name] = now
        return True

    # ------------------------------------------------------------------
    #  Full autopilot state persistence & restore
    # ------------------------------------------------------------------

    async def _persist_autopilot_state(self) -> None:
        """Persist full autopilot state to settings.json.

        Saved keys:
          - autopilot_active_strategy: str (enum value)
          - autopilot_enabled: bool
          - autopilot_active_action_ids: list[str]
          - autopilot_disabled_action_ids: list[str]
        """
        from .settings_io import write_sync

        disabled_ids = [
            a.id for a in self._all_actions
            if a.status == ActionStatus.DISABLED
        ]

        updates = {
            "autopilot_active_strategy": self._active_strategy.value,
            "autopilot_enabled": self._enabled,
            "autopilot_active_action_ids": list(self._active_action_ids),
            "autopilot_disabled_action_ids": disabled_ids,
        }

        def _do_write() -> None:
            try:
                write_sync(self.hass, updates)
            except Exception as err:
                _LOGGER.warning("Failed to persist autopilot state: %s", err)

        await self.hass.async_add_executor_job(_do_write)

    async def restore_state(self) -> None:
        """Restore full autopilot state from settings.json on startup.

        Called from __init__.py after the controller is created.
        """
        from .settings_io import read_sync

        try:
            settings = await self.hass.async_add_executor_job(
                read_sync, self.hass
            )
        except Exception as err:
            _LOGGER.debug("Could not read settings for autopilot restore: %s", err)
            return

        # Restore strategy
        saved_strategy = settings.get("autopilot_active_strategy", "")
        saved_enabled = settings.get("autopilot_enabled", False)
        saved_active_ids = settings.get("autopilot_active_action_ids", [])
        saved_disabled_ids = settings.get("autopilot_disabled_action_ids", [])

        if not saved_strategy or not saved_enabled:
            _LOGGER.debug("No active autopilot strategy to restore")
            return

        try:
            strategy = AutopilotStrategy(saved_strategy)
        except ValueError:
            _LOGGER.warning("Invalid saved strategy: %s", saved_strategy)
            return

        # Restore strategy and enabled flag (without re-triggering automation scan)
        self._active_strategy = strategy
        self._enabled = True

        # Restore action IDs
        if saved_active_ids:
            self._active_action_ids = set(saved_active_ids)
        else:
            self._active_action_ids = get_active_action_ids(strategy)

        # Restore disabled actions
        for action_id in saved_disabled_ids:
            action = self._action_map.get(action_id)
            if action:
                action.status = ActionStatus.DISABLED
                self._active_action_ids.discard(action_id)

        self._update_action_statuses()

        _LOGGER.info(
            "Restored autopilot: strategy=%s, enabled=%s, %d active actions, %d disabled",
            strategy.value, saved_enabled,
            len(self._active_action_ids), len(saved_disabled_ids),
        )

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

    def _update_daily_balance(self, data: dict[str, Any]) -> None:
        """Track daily import cost / export revenue from sensor deltas.

        Uses grid_import_today / grid_export_today sensors (cumulative kWh).
        At day boundary: saves today's RCE range as 'yesterday' and resets.
        """
        today = datetime.now().strftime("%Y-%m-%d")

        # Day boundary detection — save yesterday's RCE and reset
        if today != self._balance_date:
            # Rotate RCE: today → yesterday
            rce_min = _safe_float(data.get("rce_min_today"))
            rce_avg = _safe_float(data.get("rce_average_today"))
            rce_max = _safe_float(data.get("rce_max_today"))
            if rce_avg > 0:
                self._rce_yesterday = {
                    "min": rce_min, "avg": rce_avg, "max": rce_max,
                }

            # Reset daily balance
            self._daily_balance = {
                "import_cost": 0.0,
                "export_revenue": 0.0,
                "import_kwh": 0.0,
                "export_kwh": 0.0,
            }
            self._last_import_kwh = _safe_float(data.get("grid_import_today"))
            self._last_export_kwh = _safe_float(data.get("grid_export_today"))
            self._balance_date = today
            return

        # Calculate deltas from cumulative daily sensors
        current_import = _safe_float(data.get("grid_import_today"))
        current_export = _safe_float(data.get("grid_export_today"))

        delta_import = max(0, current_import - self._last_import_kwh)
        delta_export = max(0, current_export - self._last_export_kwh)

        if delta_import > 0 or delta_export > 0:
            # Get current prices for cost calculation
            now = datetime.now()
            g13_zone = _get_g13_zone(now.hour, now.month, now.weekday())
            g13_price = G13_PRICES.get(g13_zone, 0.63)
            rce_sell = _safe_float(data.get("rce_sell_price"))

            self._daily_balance["import_kwh"] += delta_import
            self._daily_balance["import_cost"] += delta_import * g13_price
            self._daily_balance["export_kwh"] += delta_export
            self._daily_balance["export_revenue"] += delta_export * rce_sell

        self._last_import_kwh = current_import
        self._last_export_kwh = current_export

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
