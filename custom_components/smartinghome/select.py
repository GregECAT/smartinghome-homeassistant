"""Select platform for Smarting HOME."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
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
    EMSMode,
    HEMSStrategy,
)
from .coordinator import SmartingHomeCoordinator

_LOGGER = logging.getLogger(__name__)


SELECT_DESCRIPTIONS: list[SelectEntityDescription] = [
    SelectEntityDescription(
        key="hems_ems_mode",
        name="EMS Mode",
        icon=ICON_HEMS,
        options=[mode.value for mode in EMSMode],
    ),
    SelectEntityDescription(
        key="hems_strategy",
        name="HEMS Strategy",
        icon=ICON_HEMS,
        options=[strategy.value for strategy in HEMSStrategy],
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smarting HOME selects."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartingHomeCoordinator = data["coordinator"]

    entities: list[SmartingHomeSelect] = []

    for description in SELECT_DESCRIPTIONS:
        entities.append(
            SmartingHomeSelect(
                coordinator=coordinator,
                description=description,
                entry=entry,
            )
        )

    async_add_entities(entities)
    _LOGGER.info("Added %d Smarting HOME selects", len(entities))


class SmartingHomeSelect(
    CoordinatorEntity[SmartingHomeCoordinator], SelectEntity
):
    """Smarting HOME select entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartingHomeCoordinator,
        description: SelectEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{description.key}"
        self._attr_options = list(description.options or [])
        self._current_option = self._attr_options[0] if self._attr_options else None

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
    def current_option(self) -> str | None:
        """Return current selected option."""
        return self._current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            self._current_option = option
            self.async_write_ha_state()
            _LOGGER.info(
                "Smarting HOME select %s set to %s",
                self.entity_description.key,
                option,
            )
