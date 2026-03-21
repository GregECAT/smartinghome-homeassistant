"""Switch platform for Smarting HOME."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    INTEGRATION_NAME,
    VERSION,
    ICON_HEMS,
    ICON_BATTERY,
    ICON_ARBITRAGE,
    ICON_LOAD,
)
from .coordinator import SmartingHomeCoordinator

_LOGGER = logging.getLogger(__name__)


SWITCH_DESCRIPTIONS: list[SwitchEntityDescription] = [
    SwitchEntityDescription(
        key="hems_auto_mode",
        name="HEMS Auto Mode",
        icon=ICON_HEMS,
    ),
    SwitchEntityDescription(
        key="hems_battery_export",
        name="Battery Export Enabled",
        icon=ICON_BATTERY,
    ),
    SwitchEntityDescription(
        key="hems_night_arbitrage",
        name="Night Arbitrage Enabled",
        icon=ICON_ARBITRAGE,
    ),
    SwitchEntityDescription(
        key="hems_load_cascade",
        name="Load Cascade Enabled",
        icon=ICON_LOAD,
    ),
    SwitchEntityDescription(
        key="hems_voltage_protection",
        name="Voltage Protection Enabled",
        icon="mdi:flash-alert",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smarting HOME switches."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartingHomeCoordinator = data["coordinator"]

    entities: list[SmartingHomeSwitch] = []

    for description in SWITCH_DESCRIPTIONS:
        entities.append(
            SmartingHomeSwitch(
                coordinator=coordinator,
                description=description,
                entry=entry,
            )
        )

    async_add_entities(entities)
    _LOGGER.info("Added %d Smarting HOME switches", len(entities))


class SmartingHomeSwitch(
    CoordinatorEntity[SmartingHomeCoordinator], SwitchEntity
):
    """Smarting HOME switch entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartingHomeCoordinator,
        description: SwitchEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{description.key}"
        self._is_on = True  # Default ON

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            name=INTEGRATION_NAME,
            manufacturer=MANUFACTURER,
            model="HEMS — Home Energy Management System",
            sw_version=VERSION,
            configuration_url="https://smartinghome.pl",
        )

    @property
    def is_on(self) -> bool:
        """Return True if switch is on."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        self._is_on = True
        self.async_write_ha_state()
        _LOGGER.info("Smarting HOME switch %s turned ON", self.entity_description.key)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        self._is_on = False
        self.async_write_ha_state()
        _LOGGER.info("Smarting HOME switch %s turned OFF", self.entity_description.key)
