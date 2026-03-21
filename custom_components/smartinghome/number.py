"""Number platform for Smarting HOME."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    INTEGRATION_NAME,
    VERSION,
    ICON_BATTERY,
    ICON_EXPORT,
    ICON_HEMS,
)
from .coordinator import SmartingHomeCoordinator

_LOGGER = logging.getLogger(__name__)


NUMBER_DESCRIPTIONS: list[NumberEntityDescription] = [
    NumberEntityDescription(
        key="hems_ems_power_target",
        name="EMS Power Target",
        native_min_value=0,
        native_max_value=5000,
        native_step=100,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon=ICON_HEMS,
        mode=NumberMode.SLIDER,
    ),
    NumberEntityDescription(
        key="hems_charge_current_limit",
        name="Battery Charge Current Limit",
        native_min_value=0,
        native_max_value=18.5,
        native_step=0.5,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        icon=ICON_BATTERY,
        mode=NumberMode.SLIDER,
    ),
    NumberEntityDescription(
        key="hems_export_limit",
        name="Export Limit Override",
        native_min_value=0,
        native_max_value=16000,
        native_step=500,
        native_unit_of_measurement=UnitOfPower.WATT,
        icon=ICON_EXPORT,
        mode=NumberMode.SLIDER,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smarting HOME numbers."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartingHomeCoordinator = data["coordinator"]

    entities: list[SmartingHomeNumber] = []

    for description in NUMBER_DESCRIPTIONS:
        entities.append(
            SmartingHomeNumber(
                coordinator=coordinator,
                description=description,
                entry=entry,
            )
        )

    async_add_entities(entities)
    _LOGGER.info("Added %d Smarting HOME number entities", len(entities))


class SmartingHomeNumber(
    CoordinatorEntity[SmartingHomeCoordinator], NumberEntity
):
    """Smarting HOME number entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartingHomeCoordinator,
        description: NumberEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{description.key}"
        self._value: float = description.native_min_value or 0

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
    def native_value(self) -> float:
        """Return current value."""
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        """Set the value."""
        self._value = value
        self.async_write_ha_state()
        _LOGGER.info(
            "Smarting HOME number %s set to %.1f",
            self.entity_description.key,
            value,
        )
