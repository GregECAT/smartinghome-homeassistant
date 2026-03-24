"""Inverter Sub-Agent — State-aware command executor.

Wraps EnergyManager with HA state reading. Before executing any command,
checks if the system is ALREADY in the desired state and skips if so.
Reports back what was actually changed vs already correct.

Architecture:
    AI Controller (Main Agent)  →  decides WHAT should happen
    InverterAgent (Sub-Agent)   →  checks state, executes ONLY if needed

Future: GoodWeAgent / DeyeAgent / GrowattAgent inherit from base class.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_GOODWE_DEVICE_ID,
    DEFAULT_BATTERY_CHARGE_CURRENT_MAX,
    DEFAULT_BATTERY_CHARGE_CURRENT_BLOCK,
    DEFAULT_DOD_ON_GRID,
    DEFAULT_EXPORT_LIMIT,
    NUMBER_DOD_ON_GRID,
    SELECT_WORK_MODE,
    SWITCH_BOILER,
    SWITCH_AC,
    SWITCH_SOCKET2,
    SENSOR_BATTERY_SOC,
)
from .energy_manager import EnergyManager

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between identical commands to the same target
_COMMAND_COOLDOWN = 120  # 2 minutes


@dataclass
class ExecutionResult:
    """Result of a command execution attempt."""

    tool: str
    executed: bool = False
    skipped: bool = False
    message: str = ""
    previous_state: str = ""
    new_state: str = ""
    reason: str = ""  # why skipped


# Friendly name → HA entity_id mapping for managed switches
_SWITCH_MAP: dict[str, str] = {
    "boiler": SWITCH_BOILER,
    "bojler": SWITCH_BOILER,
    "ac": SWITCH_AC,
    "klimatyzacja": SWITCH_AC,
    "socket2": SWITCH_SOCKET2,
    "gniazdko": SWITCH_SOCKET2,
}

# Friendly label for switches
_SWITCH_LABELS: dict[str, str] = {
    SWITCH_BOILER: "boiler",
    SWITCH_AC: "ac",
    SWITCH_SOCKET2: "socket2",
}


class InverterAgent:
    """State-aware command executor for GoodWe inverter + managed loads.

    Reads HA entity states before executing commands.
    Only sends commands when actual state differs from desired state.
    Tracks last command timestamps to prevent spam.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        energy_manager: EnergyManager,
        device_id: str = DEFAULT_GOODWE_DEVICE_ID,
        dry_run: bool = False,
    ) -> None:
        self.hass = hass
        self._em = energy_manager
        self._device_id = device_id
        self._dry_run = dry_run

        # Cooldown tracking: "tool:target" → last_exec_timestamp
        self._last_command: dict[str, float] = {}

        # Track last known commanded state (not just HA state)
        self._commanded_charging: bool | None = None
        self._commanded_dod: int | None = None

    # ------------------------------------------------------------------
    #  Device Status — read from HA state machine
    # ------------------------------------------------------------------

    def get_device_status(self) -> dict[str, Any]:
        """Read ALL managed device states from HA.

        This is what gets injected into the AI prompt so the AI knows
        the current state before deciding what to do.
        """
        states = self.hass.states

        # Battery charge current → determine if charging is enabled
        # We infer this from battery_power: positive = charging, negative = discharging
        bat_power = self._read_state("sensor.battery_power", 0.0)
        bat_soc = self._read_state(SENSOR_BATTERY_SOC, 0.0)

        # Work mode
        work_mode_state = states.get(SELECT_WORK_MODE)
        work_mode = work_mode_state.state if work_mode_state else "unknown"

        # DOD on grid
        dod_state = states.get(NUMBER_DOD_ON_GRID)
        dod = float(dod_state.state) if dod_state and dod_state.state not in ("unknown", "unavailable") else DEFAULT_DOD_ON_GRID

        # Switch states
        switches = {}
        for entity_id, label in _SWITCH_LABELS.items():
            sw_state = states.get(entity_id)
            switches[label] = sw_state.state if sw_state else "unknown"

        # Determine charging status from commanded state + battery power
        if self._commanded_charging is not None:
            charging_active = self._commanded_charging
        else:
            charging_active = bat_power > 50  # positive = charging

        return {
            "charging_active": charging_active,
            "charging_commanded": "enabled" if charging_active else "disabled",
            "battery_power": bat_power,
            "battery_soc": bat_soc,
            "work_mode": work_mode,
            "dod_on_grid": int(dod),
            "switches": switches,
            "inverter_brand": "goodwe",
            "dry_run": self._dry_run,
        }

    # ------------------------------------------------------------------
    #  Command Execution — state-aware
    # ------------------------------------------------------------------

    async def execute(self, tool: str, params: dict) -> ExecutionResult:
        """Execute command ONLY if state differs from desired.

        Returns ExecutionResult with details of what happened.
        """
        try:
            if tool == "force_charge":
                return await self._exec_force_charge()
            elif tool == "force_discharge":
                return await self._exec_force_discharge()
            elif tool == "set_dod":
                dod = int(params.get("dod", 80))
                dod = max(0, min(dod, 95))
                return await self._exec_set_dod(dod)
            elif tool == "set_export_limit":
                limit = int(params.get("limit", 0))
                limit = max(0, min(limit, 10000))
                return await self._exec_set_export_limit(limit)
            elif tool == "switch_on":
                entity = params.get("entity", "")
                return await self._exec_switch("on", entity)
            elif tool == "switch_off":
                entity = params.get("entity", "")
                return await self._exec_switch("off", entity)
            elif tool == "no_action":
                reason = params.get("reason", "brak akcji")
                return ExecutionResult(
                    tool="no_action",
                    skipped=True,
                    message=reason,
                    reason=reason,
                )
            else:
                _LOGGER.warning("InverterAgent: unknown tool '%s'", tool)
                return ExecutionResult(
                    tool=tool,
                    skipped=True,
                    reason=f"unknown tool: {tool}",
                )
        except Exception as err:
            _LOGGER.error("InverterAgent: failed %s: %s", tool, err)
            return ExecutionResult(
                tool=tool,
                executed=False,
                message=f"❌ {tool} failed: {err}",
                reason=str(err),
            )

    # ------------------------------------------------------------------
    #  Individual command executors
    # ------------------------------------------------------------------

    async def _exec_force_charge(self) -> ExecutionResult:
        """Force charging — skip if already charging."""
        # Check: is charging already commanded?
        if self._commanded_charging is True:
            # Also check cooldown
            if self._is_on_cooldown("force_charge"):
                return ExecutionResult(
                    tool="force_charge",
                    skipped=True,
                    reason="already charging (commanded)",
                    previous_state="charging",
                    new_state="charging",
                )

        # Check HA state: battery power > 50W = already charging
        bat_power = self._read_state("sensor.battery_power", 0.0)
        if self._commanded_charging is True and bat_power > 50:
            if self._is_on_cooldown("force_charge"):
                return ExecutionResult(
                    tool="force_charge",
                    skipped=True,
                    reason=f"already charging ({bat_power:.0f}W)",
                    previous_state=f"charging ({bat_power:.0f}W)",
                    new_state=f"charging ({bat_power:.0f}W)",
                )

        # Execute
        prefix = "🧠 AI CTRL" if not self._dry_run else "🧠 AI DRY-RUN"
        if not self._dry_run:
            await self._em.force_charge()
        self._commanded_charging = True
        self._mark_executed("force_charge")

        return ExecutionResult(
            tool="force_charge",
            executed=True,
            message=f"{prefix}: force_charge → ładowanie baterii",
            previous_state="not charging" if bat_power <= 50 else f"charging ({bat_power:.0f}W)",
            new_state="charging (commanded)",
        )

    async def _exec_force_discharge(self) -> ExecutionResult:
        """Force discharge — skip if already discharging."""
        if self._commanded_charging is False:
            if self._is_on_cooldown("force_discharge"):
                return ExecutionResult(
                    tool="force_discharge",
                    skipped=True,
                    reason="already discharging (commanded)",
                )

        bat_power = self._read_state("sensor.battery_power", 0.0)
        prefix = "🧠 AI CTRL" if not self._dry_run else "🧠 AI DRY-RUN"
        if not self._dry_run:
            await self._em.force_discharge()
        self._commanded_charging = False
        self._mark_executed("force_discharge")

        return ExecutionResult(
            tool="force_discharge",
            executed=True,
            message=f"{prefix}: force_discharge → rozładowanie baterii",
            previous_state=f"battery: {bat_power:.0f}W",
            new_state="discharging (commanded)",
        )

    async def _exec_set_dod(self, dod: int) -> ExecutionResult:
        """Set DOD — skip if already at requested value."""
        dod_state = self.hass.states.get(NUMBER_DOD_ON_GRID)
        current_dod = int(float(dod_state.state)) if dod_state and dod_state.state not in ("unknown", "unavailable") else None

        if current_dod == dod:
            if self._is_on_cooldown(f"set_dod:{dod}"):
                return ExecutionResult(
                    tool="set_dod",
                    skipped=True,
                    reason=f"DOD already at {dod}%",
                    previous_state=f"{current_dod}%",
                    new_state=f"{dod}%",
                )

        prefix = "🧠 AI CTRL" if not self._dry_run else "🧠 AI DRY-RUN"
        if not self._dry_run:
            await self._em._set_dod(dod)
        self._commanded_dod = dod
        self._mark_executed(f"set_dod:{dod}")

        return ExecutionResult(
            tool="set_dod",
            executed=True,
            message=f"{prefix}: set_dod({dod}%) → głębokość rozładowania",
            previous_state=f"{current_dod}%" if current_dod is not None else "unknown",
            new_state=f"{dod}%",
        )

    async def _exec_set_export_limit(self, limit: int) -> ExecutionResult:
        """Set export limit."""
        if self._is_on_cooldown(f"export_limit:{limit}"):
            return ExecutionResult(
                tool="set_export_limit",
                skipped=True,
                reason=f"export limit {limit}W already set recently",
            )

        prefix = "🧠 AI CTRL" if not self._dry_run else "🧠 AI DRY-RUN"
        if not self._dry_run:
            await self._em.set_export_limit(limit)
        self._mark_executed(f"export_limit:{limit}")

        return ExecutionResult(
            tool="set_export_limit",
            executed=True,
            message=f"{prefix}: set_export_limit({limit}W)",
            new_state=f"{limit}W",
        )

    async def _exec_switch(self, desired: str, friendly_name: str) -> ExecutionResult:
        """Toggle switch — skip if already in desired state."""
        entity_id = _SWITCH_MAP.get(friendly_name.lower().strip())
        if not entity_id:
            return ExecutionResult(
                tool=f"switch_{desired}",
                skipped=True,
                reason=f"unknown entity: {friendly_name}",
            )

        # Check current state
        sw_state = self.hass.states.get(entity_id)
        current = sw_state.state if sw_state else "unknown"

        if current == desired:
            return ExecutionResult(
                tool=f"switch_{desired}",
                skipped=True,
                reason=f"{friendly_name} already {desired}",
                previous_state=current,
                new_state=desired,
            )

        # Check cooldown
        cooldown_key = f"switch_{desired}:{entity_id}"
        if self._is_on_cooldown(cooldown_key):
            return ExecutionResult(
                tool=f"switch_{desired}",
                skipped=True,
                reason=f"{friendly_name} → {desired} (cooldown, sent recently)",
                previous_state=current,
                new_state=desired,
            )

        prefix = "🧠 AI CTRL" if not self._dry_run else "🧠 AI DRY-RUN"
        if not self._dry_run:
            if desired == "on":
                await self._em._switch_on(entity_id)
            else:
                await self._em._switch_off(entity_id)
        self._mark_executed(cooldown_key)

        return ExecutionResult(
            tool=f"switch_{desired}",
            executed=True,
            message=f"{prefix}: switch_{desired}({friendly_name}) → {'włączono' if desired == 'on' else 'wyłączono'}",
            previous_state=current,
            new_state=desired,
        )

    # ------------------------------------------------------------------
    #  Helpers
    # ------------------------------------------------------------------

    def _read_state(self, entity_id: str, default: float = 0.0) -> float:
        """Read numeric state from HA."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _is_on_cooldown(self, key: str) -> bool:
        """Check if a command is on cooldown."""
        last = self._last_command.get(key, 0)
        return (time.time() - last) < _COMMAND_COOLDOWN

    def _mark_executed(self, key: str) -> None:
        """Record command execution timestamp."""
        self._last_command[key] = time.time()

    def format_status_for_prompt(self) -> str:
        """Format device status as a string for the AI prompt."""
        status = self.get_device_status()
        switches = status["switches"]
        sw_lines = "\n".join(
            f"    - {name}: {'✅ ON' if state == 'on' else '⬜ OFF' if state == 'off' else '❓ ' + state}"
            for name, state in switches.items()
        )

        # Last command info
        last_cmds = []
        now = time.time()
        for key, ts in sorted(self._last_command.items(), key=lambda x: x[1], reverse=True)[:3]:
            ago = int(now - ts)
            if ago < 3600:
                last_cmds.append(f"    - {key} ({ago}s ago)")

        last_cmd_str = "\n".join(last_cmds) if last_cmds else "    - none"

        return f"""═══ DEVICE STATUS (InverterAgent: {status['inverter_brand']}) ═══
  Charging: {'✅ ACTIVE' if status['charging_active'] else '⬜ DISABLED'} (battery: {status['battery_power']:.0f}W)
  Work Mode: {status['work_mode']}
  DOD on Grid: {status['dod_on_grid']}%
  Switches:
{sw_lines}
  Recent commands:
{last_cmd_str}
  Dry-run: {'YES (log-only)' if status['dry_run'] else 'NO (live)'}"""
