"""Schedule Manager for Smarting HOME HEMS.

Provides two interlocking scheduling systems:

1. **Manual Mode Schedule** — hour-by-hour energy actions when autopilot
   is off (charge, sell, hold, etc.)
2. **Autopilot Schedule** — which hours the autopilot runs and with
   which strategy

Safety layers (W0, W3, W4) run **always**, regardless of schedule mode.

Data is persisted in ``settings.json`` via ``settings_io``.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .strategy_controller import StrategyController

from .const import (
    DOMAIN,
    ManualMode,
    AutopilotStrategy,
    MANUAL_MODE_LABELS,
    MANUAL_MODE_DESCRIPTIONS,
    MANUAL_MODE_ICONS,
    SCHEDULE_ENABLED_KEY,
    SCHEDULE_MODE_KEY,
    SCHEDULE_WEEKDAY_KEY,
    SCHEDULE_WEEKEND_KEY,
    SCHEDULE_ACTIVE_SLOT_KEY,
    SCHEDULE_LAST_TRANSITION_KEY,
    DEFAULT_SCHEDULE_WEEKDAY,
    DEFAULT_SCHEDULE_WEEKEND,
    DEFAULT_SCHEDULE_SLOT,
)
from .energy_manager import EnergyManager
from .settings_io import (
    read_sync as _read_settings,
    write_async as _write_settings,
)

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between mode transitions (avoid flapping at hour boundary)
TRANSITION_COOLDOWN = 30

# Minimum seconds between re-applying the same manual mode command
MANUAL_ACTION_COOLDOWN = 120


class ScheduleManager:
    """Central schedule engine for autopilot and manual mode orchestration.

    Called on every coordinator tick (~30s).  Determines whether the
    current hour should run in autopilot (delegated to StrategyController)
    or manual mode (executed directly via EnergyManager).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        strategy_controller: StrategyController,
        energy_manager: EnergyManager,
    ) -> None:
        self.hass = hass
        self._sc = strategy_controller
        self._em = energy_manager

        # Schedule state
        self._enabled: bool = False
        self._schedule_mode: str = "weekday_weekend"  # or "single"
        self._weekday_schedule: dict[str, dict[str, str]] = {}
        self._weekend_schedule: dict[str, dict[str, str]] = {}

        # Runtime tracking
        self._current_hour: int = -1
        self._current_slot: dict[str, str] | None = None
        self._current_mode_type: str = ""  # "autopilot" or "manual"
        self._current_manual_mode: ManualMode | None = None
        self._current_autopilot_strategy: AutopilotStrategy | None = None
        self._last_transition_time: float = 0.0
        self._last_manual_action_time: float = 0.0
        self._manual_override: ManualMode | None = None  # instant override

        # Decision log
        self._decision_log: list[dict[str, Any]] = []
        self._max_log_entries = 30

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def current_mode_type(self) -> str:
        """'autopilot', 'manual', or '' if disabled."""
        return self._current_mode_type

    @property
    def current_manual_mode(self) -> ManualMode | None:
        return self._current_manual_mode

    @property
    def current_autopilot_strategy(self) -> AutopilotStrategy | None:
        return self._current_autopilot_strategy

    @property
    def decision_log(self) -> list[dict[str, Any]]:
        return list(self._decision_log)

    async def restore_schedule(self) -> None:
        """Restore schedule from settings.json on startup."""
        try:
            settings = await self.hass.async_add_executor_job(_read_settings, self.hass)
        except Exception as err:
            _LOGGER.warning("Cannot read schedule settings: %s", err)
            return

        self._enabled = settings.get(SCHEDULE_ENABLED_KEY, False)
        self._schedule_mode = settings.get(SCHEDULE_MODE_KEY, "weekday_weekend")
        self._weekday_schedule = settings.get(
            SCHEDULE_WEEKDAY_KEY, dict(DEFAULT_SCHEDULE_WEEKDAY)
        )
        self._weekend_schedule = settings.get(
            SCHEDULE_WEEKEND_KEY, dict(DEFAULT_SCHEDULE_WEEKEND)
        )

        # Validate loaded schedules — ensure all 24 hours exist
        self._weekday_schedule = self._validate_schedule(self._weekday_schedule)
        self._weekend_schedule = self._validate_schedule(self._weekend_schedule)

        if self._enabled:
            _LOGGER.info(
                "Schedule Manager restored: enabled=%s, mode=%s, weekday=%d slots, weekend=%d slots",
                self._enabled, self._schedule_mode,
                len(self._weekday_schedule), len(self._weekend_schedule),
            )
        else:
            _LOGGER.debug("Schedule Manager: disabled")

    async def evaluate_tick(self, data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate current schedule slot on every tick.

        Called by coordinator before execute_tick. Returns dict with
        schedule state for frontend sensors.

        Args:
            data: merged raw + computed sensor data from coordinator.

        Returns:
            dict with schedule state:
            - schedule_enabled: bool
            - schedule_mode_type: "autopilot" | "manual" | ""
            - schedule_manual_mode: ManualMode value or ""
            - schedule_autopilot_strategy: strategy value or ""
            - autopilot_should_run: bool — whether StrategyController.execute_tick should run
            - safety_only: bool — whether only safety layers should run
        """
        if not self._enabled:
            return {
                "schedule_enabled": False,
                "autopilot_should_run": self._sc.enabled,
                "safety_only": False,
            }

        now = datetime.now()
        hour = now.hour

        # Check if hour changed or first run
        if hour != self._current_hour:
            await self._transition_to_hour(hour, now)

        # Handle manual override (instant apply from service call)
        if self._manual_override is not None:
            override = self._manual_override
            self._manual_override = None
            await self._apply_manual_mode(override, source="override")

        # Re-apply manual mode periodically (fight inverter auto-reset)
        if (
            self._current_mode_type == "manual"
            and self._current_manual_mode is not None
            and self._current_manual_mode != ManualMode.OFF
        ):
            now_ts = time.time()
            if now_ts - self._last_manual_action_time >= MANUAL_ACTION_COOLDOWN:
                await self._apply_manual_mode(
                    self._current_manual_mode, source="refresh"
                )

        # Build response
        result = {
            "schedule_enabled": True,
            "schedule_mode_type": self._current_mode_type,
            "schedule_manual_mode": (
                self._current_manual_mode.value
                if self._current_manual_mode
                else ""
            ),
            "schedule_manual_mode_label": (
                MANUAL_MODE_LABELS.get(self._current_manual_mode, "")
                if self._current_manual_mode
                else ""
            ),
            "schedule_autopilot_strategy": (
                self._current_autopilot_strategy.value
                if self._current_autopilot_strategy
                else ""
            ),
            "schedule_current_hour": str(hour).zfill(2),
            "schedule_slot": self._current_slot or {},
            "schedule_next_transition": self._get_next_transition(hour, now),
            "autopilot_should_run": self._current_mode_type == "autopilot",
            "safety_only": self._current_mode_type == "manual",
        }

        return result

    async def save_schedule(
        self,
        weekday_schedule: dict[str, dict[str, str]] | None = None,
        weekend_schedule: dict[str, dict[str, str]] | None = None,
        enabled: bool | None = None,
        schedule_mode: str | None = None,
    ) -> None:
        """Save schedule to settings.json (called from service)."""
        updates: dict[str, Any] = {}

        if enabled is not None:
            self._enabled = enabled
            updates[SCHEDULE_ENABLED_KEY] = enabled

        if schedule_mode is not None:
            self._schedule_mode = schedule_mode
            updates[SCHEDULE_MODE_KEY] = schedule_mode

        if weekday_schedule is not None:
            validated = self._validate_schedule(weekday_schedule)
            self._weekday_schedule = validated
            updates[SCHEDULE_WEEKDAY_KEY] = validated

        if weekend_schedule is not None:
            validated = self._validate_schedule(weekend_schedule)
            self._weekend_schedule = validated
            updates[SCHEDULE_WEEKEND_KEY] = validated

        if updates:
            await _write_settings(self.hass, updates)
            self._log_decision(
                "schedule_saved",
                f"Harmonogram zapisany: {', '.join(updates.keys())}",
            )

        # Force re-evaluation on next tick
        self._current_hour = -1

        _LOGGER.info("Schedule saved: %s", list(updates.keys()))

    async def apply_manual_override(self, mode: ManualMode) -> dict[str, Any]:
        """Instantly apply a manual mode (ignoring schedule).

        Called from the apply_manual_mode service.
        """
        self._manual_override = mode
        self._log_decision(
            "manual_override",
            f"Natychmiastowy override: {MANUAL_MODE_LABELS.get(mode, mode.value)}",
        )
        return {
            "success": True,
            "mode": mode.value,
            "label": MANUAL_MODE_LABELS.get(mode, mode.value),
        }

    def get_status(self) -> dict[str, Any]:
        """Get full schedule status for frontend."""
        now = datetime.now()
        hour = now.hour
        hour_key = str(hour).zfill(2)
        weekday = now.weekday()

        active_schedule = (
            self._weekday_schedule
            if weekday < 5
            else self._weekend_schedule
        )

        return {
            "enabled": self._enabled,
            "schedule_mode": self._schedule_mode,
            "current_hour": hour_key,
            "current_mode_type": self._current_mode_type,
            "current_manual_mode": (
                self._current_manual_mode.value
                if self._current_manual_mode else ""
            ),
            "current_manual_mode_label": (
                MANUAL_MODE_LABELS.get(self._current_manual_mode, "")
                if self._current_manual_mode else ""
            ),
            "current_autopilot_strategy": (
                self._current_autopilot_strategy.value
                if self._current_autopilot_strategy else ""
            ),
            "is_weekend": weekday >= 5,
            "weekday_schedule": self._weekday_schedule,
            "weekend_schedule": self._weekend_schedule,
            "active_schedule": active_schedule,
            "next_transition": self._get_next_transition(hour, now),
            "decision_log": list(self._decision_log[-10:]),
            "manual_modes": self._get_manual_modes_info(),
        }

    # ------------------------------------------------------------------
    # Internal — Transition Logic
    # ------------------------------------------------------------------

    async def _transition_to_hour(self, hour: int, now: datetime) -> None:
        """Handle hour change — evaluate and apply the new slot."""
        now_ts = time.time()

        # Cooldown check
        if now_ts - self._last_transition_time < TRANSITION_COOLDOWN:
            return

        self._current_hour = hour
        self._last_transition_time = now_ts

        # Determine which schedule to use
        weekday = now.weekday()
        schedule = (
            self._weekday_schedule
            if weekday < 5
            else self._weekend_schedule
        )

        hour_key = str(hour).zfill(2)
        slot = schedule.get(hour_key, dict(DEFAULT_SCHEDULE_SLOT))
        self._current_slot = slot

        slot_mode = slot.get("mode", "autopilot")
        old_mode_type = self._current_mode_type

        if slot_mode == "autopilot":
            # Autopilot mode
            strategy_str = slot.get(
                "strategy",
                AutopilotStrategy.MAX_SELF_CONSUMPTION.value,
            )
            try:
                strategy = AutopilotStrategy(strategy_str)
            except ValueError:
                strategy = AutopilotStrategy.MAX_SELF_CONSUMPTION
                _LOGGER.warning(
                    "Unknown strategy '%s' in schedule, defaulting to max_self_consumption",
                    strategy_str,
                )

            self._current_mode_type = "autopilot"
            self._current_manual_mode = None
            self._current_autopilot_strategy = strategy

            # Activate strategy on the controller
            if not self._sc.enabled or self._sc.active_strategy != strategy:
                await self._sc.activate_strategy(strategy)
                self._sc.set_schedule_managed(True)

            transition_label = f"⚙️ Autopilot: {strategy.value}"

        elif slot_mode == "manual":
            # Manual mode
            manual_mode_str = slot.get(
                "manual_mode",
                ManualMode.SELF_CONSUMPTION.value,
            )
            try:
                manual_mode = ManualMode(manual_mode_str)
            except ValueError:
                manual_mode = ManualMode.SELF_CONSUMPTION
                _LOGGER.warning(
                    "Unknown manual mode '%s' in schedule, defaulting to self_consumption",
                    manual_mode_str,
                )

            self._current_mode_type = "manual"
            self._current_manual_mode = manual_mode
            self._current_autopilot_strategy = None

            # Deactivate autopilot strategy layers (safety stays)
            if self._sc.enabled:
                await self._sc.deactivate()
            self._sc.set_schedule_managed(True)

            # Apply the manual mode
            await self._apply_manual_mode(manual_mode, source="schedule")

            transition_label = (
                f"🔧 Manual: {MANUAL_MODE_LABELS.get(manual_mode, manual_mode.value)}"
            )

        else:
            _LOGGER.warning("Unknown slot mode: %s", slot_mode)
            return

        # Log transition
        if old_mode_type != self._current_mode_type or True:  # always log hour changes
            day_type = "Pn-Pt" if weekday < 5 else "Sob-Nd"
            self._log_decision(
                "transition",
                f"[{hour_key}:00 {day_type}] {transition_label}",
            )
            _LOGGER.info(
                "Schedule transition: %s:00 → %s (%s)",
                hour_key, slot_mode, slot,
            )

        # Persist active slot info
        await self._persist_active_slot(hour_key, slot)

    async def _apply_manual_mode(
        self, mode: ManualMode, source: str = "schedule"
    ) -> None:
        """Execute the energy management commands for a manual mode.

        Maps each ManualMode to specific EnergyManager calls.
        Respects throttling to avoid command spam.
        """
        self._last_manual_action_time = time.time()

        try:
            if mode == ManualMode.SELF_CONSUMPTION:
                # General mode — standard auto-consumption
                await self._em.set_general_mode()

            elif mode == ManualMode.SELL_TO_GRID:
                # Force discharge — sell battery to grid
                await self._em.force_discharge()

            elif mode == ManualMode.CHARGE_BATTERY:
                # Force charge — PV priority to battery
                await self._em.force_charge()

            elif mode == ManualMode.CHARGE_FROM_GRID:
                # Eco charge — battery charges from grid
                await self._em.force_charge()

            elif mode == ManualMode.PEAK_SAVE:
                # Block grid import, battery powers home
                # Use general mode with grid protection (handled by safety layers)
                await self._em.set_general_mode()

            elif mode == ManualMode.ZERO_EXPORT:
                # Zero export — set export limit to 0
                await self._em.set_general_mode()
                await self._em.set_export_limit(0)

            elif mode == ManualMode.BATTERY_HOLD:
                # Battery idle — general mode, no forced charge/discharge
                await self._em.stop_force_charge()
                await self._em.stop_force_discharge()

            elif mode == ManualMode.OFF:
                # Reset to inverter defaults
                await self._em.set_general_mode()

            if source != "refresh":
                _LOGGER.info(
                    "Manual mode applied: %s (source: %s)",
                    mode.value, source,
                )

        except Exception as err:
            _LOGGER.error(
                "Failed to apply manual mode %s: %s", mode.value, err
            )
            self._log_decision(
                "manual_error",
                f"❌ Błąd trybu manualnego {mode.value}: {err}",
            )

    # ------------------------------------------------------------------
    # Internal — Helpers
    # ------------------------------------------------------------------

    def _validate_schedule(
        self, schedule: dict[str, dict[str, str]]
    ) -> dict[str, dict[str, str]]:
        """Ensure schedule has all 24 hours with valid data."""
        validated = {}
        for h in range(24):
            key = str(h).zfill(2)
            slot = schedule.get(key, dict(DEFAULT_SCHEDULE_SLOT))

            # Validate mode
            mode = slot.get("mode", "autopilot")
            if mode not in ("autopilot", "manual"):
                slot["mode"] = "autopilot"
                slot["strategy"] = AutopilotStrategy.MAX_SELF_CONSUMPTION.value

            # Validate strategy if autopilot
            if slot.get("mode") == "autopilot":
                strategy = slot.get("strategy", "")
                try:
                    AutopilotStrategy(strategy)
                except ValueError:
                    slot["strategy"] = AutopilotStrategy.MAX_SELF_CONSUMPTION.value

            # Validate manual_mode if manual
            if slot.get("mode") == "manual":
                mm = slot.get("manual_mode", "")
                try:
                    ManualMode(mm)
                except ValueError:
                    slot["manual_mode"] = ManualMode.SELF_CONSUMPTION.value

            validated[key] = slot

        return validated

    def _get_next_transition(
        self, current_hour: int, now: datetime
    ) -> dict[str, Any]:
        """Find the next hour where the mode changes."""
        weekday = now.weekday()
        schedule = (
            self._weekday_schedule
            if weekday < 5
            else self._weekend_schedule
        )

        current_key = str(current_hour).zfill(2)
        current_slot = schedule.get(current_key, {})
        current_mode = current_slot.get("mode", "autopilot")
        current_detail = current_slot.get(
            "strategy" if current_mode == "autopilot" else "manual_mode", ""
        )

        # Look ahead up to 24 hours
        for offset in range(1, 25):
            next_hour = (current_hour + offset) % 24
            next_key = str(next_hour).zfill(2)

            # After midnight, might be different day type
            if next_hour < current_hour:
                next_weekday = (weekday + 1) % 7
                next_schedule = (
                    self._weekday_schedule
                    if next_weekday < 5
                    else self._weekend_schedule
                )
            else:
                next_schedule = schedule

            next_slot = next_schedule.get(next_key, {})
            next_mode = next_slot.get("mode", "autopilot")
            next_detail = next_slot.get(
                "strategy" if next_mode == "autopilot" else "manual_mode", ""
            )

            if next_mode != current_mode or next_detail != current_detail:
                return {
                    "hour": next_key,
                    "mode": next_mode,
                    "detail": next_detail,
                    "in_hours": offset,
                    "label": self._slot_label(next_slot),
                }

        return {"hour": "", "mode": "", "detail": "", "in_hours": 24, "label": "Brak zmian"}

    def _slot_label(self, slot: dict[str, str]) -> str:
        """Human-readable label for a schedule slot."""
        mode = slot.get("mode", "autopilot")
        if mode == "autopilot":
            strategy = slot.get("strategy", "max_self_consumption")
            from .const import AUTOPILOT_STRATEGY_LABELS
            try:
                s = AutopilotStrategy(strategy)
                return f"⚙️ {AUTOPILOT_STRATEGY_LABELS.get(s, strategy)}"
            except ValueError:
                return f"⚙️ {strategy}"
        elif mode == "manual":
            mm = slot.get("manual_mode", "self_consumption")
            try:
                m = ManualMode(mm)
                return MANUAL_MODE_LABELS.get(m, mm)
            except ValueError:
                return f"🔧 {mm}"
        return "❓ Nieznany"

    async def _persist_active_slot(
        self, hour_key: str, slot: dict[str, str]
    ) -> None:
        """Save the currently active slot to settings.json."""
        try:
            active_info = {
                "hour": hour_key,
                "mode": slot.get("mode", "autopilot"),
                "detail": slot.get(
                    "strategy"
                    if slot.get("mode") == "autopilot"
                    else "manual_mode",
                    "",
                ),
                "since": f"{hour_key}:00",
                "timestamp": time.time(),
            }
            await _write_settings(self.hass, {
                SCHEDULE_ACTIVE_SLOT_KEY: active_info,
                SCHEDULE_LAST_TRANSITION_KEY: {
                    "hour": hour_key,
                    "timestamp": time.time(),
                    "mode": slot.get("mode", ""),
                },
            })
        except Exception as err:
            _LOGGER.debug("Failed to persist active slot: %s", err)

    def _log_decision(self, action: str, message: str) -> None:
        """Add entry to decision log."""
        entry = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "message": message,
        }
        self._decision_log.append(entry)
        if len(self._decision_log) > self._max_log_entries:
            self._decision_log = self._decision_log[-self._max_log_entries:]

    @staticmethod
    def _get_manual_modes_info() -> list[dict[str, str]]:
        """Get list of all manual modes with labels and descriptions."""
        return [
            {
                "value": mode.value,
                "label": MANUAL_MODE_LABELS.get(mode, mode.value),
                "description": MANUAL_MODE_DESCRIPTIONS.get(mode, ""),
                "icon": MANUAL_MODE_ICONS.get(mode, "mdi:help"),
            }
            for mode in ManualMode
        ]
