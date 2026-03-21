"""Binary sensor platform for Smarting HOME."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
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
    ICON_TARIFF,
    ICON_RCE,
    ICON_BATTERY,
    ICON_LICENSE,
    ICON_AI,
    ICON_VOLTAGE,
    G13Zone,
    SOC_EMERGENCY,
)
from .coordinator import SmartingHomeCoordinator

_LOGGER = logging.getLogger(__name__)


BINARY_SENSOR_DESCRIPTIONS: list[BinarySensorEntityDescription] = [
    BinarySensorEntityDescription(
        key="g13_is_afternoon_peak",
        name="G13 Afternoon Peak",
        icon=ICON_TARIFF,
    ),
    BinarySensorEntityDescription(
        key="g13_is_off_peak",
        name="G13 Off-Peak",
        icon=ICON_TARIFF,
    ),
    BinarySensorEntityDescription(
        key="license_valid",
        name="License Valid",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        icon=ICON_LICENSE,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smarting HOME binary sensors."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartingHomeCoordinator = data["coordinator"]

    entities: list[SmartingHomeBinarySensor] = []

    for description in BINARY_SENSOR_DESCRIPTIONS:
        entities.append(
            SmartingHomeBinarySensor(
                coordinator=coordinator,
                description=description,
                entry=entry,
            )
        )

    async_add_entities(entities)
    _LOGGER.info("Added %d Smarting HOME binary sensors", len(entities))


class SmartingHomeBinarySensor(
    CoordinatorEntity[SmartingHomeCoordinator], BinarySensorEntity
):
    """Smarting HOME binary sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartingHomeCoordinator,
        description: BinarySensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{description.key}"

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
    def is_on(self) -> bool | None:
        """Return True if binary sensor is on."""
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self.entity_description.key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("on", "true", "1")
        return bool(value) if value is not None else None
