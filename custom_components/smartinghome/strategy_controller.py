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
from homeassistant.util import dt as dt_util

from .arbitrage import (
    ACT_CHARGE_GRID,
    ACT_DISCHARGE,
    ACT_HOLD,
    ACT_PV_CHARGE,
    ACTION_LABELS,
    ArbitragePlan,
    ArbitrageParams,
    build_inputs,
    optimize,
    rce_hourly,
    rce_slots,
)

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
    RCE_PROSUMER_COEFFICIENT,
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

    SOC_GRID_IMPORT_THRESHOLD,
    GRID_IMPORT_DETECT_THRESHOLD_W,
    SOC_CHECK_11_THRESHOLD,
    SOC_CHECK_12_THRESHOLD,
    NIGHT_ARBITRAGE_MIN_FORECAST,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_BATTERY_MIN_SOC,
    DEFAULT_PEAK_SELL_SOC_PERCENT,
    PEAK_SELL_SOC_MIN,
    PEAK_SELL_SOC_MAX,
    PEAK_SELL_SOC_FLOOR,
    PEAK_SELL_SETTINGS_KEY,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_BATTERY_SOC,
    SENSOR_BATTERY_POWER,
    SENSOR_PV_POWER,
    SENSOR_LOAD_TOTAL,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_RCE_PRICE,
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


_ZONE_LABELS = {
    "off_peak": "tania",
    "morning_peak": "przedpołudniowa",
    "afternoon_peak": "szczyt",
    "flat": "całodobowa",
}


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
        # AI convention: +import / -export (meter reports +export) — same as cron_scheduler
        "grid_power": -_safe_float(data.get(SENSOR_GRID_POWER_TOTAL)),
        "pv_surplus": _safe_float(data.get("hems_pv_surplus_power")),
        "battery_capacity": DEFAULT_BATTERY_CAPACITY,
        # RCE
        "rce_price": _safe_float(data.get(SENSOR_RCE_PRICE)),
        "rce_sell": _safe_float(data.get("rce_sell_price")),
        "rce_next_period": data.get("rce_sell_price_next_hour"),
        "rce_2h": data.get("rce_sell_price_2h"),
        "rce_3h": data.get("rce_sell_price_3h"),
        "rce_avg_today": data.get("rce_average_today"),
        "rce_min_today": data.get("rce_min_today"),
        "rce_max_today": data.get("rce_max_today"),
        "rce_median_today": data.get("rce_median_today"),
        "rce_trend": str(data.get("rce_price_trend", "")),
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
        # Tariff config (for dynamic prompt rendering)
        "tariff_type": data.get("tariff_type", "g13"),
        "energy_provider": data.get("energy_provider", "tauron"),
        # Phase 1: Battery health & diagnostics
        "battery_soh": data.get("battery_soh"),
        "battery_health": data.get("battery_health_score"),
        "battery_charge_limit_a": data.get("battery_charge_limit_a"),
        "battery_discharge_limit_a": data.get("battery_discharge_limit_a"),
        # Phase 1: Inverter thermal
        "inverter_thermal": data.get("inverter_thermal_status"),
        "inverter_temp_radiator": data.get("inverter_temp_radiator"),
        # Phase 1: Grid quality
        "grid_power_factor": data.get("grid_power_factor"),
        "grid_quality": data.get("grid_quality"),
        # Phase 1: Backup / UPS
        "backup_load_w": data.get("backup_load_w"),
        "ups_load_pct": data.get("ups_load_pct"),
        # Phase 1: Diagnostics
        "has_errors": data.get("has_active_errors"),
        "diag_status_code": data.get("diag_status_code"),
        "ems_mode": data.get("ems_mode"),
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
        self._block_charge_completed: bool = False  # True when charge block reached SOC target → switched to general

        # Energy history cache (refreshed hourly for AI prompts)
        self._energy_history_cache: list[dict[str, Any]] = []
        self._energy_history_last_fetch: float = 0.0
        self._energy_history_ttl: int = 3600  # cache for 1 hour

        # Peak Sell — active energy export during expensive afternoon peak
        self._peak_sell_soc_percent: int = DEFAULT_PEAK_SELL_SOC_PERCENT
        self._peak_sell_phase: str = ""  # "sell" or "reserve" — tracks current phase
        self._peak_sell_soc_at_entry: float = 0.0  # SOC when entering peak zone

        # ── Arbitrage planner (💰 Max Zysk) ──
        self._arb_params = ArbitrageParams()
        self._arb_plan: ArbitragePlan | None = None
        self._arb_plan_ts: float = 0.0
        self._arb_plan_key: tuple = ()
        self._arb_cmd: tuple[str, int] | None = None
        self._arb_cmd_ts: float = 0.0
        self._arb_log_key: tuple = ()
        self._arb_status: dict[str, Any] = {}
        self._arb_drift_since: float = 0.0
        self._arb_planning: bool = False  # a plan is being computed
        # Action committed for the current hour: (hour key, action, power W)
        self._arb_commit: tuple[str, str, int] | None = None
        self._arb_intent: str = ""  # EnergyManager.intent right after our last command
        self._load_profile: list[float | None] = [None] * 24  # kW per hour of day
        self._load_profile_loaded: bool = False
        self._load_profile_hour: int = -1
        self._load_stats: list[float | None] = [None] * 24  # kW, recorder hourly means
        self._load_stats_day: Any = None

        # ── Manual hold: panel force buttons pause the autopilot ──
        self._manual_hold_until: float = 0.0

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

        # Schedule Manager integration
        self._schedule_managed: bool = False  # True when ScheduleManager controls this controller

    def set_ai_advisor(self, ai_advisor: AIAdvisor) -> None:
        """Inject AI advisor reference (called after services setup)."""
        self._ai = ai_advisor
        _LOGGER.info("AI Controller: advisor connected (dry_run=%s)", self._ai_dry_run)

    @property
    def energy_manager(self) -> EnergyManager:
        """Shared EnergyManager (configured with the inverter brand)."""
        return self._em

    def set_inverter_brand(self, brand: str) -> None:
        """Set inverter brand for entity discovery."""
        self._inverter_agent._inverter_brand = brand
        _LOGGER.info("InverterAgent: brand set to %s", brand)

    def set_schedule_managed(self, managed: bool) -> None:
        """Set whether ScheduleManager controls this controller.

        When True, the controller knows that its activation/deactivation
        is driven by the hourly schedule — not by the user directly.
        """
        self._schedule_managed = managed
        _LOGGER.debug("StrategyController: schedule_managed=%s", managed)

    @property
    def schedule_managed(self) -> bool:
        """Whether the schedule manager is controlling this controller."""
        return self._schedule_managed

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

    async def set_peak_sell_soc_percent(self, percent: int) -> None:
        """Set peak sell SOC percentage (called from panel.js via service).

        Args:
            percent: 0-80, what % of battery SOC to actively sell to grid
                     during the most expensive afternoon peak.
        """
        self._peak_sell_soc_percent = max(
            PEAK_SELL_SOC_MIN, min(int(percent), PEAK_SELL_SOC_MAX)
        )
        self._log_decision(
            "peak_sell_config",
            f"Peak sell ustawiony na {self._peak_sell_soc_percent}% baterii do sprzedaży w szczycie",
        )
        _LOGGER.info(
            "Peak sell SOC percent set to %d%%", self._peak_sell_soc_percent,
        )
        await self._persist_autopilot_state()

    @property
    def peak_sell_soc_percent(self) -> int:
        """Current peak sell SOC percentage."""
        return self._peak_sell_soc_percent

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
        states = {
            action.id: action.status.value
            for action in self._all_actions
        }
        # Expose peak sell setting so AI knows the user's preference
        states["__peak_sell_soc_percent"] = str(self._peak_sell_soc_percent)
        return states

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
        elif tool == "battery_to_home":
            await em.battery_to_home()
            return "Battery powers home (no charging, no export)"
        elif tool == "battery_hold":
            await em.battery_hold()
            return "Battery hold (idle)"
        elif tool == "set_dod":
            dod = params.get("dod", 95)
            # Safety net: values < 50 are almost certainly confused with SOC target
            if isinstance(dod, (int, float)) and dod < 50:
                corrected = min(100 - int(dod), 95)
                _LOGGER.warning("set_dod(%s) auto-corrected to %d%% (DOD = capacity available)", dod, corrected)
                dod = corrected
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
        # Próg krytyczny ZAWSZE 5% (DEFAULT_BATTERY_MIN_SOC) — spójny we wszystkich strefach.
        # Poniżej tego progu dozwolone ładowanie z sieci (jedyny automatyczny wyjątek).
        soc_emergency_threshold = DEFAULT_BATTERY_MIN_SOC  # Zawsze 5%
        if soc < soc_emergency_threshold:
            # We must execute frequently (every 60s) to fight the integration auto-reset
            if await self._throttled_action("soc_emergency_execute", cooldown=60):
                if pv > 100:
                    # Dzień: PV jest — użyj general mode, PV naładuje baterię
                    # BEZ pobierania z sieci na baterię. Dom ma priorytet.
                    await self._em.charge_pv_only()
                    self._charging_enabled = True
                    actions_taken.append(
                        f"W3: ⚠️ SOC emergency — charge_pv_only (PV={pv:.0f}W, SOC={soc:.0f}%)"
                    )
                else:
                    # Noc/brak PV — jedyne źródło to sieć
                    await self._em.charge_from_grid()
                    self._charging_enabled = True
                    actions_taken.append(
                        f"W3: ⚠️ SOC emergency — charge_from_grid (PV={pv:.0f}W, brak PV → sieć)"
                    )
                
                # But only log to the UI every 15 minutes to avoid spam
                if await self._throttled_action("soc_emergency_log", cooldown=900):
                    source = "PV-only" if pv > 100 else "sieć"
                    self._log_decision("soc_emergency", f"SOC={soc:.0f}% < {soc_emergency_threshold}% — ładowanie awaryjne [{source}]")

        # Right after start: don't write to the inverter (GoodWe still starting)
        if self._em.in_startup_grace:
            return {
                "enabled": True,
                "strategy": strategy.value,
                "strategy_label": AUTOPILOT_STRATEGY_LABELS.get(strategy, ""),
                "actions": ["⏳ Start — autopilot czeka, aż integracja falownika się uruchomi (do 3 min)"],
                "soc": soc, "pv": pv, "load": load, "surplus": surplus,
                "g13_zone": g13_zone.value, "g13_price": g13_price,
                "rce_price_mwh": rce_mwh, "ai_reasoning": "",
                "timestamp": now.strftime("%H:%M:%S"),
                "action_states": self._get_action_states_for_ai(),
                "manual_hold_until": self._manual_hold_until,
                "arbitrage": self._arb_status if strategy == AutopilotStrategy.MAX_PROFIT else None,
            }

        # Manual command from the panel → only emergency layers until the hold ends
        if self.manual_hold_active:
            mins = int((self._manual_hold_until - time.time()) / 60) + 1
            actions_taken.append(f"✋ Sterowanie ręczne — autopilot wstrzymany jeszcze ~{mins} min")
            return {
                "enabled": True,
                "strategy": strategy.value,
                "strategy_label": AUTOPILOT_STRATEGY_LABELS.get(strategy, ""),
                "actions": actions_taken,
                "soc": soc, "pv": pv, "load": load, "surplus": surplus,
                "g13_zone": g13_zone.value, "g13_price": g13_price,
                "rce_price_mwh": rce_mwh, "ai_reasoning": "",
                "timestamp": now.strftime("%H:%M:%S"),
                "action_states": self._get_action_states_for_ai(),
                "manual_hold_until": self._manual_hold_until,
                "arbitrage": self._arb_status if strategy == AutopilotStrategy.MAX_PROFIT else None,
            }

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

        # W6: Thermal Throttling — protect inverter from overheating
        thermal_status = data.get("inverter_thermal_status", "ok")
        if thermal_status in ("hot", "critical"):
            if await self._throttled_action("thermal_throttle", cooldown=300):
                radiator_temp = data.get("inverter_temp_radiator", 0)
                if thermal_status == "critical":
                    # Critical: stop all forced operations, go to general
                    await self._em.set_general_mode()
                    actions_taken.append(
                        f"W6: 🔥 THERMAL CRITICAL ({radiator_temp}°C) — emergency set_general, all force ops stopped"
                    )
                    self._log_decision("thermal_critical", f"Radiator {radiator_temp}°C — awaryjne zatrzymanie")
                else:
                    # Hot: only block force_discharge, allow gentle charging
                    actions_taken.append(
                        f"W6: 🌡️ THERMAL HOT ({radiator_temp}°C) — force_discharge blocked, gentle mode"
                    )
                    self._log_decision("thermal_hot", f"Radiator {radiator_temp}°C — ograniczenie mocy")

        # W7: SOH Battery Protection — gentle cycling for degraded batteries
        battery_health = data.get("battery_health_score", "good")
        if battery_health in ("warning", "critical"):
            if await self._throttled_action("soh_protection", cooldown=600):
                soh_val = data.get("battery_soh", 0)
                if battery_health == "critical":
                    # Critical SOH: restrict DOD to 70, avoid force operations
                    await self._em._set_dod(70)
                    actions_taken.append(
                        f"W7: ⚠️ SOH CRITICAL ({soh_val}%) — DOD limited to 70%, gentle cycling only"
                    )
                    self._log_decision("soh_critical", f"SOH={soh_val}% — ochrona baterii, DOD=70")
                else:
                    actions_taken.append(
                        f"W7: 🔋 SOH WARNING ({soh_val}%) — monitoring, prefer gentle cycles"
                    )

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
            data=data,
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
            "manual_hold_until": 0,
            "arbitrage": self._arb_status if strategy == AutopilotStrategy.MAX_PROFIT else None,
        }

    async def execute_safety_only(self, data: dict[str, Any]) -> dict[str, Any]:
        """Execute only safety layers (W0, W3, W4) — no strategy logic.

        Used by ScheduleManager when the system is in manual mode.
        Safety layers must ALWAYS run regardless of operating mode.

        Args:
            data: merged raw + computed sensor data from coordinator.

        Returns:
            dict with safety actions taken.
        """
        now = datetime.now()
        hour = now.hour
        month = now.month
        weekday = now.weekday()

        soc = _safe_float(data.get(SENSOR_BATTERY_SOC))
        pv = _safe_float(data.get(SENSOR_PV_POWER))
        load = _safe_float(data.get(SENSOR_LOAD_TOTAL))
        grid = _safe_float(data.get(SENSOR_GRID_POWER_TOTAL))
        v_l1 = _safe_float(data.get(SENSOR_GRID_VOLTAGE_L1))
        v_l2 = _safe_float(data.get(SENSOR_GRID_VOLTAGE_L2))
        v_l3 = _safe_float(data.get(SENSOR_GRID_VOLTAGE_L3))
        rce_mwh = _safe_float(data.get(SENSOR_RCE_PRICE))
        surplus = max(pv - load, 0)

        g13_zone = _get_g13_zone(hour, month, weekday)

        actions_taken: list[str] = []

        # W3: SOC Emergency
        from .const import DEFAULT_BATTERY_MIN_SOC
        soc_emergency_threshold = DEFAULT_BATTERY_MIN_SOC  # Zawsze 5% — spójny próg awaryjny
        if soc < soc_emergency_threshold:
            if await self._throttled_action("soc_emergency_execute", cooldown=60):
                if pv > 100:
                    await self._em.charge_pv_only()
                else:
                    await self._em.charge_from_grid()
                source = "PV-only" if pv > 100 else "sieć"
                actions_taken.append(
                    f"W3: ⚠️ SOC emergency — ładowanie [{source}] (SOC={soc:.0f}%)"
                )
                if await self._throttled_action("soc_emergency_log", cooldown=900):
                    self._log_decision(
                        "soc_emergency",
                        f"SOC={soc:.0f}% < {soc_emergency_threshold}% — ładowanie awaryjne [{source}] (manual mode)",
                    )

        # W0: Grid Import Guard
        w0_actions = await self._execute_w0_grid_import_guard(
            soc, pv, load, grid, g13_zone,
            G13_PRICES.get(g13_zone, 0.63), rce_mwh, hour,
        )
        actions_taken.extend(w0_actions)

        # W4: Voltage cascade
        if pv > 50:
            v_actions = await self._execute_w4_voltage_cascade(v_l1, v_l2, v_l3, soc)
            actions_taken.extend(v_actions)

        # W4: PV Surplus cascade
        surplus_actions = await self._execute_w4_pv_surplus_cascade(surplus, soc)
        actions_taken.extend(surplus_actions)

        return {
            "enabled": False,
            "safety_only": True,
            "strategy": "manual_schedule",
            "actions": actions_taken,
            "soc": soc,
            "pv": pv,
            "surplus": surplus,
            "timestamp": now.strftime("%H:%M:%S"),
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
        data: dict[str, Any] | None = None,
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
                and 0 < self._hours_until_expensive_zone(hour, datetime.now().month, weekday) <= 2
                and soc < 90
                and not is_weekend
                and self._should_grid_charge_before_peak(soc, pv, load, data or {})
            ),
            "evening_peak": (
                g13_zone == G13Zone.AFTERNOON_PEAK
                and soc > 15
            ),
            "weekend": is_weekend,

            # W2: RCE Dynamic
            "night_arbitrage": (
                (22 <= hour or hour < 7)
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
                and soc > (DEFAULT_BATTERY_MIN_SOC if g13_zone == G13Zone.AFTERNOON_PEAK else 20)
            ),
            "rce_peak_g13": (
                rce_mwh > RCE_PRICE_THRESHOLDS.get("very_expensive", 500)
                and g13_zone == G13Zone.AFTERNOON_PEAK
                and soc > DEFAULT_BATTERY_MIN_SOC
            ),
            "negative_price": rce_mwh < 0,

            # W3: SOC Safety
            "soc_check_11": (hour == 11 and soc < SOC_CHECK_11_THRESHOLD),
            "soc_check_12": (hour == 12 and soc < SOC_CHECK_12_THRESHOLD),
            "smart_soc_protection": (soc < DEFAULT_BATTERY_MIN_SOC),  # Spójny próg 5%
            "soc_emergency": (soc < DEFAULT_BATTERY_MIN_SOC),  # Spójny próg 5%

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
    #  Helper: hours until expensive zone & PV-aware grid charge decision
    # ------------------------------------------------------------------

    def _hours_until_expensive_zone(
        self, hour: int, month: int, weekday: int,
    ) -> float:
        """Calculate hours until next AFTERNOON_PEAK zone starts.

        Returns a float: e.g. 6.0 if peak starts in 6 hours.
        Returns 24.0 if no upcoming peak (weekend, already past peak).
        """
        if weekday >= 5:  # Weekend — no expensive zones
            return 24.0

        schedule = G13_WINTER_SCHEDULE if month in WINTER_MONTHS else G13_SUMMER_SCHEDULE
        for (start, end), zone in schedule.items():
            if zone == G13Zone.AFTERNOON_PEAK:
                if hour < start:
                    return float(start - hour)
                elif start <= hour < end:
                    return 0.0  # Already in expensive zone
        return 24.0  # No upcoming peak found (past all peaks today)

    def _should_grid_charge_before_peak(
        self,
        soc: float,
        pv: float,
        load: float,
        data: dict,
    ) -> bool:
        """Determine if grid charging is justified before peak.

        Returns False (= DON'T charge from grid) if:
        - PV forecast remaining is sufficient to charge battery naturally
        - PV is currently producing enough surplus to charge battery
        Returns True (= charge from grid) if:
        - PV forecast is insufficient to fill battery before peak
        - PV production is weak relative to load
        """
        forecast_remaining = _safe_float(
            data.get("pv_forecast_remaining_today_total")
        )
        bat_cap_kwh = DEFAULT_BATTERY_CAPACITY / 1000
        # Energy needed to charge from current SOC to ~95%
        energy_needed_kwh = (95 - soc) / 100 * bat_cap_kwh * 0.9

        if energy_needed_kwh <= 0:
            return False  # Battery already near full

        # PV forecast remaining can cover battery needs with 30% margin
        if forecast_remaining > energy_needed_kwh * 1.3:
            _LOGGER.debug(
                "Pre-peak PV check: forecast_remaining=%.1fkWh > needed=%.1fkWh*1.3 → skip grid charge",
                forecast_remaining, energy_needed_kwh,
            )
            return False

        # PV currently producing surplus (PV > load * 1.2) AND SOC already reasonable
        if pv > load * 1.2 and soc > 50:
            _LOGGER.debug(
                "Pre-peak PV check: PV=%.0fW > load*1.2=%.0fW and SOC=%.0f%% > 50%% → skip grid charge",
                pv, load * 1.2, soc,
            )
            return False

        _LOGGER.debug(
            "Pre-peak PV check: forecast=%.1fkWh, needed=%.1fkWh, PV=%.0fW, load=%.0fW → GRID CHARGE justified",
            forecast_remaining, energy_needed_kwh, pv, load,
        )
        return True  # Weak PV forecast / low production → charge from grid

    # ------------------------------------------------------------------
    #  W0 — Grid Import Guard
    # ------------------------------------------------------------------

    async def _execute_w0_grid_import_guard(
        self,
        soc: float, pv: float, load: float, grid: float,
        g13_zone: G13Zone, g13_price: float, rce_mwh: float,
        hour: int,
    ) -> list[str]:
        """W0: BEZWZGLĘDNA blokada importu z sieci gdy SOC > 5%.

        ZASADA NADRZĘDNA:
        Jeśli SOC > SOC_GRID_IMPORT_THRESHOLD (5%), system MUSI korzystać
        z baterii zamiast z sieci. Jedyny wyjątek: ręczne wymuszenie
        ładowania (ManualMode.CHARGE_FROM_GRID).

        Dodatkowa logika dla drogich stref G13 (jak dotychczas).
        """
        actions: list[str] = []

        # Deliberate grid charging (arbitrage, manual, W3), holding or selling
        if self._em.intent in ("charge_grid", "hold", "sell") or self.manual_hold_active:
            return actions

        # ══════════════════════════════════════════════════════════
        # GUARD NADRZĘDNY: Zero Grid Import gdy SOC > 5%
        # ══════════════════════════════════════════════════════════
        battery_available = soc > SOC_GRID_IMPORT_THRESHOLD
        # Meter convention: +export / -import → import is the negative part
        grid_import_w = -grid
        grid_importing = grid_import_w > GRID_IMPORT_DETECT_THRESHOLD_W
        pv_insufficient = pv < load * 0.8  # PV nie pokrywa popytu

        if battery_available and grid_importing and pv_insufficient and self._em.ems_state() == "auto":
            # Already in general mode — the battery gives what it can; the rest is the
            # house above the battery's max power. Re-setting the mode every 30 s
            # changes nothing, so note it once per 30 min instead.
            # < 500 W is the battery catching up with a load step — nothing to report
            if grid_import_w >= 500 and await self._throttled_action("w0_battery_max", cooldown=1800):
                bat_state = self.hass.states.get(SENSOR_BATTERY_POWER)
                bat_w = _safe_float(bat_state.state if bat_state else None)
                at_max = bat_w >= 0.8 * self._arb_params.discharge_kw * 1000
                msg = (
                    f"W0: 🔋 Bateria na maksymalnej mocy ({bat_w:.0f} W) — dom {load:.0f} W, "
                    f"{grid_import_w:.0f} W z sieci (powyżej mocy baterii)"
                    if at_max else
                    f"W0: ⚠️ Tryb ogólny, a bateria oddaje tylko {bat_w:.0f} W przy SOC {soc:.0f}% — "
                    f"{grid_import_w:.0f} W z sieci (limit BMS / temperatura?)"
                )
                actions.append(msg)
                self._log_decision("w0_battery_max", msg)
            return actions
        if battery_available and grid_importing and pv_insufficient:
            # System pobiera z sieci mimo dostępnej baterii!
            # Przełącz na general mode — bateria zasili dom naturalnie
            # NIE używamy force_discharge bo to SPRZEDAJE do sieci!
            if await self._throttled_action("w0_zero_grid", cooldown=30):
                await self._em.set_general_mode()
                self._charging_enabled = False
                msg = (
                    f"W0: 🛡️ ZERO GRID — SOC={soc:.0f}% > {SOC_GRID_IMPORT_THRESHOLD}%, "
                    f"grid_import={grid_import_w:.0f}W → set_general_mode "
                    f"(bateria zasila dom, BEZ sprzedaży do sieci)"
                )
                actions.append(msg)
                self._log_decision("w0_zero_grid", msg)
                return actions  # Guard nadrzędny — nie wykonuj dalszej logiki

        # ══════════════════════════════════════════════════════════
        # GUARD DODATKOWY: Ochrona w drogich strefach G13
        # ══════════════════════════════════════════════════════════
        is_expensive = g13_zone in (G13Zone.MORNING_PEAK, G13Zone.AFTERNOON_PEAK)
        rce_cheap_exception = rce_mwh < RCE_PRICE_THRESHOLDS["very_cheap"]

        if is_expensive and not rce_cheap_exception:
            # Expensive tariff zone — block grid charging
            if grid_import_w > 200 and pv < load * 0.5:
                # Importing significantly from grid AND low PV
                if self._charging_enabled is not False:
                    if await self._throttled_action("w0_block_charge"):
                        await self._em.battery_to_home()
                        self._charging_enabled = False
                        msg = f"W0: Grid Import Guard — blokada ładowania (G13={g13_zone.value}, {g13_price:.2f} PLN)"
                        actions.append(msg)
                        self._log_decision("w0_block", msg)
        elif is_expensive and rce_cheap_exception:
            # Very cheap RCE only lowers what exported PV is worth — a G13 buyer still
            # pays the tariff (0.91 / 1.50 zł). So store PV surplus instead of selling
            # it, but never buy from the grid here (12:46 today it forced EMS
            # charge_battery at full power in the morning peak and fought the plan).
            if self._charging_enabled is False and pv > load:
                if await self._throttled_action("w0_rce_exception"):
                    await self._em.charge_pv_only()
                    self._charging_enabled = True
                    msg = f"W0: RCE {rce_mwh:.0f} PLN/MWh — nadwyżka PV do baterii zamiast sprzedaży (bez poboru z sieci)"
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
    #  PEAK SELL — Active energy export during expensive peak
    # ==================================================================

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
            # PV excess → charge battery (PV only, no grid!)
            if self._charging_enabled is not True:
                if await self._throttled_action("msc_charge"):
                    await self._em.charge_pv_only()
                    self._charging_enabled = True
                    msg = f"🟢 MSC: PV nadwyżka {surplus:.0f}W → charge_pv_only (SOC={soc:.0f}%)"
                    actions.append(msg)
                    self._log_decision("msc_charge", msg)

        elif pv < load * 0.3 and soc > 15:
            # Low PV → discharge battery to cover load
            if self._charging_enabled is not False:
                if await self._throttled_action("msc_discharge"):
                    await self._em.battery_to_home()
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
        """💰 Max Zysk — battery arbitrage planned hour by hour (arbitrage.py).

        Charges in cheap tariff hours (and from PV), keeps the energy instead of
        spending it at the cheapest rate, and uses it where it is worth most:
        covering the house in expensive tariff hours or selling at the best RCE
        hours — only when the margin beats battery wear + the minimum profit.
        """
        actions: list[str] = []
        now = dt_util.now()  # tz-aware: plan hours stay correct across DST changes
        self._update_load_profile(now.hour, load)
        await self._refresh_arbitrage_plan(soc, data, now)
        plan = self._arb_plan
        first = plan.first if plan else None
        if first is None:
            return actions

        action, power_w = self._commit_hour_action(now, first.action, first.power_w, soc)
        if action == ACT_DISCHARGE and first.no_import:
            # Tariff peak: a fixed discharge power must cover the house as it is
            # now plus the planned export — never leave part of the house on the grid
            live_deficit = max(load - pv, 0.0)
            max_w = self._arb_params.discharge_kw * 1000
            power_w = int(min(max(power_w, live_deficit + first.export_w), max_w))
        cmd = (action, int(round(power_w / 500.0)) * 500)
        changed = cmd != self._arb_cmd
        if (
            changed and action == ACT_DISCHARGE and first.no_import
            and self._arb_cmd and self._arb_cmd[0] == action
            and abs(power_w - self._arb_cmd[1]) < 500
            and time.time() - self._arb_cmd_ts < 90
        ):
            # Peak power follows the house: small swings don't rewrite the inverter
            # every 30 s (re-set only on a ≥ 500 W change or after 90 s)
            changed = False
        drifted = self._arbitrage_drifted(action) if not changed else False
        if drifted:
            self._log_decision(
                "arbitrage_drift",
                f"⚠️ Falownik nie wykonał polecenia ({ACTION_LABELS.get(action, action)}, "
                f"EMS={self._em.ems_state()}) — ponawiam",
            )
        if changed or drifted or time.time() - self._arb_cmd_ts > 900:  # re-assert every 15 min
            await self._apply_arbitrage(action, power_w)
            self._arb_intent = self._em.intent
            self._arb_drift_since = 0.0
            self._arb_cmd, self._arb_cmd_ts = cmd, time.time()
            self._charging_enabled = action in (ACT_CHARGE_GRID, ACT_PV_CHARGE)
            power = f" {power_w} W" if power_w else ""
            label = ACTION_LABELS.get(action, action)
            if action == ACT_DISCHARGE and first.no_import:
                label = "💰 Dom z baterii + sprzedaż"
                power = f" {first.export_w} W (razem {power_w} W)"
            elif action == ACT_DISCHARGE and first.export_w < 100:
                label = "🔋 Bateria stałą mocą"  # rationing before a pricier peak, no export
            msg = (
                f"💰 Arbitraż: {label}{power} — "
                f"strefa {_ZONE_LABELS.get(first.zone, first.zone)}, "
                f"zakup {first.buy:.2f} / sprzedaż {first.sell:.2f} zł/kWh, "
                f"SOC {first.soc_start:.0f}→{first.soc_end:.0f}% "
                f"(plan 30 h: {plan.baseline_cost - plan.total_cost:+.2f} zł vs bateria bez pracy)"
            )
            actions.append(msg)
            # Log a decision, not every power adjustment of the same plan step
            log_key = (action, first.start, first.export_w // 200)
            if changed and log_key != self._arb_log_key:
                self._arb_log_key = log_key
                self._log_decision("arbitrage", msg)
        return actions

    def _commit_hour_action(
        self, now: datetime, action: str, power_w: int, soc: float
    ) -> tuple[str, int]:
        """Keep one action per 30-min block (no flapping on every re-plan).

        Re-plans every few minutes can flip between near-equal options (e.g.
        "charge now, sell at 7:00" vs "sell now"). The first decision of the
        block stands unless it became infeasible (battery full / at its floor).
        """
        # 30-min blocks (:00–:29 / :30–:59) — a good price may last only half an hour
        hour_key = now.strftime("%Y%m%d%H") + ("a" if now.minute < 30 else "b")
        commit = self._arb_commit
        p = self._arb_params
        # Battery-in vs battery-out actions don't flip inside a block (seen 10:35–10:49:
        # home → PV charge → sell 700 W → PV charge on near-equal re-plans)
        opposite = {
            ACT_CHARGE_GRID: {ACT_DISCHARGE},
            ACT_PV_CHARGE: {ACT_DISCHARGE},
            ACT_DISCHARGE: {ACT_CHARGE_GRID, ACT_PV_CHARGE},
        }
        if commit and commit[0] == hour_key and action in opposite.get(commit[1], ()):
            held = commit[1]
            infeasible = (
                (held in (ACT_CHARGE_GRID, ACT_PV_CHARGE) and soc >= p.max_soc - 1)
                or (held == ACT_DISCHARGE and soc <= p.peak_floor_soc + 1)
            )
            if not infeasible:
                return held, commit[2]
        self._arb_commit = (hour_key, action, power_w)
        return action, power_w

    _EXPECTED_EMS = {
        ACT_CHARGE_GRID: "charge_battery",
        ACT_DISCHARGE: "discharge_battery",
        ACT_HOLD: "battery_standby",
    }

    def _arbitrage_drifted(self, action: str) -> bool:
        """EMS mode differs from what the plan commanded for > 90 s."""
        if self._em.intent != self._arb_intent:
            self._arb_drift_since = 0.0
            return False  # another layer (W3, manual) took over — don't fight it
        actual = self._em.ems_state()
        if actual in (None, "unavailable", "unknown"):
            self._arb_drift_since = 0.0
            return False  # watchdog handles unavailability
        expected = self._EXPECTED_EMS.get(action, "auto")
        if actual == expected:
            self._arb_drift_since = 0.0
            return False
        now = time.time()
        if not self._arb_drift_since:
            self._arb_drift_since = now
            return False
        if now - self._arb_drift_since > 90:
            self._arb_drift_since = 0.0
            return True
        return False

    def on_inverter_healed(self, message: str) -> None:
        """Watchdog reloaded the inverter integration — log and re-assert the plan."""
        self._log_decision("inverter_watchdog", message)
        self._arb_cmd = None

    async def _apply_arbitrage(self, action: str, power_w: int) -> None:
        if action == ACT_CHARGE_GRID:
            # Full power: the plan assumed 90 %, so the battery is ready early;
            # the next re-plan stops grid charging once the target is reached.
            await self._em.charge_from_grid(power_w=None)
        elif action == ACT_DISCHARGE:
            await self._em.force_discharge(power_w=power_w or None)
        elif action == ACT_HOLD:
            await self._em.battery_hold()
        else:  # home / pv_charge — battery follows the house, PV charges it
            await self._em.set_general_mode()

    async def _recorder_load_profile(self, now: datetime) -> list[float | None]:
        """Average house load per hour of day over the last 14 days (kW) from HA statistics."""
        out: list[float | None] = [None] * 24
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import statistics_during_period

            start = dt_util.as_utc(now - timedelta(days=14))
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, start, None,
                {SENSOR_LOAD_TOTAL}, "hour", None, {"mean"},
            )
        except Exception as err:  # noqa: BLE001 — recorder missing / no statistics
            _LOGGER.debug("Load profile from recorder unavailable: %s", err)
            return out
        buckets: list[list[float]] = [[] for _ in range(24)]
        for row in stats.get(SENSOR_LOAD_TOTAL, []):
            mean = row.get("mean")
            begin = row.get("start")
            if mean is None or begin is None:
                continue
            if isinstance(begin, (int, float)):
                begin = datetime.fromtimestamp(begin, tz=dt_util.UTC)
            buckets[dt_util.as_local(begin).hour].append(float(mean))
        for h, values in enumerate(buckets):
            if values:
                out[h] = max(sum(values) / len(values) / 1000, 0.1)
        return out

    def _update_load_profile(self, hour: int, load_w: float) -> None:
        """Learn average house load per hour of day (EMA, kW)."""
        if load_w <= 0:
            return
        kw = load_w / 1000
        prev = self._load_profile[hour]
        self._load_profile[hour] = kw if prev is None else prev + 0.02 * (kw - prev)

    async def _refresh_arbitrage_plan(self, soc: float, data: dict, now: datetime) -> None:
        key = (now.hour, now.minute // 30, int(soc // 3))
        if self._arb_planning:
            return  # never stack plan computations (slow host / long horizon)
        if self._arb_plan and key == self._arb_plan_key and time.time() - self._arb_plan_ts < 300:
            return
        self._arb_planning = True
        try:
            await self._compute_arbitrage_plan(soc, data, now, key)
        finally:
            self._arb_planning = False

    async def _compute_arbitrage_plan(self, soc: float, data: dict, now: datetime, key: tuple) -> None:
        from .settings_io import read_async, write_async

        settings = await read_async(self.hass)

        if not self._load_profile_loaded:
            saved = settings.get("arbitrage_load_profile") or []
            for h, v in enumerate(saved[:24]):
                if self._load_profile[h] is None and isinstance(v, (int, float)) and v > 0:
                    self._load_profile[h] = float(v)
            self._load_profile_loaded = True
        if now.hour != self._load_profile_hour:
            self._load_profile_hour = now.hour
            await write_async(self.hass, {
                "arbitrage_load_profile": [round(v, 3) if v else None for v in self._load_profile],
            })
        if self._load_stats_day != now.date():
            self._load_stats_day = now.date()
            self._load_stats = await self._recorder_load_profile(now)
        known = [v for v in self._load_profile if v]
        default_kw = sum(known) / len(known) if known else 1.0
        # Hours not learned yet: average of that hour in the last 14 days (HA history)
        profile = [
            v if v else (self._load_stats[h] or default_kw)
            for h, v in enumerate(self._load_profile)
        ]

        params = ArbitrageParams.from_dict(settings.get("arbitrage_params"))
        cap = _safe_float(settings.get("battery_capacity_kwh"))
        params.capacity_kwh = cap if cap > 0 else DEFAULT_BATTERY_CAPACITY / 1000
        self._arb_params = params

        rce: dict = {}
        rce_slot: dict = {}
        for eid in ("sensor.rce_pse_cena", "sensor.rce_pse_cena_jutro"):
            state = self.hass.states.get(eid)
            prices = state.attributes.get("prices") if state else None
            rce.update(rce_hourly(prices))
            rce_slot.update(rce_slots(prices, params.slot_minutes))
        sunrise, sunset = self._sun_hours()
        pv_factor = self._pv_nowcast_factor(data)
        pv_today_conf = min(1.0, params.pv_confidence * pv_factor)
        inputs = build_inputs(
            now,
            tariff=str(data.get("tariff_type") or "g13"),
            provider=str(settings.get("energy_provider") or data.get("energy_provider") or "tauron"),
            rce=rce,
            load_profile_kw=profile,
            # Rely on part of the forecast only — a cloudy peak must not hit the grid
            pv_today_remaining_kwh=_safe_float(data.get("pv_forecast_remaining_today_total")) * pv_today_conf,
            pv_tomorrow_kwh=_safe_float(data.get("pv_forecast_tomorrow_total")) * params.pv_confidence,
            sunrise_h=sunrise,
            sunset_h=sunset,
            horizon_h=params.horizon_h,
            slot_minutes=params.slot_minutes,
            rce_slot=rce_slot,
        )
        if inputs:
            # Current slot: what PV and the house do right now beats the forecast
            pv_kw = _safe_float(data.get(SENSOR_PV_POWER)) / 1000
            load_kw = _safe_float(data.get(SENSOR_LOAD_TOTAL)) / 1000
            inputs[0].pv_kwh = max(pv_kw, 0.0) * inputs[0].duration
            if load_kw > 0:
                inputs[0].load_kwh = load_kw * inputs[0].duration
        t0 = time.monotonic()
        plan = await self.hass.async_add_executor_job(optimize, soc, inputs, params)
        compute_ms = int((time.monotonic() - t0) * 1000)
        if compute_ms > 2000:
            _LOGGER.warning("Arbitrage plan took %d ms (%d h, %.1f kWh)", compute_ms, len(inputs), params.capacity_kwh)
        self._arb_plan, self._arb_plan_ts, self._arb_plan_key = plan, time.time(), key
        from dataclasses import asdict

        self._arb_status = {
            **plan.as_dict(limit=60),
            "params": asdict(params),
            "updated": now.strftime("%H:%M"),
            "rce_hours": len(rce),
            "compute_ms": compute_ms,
            "pv_factor": round(pv_factor, 2),
        }

    @staticmethod
    def _pv_nowcast_factor(data: dict[str, Any]) -> float:
        """Correct today's remaining PV forecast by how today has gone so far.

        Forecasts can be off by 2–3× on a given day (fog, wrong cloud model).
        Once the forecast expected ≥ 1 kWh so far, scale the rest of the day by
        actual/expected (clamped 0.5–2.0), blending in as evidence grows.
        """
        so_far = _safe_float(data.get("pv_forecast_so_far_kwh"))
        actual = _safe_float((data.get("energy_today") or {}).get("pv_kwh"))
        if so_far < 1.0 or actual <= 0:
            return 1.0
        ratio = max(0.5, min(2.0, actual / so_far))
        weight = min(1.0, so_far / 2.5)
        return 1.0 + weight * (ratio - 1.0)

    def _sun_hours(self) -> tuple[float, float]:
        """Local sunrise/sunset hours (float) from sun.sun, default 7:00/17:30."""
        state = self.hass.states.get("sun.sun")
        try:
            rise = dt_util.as_local(dt_util.parse_datetime(state.attributes["next_rising"]))
            sett = dt_util.as_local(dt_util.parse_datetime(state.attributes["next_setting"]))
            return rise.hour + rise.minute / 60, sett.hour + sett.minute / 60
        except (AttributeError, KeyError, TypeError, ValueError):
            return 7.0, 17.5

    def invalidate_arbitrage_plan(self) -> None:
        """Force a re-plan on the next tick (parameters changed)."""
        self._arb_plan_ts = 0.0
        self._arb_plan_key = ()

    # ── Manual hold ───────────────────────────────────────────────────

    @property
    def manual_hold_active(self) -> bool:
        return time.time() < self._manual_hold_until

    def set_manual_hold(self, minutes: int, reason: str = "") -> None:
        """Pause strategy layers after a manual command (0 = resume now)."""
        self._manual_hold_until = time.time() + minutes * 60 if minutes > 0 else 0.0
        self._arb_cmd = None  # re-apply the plan when the hold ends
        self._arb_commit = None
        if minutes > 0:
            self._log_decision("manual_hold", f"✋ {reason or 'Ręczne polecenie'} — autopilot wstrzymany na {minutes} min")
        else:
            self._log_decision("manual_hold_end", f"▶️ {reason or 'Koniec sterowania ręcznego'} — autopilot wznowiony")

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
                    await self._em.battery_to_home()
                    self._charging_enabled = False
                    msg = f"🔋 BP: SOC={soc:.0f}% >= {limits['max']:.0f}% — stop ładowania (ochrona)"
                    actions.append(msg)
                    self._log_decision("bp_high", msg)

        elif soc <= limits["min"]:
            # SOC too low — charge (PV only if possible, grid only if SOC < critical 5%)
            if soc < SOC_GRID_IMPORT_THRESHOLD:
                # Krytycznie niski SOC — musisz ładować z sieci
                if self._charging_enabled is not True:
                    if await self._throttled_action("bp_soc_low"):
                        await self._em.charge_from_grid()
                        self._charging_enabled = True
                        msg = f"🔋 BP: SOC={soc:.0f}% < {SOC_GRID_IMPORT_THRESHOLD}% — charge_from_grid (SOC krytyczny)"
                        actions.append(msg)
                        self._log_decision("bp_low", msg)
            elif surplus > 100:
                # Jest PV — ładuj tylko z PV
                if self._charging_enabled is not True:
                    if await self._throttled_action("bp_soc_low_pv"):
                        await self._em.charge_pv_only()
                        self._charging_enabled = True
                        msg = f"🔋 BP: SOC={soc:.0f}% <= {limits['min']:.0f}% — charge_pv_only (PV surplus={surplus:.0f}W)"
                        actions.append(msg)
                        self._log_decision("bp_low_pv", msg)

        elif surplus > 300 and soc < limits["max"]:
            # PV surplus and room to charge (PV only!)
            if self._charging_enabled is not True:
                if await self._throttled_action("bp_pv_charge"):
                    await self._em.charge_pv_only()
                    self._charging_enabled = True
                    msg = f"🔋 BP: PV nadwyżka {surplus:.0f}W → charge_pv_only (SOC={soc:.0f}%)"
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
            # Any PV surplus → absorb into battery (PV only!)
            if self._charging_enabled is not True:
                if await self._throttled_action("ze_charge"):
                    await self._em.charge_pv_only()
                    self._charging_enabled = True
                    # Also set zero export limit
                    await self._em.set_export_limit(0)
                    msg = f"⚡ ZE: Nadwyżka {surplus:.0f}W → charge_pv_only (zero eksport)"
                    actions.append(msg)
                    self._log_decision("ze_charge", msg)

        elif pv < load and soc > 15:
            # No surplus → use battery
            if self._charging_enabled is not False:
                if await self._throttled_action("ze_discharge"):
                    await self._em.battery_to_home()
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
        # Grid charge ONLY when ≤ 2h before expensive zone AND PV can't cover needs
        hours_to_peak = self._hours_until_expensive_zone(
            hour, datetime.now().month, datetime.now().weekday(),
        )
        if (
            0 < hours_to_peak <= 2
            and soc < 60
            and soc < SOC_GRID_IMPORT_THRESHOLD  # ZERO GRID: ładuj z sieci TYLKO gdy bateria krytycznie niska
            and forecast_today_remaining < 3
            and self._should_grid_charge_before_peak(soc, pv, load, data)
        ):
            if await self._throttled_action("wa_prepeak_fill"):
                await self._em.charge_from_grid()
                self._charging_enabled = True
                msg = (
                    f"🌧️ WA: Pre-peak fill z sieci ({hours_to_peak:.0f}h do szczytu, "
                    f"SOC={soc:.0f}%, prognoza={forecast_today_remaining:.1f}kWh)"
                )
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
                self._block_charge_completed = False  # Reset charge completion on block transition
                _LOGGER.info(
                    "AI Executor: entered block %s (%s) — %s",
                    block_key,
                    block.get("strategy", "?"),
                    block.get("reasoning", "")[:80],
                )

            strategy = block.get("strategy", "")  # Available for both auto-completion and drift detection

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

            # ── CHARGE AUTO-COMPLETION ────────────────────────
            # If charging block and SOC >= 95%, auto-switch to general
            # Battery is full → let it power the house naturally
            if (
                self._block_commands_executed
                and not self._block_charge_completed
                and strategy in ("aggressive_charge", "charge", "night_charge")
            ):
                soc_state = self.hass.states.get(SENSOR_BATTERY_SOC)
                soc_now = _safe_float(soc_state.state if soc_state else 0)
                if soc_now >= 95:
                    result = await self._inverter_agent.execute("set_general", {})
                    if result.executed:
                        self._block_charge_completed = True
                        msg = (
                            f"🧠 Auto-complete: SOC={soc_now:.0f}% ≥ 95% → general mode "
                            f"(bateria pełna, zasil dom z baterii zamiast z sieci)"
                        )
                        actions.append(msg)
                        self._log_decision("charge_complete", msg)
                        _LOGGER.info(
                            "AI Executor: charge auto-completed — SOC=%.0f%% → general mode (block %s)",
                            soc_now, block_key,
                        )

            if self._block_commands_executed and not self._block_charge_completed:
                # ── STATE-DRIFT DETECTION ────────────────────────
                # Check every tick if device state matches plan expectations
                # This catches manual overrides by user or external automations
                drift_cooldown = getattr(self, "_drift_last_reexec", 0)
                if now - drift_cooldown >= 120:  # Max once per 120s
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
                        # GoodWe HA integration reports "eco" for both
                        # eco_charge and eco_discharge in the select entity
                        "aggressive_charge": ("eco_charge", "eco"),
                        "charge": ("eco_charge", "eco"),
                        "night_charge": ("eco_charge", "eco"),
                        "discharge_self_consume": ("general",),
                        "discharge": ("eco_discharge", "eco"),
                    }
                    drifted = False
                    drift_reason = ""
                    expected_modes = _STRATEGY_EXPECTED_MODES.get(strategy)

                    # ── SOC-AWARE DRIFT EXCEPTION ────────────────
                    # If charge strategy reached SOC target (>= 95%), general mode
                    # is CORRECT — battery is full, it should power the house.
                    # Do NOT treat this as drift!
                    soc_for_drift = device_status.get("battery_soc", 0)
                    charge_goal_reached = (
                        strategy in ("aggressive_charge", "charge", "night_charge")
                        and soc_for_drift >= 95
                        and work_mode == "general"
                    )
                    if charge_goal_reached:
                        # Mark as completed so we don't re-check
                        if not self._block_charge_completed:
                            self._block_charge_completed = True
                            _LOGGER.info(
                                "AI Executor: charge goal reached (SOC=%.0f%%) — "
                                "general mode is correct, skipping drift detection (block %s)",
                                soc_for_drift, block_key,
                            )
                    elif expected_modes and work_mode not in expected_modes and work_mode not in ("unknown", "", "unavailable"):
                        drifted = True
                        drift_reason = f"mode mismatch: expected={expected_modes}, got={work_mode}"
                        _LOGGER.warning(
                            "AI Executor: STATE DRIFT — plan=%s expects mode=%s but got mode=%s, bat=%dW → re-executing",
                            strategy, expected_modes, work_mode, bat_power,
                        )

                    # ── DEAD BATTERY DETECTION ────────────────────
                    # Mode is correct but battery isn't actually discharging
                    # (DOD too low, BMS blocking, etc.)
                    if (
                        not drifted
                        and strategy in ("discharge_self_consume", "discharge")
                        and expected_modes
                        and work_mode in expected_modes
                        and bat_power <= 50  # battery_power: +discharge / -charge → not discharging
                    ):
                        # Check if grid is importing significantly (home is pulling from grid)
                        grid_state = self.hass.states.get(SENSOR_GRID_POWER_TOTAL)
                        # Meter: +export / -import
                        grid_power = -_safe_float(grid_state.state if grid_state else None)
                        if grid_power > 500:  # >500W import from grid
                            drifted = True
                            # Read DOD to diagnose root cause
                            from .const import NUMBER_DOD_ON_GRID
                            dod_state = self.hass.states.get(NUMBER_DOD_ON_GRID)
                            dod_val = float(dod_state.state) if dod_state and dod_state.state not in ("unknown", "unavailable") else -1
                            drift_reason = (
                                f"dead battery: mode={work_mode} OK but bat_power={bat_power:.0f}W "
                                f"(not discharging), grid_import={grid_power:.0f}W, DOD={dod_val:.0f}%"
                            )
                            _LOGGER.warning(
                                "AI Executor: DEAD BATTERY — mode=%s correct but battery not discharging! "
                                "bat=%dW, grid_import=%dW, DOD=%d%% → forcing DOD=95%% to unblock discharge",
                                work_mode, bat_power, grid_power, dod_val,
                            )
                            # Immediately fix DOD — this is the #1 cause of dead battery
                            try:
                                await self._em._set_dod(95)
                                self._log_decision("drift_fix", "🔧 DOD reset to 95% (was {:.0f}%) — unblocking discharge".format(dod_val))
                            except Exception as dod_err:
                                _LOGGER.error("Failed to reset DOD: %s", dod_err)

                    if drifted:
                        self._drift_last_reexec = now
                        self._block_commands_executed = False  # Force re-execution on next tick
                        self._log_decision("drift", f"⚠️ State drift detected — {drift_reason} — re-applying block {block_key}")
                        actions.append(f"⚠️ Drift: {drift_reason} → re-applying {block_key}")

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
                self._block_charge_completed = False

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
            if tool == "charge_pv_only":
                if not self._ai_dry_run:
                    await self._em.charge_pv_only()
                    self._charging_enabled = True
                msg = f"{prefix}: charge_pv_only → PV → dom → bateria (bez sieci)"
                self._log_decision("ai_cmd", msg)
                return msg

            elif tool == "charge_from_grid":
                if not self._ai_dry_run:
                    await self._em.charge_from_grid()
                    self._charging_enabled = True
                msg = f"{prefix}: charge_from_grid → ładowanie z sieci + PV (eco_charge)"
                self._log_decision("ai_cmd", msg)
                return msg

            elif tool == "force_charge":
                # Backwards compat — redirect to charge_from_grid
                if not self._ai_dry_run:
                    await self._em.charge_from_grid()
                    self._charging_enabled = True
                msg = f"{prefix}: force_charge [DEPRECATED → charge_from_grid] → ładowanie z sieci"
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
                # Safety net: AI often confuses DOD with min-SOC target.
                # DOD=5 means "allow only 5% discharge" (battery blocked!).
                # If AI sends low DOD during discharge, auto-correct.
                if dod < 50:
                    corrected = 100 - dod
                    corrected = min(corrected, 95)
                    _LOGGER.warning(
                        "AI sent set_dod(%d%%) — likely confused with SOC target. "
                        "Auto-correcting to DOD=%d%% (allows discharge to ~%d%% SOC). "
                        "DOD = %% of capacity AVAILABLE for discharge.",
                        dod, corrected, 100 - corrected,
                    )
                    dod = corrected
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
            PEAK_SELL_SETTINGS_KEY: self._peak_sell_soc_percent,
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

        # Restore peak sell setting (always, even if autopilot is disabled)
        self._peak_sell_soc_percent = int(
            settings.get(PEAK_SELL_SETTINGS_KEY, DEFAULT_PEAK_SELL_SOC_PERCENT)
        )
        _LOGGER.info(
            "Restored peak sell SOC percent: %d%%", self._peak_sell_soc_percent,
        )

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
