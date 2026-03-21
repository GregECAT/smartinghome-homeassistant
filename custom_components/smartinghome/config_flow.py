"""Config flow for Smarting HOME Energy Management."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SmartingHomeAPI, AuthenticationError, ConnectionError as APIConnectionError
from .const import (
    DOMAIN,
    CONF_LICENSE_KEY,
    CONF_LICENSE_MODE,
    CONF_DEVICE_ID,
    CONF_TARIFF,
    CONF_RCE_ENABLED,
    CONF_GEMINI_API_KEY,
    CONF_ANTHROPIC_API_KEY,
    CONF_AI_ENABLED,
    CONF_MODBUS_ENABLED,
    CONF_MODBUS_PORT,
    CONF_MODBUS_SLAVE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_GOODWE_DEVICE_ID,
    DEFAULT_MODBUS_PORT,
    DEFAULT_MODBUS_SLAVE,
    DEFAULT_UPDATE_INTERVAL,
    LICENSE_MODE_FREE,
    LICENSE_MODE_PRO,
    TariffType,
    HEMSStrategy,
    G13_PRICES,
    G13Zone,
)

_LOGGER = logging.getLogger(__name__)


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

            # FREE mode — skip license, go to inverter
            self._data[CONF_LICENSE_KEY] = ""
            return await self.async_step_inverter()

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
            api = SmartingHomeAPI(session, self._license_key)

            try:
                info = await api.validate_license()
                if info.valid:
                    self._license_tier = info.tier
                    self._data[CONF_LICENSE_KEY] = self._license_key
                    return await self.async_step_inverter()
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

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Inverter configuration."""
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
        """Step 3: Tariff configuration."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._data[CONF_TARIFF] = user_input.get(
                CONF_TARIFF, TariffType.G13
            )
            self._data[CONF_RCE_ENABLED] = user_input.get(
                CONF_RCE_ENABLED, True
            )

            # If PRO mode, show AI step; if FREE, skip AI
            if self._license_mode == LICENSE_MODE_PRO:
                return await self.async_step_ai()

            # FREE mode — skip AI, set defaults, create entry
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

        return self.async_show_form(
            step_id="tariff",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TARIFF, default=TariffType.G13
                    ): vol.In(
                        {
                            TariffType.G11: "G11 — Flat rate",
                            TariffType.G12: "G12 — Two-zone",
                            TariffType.G13: "G13 — Three-zone (recommended)",
                        }
                    ),
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

    async def async_step_ai(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: AI configuration (PRO only, optional)."""
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
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.data
        is_pro = current.get(CONF_LICENSE_MODE, LICENSE_MODE_FREE) == LICENSE_MODE_PRO

        schema_dict = {
            vol.Optional(
                CONF_TARIFF,
                default=current.get(CONF_TARIFF, TariffType.G13),
            ): vol.In(
                {
                    TariffType.G11: "G11 — Flat rate",
                    TariffType.G12: "G12 — Two-zone",
                    TariffType.G13: "G13 — Three-zone",
                }
            ),
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

        # Only show AI fields for PRO users
        if is_pro:
            schema_dict[vol.Optional(
                CONF_GEMINI_API_KEY,
                default=current.get(CONF_GEMINI_API_KEY, ""),
            )] = str
            schema_dict[vol.Optional(
                CONF_ANTHROPIC_API_KEY,
                default=current.get(CONF_ANTHROPIC_API_KEY, ""),
            )] = str

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )
