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
    ) -> None:
        """Initialize the energy manager."""
        self.hass = hass
        self._device_id = device_id
        self._strategy = strategy
        self._current_mode = HEMSMode.AUTO
        self._voltage_cascade_active = False
        self._surplus_cascade_active = False

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
        """Force battery charging — enables grid→battery charging."""
        _LOGGER.info("Forcing battery charge (work_mode=eco_charge)")
        await self._enable_charging()
        # CRITICAL: Set work mode to eco_charge to enable grid→battery charging
        # In general mode, inverter won't charge from grid (only PV→battery)
        # eco_charge is confirmed working from user's night arbitrage automation
        await self._set_work_mode("eco_charge")
        self._current_mode = HEMSMode.CHARGE

    async def force_discharge(self) -> None:
        """Force battery discharge (block charging, restore general mode)."""
        _LOGGER.info("Forcing battery discharge (work_mode=general)")
        await self._block_charging()
        await self._set_work_mode("general")
        self._current_mode = HEMSMode.SELL

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

        # Emergency: SOC < 20%
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
    # Private helpers — GoodWe control
    # =========================================================================

    async def _enable_charging(self) -> None:
        """Enable battery charging (set current to max)."""
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
        """Block battery charging (set current to 0)."""
        await self.hass.services.async_call(
            "goodwe",
            "set_parameter",
            {
                "device_id": self._device_id,
                "parameter": "battery_charge_current",
                "value": DEFAULT_BATTERY_CHARGE_CURRENT_BLOCK,
            },
        )

    async def _set_export_limit(self, limit: int) -> None:
        """Set grid export limit."""
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
        # Safety clamp: GoodWe entity max is 95%
        dod = max(0, min(dod, DEFAULT_DOD_ON_GRID))
        await self.hass.services.async_call(
            "number",
            "set_value",
            {
                "entity_id": NUMBER_DOD_ON_GRID,
                "value": dod,
            },
        )

    async def _set_work_mode(self, mode: str) -> None:
        """Set inverter work mode."""
        await self.hass.services.async_call(
            "select",
            "select_option",
            {
                "entity_id": SELECT_WORK_MODE,
                "option": mode,
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
