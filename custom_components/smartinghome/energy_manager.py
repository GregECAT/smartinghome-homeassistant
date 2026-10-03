# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Energy management engine for Smarting HOME."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import (
    DEFAULT_GOODWE_DEVICE_ID,
    DEFAULT_BATTERY_CHARGE_CURRENT_MAX,
    DEFAULT_BATTERY_CHARGE_CURRENT_BLOCK,
    DEFAULT_EXPORT_LIMIT,
    DEFAULT_DOD_ON_GRID,
    DEFAULT_BATTERY_CAPACITY,
    G13Zone,
    G13_PRICES,
    HEMSMode,
    HEMSStrategy,
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
    SOC_NIGHT_CHARGE_TARGET,
    NIGHT_ARBITRAGE_MIN_FORECAST,
    SWITCH_BOILER,
    SWITCH_AC,
    SWITCH_SOCKET2,
    SELECT_WORK_MODE,
    NUMBER_DOD_ON_GRID,
    NUMBER_EXPORT_LIMIT,
    NUMBER_ECO_MODE_POWER,
    NUMBER_ECO_MODE_SOC,
    # Sofar Solar control entities
    INVERTER_BRAND_GOODWE,
    INVERTER_BRAND_SOFAR,
    SELECT_SOFAR_WORK_MODE,
    SELECT_SOFAR_EXPORT_SURPLUS,
    SELECT_SOFAR_TIMED_CONTROL,
    NUMBER_SOFAR_DOD,
    NUMBER_SOFAR_EXPORT_LIMIT,
    NUMBER_SOFAR_CHARGE_POWER,
    NUMBER_SOFAR_DISCHARGE_POWER,
    NUMBER_SOFAR_TIMED_PROGRAM,
    NUMBER_SOFAR_PASSIVE_GRID_POWER,
    NUMBER_SOFAR_PASSIVE_MAX_BATTERY,
    NUMBER_SOFAR_PASSIVE_MIN_BATTERY,
    TIME_SOFAR_CHARGE_START,
    TIME_SOFAR_CHARGE_END,
    TIME_SOFAR_DISCHARGE_START,
    TIME_SOFAR_DISCHARGE_END,
)

_LOGGER = logging.getLogger(__name__)

# GoodWe work modes → EMS modes of the newer GoodWe integration (select "EMS mode"
# + number "EMS power limit"), used when the "Inverter operation mode" select
# (general / eco_charge / eco_discharge) is not exposed.
_GOODWE_EMS_MODE_FOR_WORK_MODE: dict[str, str] = {
    "general": "auto",
    "eco_charge": "charge_battery",
    "eco_discharge": "discharge_battery",
    "battery_standby": "battery_standby",
}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


class EnergyManager:
    """HEMS Energy Management engine.

    Implements the 3-layer strategy:
    W1 — G13 schedule (time-based)
    W2 — RCE dynamic pricing (price-based)
    W3 — SOC safety (battery protection)
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device_id: str = DEFAULT_GOODWE_DEVICE_ID,
        strategy: HEMSStrategy = HEMSStrategy.BALANCED,
        inverter_brand: str = INVERTER_BRAND_GOODWE,
    ) -> None:
        """Initialize the energy manager."""
        self.hass = hass
        self._device_id = device_id
        self._strategy = strategy
        self._inverter_brand = inverter_brand
        self._current_mode = HEMSMode.AUTO
        self._voltage_cascade_active = False
        self._surplus_cascade_active = False
        self._last_charge_current = None
        self._last_export_limit = None
        # Voltage guard: export limit ceiling while the grid voltage is high (None = off).
        # Every other export limit request is clamped to it and restored when it goes.
        self.export_cap_w: int | None = None
        self._requested_export_limit: int | None = None
        self._control_error: str | None = None
        # What the inverter was last told to do: "charge_grid" | "sell" | "hold" |
        # "home" | "general". Safety layers (W0) must not undo deliberate grid
        # charging or holding.
        self.intent: str = "general"
        self._last_goodwe_reload: float = 0.0
        self._goodwe_reloads: list[float] = []
        self._started: float = time.monotonic()
        self._control_unavailable_since: float = 0.0

    @property
    def inverter_brand(self) -> str:
        """Return configured inverter brand."""
        return self._inverter_brand

    @property
    def current_mode(self) -> HEMSMode:
        """Return current HEMS mode."""
        return self._current_mode

    @property
    def strategy(self) -> HEMSStrategy:
        """Return current strategy."""
        return self._strategy

    # =========================================================================
    # Public API — Service Handlers
    # =========================================================================

    async def set_mode(self, mode: HEMSMode) -> None:
        """Set HEMS operating mode."""
        _LOGGER.info("Setting HEMS mode to %s", mode)
        self._current_mode = mode

        if mode == HEMSMode.AUTO:
            # Back to automatic: undo any forced charge/discharge on the inverter
            await self.set_general_mode()
        elif mode == HEMSMode.SELL:
            await self._block_charging()
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
        elif mode == HEMSMode.CHARGE:
            await self._enable_charging()
        elif mode == HEMSMode.PEAK_SAVE:
            await self._block_charging()
        elif mode == HEMSMode.NIGHT_ARBITRAGE:
            await self._enable_charging()
        elif mode == HEMSMode.EMERGENCY:
            await self._enable_charging()
        elif mode == HEMSMode.MANUAL:
            pass  # No automatic actions

    async def charge_pv_only(self) -> None:
        """Ładuj baterię TYLKO z PV — priorytet: dom → bateria.

        Tryb General / Self Use — falownik naturalnie priorytetyzuje:
        1. PV → dom (pokrycie zużycia)
        2. Nadwyżka PV → bateria (ładowanie)
        3. Sieć → NIE używana do ładowania baterii

        Użyj w dzień, gdy PV jest aktywne. Bezpieczny tryb —
        nigdy nie spowoduje importu z sieci na baterię.

        GoodWe: General mode + enable_charging + eco_mode_soc=100.
        Sofar:  Passive mode — grid_power=0 (zero grid), battery=max charge.
        """
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] charge_pv_only — Passive (grid=0, battery=+6000 max charge)")
            await self._sofar_set_passive(grid_power=0, max_battery=6000, min_battery=0)
        else:
            _LOGGER.info("charge_pv_only — General mode (PV → dom → bateria, bez importu z sieci)")
            await self._set_eco_mode_soc(100)
            await self._set_eco_mode_power(0)  # power=0 → no forced eco operation
            await self._set_work_mode("general")
            await self._enable_charging()
            await self._set_dod(DEFAULT_DOD_ON_GRID)
        self._current_mode = HEMSMode.CHARGE
        self.intent = "general"

    async def charge_from_grid(self, power_w: int | None = None) -> None:
        """Ładuj baterię z sieci + PV (agresywnie).

        ⚠️ UWAGA: Ten tryb POBIERA energię z sieci!
        Użyj TYLKO gdy:
        - Noc (brak PV) — arbitraż nocny
        - Cena RCE ujemna (darmowa energia)
        - SOC krytycznie niski + brak PV

        GoodWe: Eco Mode (eco_charge) — soc=100, power=100, charge_current=max.
        Sofar:  Passive mode — grid_power=+6000 (import), battery=max charge.
        """
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] charge_from_grid — Passive (grid=+6000 import, battery=+6000 charge)")
            await self._sofar_set_passive(grid_power=6000, max_battery=6000, min_battery=6000)
        elif self._goodwe_ems_select():
            _LOGGER.info("charge_from_grid — GoodWe EMS charge_battery (PV + sieć → bateria)")
            await self._enable_charging()
            await self._set_work_mode("eco_charge", power_w)  # → EMS charge_battery
        else:
            _LOGGER.info("charge_from_grid — eco_charge (PV + sieć → bateria, soc=100, power=100)")
            await self._set_eco_mode_soc(100)
            await self._set_eco_mode_power(100)
            await self._enable_charging()
            await self._set_work_mode("eco_charge")
        self._current_mode = HEMSMode.CHARGE
        self.intent = "charge_grid"

    async def force_charge(self) -> None:
        """Backwards-compatible alias → charge_from_grid().

        ⚠️ DEPRECATED: Użyj charge_pv_only() (dzień) lub charge_from_grid() (noc).
        """
        await self.charge_from_grid()

    async def force_discharge(self, power_w: int | None = None) -> None:
        """Force battery discharge to grid.

        GoodWe: Eco Mode (eco_discharge) — grid export ON, charge_current=0.
        Sofar:  Passive mode — grid_power=-6000 (export), battery=-6000 (discharge).
        """
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] force_discharge — Passive (grid=-6000 export, battery=-6000 discharge)")
            await self._sofar_set_passive(grid_power=-6000, max_battery=-6000, min_battery=-6000)
        elif self._goodwe_ems_select():
            _LOGGER.info("Forcing battery discharge via GoodWe EMS (discharge_battery)")
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
            await self._set_dod(95)
            await self._set_work_mode("eco_discharge", power_w)  # → EMS discharge_battery
        else:
            _LOGGER.info("Forcing battery discharge (eco_discharge, DOD=95%%, soc=5, power=100, grid_export=ON)")
            # Enable grid export (47509=1) — critical for BT! Optional RS485 hub.
            try:
                await self.hass.services.async_call(
                    "modbus",
                    "write_register",
                    {
                        "hub": "goodwe_rs485",
                        "slave": 247,
                        "address": 47509,
                        "value": 1,
                    },
                )
            except Exception as err:  # noqa: BLE001 — hub not configured on this install
                _LOGGER.debug("Modbus 47509 write skipped: %s", err)
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
            await self._block_charging()
            # DOD=95% means 95% of battery capacity CAN be discharged (SOC → ~5%)
            # DOD=5% would BLOCK discharge (only 5% usable)! This is the critical fix.
            await self._set_dod(95)
            await self._set_eco_mode_soc(5)
            await self._set_eco_mode_power(100)
            await self._set_work_mode("eco_discharge")
        self._current_mode = HEMSMode.SELL
        self.intent = "sell"

    async def stop_force_charge(self) -> None:
        """Stop forced charging — restore general/self-use mode."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] STOP force charge — restoring Self Use (safe idle)")
            await self._sofar_restore_self_use()
        else:
            _LOGGER.info("STOP force charge — restoring general mode")
            await self._set_eco_mode_power(0)
            await self._set_eco_mode_soc(100)
            await self._set_work_mode("general")
            await self._enable_charging()
            await self._set_dod(DEFAULT_DOD_ON_GRID)
        # Turn off force flag (if helper exists)
        _eid = "input_boolean.hems_force_grid_charge"
        if self.hass.states.get(_eid):
            try:
                await self.hass.services.async_call(
                    "input_boolean", "turn_off", {"entity_id": _eid},
                )
            except Exception:
                _LOGGER.debug("%s turn_off failed", _eid)
        self._current_mode = HEMSMode.AUTO
        self.intent = "general"

    async def stop_force_discharge(self) -> None:
        """Stop forced discharge — restore general/self-use mode."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] STOP force discharge — restoring Self Use (safe idle)")
            await self._sofar_restore_self_use()
        else:
            _LOGGER.info("STOP force discharge — restoring general mode")
            await self._set_eco_mode_power(0)
            await self._set_eco_mode_soc(100)
            await self._set_work_mode("general")
            await self._enable_charging()
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
            await self._set_dod(DEFAULT_DOD_ON_GRID)
        # Turn off force flag (if helper exists)
        _eid = "input_boolean.hems_force_battery_discharge"
        if self.hass.states.get(_eid):
            try:
                await self.hass.services.async_call(
                    "input_boolean", "turn_off", {"entity_id": _eid},
                )
            except Exception:
                _LOGGER.debug("%s turn_off failed", _eid)
        self._current_mode = HEMSMode.AUTO
        self.intent = "general"

    async def battery_to_home(self) -> None:
        """Battery powers the house — no charging, no forced export.

        This is what most autopilot rules mean by "discharge" (peak zone,
        grid import guard, low PV). force_discharge() instead EXPORTS the
        battery to the grid at full power — use it only for deliberate selling.
        """
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] battery_to_home — Passive (grid=0, battery discharge only)")
            await self._sofar_set_passive(grid_power=0, max_battery=0, min_battery=-6000)
        else:
            _LOGGER.info("battery_to_home — general mode, charging blocked (battery → house)")
            await self.set_general_mode()
            await self._block_charging()
        self._current_mode = HEMSMode.PEAK_SAVE
        self.intent = "home"

    async def pv_export(self) -> None:
        """PV surplus to the grid now, battery charging deferred (planner: cheaper RCE later).

        Same inverter state as battery_to_home (general mode, charging blocked —
        the battery still covers the house), but its own intent so the W0 guard
        does not undo it.
        """
        await self.battery_to_home()
        self.intent = "pv_export"

    async def battery_hold(self) -> None:
        """Keep the battery idle — no charge, no discharge (house runs on PV/grid)."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] battery_hold — Passive (battery=0)")
            await self._sofar_set_passive(grid_power=0, max_battery=0, min_battery=0)
        elif self._goodwe_ems_select():
            _LOGGER.info("battery_hold — GoodWe EMS battery_standby")
            await self._set_work_mode("battery_standby")
        else:
            # No EMS: block charging and allow no discharge (DOD 0), restored by general mode
            _LOGGER.info("battery_hold — general mode, charging blocked, DOD 0")
            await self._set_work_mode("general")
            await self._block_charging()
            await self._set_dod(0)
        self._current_mode = HEMSMode.MANUAL
        self.intent = "hold"

    async def set_general_mode(self) -> None:
        """Switch to General/Self Use mode — battery self-consumption."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] SET Self Use MODE — battery self-consumption (safe idle)")
            await self._sofar_restore_self_use()
        else:
            _LOGGER.info("SET GENERAL MODE — battery self-consumption (house powered by battery)")
            await self._set_eco_mode_power(0)
            await self._set_eco_mode_soc(100)
            await self._set_work_mode("general")
            await self._enable_charging()
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
            await self._set_dod(DEFAULT_DOD_ON_GRID)
        self._current_mode = HEMSMode.AUTO
        self.intent = "general"

    async def emergency_stop(self) -> None:
        """Emergency stop — kill all forced operations immediately."""
        _LOGGER.warning("EMERGENCY STOP — killing all force operations")
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            await self._sofar_restore_self_use()
        else:
            await self._set_eco_mode_power(0)
            await self._set_eco_mode_soc(100)
            # Reset EMS mode register
            try:
                await self.hass.services.async_call(
                    "modbus", "write_register",
                    {"hub": "goodwe_rs485", "slave": 247, "address": 47511, "value": 0},
                )
            except Exception:
                _LOGGER.debug("Modbus 47511 reset skipped")
            await self._set_work_mode("general")
            await self._enable_charging()
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
        for entity_id in (
            "input_boolean.hems_force_grid_charge",
            "input_boolean.hems_force_battery_discharge",
        ):
            if self.hass.states.get(entity_id):
                try:
                    await self.hass.services.async_call(
                        "input_boolean", "turn_off", {"entity_id": entity_id},
                    )
                except Exception:
                    _LOGGER.debug("%s turn_off failed", entity_id)
        self._current_mode = HEMSMode.AUTO
        self.intent = "general"

    async def force_custom(
        self,
        work_mode: str | None = None,
        modbus_47511: int | None = None,
        charge_current: str | None = None,
        export_limit: int | None = None,
        eco_mode_power: int | None = None,
        eco_mode_soc: int | None = None,
    ) -> dict[str, Any]:
        """Execute custom force command with user-specified parameters.

        Each parameter is optional — only provided values are applied.
        This allows manual testing of different inverter configurations.
        """
        results: list[str] = []

        # 1. Eco Mode SOC (set early — before power and mode switch)
        if eco_mode_soc is not None:
            await self._set_eco_mode_soc(eco_mode_soc)
            results.append(f"eco_mode_soc={eco_mode_soc}")

        # 2. Eco Mode Power
        if eco_mode_power is not None:
            await self._set_eco_mode_power(eco_mode_power)
            results.append(f"eco_mode_power={eco_mode_power}")

        # 3. Set charge current
        if charge_current is not None:
            await self._set_charge_current(charge_current)
            results.append(f"charge_current={charge_current}")

        # 4. Set export limit
        if export_limit is not None:
            await self._set_export_limit(export_limit)
            results.append(f"export_limit={export_limit}")

        # 5. Write Modbus register 47511 (EMS mode, -1 = skip)
        if modbus_47511 is not None and modbus_47511 >= 0:
            await self.hass.services.async_call(
                "modbus",
                "write_register",
                {
                    "hub": "goodwe_rs485",
                    "slave": 247,
                    "address": 47511,
                    "value": modbus_47511,
                },
            )
            results.append(f"modbus_47511={modbus_47511}")

        # 6. Set work mode (LAST — per Instrukcja.md)
        if work_mode is not None:
            await self._set_work_mode(work_mode)
            results.append(f"work_mode={work_mode}")

        _LOGGER.info("Force custom executed: %s", ", ".join(results) or "no actions")
        return {"success": True, "actions": results}

    async def set_export_limit(self, limit: int) -> None:
        """Set grid export limit."""
        _LOGGER.info("Setting export limit to %d W", limit)
        await self._set_export_limit(limit)

    # =========================================================================
    # Voltage Protection Cascade
    # =========================================================================

    async def check_voltage_protection(
        self,
        voltage_l1: float,
        voltage_l2: float,
        voltage_l3: float,
        soc: float,
    ) -> dict[str, Any]:
        """Check voltage protection cascade.

        Returns dict with actions taken.
        """
        max_voltage = max(voltage_l1, voltage_l2, voltage_l3)
        actions: dict[str, Any] = {
            "max_voltage": max_voltage,
            "cascade_active": False,
            "actions": [],
        }

        if max_voltage > VOLTAGE_THRESHOLD_CRITICAL:
            # Tier 3: > 254V → Restore battery charging
            await self._enable_charging()
            await self._cascade_boiler(True)
            await self._switch_on(SWITCH_AC)
            self._voltage_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = [
                "boiler_on", "ac_on", "battery_charging_restored"
            ]
            _LOGGER.warning(
                "Voltage cascade T3: %.1fV — All loads ON + charging restored",
                max_voltage,
            )

        elif max_voltage > VOLTAGE_THRESHOLD_HIGH:
            # Tier 2: > 253V → Boiler + AC
            await self._cascade_boiler(True)
            await self._switch_on(SWITCH_AC)
            self._voltage_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on", "ac_on"]
            _LOGGER.warning(
                "Voltage cascade T2: %.1fV — AC ON%s", max_voltage, "" if self.boiler_owned_elsewhere else " + Boiler ON"
            )

        elif max_voltage > VOLTAGE_THRESHOLD_WARNING:
            # Tier 1: > 252V → Boiler only
            await self._cascade_boiler(True)
            self._voltage_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on"]
            _LOGGER.warning(
                "Voltage cascade T1: %.1fV — Boiler ON%s", max_voltage, " (via W4b)" if self.boiler_owned_elsewhere else ""
            )

        elif max_voltage < VOLTAGE_THRESHOLD_RECOVERY and self._voltage_cascade_active:
            # Recovery: < 248V for 5 min
            await self._cascade_boiler(False)
            await self._switch_off(SWITCH_AC)
            self._voltage_cascade_active = False
            actions["actions"] = ["cascade_recovered"]
            _LOGGER.info(
                "Voltage cascade recovered: %.1fV", max_voltage
            )

        return actions

    # =========================================================================
    # PV Surplus Cascade
    # =========================================================================

    async def check_pv_surplus(
        self, surplus_power: float, soc: float
    ) -> dict[str, Any]:
        """Check PV surplus cascade for load management.

        Returns dict with actions taken.
        """
        actions: dict[str, Any] = {
            "surplus_power": surplus_power,
            "cascade_active": False,
            "actions": [],
        }

        # Emergency — SOC too low
        if soc < 50:
            if self._surplus_cascade_active:
                await self._cascade_boiler(False)
                await self._switch_off(SWITCH_AC)
                await self._switch_off(SWITCH_SOCKET2)
                self._surplus_cascade_active = False
                actions["actions"] = ["emergency_all_off"]
            return actions

        # Not enough surplus — turn off
        if surplus_power < PV_SURPLUS_OFF:
            if self._surplus_cascade_active:
                await self._cascade_boiler(False)
                await self._switch_off(SWITCH_AC)
                await self._switch_off(SWITCH_SOCKET2)
                self._surplus_cascade_active = False
                actions["actions"] = ["surplus_low_all_off"]
            return actions

        # Tier 3: > 4kW surplus + SOC > 90%
        if surplus_power > PV_SURPLUS_TIER3 and soc >= PV_SURPLUS_MIN_SOC_TIER3:
            await self._cascade_boiler(True)
            await self._switch_on(SWITCH_AC)
            await self._switch_on(SWITCH_SOCKET2)
            self._surplus_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on", "ac_on", "socket2_on"]

        # Tier 2: > 3kW surplus + SOC > 85%
        elif surplus_power > PV_SURPLUS_TIER2 and soc >= PV_SURPLUS_MIN_SOC_TIER2:
            await self._cascade_boiler(True)
            await self._switch_on(SWITCH_AC)
            self._surplus_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on", "ac_on"]

        # Tier 1: > 2kW surplus + SOC > 80%
        elif surplus_power > PV_SURPLUS_TIER1 and soc >= PV_SURPLUS_MIN_SOC_TIER1:
            await self._cascade_boiler(True)
            self._surplus_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on"]

        return actions

    # =========================================================================
    # SOC Safety Layer
    # =========================================================================

    async def check_soc_safety(
        self,
        soc: float,
        hour: int,
        forecast_tomorrow: float = 0.0,
    ) -> dict[str, Any]:
        """Check SOC safety conditions.

        Returns dict with actions taken.
        """
        actions: dict[str, Any] = {
            "soc": soc,
            "actions": [],
        }

        # Emergency: SOC < 5% (próg krytyczny)
        if soc < SOC_EMERGENCY:
            await self._enable_charging()
            self._current_mode = HEMSMode.EMERGENCY
            actions["actions"].append("emergency_charge")
            _LOGGER.warning("SOC emergency: %.0f%% — Charging NOW", soc)

        # 11:00 check: SOC < 50%
        elif hour == 11 and soc < SOC_CHECK_11_THRESHOLD:
            await self._enable_charging()
            actions["actions"].append("soc_check_11_charge")
            _LOGGER.info("11:00 SOC check: %.0f%% < 50%% — Enabling charge", soc)

        # 12:00 check: SOC < 70%
        elif hour == 12 and soc < SOC_CHECK_12_THRESHOLD:
            await self._enable_charging()
            actions["actions"].append("soc_check_12_charge")
            _LOGGER.info("12:00 SOC check: %.0f%% < 70%% — Enabling charge", soc)

        # Battery protection based on forecast
        if forecast_tomorrow < 5.0:
            # Low forecast — protect battery (DOD 70%)
            await self._set_dod(70)
            actions["actions"].append("low_forecast_dod_70")
        elif forecast_tomorrow > 0:
            # Good forecast — full DOD
            await self._set_dod(DEFAULT_DOD_ON_GRID)
            actions["actions"].append("normal_dod_95")

        return actions

    # =========================================================================
    # Night Arbitrage
    # =========================================================================

    async def check_night_arbitrage(
        self,
        soc: float,
        hour: int,
        forecast_tomorrow: float,
    ) -> dict[str, Any]:
        """Check conditions for night arbitrage.

        Returns dict with actions and whether arbitrage should be active.
        """
        actions: dict[str, Any] = {
            "eligible": False,
            "active": False,
            "actions": [],
            "potential_profit": 0.0,
        }

        # Conditions: 23:00, bad forecast, low SOC
        if (
            hour == 23
            and forecast_tomorrow < NIGHT_ARBITRAGE_MIN_FORECAST
            and soc < 50
        ):
            actions["eligible"] = True
            # Calculate potential
            capacity = DEFAULT_BATTERY_CAPACITY / 1000
            profit = capacity * (
                G13_PRICES[G13Zone.AFTERNOON_PEAK]
                - G13_PRICES[G13Zone.OFF_PEAK]
            )
            actions["potential_profit"] = round(profit, 2)

            await self._enable_charging()
            self._current_mode = HEMSMode.NIGHT_ARBITRAGE
            actions["active"] = True
            actions["actions"].append("night_charge_started")
            _LOGGER.info(
                "Night arbitrage started (forecast: %.1fkWh, SOC: %.0f%%, "
                "profit potential: %.2f PLN)",
                forecast_tomorrow, soc, profit,
            )

        # Stop conditions: SOC > 90% or hour = 6
        elif self._current_mode == HEMSMode.NIGHT_ARBITRAGE:
            if soc >= SOC_NIGHT_CHARGE_TARGET or hour >= 6:
                self._current_mode = HEMSMode.AUTO
                actions["actions"].append("night_charge_stopped")
                _LOGGER.info(
                    "Night arbitrage stopped (SOC: %.0f%%, hour: %d)",
                    soc, hour,
                )

        return actions

    # =========================================================================
    # Private helpers — Brand-aware inverter control
    # =========================================================================

    @property
    def _is_sofar(self) -> bool:
        """Return True if configured for Sofar Solar."""
        return self._inverter_brand == INVERTER_BRAND_SOFAR

    async def _enable_charging(self) -> None:
        """Enable battery charging."""
        if self._is_sofar:
            # In Passive mode: set battery max to allow charge
            await self._sofar_set_passive(grid_power=0, max_battery=6000, min_battery=0)
        else:
            await self.hass.services.async_call(
                "goodwe",
                "set_parameter",
                {
                    "device_id": self._goodwe_device_id(),
                    "parameter": "battery_charge_current",
                    "value": DEFAULT_BATTERY_CHARGE_CURRENT_MAX,
                },
            )

    async def _block_charging(self) -> None:
        """Block battery charging."""
        if self._is_sofar:
            # In Passive mode: set battery max to 0 (no charge)
            await self._sofar_set_passive(grid_power=0, max_battery=0, min_battery=0)
        else:
            await self.hass.services.async_call(
                "goodwe",
                "set_parameter",
                {
                    "device_id": self._goodwe_device_id(),
                    "parameter": "battery_charge_current",
                    "value": DEFAULT_BATTERY_CHARGE_CURRENT_BLOCK,
                },
            )

    async def _set_charge_current(self, value: int | str) -> None:
        """Set battery charge current."""
        value_str = str(value)
        if self._last_charge_current == value_str:
            return
        self._last_charge_current = value_str

        if self._is_sofar:
            # Sofar doesn't use current — convert to approximate power
            try:
                current_val = float(value)
                power_approx = int(current_val * 52)  # ~52V battery
                await self._sofar_set_charge_power(power_approx)
            except (ValueError, TypeError):
                _LOGGER.debug("Cannot parse charge current for Sofar: %s", value)
        else:
            await self.hass.services.async_call(
                "goodwe",
                "set_parameter",
                {
                    "device_id": self._goodwe_device_id(),
                    "parameter": "battery_charge_current",
                    "value": value,
                },
            )

    def current_export_limit(self) -> int | None:
        """The inverter's export limit as reported by its number entity."""
        entity = self._find_goodwe_number(NUMBER_EXPORT_LIMIT, "grid_export_limit")
        state = self.hass.states.get(entity) if entity else None
        val = _safe_int(state.state, -1) if state else -1
        return val if val >= 0 else None

    async def set_export_cap(self, cap: int | None) -> None:
        """Voltage guard: cap the export (W), or None to restore the limit the
        rest of the system asked for (or the one the inverter had before)."""
        if cap is not None and self.export_cap_w is None and self._requested_export_limit is None:
            self._requested_export_limit = self.current_export_limit()
        self.export_cap_w = cap
        await self._set_export_limit(
            self._requested_export_limit if self._requested_export_limit is not None else DEFAULT_EXPORT_LIMIT
        )

    async def _set_export_limit(self, limit: int) -> None:
        """Set grid export limit (clamped by the voltage guard's cap)."""
        self._requested_export_limit = limit
        if self.export_cap_w is not None:
            limit = min(limit, self.export_cap_w)
        if self._is_sofar:
            if self._last_export_limit == limit:
                return
            self._last_export_limit = limit
            await self._sofar_set_export_limit(limit)
            return

        # Prefer the GoodWe number entity: goodwe.set_parameter("grid_export_limit")
        # did not change the limit on GW8K-ET, number.set_value did (tested).
        entity = self._find_goodwe_number(NUMBER_EXPORT_LIMIT, "grid_export_limit")
        if entity:
            state = self.hass.states.get(entity)
            if state and _safe_int(state.state, -1) == limit:
                return
            await self.hass.services.async_call(
                "number", "set_value", {"entity_id": entity, "value": limit},
            )
            self._last_export_limit = limit
            return

        if self._last_export_limit == limit:
            return
        self._last_export_limit = limit
        await self.hass.services.async_call(
            "goodwe",
            "set_parameter",
            {
                "device_id": self._goodwe_device_id(),
                "parameter": "grid_export_limit",
                "value": str(limit),
            },
        )

    async def _set_dod(self, dod: int) -> None:
        """Set depth of discharge on grid."""
        if self._is_sofar:
            entity = NUMBER_SOFAR_DOD
            if not entity:
                # Sofar: DOD is read-only (sensor.sofarsolar_battery_dod),
                # set implicitly by storage mode — nothing to do here.
                _LOGGER.debug("[Sofar] DOD is read-only — skipping set to %d%%", dod)
                return
        else:
            entity = self._find_goodwe_number(NUMBER_DOD_ON_GRID, "depth_of_discharge_on_grid") or NUMBER_DOD_ON_GRID
        state = self.hass.states.get(entity)
        if not state:
            _LOGGER.warning("Entity %s not available — skipping DOD set to %d%%", entity, dod)
            return
        current_dod = state.state
        dod = max(0, min(dod, DEFAULT_DOD_ON_GRID))
        try:
            if round(float(current_dod)) == dod:
                return  # already set — don't rewrite the inverter register
        except (TypeError, ValueError):
            pass
        _LOGGER.warning(
            "Setting DOD on-grid: %s → %d%% (current: %s)",
            entity, dod, current_dod,
        )
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity,
                "value": dod,
            },
        )

    def _goodwe_entity_ids(self, domain: str) -> list[str]:
        """Enabled entities of the GoodWe integration in a domain."""
        registry = er.async_get(self.hass)
        return [
            entry.entity_id
            for entry in registry.entities.values()
            if entry.platform == "goodwe"
            and entry.domain == domain
            and entry.disabled_by is None
        ]

    def _find_goodwe_select(self, option: str) -> str | None:
        """Find a GoodWe select offering `option` (entity IDs vary by language/setup).

        Options come from the entity registry capabilities too, so a select that is
        briefly unavailable (e.g. right after restart) is still recognised — falling
        back to another control path then would do the wrong thing.
        """
        registry = er.async_get(self.hass)
        for entity_id in self._goodwe_entity_ids("select"):
            state = self.hass.states.get(entity_id)
            options = (state.attributes.get("options") if state else None) or []
            if not options:
                entry = registry.async_get(entity_id)
                options = ((entry.capabilities or {}).get("options") if entry else None) or []
            if option in options:
                return entity_id
        return None

    def _entity_unavailable(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        return state is None or state.state in ("unavailable", "unknown")

    async def _reload_goodwe_for(self, entity_id: str, reason: str) -> bool:
        """Reload the GoodWe config entry owning entity_id.

        Not in the first 3 min after start, at most every 3 min and 3× per hour.
        """
        entry = er.async_get(self.hass).async_get(entity_id)
        now = time.monotonic()
        self._goodwe_reloads = [t for t in self._goodwe_reloads if now - t < 3600]
        if (
            not entry or entry.platform != "goodwe" or not entry.config_entry_id
            # GoodWe may still be starting — reloading it then made EMS unavailable
            or now - self._started < 180
            or now - self._last_goodwe_reload < 180
            or len(self._goodwe_reloads) >= 3
        ):
            return False
        self._last_goodwe_reload = now
        self._goodwe_reloads.append(now)
        _LOGGER.warning("GoodWe watchdog: %s — reloading the GoodWe integration", reason)
        try:
            await self.hass.config_entries.async_reload(entry.config_entry_id)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("GoodWe reload failed: %s", err)
            return False
        return True

    async def _ensure_available(self, entity_id: str, timeout: int = 25) -> bool:
        """Self-heal an unavailable GoodWe control entity.

        After an HA restart the GoodWe integration sometimes never reads the EMS
        mode and the select stays unavailable (service calls on it are ignored);
        reloading the GoodWe config entry fixes it (verified on GW8K-ET).
        """
        if not self._entity_unavailable(entity_id):
            return True
        await self._reload_goodwe_for(entity_id, f"{entity_id} unavailable")
        for _ in range(timeout):
            if not self._entity_unavailable(entity_id):
                return True
            await asyncio.sleep(1)
        return False

    @property
    def in_startup_grace(self) -> bool:
        """First 3 min after start: writing to GoodWe while it is still starting
        made its EMS select unavailable (seen on every HA restart on bobrek)."""
        return time.monotonic() - self._started < 180

    def ems_state(self) -> str | None:
        """Current GoodWe EMS mode (None if the integration has no EMS select)."""
        select = self._goodwe_ems_select()
        state = self.hass.states.get(select) if select else None
        return state.state if state else None

    async def async_watchdog(self, battery_power_entity: str = "") -> str | None:
        """Called every coordinator cycle — heal a stuck GoodWe integration.

        - control select (EMS / operation mode) unavailable for > 2 min
        - battery power sensor not reporting for > 10 min (frozen data)
        Returns a message when a reload was triggered.
        """
        if self._is_sofar:
            return None
        control = self._goodwe_ems_select() or self._goodwe_operation_mode_select()
        if not control:
            return None
        now = time.monotonic()
        if self._entity_unavailable(control):
            if not self._control_unavailable_since:
                self._control_unavailable_since = now
            elif now - self._control_unavailable_since > 120:
                if await self._reload_goodwe_for(control, f"{control} unavailable > 2 min"):
                    self._control_unavailable_since = 0.0
                    return f"🔧 Watchdog: {control} niedostępny > 2 min — przeładowano integrację GoodWe"
            return None
        self._control_unavailable_since = 0.0

        state = self.hass.states.get(battery_power_entity) if battery_power_entity else None
        reported = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
        if state is not None and reported is not None:
            from homeassistant.util import dt as dt_util

            age = (dt_util.utcnow() - reported).total_seconds()
            if age > 600 and await self._reload_goodwe_for(
                battery_power_entity, f"{battery_power_entity} not reporting for {age:.0f} s"
            ):
                return (
                    f"🔧 Watchdog: brak nowych danych z falownika od {age / 60:.0f} min "
                    "— przeładowano integrację GoodWe"
                )
        return None

    def _find_goodwe_number(self, preferred: str, suffix: str) -> str | None:
        """Return `preferred` if present, else a GoodWe number ending with `suffix`."""
        if preferred and self.hass.states.get(preferred):
            return preferred
        for entity_id in self._goodwe_entity_ids("number"):
            if entity_id.endswith(suffix) and self.hass.states.get(entity_id):
                return entity_id
        return None

    def _goodwe_device_id(self) -> str:
        """Device ID of the GoodWe inverter (for goodwe.set_parameter).

        The configured ID defaults to a constant from another install; prefer the
        device that actually owns the GoodWe control entities.
        """
        registry = er.async_get(self.hass)
        for entity_id in self._goodwe_entity_ids("select") + self._goodwe_entity_ids("number"):
            entry = registry.async_get(entity_id)
            if entry and entry.device_id:
                return entry.device_id
        return self._device_id

    def _goodwe_ems_select(self) -> str | None:
        """GoodWe "EMS mode" select, if the integration exposes it."""
        if self._is_sofar:
            return None
        return self._find_goodwe_select("discharge_battery")

    def _goodwe_operation_mode_select(self, option: str = "general") -> str | None:
        """GoodWe "Inverter operation mode" select (general / eco_* ...)."""
        if self.hass.states.get(SELECT_WORK_MODE):
            return SELECT_WORK_MODE
        return self._find_goodwe_select(option)

    def raise_on_control_error(self) -> None:
        """Raise (for service calls) if the last command could not reach the inverter."""
        if self._control_error:
            error, self._control_error = self._control_error, None
            raise HomeAssistantError(error)

    async def _set_work_mode(self, mode: str, power_w: int | None = None) -> None:
        """Set inverter work mode via select entity."""
        if self._is_sofar:
            entity = SELECT_SOFAR_WORK_MODE
            if not self.hass.states.get(entity):
                _LOGGER.debug("Entity %s not available — skipping work mode set", entity)
                return
            await self.hass.services.async_call(
                "select", "select_option", {"entity_id": entity, "option": mode},
            )
            return

        # 1) EMS mode (newer GoodWe integration/firmware) — direct and immediate.
        #    Preferred: on GW8K-ET "eco_discharge" via operation mode left the battery
        #    idle (house fell back to grid), while EMS "discharge_battery" discharged
        #    at full power right away (verified 2026-09-30).
        ems_mode = _GOODWE_EMS_MODE_FOR_WORK_MODE.get(mode)
        ems_select = self._goodwe_ems_select()
        if ems_mode and ems_select:
            if not await self._ensure_available(ems_select):
                self._control_error = (
                    f"{ems_select} jest niedostępny — watchdog przeładuje integrację GoodWe "
                    "(jeśli to się powtarza, sprawdź połączenie z falownikiem)."
                )
                _LOGGER.warning(self._control_error)
                return
            if ems_mode == "auto":
                # Also leave any eco_* operation mode set by older versions/users
                op_select = self._goodwe_operation_mode_select()
                if op_select:
                    await self.hass.services.async_call(
                        "select", "select_option", {"entity_id": op_select, "option": "general"},
                    )
            power_entity = self._find_goodwe_number("", "ems_power_limit")
            if power_entity:
                power_state = self.hass.states.get(power_entity)
                # The limit is the charge/discharge power; any other mode must get 0 —
                # battery_standby with a non-zero limit CHARGED from grid (tested on ET)
                max_w = _safe_int(power_state.attributes.get("max") if power_state else None, 10000)
                power = (
                    min(int(power_w), max_w) if power_w else max_w
                ) if ems_mode in ("charge_battery", "discharge_battery") else 0
                await self.hass.services.async_call(
                    "number", "set_value", {"entity_id": power_entity, "value": power},
                )
            await self.hass.services.async_call(
                "select", "select_option", {"entity_id": ems_select, "option": ems_mode},
            )
            _LOGGER.info("GoodWe EMS mode → %s (work mode %s)", ems_mode, mode)
            self._control_error = None
            return

        # 2) "Inverter operation mode" select (general / eco_charge / eco_discharge)
        entity = self._goodwe_operation_mode_select(mode)
        if entity:
            await self.hass.services.async_call(
                "select", "select_option", {"entity_id": entity, "option": mode},
            )
            self._control_error = None
            return

        self._control_error = (
            f"Nie znaleziono encji trybu pracy falownika GoodWe dla trybu '{mode}' "
            "(ani 'Inverter operation mode', ani 'EMS mode') — sprawdź integrację GoodWe."
        )
        _LOGGER.warning(self._control_error)

    async def _set_eco_mode_power(self, value: int) -> None:
        """Set Eco Mode power percentage (0-100%)."""
        entity = self._find_goodwe_number(NUMBER_ECO_MODE_POWER, "eco_mode_power")
        if not entity:
            _LOGGER.debug("Entity %s not available — skipping eco mode power set", NUMBER_ECO_MODE_POWER)
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity,
                "value": value,
            },
        )

    async def _set_eco_mode_soc(self, value: int) -> None:
        """Set Eco Mode target SOC percentage (0-100%)."""
        entity = self._find_goodwe_number(NUMBER_ECO_MODE_SOC, "eco_mode_soc")
        if not entity:
            _LOGGER.debug("Entity %s not available — skipping eco mode SOC set", NUMBER_ECO_MODE_SOC)
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity,
                "value": value,
            },
        )

    # The boiler has its own owner (boiler_surplus.py) when that is enabled;
    # the voltage / PV-surplus cascades then switch only the other loads.
    boiler_owned_elsewhere: bool = False

    async def _cascade_boiler(self, on: bool) -> None:
        if self.boiler_owned_elsewhere:
            return
        if on:
            await self._switch_on(SWITCH_BOILER)
        else:
            await self._switch_off(SWITCH_BOILER)

    async def _switch_on(self, entity_id: str) -> None:
        """Turn on a switch."""
        await self.hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": entity_id},
        )

    async def _switch_off(self, entity_id: str) -> None:
        """Turn off a switch."""
        await self.hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": entity_id},
        )

    # =========================================================================
    # Sofar Solar — specific control helpers
    # =========================================================================

    async def _sofar_set_passive(
        self,
        grid_power: int,
        max_battery: int,
        min_battery: int,
    ) -> None:
        """Set Sofar to Passive mode with precise power targets.

        Passive mode gives HEMS direct, watt-level control over:
        - grid_power:  target grid flow (W). + = import, - = export, 0 = zero exchange
        - max_battery:  max battery power (W). + = charge, - = discharge
        - min_battery:  min battery power (W). + = charge, - = discharge

        Typical usage patterns:
        - charge_pv_only:  grid=0,     max=+6000, min=0      (PV→bat, no grid)
        - charge_from_grid: grid=+6000, max=+6000, min=+6000  (grid→bat, force)
        - force_discharge:  grid=-6000, max=-6000, min=-6000  (bat→grid, force)
        - hold (no flow):   grid=0,     max=0,     min=0      (freeze)
        """
        # Set power targets FIRST, then switch mode
        await self._sofar_set_number(NUMBER_SOFAR_PASSIVE_GRID_POWER, grid_power)
        await self._sofar_set_number(NUMBER_SOFAR_PASSIVE_MAX_BATTERY, max_battery)
        await self._sofar_set_number(NUMBER_SOFAR_PASSIVE_MIN_BATTERY, min_battery)
        # Activate Passive mode
        await self._set_work_mode("Passive")
        _LOGGER.info(
            "[Sofar] Passive mode: grid=%+dW, battery=[%+d, %+d]W",
            grid_power, min_battery, max_battery,
        )

    async def _sofar_restore_self_use(self) -> None:
        """Restore Sofar to Self Use mode — safe autonomous operation.

        Self Use is the safe fallback: inverter manages charge/discharge
        automatically to maximize self-consumption. No HEMS intervention needed.
        """
        await self._set_work_mode("Self Use")
        _LOGGER.info("[Sofar] Restored to Self Use (safe idle)")

    async def _sofar_set_number(self, entity_id: str, value: int) -> None:
        """Set a Sofar number entity value with safety checks."""
        if not entity_id:
            return
        if not self.hass.states.get(entity_id):
            _LOGGER.debug(
                "[Sofar] Entity %s not available — skipping set to %d",
                entity_id, value,
            )
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": entity_id,
                "value": value,
            },
        )
        _LOGGER.debug("[Sofar] %s → %d", entity_id, value)

    async def _sofar_set_charge_power(self, power_w: int) -> None:
        """Set Sofar timed charge power limit (W).

        Uses number.set_value on the Sofar timed charge power entity.
        Typical range: 0–6000W depending on model.
        """
        power_w = max(0, min(power_w, 6000))
        await self._sofar_set_number(NUMBER_SOFAR_CHARGE_POWER, power_w)

    async def _sofar_set_discharge_power(self, power_w: int) -> None:
        """Set Sofar timed discharge power limit (W).

        Uses number.set_value on the Sofar timed discharge power entity.
        Typical range: 0–6000W depending on model.
        """
        power_w = max(0, min(power_w, 6000))
        await self._sofar_set_number(NUMBER_SOFAR_DISCHARGE_POWER, power_w)

    async def _sofar_set_export_limit(self, limit_w: int) -> None:
        """Set Sofar export surplus power (W).

        Uses number.set_value on the Sofar export surplus power entity.
        """
        limit_w = max(0, min(limit_w, 16000))
        await self._sofar_set_number(NUMBER_SOFAR_EXPORT_LIMIT, limit_w)
