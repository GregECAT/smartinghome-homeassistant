"""Config flow for Smarting HOME Energy Management."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartingHomeAPI, AuthenticationError, ConnectionError as APIConnectionError
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_LICENSE_KEY,
    CONF_LICENSE_MODE,
    CONF_DEVICE_ID,
    CONF_TARIFF,
    CONF_ENERGY_PROVIDER,
    CONF_RCE_ENABLED,
    CONF_GEMINI_API_KEY,
    CONF_ANTHROPIC_API_KEY,
    CONF_AI_ENABLED,
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_PORT,
    CONF_MODBUS_SLAVE,
    CONF_UPDATE_INTERVAL,
    CONF_SENSOR_MAP,
    CONF_INVERTER_BRAND,
    CONF_ECOWITT_ENABLED,
    DEFAULT_GOODWE_DEVICE_ID,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_SLAVE,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_SENSOR_MAP,
    DEFAULT_SENSOR_MAP_DEYE,
    DEFAULT_SENSOR_MAP_GROWATT,
    DEFAULT_SENSOR_MAP_SOFAR,
    SENSOR_MAP_KEYS,
    INVERTER_BRAND_GOODWE,
    INVERTER_BRAND_DEYE,
    INVERTER_BRAND_GROWATT,
    INVERTER_BRAND_SOFAR,
    INVERTER_BRAND_OTHER,
    DEFAULT_ECOWITT_SENSOR_MAP,
    LICENSE_MODE_FREE,
    LICENSE_MODE_PRO,
    TariffType,
    EnergyProvider,
    ENERGY_PROVIDER_LABELS,
    DEFAULT_ENERGY_PROVIDER,
    PROVIDER_TARIFFS,
    PROVIDER_TARIFF_LABELS,
    G13_PRICES,
    G13Zone,
)

_LOGGER = logging.getLogger(__name__)


def _get_defaults_for_brand(brand: str) -> dict:
    """Return sensor map defaults based on inverter brand."""
    if brand == INVERTER_BRAND_DEYE:
        return DEFAULT_SENSOR_MAP_DEYE
    if brand == INVERTER_BRAND_GROWATT:
        return DEFAULT_SENSOR_MAP_GROWATT
    if brand == INVERTER_BRAND_SOFAR:
        return DEFAULT_SENSOR_MAP_SOFAR
    return DEFAULT_SENSOR_MAP


def _get_tariff_options(provider: str) -> dict[str, str]:
    """Return tariff selection options based on energy provider."""
    try:
        prov = EnergyProvider(provider)
    except ValueError:
        prov = DEFAULT_ENERGY_PROVIDER
    labels = PROVIDER_TARIFF_LABELS.get(prov, PROVIDER_TARIFF_LABELS[DEFAULT_ENERGY_PROVIDER])
    tariffs = PROVIDER_TARIFFS.get(prov, PROVIDER_TARIFFS[DEFAULT_ENERGY_PROVIDER])
    return {t: labels[t] for t in tariffs if t in labels}


class SmartingHomeConfigFlow(
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Handle a config flow for Smarting HOME."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._license_key: str = ""
        self._license_tier: str = ""
        self._license_mode: str = ""
        self._device_id: str = DEFAULT_GOODWE_DEVICE_ID
        self._tariff: str = TariffType.G13
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 0: Choose license mode — FREE or PRO."""
        if user_input is not None:
            self._license_mode = user_input.get(CONF_LICENSE_MODE, LICENSE_MODE_FREE)
            self._data[CONF_LICENSE_MODE] = self._license_mode

            if self._license_mode == LICENSE_MODE_PRO:
                return await self.async_step_license()

            # FREE mode — skip license, go to brand selection
            self._data[CONF_LICENSE_KEY] = ""
            return await self.async_step_brand()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LICENSE_MODE, default=LICENSE_MODE_FREE
                    ): vol.In(
                        {
                            LICENSE_MODE_FREE: "🆓 FREE — Basic energy monitoring (free forever)",
                            LICENSE_MODE_PRO: "⭐ PRO — Full HEMS + AI Advisor (license required)",
                        }
                    ),
                }
            ),
            description_placeholders={
                "website": "https://smartinghome.pl",
            },
        )

    async def async_step_license(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: License key entry (PRO mode only)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._license_key = user_input[CONF_LICENSE_KEY]

            # Validate license key
            session = async_get_clientsession(self.hass)
            device_id = str(self.hass.data.get("core.uuid", ""))
            ha_version = self.hass.config.version or "unknown"
            api = SmartingHomeAPI(
                session, self._license_key,
                device_id=device_id,
                ha_version=ha_version,
                integration_version="1.14.0",
            )

            try:
                info = await api.validate_license()
                if info.valid:
                    self._license_tier = info.tier
                    self._data[CONF_LICENSE_KEY] = self._license_key
                    return await self.async_step_brand()
                errors["base"] = "invalid_license"
            except AuthenticationError:
                errors["base"] = "invalid_license"
            except APIConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during license validation")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="license",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LICENSE_KEY): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "website": "https://smartinghome.pl",
            },
        )

    async def async_step_brand(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Choose inverter brand."""
        if user_input is not None:
            self._data[CONF_INVERTER_BRAND] = user_input.get(
                CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE
            )
            return await self.async_step_inverter()

        return self.async_show_form(
            step_id="brand",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTER_BRAND, default=INVERTER_BRAND_GOODWE
                    ): vol.In(
                        {
                            INVERTER_BRAND_GOODWE: "GoodWe",
                            INVERTER_BRAND_DEYE: "Deye",
                            INVERTER_BRAND_GROWATT: "Growatt",
                            INVERTER_BRAND_SOFAR: "Sofar Solar",
                            INVERTER_BRAND_OTHER: "Inny / Other",
                        }
                    ),
                }
            ),
        )

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Inverter configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_DEVICE_ID] = user_input.get(
                CONF_DEVICE_ID, DEFAULT_GOODWE_DEVICE_ID
            )
            self._data[CONF_MODBUS_ENABLED] = user_input.get(
                CONF_MODBUS_ENABLED, False
            )
            if self._data[CONF_MODBUS_ENABLED]:
                self._data[CONF_MODBUS_PORT] = user_input.get(
                    CONF_MODBUS_PORT, DEFAULT_MODBUS_PORT
                )
                self._data[CONF_MODBUS_SLAVE] = user_input.get(
                    CONF_MODBUS_SLAVE, DEFAULT_MODBUS_SLAVE
                )
            return await self.async_step_tariff()

        return self.async_show_form(
            step_id="inverter",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_DEVICE_ID,
                        default=DEFAULT_GOODWE_DEVICE_ID,
                    ): str,
                    vol.Optional(
                        CONF_MODBUS_ENABLED, default=False
                    ): bool,
                    vol.Optional(
                        CONF_MODBUS_PORT,
                        default=DEFAULT_MODBUS_PORT,
                    ): str,
                    vol.Optional(
                        CONF_MODBUS_SLAVE,
                        default=DEFAULT_MODBUS_SLAVE,
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=247)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_tariff(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Provider + Tariff configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            provider = user_input.get(
                CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER
            )
            self._data[CONF_ENERGY_PROVIDER] = provider
            self._data[CONF_TARIFF] = user_input.get(
                CONF_TARIFF, TariffType.G13
            )
            self._data[CONF_RCE_ENABLED] = user_input.get(
                CONF_RCE_ENABLED, True
            )

            # Auto-fill sensor_map with brand defaults (skip manual sensor step)
            brand = self._data.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
            defaults = _get_defaults_for_brand(brand)
            self._data[CONF_SENSOR_MAP] = defaults

            # If PRO mode, show AI step; if FREE, create entry now
            if self._license_mode == LICENSE_MODE_PRO:
                return await self.async_step_ai()

            # FREE mode — set defaults and create entry
            self._data[CONF_AI_ENABLED] = False
            self._data[CONF_GEMINI_API_KEY] = ""
            self._data[CONF_ANTHROPIC_API_KEY] = ""
            self._data[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL

            await self.async_set_unique_id(
                f"smartinghome_{self._data[CONF_DEVICE_ID][:8]}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="Smarting HOME — Energy Management",
                data=self._data,
            )

        # Build dynamic tariff options based on default provider
        tariff_options = _get_tariff_options(DEFAULT_ENERGY_PROVIDER)
        default_tariff = TariffType.G13 if TariffType.G13 in tariff_options else list(tariff_options.keys())[0]

        return self.async_show_form(
            step_id="tariff",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ENERGY_PROVIDER, default=DEFAULT_ENERGY_PROVIDER
                    ): vol.In(ENERGY_PROVIDER_LABELS),
                    vol.Required(
                        CONF_TARIFF, default=default_tariff
                    ): vol.In(tariff_options),
                    vol.Optional(
                        CONF_RCE_ENABLED, default=True
                    ): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "g13_off_peak": f"{G13_PRICES[G13Zone.OFF_PEAK]} PLN/kWh",
                "g13_morning": f"{G13_PRICES[G13Zone.MORNING_PEAK]} PLN/kWh",
                "g13_afternoon": f"{G13_PRICES[G13Zone.AFTERNOON_PEAK]} PLN/kWh",
            },
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Sensor mapping — select entities for each logical sensor."""
        brand = self._data.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
        defaults = _get_defaults_for_brand(brand)

        if user_input is not None:
            # Store sensor map
            sensor_map = {}
            for key in SENSOR_MAP_KEYS:
                sensor_map[key] = user_input.get(key, defaults.get(key, ""))
            self._data[CONF_SENSOR_MAP] = sensor_map

            # If PRO mode, show AI step; if FREE, create entry now
            if self._license_mode == LICENSE_MODE_PRO:
                return await self.async_step_ai()

            # FREE mode — set defaults and create entry
            self._data[CONF_AI_ENABLED] = False
            self._data[CONF_GEMINI_API_KEY] = ""
            self._data[CONF_ANTHROPIC_API_KEY] = ""
            self._data[CONF_UPDATE_INTERVAL] = DEFAULT_UPDATE_INTERVAL

            await self.async_set_unique_id(
                f"smartinghome_{self._data[CONF_DEVICE_ID][:8]}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="Smarting HOME — Energy Management",
                data=self._data,
            )

        schema_dict = {}
        for key, _desc in SENSOR_MAP_KEYS.items():
            default_val = defaults.get(key, "")
            schema_dict[vol.Optional(key, default=default_val)] = (
                selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            )

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_ai(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 5: AI configuration (PRO only, optional)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            ai_enabled = bool(
                user_input.get(CONF_GEMINI_API_KEY)
                or user_input.get(CONF_ANTHROPIC_API_KEY)
            )
            self._data[CONF_AI_ENABLED] = ai_enabled
            self._data[CONF_GEMINI_API_KEY] = user_input.get(
                CONF_GEMINI_API_KEY, ""
            )
            self._data[CONF_ANTHROPIC_API_KEY] = user_input.get(
                CONF_ANTHROPIC_API_KEY, ""
            )
            self._data[CONF_UPDATE_INTERVAL] = user_input.get(
                CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            )

            # Create the config entry
            await self.async_set_unique_id(
                f"smartinghome_{self._data[CONF_DEVICE_ID][:8]}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="Smarting HOME — Energy Management",
                data=self._data,
            )

        return self.async_show_form(
            step_id="ai",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_GEMINI_API_KEY, default=""): str,
                    vol.Optional(CONF_ANTHROPIC_API_KEY, default=""): str,
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=DEFAULT_UPDATE_INTERVAL,
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=300)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> SmartingHomeOptionsFlow:
        """Get the options flow handler."""
        return SmartingHomeOptionsFlow(config_entry)


class SmartingHomeOptionsFlow(config_entries.OptionsFlow):
    """Handle Smarting HOME options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options — show menu to choose what to configure."""
        current = self._config_entry.data
        is_free = current.get(CONF_LICENSE_MODE, LICENSE_MODE_FREE) == LICENSE_MODE_FREE

        menu_options = ["inverter_brand", "sensors"]
        menu_options.append("ecowitt")
        menu_options.append("api_keys")
        if is_free:
            menu_options.insert(0, "upgrade")
        else:
            menu_options.insert(0, "settings")
            menu_options.append("downgrade")

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Sensor mapping step in Options Flow."""
        current = self._config_entry.data
        brand = current.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
        brand_defaults = _get_defaults_for_brand(brand)
        current_map = current.get(CONF_SENSOR_MAP, brand_defaults)

        if user_input is not None:
            sensor_map = {}
            for key in SENSOR_MAP_KEYS:
                sensor_map[key] = user_input.get(key, current_map.get(key, ""))
            # Update entry data with new sensor map
            new_data = {**current, CONF_SENSOR_MAP: sensor_map}
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        schema_dict = {}
        for key, _desc in SENSOR_MAP_KEYS.items():
            current_val = current_map.get(key, brand_defaults.get(key, ""))
            schema_dict[vol.Optional(key, default=current_val)] = (
                selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
            )

        return self.async_show_form(
            step_id="sensors",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_upgrade(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """FREE → PRO upgrade step: enter license key."""
        errors: dict[str, str] = {}
        current = self._config_entry.data

        if user_input is not None:
            license_key = user_input.get(CONF_LICENSE_KEY, "").strip()

            # Always persist provider/tariff/RCE/interval to entry.data
            new_data = {**current}
            new_data[CONF_ENERGY_PROVIDER] = user_input.get(
                CONF_ENERGY_PROVIDER,
                current.get(CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER),
            )
            new_data[CONF_TARIFF] = user_input.get(
                CONF_TARIFF, current.get(CONF_TARIFF, TariffType.G13)
            )
            new_data[CONF_RCE_ENABLED] = user_input.get(
                CONF_RCE_ENABLED, current.get(CONF_RCE_ENABLED, True)
            )
            new_data[CONF_UPDATE_INTERVAL] = user_input.get(
                CONF_UPDATE_INTERVAL,
                current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            )

            if license_key:
                # Validate the license key
                session = async_get_clientsession(self.hass)
                device_id = str(self.hass.data.get("core.uuid", ""))
                ha_version = self.hass.config.version or "unknown"
                api = SmartingHomeAPI(
                    session, license_key,
                    device_id=device_id,
                    ha_version=ha_version,
                    integration_version="1.14.0",
                )

                try:
                    info = await api.validate_license()
                    if info.valid:
                        # Upgrade: update entry data to PRO
                        new_data[CONF_LICENSE_MODE] = LICENSE_MODE_PRO
                        new_data[CONF_LICENSE_KEY] = license_key
                        self.hass.config_entries.async_update_entry(
                            self._config_entry, data=new_data
                        )
                        _LOGGER.info(
                            "License upgraded to %s (key: %s...)",
                            info.tier.value,
                            license_key[:12],
                        )
                        return self.async_create_entry(title="", data={})
                    errors["base"] = "invalid_license"
                except AuthenticationError:
                    errors["base"] = "invalid_license"
                except APIConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Error during license upgrade")
                    errors["base"] = "unknown"
            else:
                # No key entered — just save settings
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

        provider = current.get(CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER)
        tariff_options = _get_tariff_options(provider)
        current_tariff = current.get(CONF_TARIFF, TariffType.G13)
        if current_tariff not in tariff_options:
            current_tariff = list(tariff_options.keys())[0]

        return self.async_show_form(
            step_id="upgrade",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_LICENSE_KEY,
                        default="",
                        description={"suggested_value": ""},
                    ): str,
                    vol.Optional(
                        CONF_ENERGY_PROVIDER,
                        default=provider,
                    ): vol.In(ENERGY_PROVIDER_LABELS),
                    vol.Optional(
                        CONF_TARIFF,
                        default=current_tariff,
                    ): vol.In(tariff_options),
                    vol.Optional(
                        CONF_RCE_ENABLED,
                        default=current.get(CONF_RCE_ENABLED, True),
                    ): bool,
                    vol.Optional(
                        CONF_UPDATE_INTERVAL,
                        default=current.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=300)
                    ),
                }
            ),
            errors=errors,
            description_placeholders={
                "website": "https://smartinghome.pl/buy",
            },
        )

    async def async_step_ecowitt(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ecowitt integration toggle — applies default sensor map."""
        current = self._config_entry.data

        if user_input is not None:
            ecowitt_enabled = user_input.get(CONF_ECOWITT_ENABLED, False)
            new_data = {**current, CONF_ECOWITT_ENABLED: ecowitt_enabled}

            if ecowitt_enabled:
                # Auto-merge Ecowitt sensor defaults into sensor_map
                sensor_map = dict(new_data.get(CONF_SENSOR_MAP, {}))
                for key, entity in DEFAULT_ECOWITT_SENSOR_MAP.items():
                    if not sensor_map.get(key):
                        sensor_map[key] = entity
                new_data[CONF_SENSOR_MAP] = sensor_map

            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="ecowitt",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ECOWITT_ENABLED,
                        default=current.get(CONF_ECOWITT_ENABLED, False),
                    ): bool,
                }
            ),
        )

    async def async_step_sensor_mapping(
        self, _user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Redirect to sensors step from menu."""
        return await self.async_step_sensors()

    async def async_step_inverter_brand(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change inverter brand in options flow."""
        current = self._config_entry.data

        if user_input is not None:
            new_brand = user_input.get(
                CONF_INVERTER_BRAND,
                current.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE),
            )
            new_data = {**current, CONF_INVERTER_BRAND: new_brand}
            # Update sensor map defaults for the new brand
            brand_defaults = _get_defaults_for_brand(new_brand)
            current_map = dict(new_data.get(CONF_SENSOR_MAP, {}))
            for key in SENSOR_MAP_KEYS:
                if not current_map.get(key):
                    current_map[key] = brand_defaults.get(key, "")
            new_data[CONF_SENSOR_MAP] = current_map
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="inverter_brand",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTER_BRAND,
                        default=current.get(
                            CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE
                        ),
                    ): vol.In(
                        {
                            INVERTER_BRAND_GOODWE: "GoodWe",
                            INVERTER_BRAND_DEYE: "Deye",
                            INVERTER_BRAND_GROWATT: "Growatt",
                            INVERTER_BRAND_SOFAR: "Sofar Solar",
                            INVERTER_BRAND_OTHER: "Inny / Other",
                        }
                    ),
                }
            ),
        )

    async def async_step_api_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Dedicated step for API key management.

        Always visible in menu (both FREE and PRO).
        Saves keys to entry.data + settings.json without triggering reload.
        """
        if user_input is not None:

            current = self._config_entry.data
            new_data = {**current}

            # Merge API keys into entry data
            for key in (CONF_GEMINI_API_KEY, CONF_ANTHROPIC_API_KEY):
                val = user_input.get(key, "")
                if val:
                    new_data[key] = val

            # Mark as soft update — skip integration reload
            new_data["_keys_only_update"] = True

            # Persist to entry.data
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )

            # Also write to settings.json for panel UI
            from .settings_io import write_sync

            gk = user_input.get(CONF_GEMINI_API_KEY, "")
            ak = user_input.get(CONF_ANTHROPIC_API_KEY, "")

            updates = {}
            if gk:
                updates["gemini_api_key"] = gk
                updates["gemini_key_status"] = "saved"
                updates["gemini_key_masked"] = (
                    gk[:6] + "***" + gk[-4:] if len(gk) > 10 else "***"
                )
            if ak:
                updates["anthropic_api_key"] = ak
                updates["anthropic_key_status"] = "saved"
                updates["anthropic_key_masked"] = (
                    ak[:7] + "***" + ak[-4:] if len(ak) > 11 else "***"
                )

            if updates:
                write_sync(self.hass, updates)

            _LOGGER.info("API keys saved via options flow")
            return self.async_create_entry(title="", data={})

        current = self._config_entry.data

        return self.async_show_form(
            step_id="api_keys",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_GEMINI_API_KEY,
                        default=current.get(CONF_GEMINI_API_KEY, ""),
                    ): str,
                    vol.Optional(
                        CONF_ANTHROPIC_API_KEY,
                        default=current.get(CONF_ANTHROPIC_API_KEY, ""),
                    ): str,
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """PRO settings — provider, tariff, RCE, update interval."""
        if user_input is not None:
            current = self._config_entry.data
            new_data = {**current}
            new_data[CONF_ENERGY_PROVIDER] = user_input.get(
                CONF_ENERGY_PROVIDER,
                current.get(CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER),
            )
            new_data[CONF_TARIFF] = user_input.get(
                CONF_TARIFF, current.get(CONF_TARIFF, TariffType.G13)
            )
            new_data[CONF_RCE_ENABLED] = user_input.get(
                CONF_RCE_ENABLED, current.get(CONF_RCE_ENABLED, True)
            )
            new_data[CONF_UPDATE_INTERVAL] = user_input.get(
                CONF_UPDATE_INTERVAL,
                current.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            )
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        current = self._config_entry.data
        provider = current.get(CONF_ENERGY_PROVIDER, DEFAULT_ENERGY_PROVIDER)
        tariff_options = _get_tariff_options(provider)
        current_tariff = current.get(CONF_TARIFF, TariffType.G13)
        if current_tariff not in tariff_options:
            current_tariff = list(tariff_options.keys())[0]

        schema_dict = {
            vol.Optional(
                CONF_ENERGY_PROVIDER,
                default=provider,
            ): vol.In(ENERGY_PROVIDER_LABELS),
            vol.Optional(
                CONF_TARIFF,
                default=current_tariff,
            ): vol.In(tariff_options),
            vol.Optional(
                CONF_RCE_ENABLED,
                default=current.get(CONF_RCE_ENABLED, True),
            ): bool,
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=current.get(
                    CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                ),
            ): vol.All(
                vol.Coerce(int), vol.Range(min=10, max=300)
            ),
        }

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_downgrade(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """PRO → FREE downgrade: remove license key and revert to FREE mode."""
        if user_input is not None:
            confirm = user_input.get("confirm_downgrade", False)
            if confirm:
                current = self._config_entry.data
                new_data = {**current}
                new_data[CONF_LICENSE_MODE] = LICENSE_MODE_FREE
                new_data[CONF_LICENSE_KEY] = ""
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                _LOGGER.info("License downgraded to FREE mode")
                return self.async_create_entry(title="", data={})
            # User unchecked confirm — go back to menu
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="downgrade",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "confirm_downgrade", default=False
                    ): bool,
                }
            ),
        )

