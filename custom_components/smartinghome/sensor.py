"""Sensor platform for Smarting HOME."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    INTEGRATION_NAME,
    VERSION,
    CONF_SENSOR_MAP,
    DEFAULT_SENSOR_MAP,
    ICON_PV,
    ICON_BATTERY,
    ICON_GRID,
    ICON_LOAD,
    ICON_MONEY,
    ICON_TARIFF,
    ICON_HEMS,
    ICON_FORECAST,
    ICON_RCE,
    ICON_AUTARKY,
    ICON_IMPORT,
    ICON_EXPORT,
    ICON_FREQUENCY,
    ICON_ARBITRAGE,
    ICON_LICENSE,
)
from .coordinator import SmartingHomeCoordinator

_LOGGER = logging.getLogger(__name__)


# =============================================================================
# Sensor Descriptions — organized by category
# =============================================================================

HEMS_SENSOR_DESCRIPTIONS: list[SensorEntityDescription] = [
    # —— Grid Directional ——
    SensorEntityDescription(
        key="grid_import_power",
        name="Grid Import Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_IMPORT,
    ),
    SensorEntityDescription(
        key="grid_export_power",
        name="Grid Export Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_EXPORT,
    ),
    # —— HEMS ——
    SensorEntityDescription(
        key="hems_pv_surplus_power",
        name="PV Surplus Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_PV,
    ),
    SensorEntityDescription(
        key="hems_active_loads",
        name="Active Loads",
        icon=ICON_LOAD,
    ),
    SensorEntityDescription(
        key="hems_rce_recommendation",
        name="HEMS Recommendation",
        icon=ICON_HEMS,
    ),
    # —— Battery Calculations ——
    SensorEntityDescription(
        key="goodwe_battery_energy_available",
        name="Battery Energy Available",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_BATTERY,
    ),
    SensorEntityDescription(
        key="goodwe_battery_runtime",
        name="Battery Runtime",
        native_unit_of_measurement="h",
        icon=ICON_BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # —— Grid Stats ——
    SensorEntityDescription(
        key="goodwe_grid_frequency_average",
        name="Grid Frequency Average",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_FREQUENCY,
    ),
    SensorEntityDescription(
        key="goodwe_load_balance_difference",
        name="Load Balance Difference",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_LOAD,
    ),
    # —— G13 Tariff ——
    SensorEntityDescription(
        key="g13_current_zone",
        name="G13 Current Zone",
        icon=ICON_TARIFF,
    ),
    SensorEntityDescription(
        key="g13_buy_price",
        name="G13 Buy Price",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_MONEY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # —— RCE Prices ——
    SensorEntityDescription(
        key="rce_sell_price",
        name="RCE Sell Price",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_sell_price_next_hour",
        name="RCE Sell Price +1h",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_sell_price_2h",
        name="RCE Sell Price +2h",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_sell_price_3h",
        name="RCE Sell Price +3h",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_average_today",
        name="RCE Average Today",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_min_today",
        name="RCE Min Today",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_max_today",
        name="RCE Max Today",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_RCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="g13_rce_spread",
        name="G13 vs RCE Spread",
        native_unit_of_measurement="PLN/kWh",
        icon=ICON_ARBITRAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="rce_price_trend",
        name="RCE Price Trend",
        icon=ICON_RCE,
    ),
    SensorEntityDescription(
        key="rce_good_sell",
        name="RCE Sell Evaluation",
        icon=ICON_RCE,
    ),
    SensorEntityDescription(
        key="g13_battery_arbitrage_potential",
        name="Battery Arbitrage Potential",
        native_unit_of_measurement="PLN",
        icon=ICON_ARBITRAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # —— Forecast Totals ——
    SensorEntityDescription(
        key="pv_forecast_power_now_total",
        name="PV Forecast Power Now (Total)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon=ICON_FORECAST,
    ),
    SensorEntityDescription(
        key="pv_forecast_today_total",
        name="PV Forecast Today (Total)",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon=ICON_FORECAST,
    ),
    SensorEntityDescription(
        key="pv_forecast_remaining_today_total",
        name="PV Forecast Remaining Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon=ICON_FORECAST,
    ),
    SensorEntityDescription(
        key="pv_forecast_tomorrow_total",
        name="PV Forecast Tomorrow (Total)",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon=ICON_FORECAST,
    ),
    SensorEntityDescription(
        key="pv_forecast_accuracy_today",
        name="PV Forecast Accuracy Today",
        native_unit_of_measurement=PERCENTAGE,
        icon=ICON_FORECAST,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # —— Autarky & Self-consumption ——
    SensorEntityDescription(
        key="goodwe_autarky_today",
        name="Autarky Today",
        native_unit_of_measurement=PERCENTAGE,
        icon=ICON_AUTARKY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="goodwe_self_consumption_today",
        name="Self-Consumption Today",
        native_unit_of_measurement=PERCENTAGE,
        icon=ICON_AUTARKY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="goodwe_home_consumption_from_pv_today",
        name="Home Consumption from PV Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon=ICON_PV,
    ),
    SensorEntityDescription(
        key="goodwe_net_grid_today",
        name="Net Grid Today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        icon=ICON_GRID,
    ),
    # —— System Status ——
    SensorEntityDescription(
        key="goodwe_system_status",
        name="System Status",
        icon=ICON_HEMS,
    ),
    # —— License ——
    SensorEntityDescription(
        key="license_tier",
        name="License Tier",
        icon=ICON_LICENSE,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Smarting HOME sensor platform."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SmartingHomeCoordinator = data["coordinator"]

    entities: list[SensorEntity] = []

    for description in HEMS_SENSOR_DESCRIPTIONS:
        entities.append(
            SmartingHomeSensor(
                coordinator=coordinator,
                description=description,
                entry=entry,
            )
        )

    # Add sensor map diagnostic entity
    entities.append(SmartingHomeSensorMapSensor(entry=entry))

    async_add_entities(entities)
    _LOGGER.info("Added %d Smarting HOME sensors (incl. sensor map)", len(entities))


class SmartingHomeSensor(
    CoordinatorEntity[SmartingHomeCoordinator], SensorEntity
):
    """Smarting HOME sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SmartingHomeCoordinator,
        description: SensorEntityDescription,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
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
    def native_value(self) -> Any:
        """Return sensor value from coordinator data."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.key)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            super().available
            and self.coordinator.data is not None
            and self.entity_description.key in self.coordinator.data
        )


class SmartingHomeSensorMapSensor(SensorEntity):
    """Diagnostic sensor exposing the sensor mapping as attributes.

    Panel.js reads hass.states["sensor.smartinghome_sensor_map"].attributes
    to know which entity IDs to display for PV, grid, battery, etc.
    """

    _attr_has_entity_name = True
    _attr_entity_category = "diagnostic"
    _attr_icon = "mdi:map-legend"

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize sensor map entity."""
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_sensor_map"
        self._attr_name = "Sensor Map"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=INTEGRATION_NAME,
            manufacturer=MANUFACTURER,
            model="HEMS — Home Energy Management System",
            sw_version=VERSION,
            configuration_url="https://smartinghome.pl",
        )

    @property
    def native_value(self) -> str:
        """Return 'configured' if user has custom map, else 'defaults'."""
        sensor_map = self._entry.data.get(CONF_SENSOR_MAP)
        return "configured" if sensor_map else "defaults"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the sensor mapping as attributes."""
        return self._entry.data.get(CONF_SENSOR_MAP, DEFAULT_SENSOR_MAP)

    @property
    def available(self) -> bool:
        """Always available."""
        return True
