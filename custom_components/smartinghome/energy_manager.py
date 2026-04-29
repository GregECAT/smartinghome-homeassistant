"""Energy management engine for Smarting HOME."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

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
    NUMBER_ECO_MODE_POWER,
    NUMBER_ECO_MODE_SOC,
    # Sofar Solar control entities
    INVERTER_BRAND_GOODWE,
    INVERTER_BRAND_SOFAR,
    SELECT_SOFAR_WORK_MODE,
    NUMBER_SOFAR_DOD,
    NUMBER_SOFAR_EXPORT_LIMIT,
    NUMBER_SOFAR_CHARGE_POWER,
    NUMBER_SOFAR_DISCHARGE_POWER,
)

_LOGGER = logging.getLogger(__name__)


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

        if mode == HEMSMode.SELL:
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

    async def force_charge(self) -> None:
        """Force battery charging.

        GoodWe: Eco Mode (eco_charge) — soc=100, power=100, charge_current=max.
        Sofar: Self Use — charge_power=max, discharge_power=0.
        """
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] Forcing battery charge (Self Use, max charge, zero discharge)")
            await self._sofar_set_charge_power(6000)  # Max charge power
            await self._sofar_set_discharge_power(0)   # Block discharge
            await self._set_work_mode("Self Use")
        else:
            _LOGGER.info("Forcing battery charge (eco_charge, soc=100, power=100)")
            await self._set_eco_mode_soc(100)
            await self._set_eco_mode_power(100)
            await self._enable_charging()
            await self._set_work_mode("eco_charge")
        self._current_mode = HEMSMode.CHARGE

    async def force_discharge(self) -> None:
        """Force battery discharge to grid.

        GoodWe: Eco Mode (eco_discharge) — grid export ON, charge_current=0.
        Sofar: Self Use — charge_power=0, discharge_power=max, export_limit=max.
        """
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] Forcing battery discharge (Self Use, max discharge, zero charge)")
            await self._sofar_set_charge_power(0)       # Block charge
            await self._sofar_set_discharge_power(6000)  # Max discharge
            await self._sofar_set_export_limit(16000)    # Allow grid export
            await self._set_work_mode("Self Use")
        else:
            _LOGGER.info("Forcing battery discharge (eco_discharge, DOD=95%%, soc=5, power=100, grid_export=ON)")
            # Enable grid export (47509=1) — critical for BT!
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
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
            await self._block_charging()
            # DOD=95% means 95% of battery capacity CAN be discharged (SOC → ~5%)
            # DOD=5% would BLOCK discharge (only 5% usable)! This is the critical fix.
            await self._set_dod(95)
            await self._set_eco_mode_soc(5)
            await self._set_eco_mode_power(100)
            await self._set_work_mode("eco_discharge")
        self._current_mode = HEMSMode.SELL

    async def stop_force_charge(self) -> None:
        """Stop forced charging — restore general/self-use mode."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] STOP force charge — restoring Self Use auto mode")
            await self._sofar_set_charge_power(3000)     # Restore auto charge
            await self._sofar_set_discharge_power(3000)   # Restore auto discharge
            await self._set_work_mode("Self Use")
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

    async def stop_force_discharge(self) -> None:
        """Stop forced discharge — restore general/self-use mode."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] STOP force discharge — restoring Self Use auto mode")
            await self._sofar_set_charge_power(3000)
            await self._sofar_set_discharge_power(3000)
            await self._sofar_set_export_limit(16000)
            await self._set_work_mode("Self Use")
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

    async def set_general_mode(self) -> None:
        """Switch to General/Self Use mode — battery self-consumption."""
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            _LOGGER.info("[Sofar] SET Self Use MODE — battery self-consumption")
            await self._sofar_set_charge_power(3000)
            await self._sofar_set_discharge_power(3000)
            await self._sofar_set_export_limit(16000)
            await self._set_work_mode("Self Use")
        else:
            _LOGGER.info("SET GENERAL MODE — battery self-consumption (house powered by battery)")
            await self._set_eco_mode_power(0)
            await self._set_eco_mode_soc(100)
            await self._set_work_mode("general")
            await self._enable_charging()
            await self._set_export_limit(DEFAULT_EXPORT_LIMIT)
            await self._set_dod(DEFAULT_DOD_ON_GRID)
        self._current_mode = HEMSMode.AUTO

    async def emergency_stop(self) -> None:
        """Emergency stop — kill all forced operations immediately."""
        _LOGGER.warning("EMERGENCY STOP — killing all force operations")
        if self._inverter_brand == INVERTER_BRAND_SOFAR:
            await self._sofar_set_charge_power(3000)
            await self._sofar_set_discharge_power(3000)
            await self._sofar_set_export_limit(16000)
            await self._set_work_mode("Self Use")
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
            await self._switch_on(SWITCH_BOILER)
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
            await self._switch_on(SWITCH_BOILER)
            await self._switch_on(SWITCH_AC)
            self._voltage_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on", "ac_on"]
            _LOGGER.warning(
                "Voltage cascade T2: %.1fV — Boiler + AC ON", max_voltage
            )

        elif max_voltage > VOLTAGE_THRESHOLD_WARNING:
            # Tier 1: > 252V → Boiler only
            await self._switch_on(SWITCH_BOILER)
            self._voltage_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on"]
            _LOGGER.warning(
                "Voltage cascade T1: %.1fV — Boiler ON", max_voltage
            )

        elif max_voltage < VOLTAGE_THRESHOLD_RECOVERY and self._voltage_cascade_active:
            # Recovery: < 248V for 5 min
            await self._switch_off(SWITCH_BOILER)
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
                await self._switch_off(SWITCH_BOILER)
                await self._switch_off(SWITCH_AC)
                await self._switch_off(SWITCH_SOCKET2)
                self._surplus_cascade_active = False
                actions["actions"] = ["emergency_all_off"]
            return actions

        # Not enough surplus — turn off
        if surplus_power < PV_SURPLUS_OFF:
            if self._surplus_cascade_active:
                await self._switch_off(SWITCH_BOILER)
                await self._switch_off(SWITCH_AC)
                await self._switch_off(SWITCH_SOCKET2)
                self._surplus_cascade_active = False
                actions["actions"] = ["surplus_low_all_off"]
            return actions

        # Tier 3: > 4kW surplus + SOC > 90%
        if surplus_power > PV_SURPLUS_TIER3 and soc >= PV_SURPLUS_MIN_SOC_TIER3:
            await self._switch_on(SWITCH_BOILER)
            await self._switch_on(SWITCH_AC)
            await self._switch_on(SWITCH_SOCKET2)
            self._surplus_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on", "ac_on", "socket2_on"]

        # Tier 2: > 3kW surplus + SOC > 85%
        elif surplus_power > PV_SURPLUS_TIER2 and soc >= PV_SURPLUS_MIN_SOC_TIER2:
            await self._switch_on(SWITCH_BOILER)
            await self._switch_on(SWITCH_AC)
            self._surplus_cascade_active = True
            actions["cascade_active"] = True
            actions["actions"] = ["boiler_on", "ac_on"]

        # Tier 1: > 2kW surplus + SOC > 80%
        elif surplus_power > PV_SURPLUS_TIER1 and soc >= PV_SURPLUS_MIN_SOC_TIER1:
            await self._switch_on(SWITCH_BOILER)
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
            await self._sofar_set_charge_power(3000)  # Default auto charge
        else:
            await self.hass.services.async_call(
                "goodwe",
                "set_parameter",
                {
                    "device_id": self._device_id,
                    "parameter": "battery_charge_current",
                    "value": DEFAULT_BATTERY_CHARGE_CURRENT_MAX,
                },
            )

    async def _block_charging(self) -> None:
        """Block battery charging."""
        if self._is_sofar:
            await self._sofar_set_charge_power(0)
        else:
            await self.hass.services.async_call(
                "goodwe",
                "set_parameter",
                {
                    "device_id": self._device_id,
                    "parameter": "battery_charge_current",
                    "value": DEFAULT_BATTERY_CHARGE_CURRENT_BLOCK,
                },
            )

    async def _set_charge_current(self, value: str) -> None:
        """Set battery charge current (GoodWe only, Sofar uses power limits)."""
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
                    "device_id": self._device_id,
                    "parameter": "battery_charge_current",
                    "value": value,
                },
            )

    async def _set_export_limit(self, limit: int) -> None:
        """Set grid export limit."""
        if self._is_sofar:
            await self._sofar_set_export_limit(limit)
        else:
            await self.hass.services.async_call(
                "goodwe",
                "set_parameter",
                {
                    "device_id": self._device_id,
                    "parameter": "grid_export_limit",
                    "value": str(limit),
                },
            )

    async def _set_dod(self, dod: int) -> None:
        """Set depth of discharge on grid."""
        if self._is_sofar:
            entity = NUMBER_SOFAR_DOD
        else:
            entity = NUMBER_DOD_ON_GRID
        state = self.hass.states.get(entity)
        if not state:
            _LOGGER.warning("Entity %s not available — skipping DOD set to %d%%", entity, dod)
            return
        current_dod = state.state
        dod = max(0, min(dod, DEFAULT_DOD_ON_GRID))
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

    async def _set_work_mode(self, mode: str) -> None:
        """Set inverter work mode via select entity."""
        if self._is_sofar:
            entity = SELECT_SOFAR_WORK_MODE
        else:
            entity = SELECT_WORK_MODE
        if not self.hass.states.get(entity):
            _LOGGER.debug("Entity %s not available — skipping work mode set", entity)
            return
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": entity,
                "option": mode,
            },
        )

    async def _set_eco_mode_power(self, value: int) -> None:
        """Set Eco Mode power percentage (0-100%)."""
        if not self.hass.states.get(NUMBER_ECO_MODE_POWER):
            _LOGGER.debug("Entity %s not available — skipping eco mode power set", NUMBER_ECO_MODE_POWER)
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": NUMBER_ECO_MODE_POWER,
                "value": value,
            },
        )

    async def _set_eco_mode_soc(self, value: int) -> None:
        """Set Eco Mode target SOC percentage (0-100%)."""
        if not self.hass.states.get(NUMBER_ECO_MODE_SOC):
            _LOGGER.debug("Entity %s not available — skipping eco mode SOC set", NUMBER_ECO_MODE_SOC)
            return
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": NUMBER_ECO_MODE_SOC,
                "value": value,
            },
        )

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

    async def _sofar_set_charge_power(self, power_w: int) -> None:
        """Set Sofar battery charge power limit (W).

        Uses number.set_value on the Sofar charge power limit entity.
        Typical range: 0–6000W depending on model.
        """
        if not self.hass.states.get(NUMBER_SOFAR_CHARGE_POWER):
            _LOGGER.debug(
                "[Sofar] Entity %s not available — skipping charge power set",
                NUMBER_SOFAR_CHARGE_POWER,
            )
            return
        power_w = max(0, min(power_w, 6000))
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": NUMBER_SOFAR_CHARGE_POWER,
                "value": power_w,
            },
        )
        _LOGGER.debug("[Sofar] Charge power set to %dW", power_w)

    async def _sofar_set_discharge_power(self, power_w: int) -> None:
        """Set Sofar battery discharge power limit (W).

        Uses number.set_value on the Sofar discharge power limit entity.
        Typical range: 0–6000W depending on model.
        """
        if not self.hass.states.get(NUMBER_SOFAR_DISCHARGE_POWER):
            _LOGGER.debug(
                "[Sofar] Entity %s not available — skipping discharge power set",
                NUMBER_SOFAR_DISCHARGE_POWER,
            )
            return
        power_w = max(0, min(power_w, 6000))
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": NUMBER_SOFAR_DISCHARGE_POWER,
                "value": power_w,
            },
        )
        _LOGGER.debug("[Sofar] Discharge power set to %dW", power_w)

    async def _sofar_set_export_limit(self, limit_w: int) -> None:
        """Set Sofar grid export limit (W).

        Uses number.set_value on the Sofar export limit entity.
        """
        if not self.hass.states.get(NUMBER_SOFAR_EXPORT_LIMIT):
            _LOGGER.debug(
                "[Sofar] Entity %s not available — skipping export limit set",
                NUMBER_SOFAR_EXPORT_LIMIT,
            )
            return
        limit_w = max(0, min(limit_w, 16000))
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": NUMBER_SOFAR_EXPORT_LIMIT,
                "value": limit_w,
            },
        )
        _LOGGER.debug("[Sofar] Export limit set to %dW", limit_w)
