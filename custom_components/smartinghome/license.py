# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""License management for Smarting HOME integration."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.const import __version__ as HA_VERSION

from .api import (
    SmartingHomeAPI,
    LicenseInfo,
    AuthenticationError,
    ConnectionError as APIConnectionError,
)
from .const import (
    DOMAIN,
    LICENSE_CHECK_INTERVAL,
    LICENSE_GRACE_PERIOD,
    LICENSE_MODE_FREE,
    LicenseTier,
    ATTR_LICENSE_VALID,
    ATTR_LICENSE_TIER,
    ATTR_LICENSE_EXPIRES,
    ATTR_LICENSE_LAST_CHECK,
)
from .features import features_for
from .license_key import check_license_key, is_lifetime, mask_key

_LOGGER = logging.getLogger(__name__)


class LicenseManager:
    """Manages license validation and caching."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SmartingHomeAPI,
        license_mode: str = LICENSE_MODE_FREE,
        free_legacy: bool = False,
    ) -> None:
        """Initialize the license manager.

        free_legacy — the config entry predates the v1.71 FREE/PRO split; FREE and
        DEMO keep the features such installs had without a license.
        """
        self.hass = hass
        self._api = api
        self._license_mode = license_mode
        self._free_legacy = bool(free_legacy)
        self._license_info: LicenseInfo | None = None
        self._last_successful_check: float = 0.0
        self._last_check_attempt: float = 0.0
        self._cached_valid: bool = False
        self._cached_tier: LicenseTier = (
            LicenseTier.FREE if license_mode == LICENSE_MODE_FREE
            else LicenseTier.DEMO
        )

    @property
    def is_free_mode(self) -> bool:
        """Return True if running in FREE mode (no license needed)."""
        return self._license_mode == LICENSE_MODE_FREE

    @property
    def is_valid(self) -> bool:
        """Return True if license is currently valid (including grace period).

        FREE mode is always valid.
        """
        if self.is_free_mode:
            return True

        if self._cached_valid:
            return True

        # Check grace period
        if self._last_successful_check > 0:
            elapsed = time.time() - self._last_successful_check
            if elapsed < LICENSE_GRACE_PERIOD:
                _LOGGER.debug(
                    "License in grace period: %.0f/%.0f seconds",
                    elapsed,
                    LICENSE_GRACE_PERIOD,
                )
                return True

        return False

    @property
    def tier(self) -> LicenseTier:
        """Return current license tier."""
        if self.is_free_mode:
            return LicenseTier.FREE

        if self.is_valid:
            return self._cached_tier
        return LicenseTier.DEMO

    @property
    def is_pro(self) -> bool:
        """Return True if license is PRO or ENTERPRISE."""
        return self.tier in (LicenseTier.PRO, LicenseTier.ENTERPRISE)

    @property
    def license_mode(self) -> str:
        """Configured mode: "free" or "pro" (a key is stored)."""
        return self._license_mode

    @property
    def last_successful_check(self) -> float:
        """Unix time of the last accepted validation (0 = none yet)."""
        return self._last_successful_check

    @property
    def lifetime(self) -> bool:
        """True for a perpetual license (as the license server reported it)."""
        info = self._license_info
        if info is None or self.is_free_mode:
            return False
        return is_lifetime(info.expires, getattr(info, "lifetime", False))

    @property
    def free_legacy(self) -> bool:
        """True for installs set up before v1.71 (grandfathered FREE features)."""
        return self._free_legacy

    @property
    def features(self) -> frozenset[str]:
        """Gated features enabled for the current tier."""
        return features_for(self.tier.value, self._free_legacy)

    def has_feature(self, feature: str) -> bool:
        """True when a gated feature (const.FEATURE_*) is available."""
        return feature in self.features

    @property
    def is_enterprise(self) -> bool:
        """Return True if license is ENTERPRISE."""
        return self.tier == LicenseTier.ENTERPRISE

    @property
    def license_info(self) -> LicenseInfo | None:
        """Return full license info."""
        return self._license_info

    @property
    def grace_period_remaining(self) -> float:
        """Return remaining grace period in seconds."""
        if self.is_free_mode:
            return float("inf")

        if self._last_successful_check <= 0:
            return 0.0
        elapsed = time.time() - self._last_successful_check
        remaining = LICENSE_GRACE_PERIOD - elapsed
        return max(0.0, remaining)

    @property
    def needs_recheck(self) -> bool:
        """Return True if license needs re-validation."""
        if self.is_free_mode:
            return False  # FREE mode never needs server checks

        if self._last_check_attempt <= 0:
            return True
        elapsed = time.time() - self._last_check_attempt
        return elapsed >= LICENSE_CHECK_INTERVAL

    @property
    def state_attributes(self) -> dict[str, Any]:
        """Return license state as attributes dict."""
        return {
            ATTR_LICENSE_VALID: self.is_valid,
            ATTR_LICENSE_TIER: self.tier.value,
            ATTR_LICENSE_EXPIRES: (
                self._license_info.expires if self._license_info else None
            ),
            ATTR_LICENSE_LAST_CHECK: self._last_successful_check,
            "license_mode": self._license_mode,
            "features": sorted(self.features),
            "free_legacy": self._free_legacy,
            "grace_period_remaining_hours": round(
                self.grace_period_remaining / 3600, 1
            ) if not self.is_free_mode else None,
        }

    async def validate(self) -> LicenseInfo:
        """Perform initial license validation.

        FREE mode returns a synthetic valid LicenseInfo immediately
        and sends a non-blocking anonymous telemetry ping.
        PRO mode validates against the license server.
        """
        if self.is_free_mode:
            info = LicenseInfo(
                valid=True,
                tier=LicenseTier.FREE,
                expires=None,
                email=None,
                max_installations=1,
                features=[
                    "sensors", "binary_sensors", "g13_tariff",
                    "rce_read", "hems_auto_mode",
                ],
                message="FREE mode — basic energy monitoring active.",
            )
            self._license_info = info
            self._cached_valid = True
            self._cached_tier = LicenseTier.FREE
            _LOGGER.info("Running in FREE mode — no license required")

            # Fire-and-forget anonymous telemetry (never blocks startup)
            import asyncio
            asyncio.create_task(self._register_free_ping())

            return info

        # PRO/ENTERPRISE — validate with server
        try:
            info = await self._api.validate_license()
            self._license_info = info
            self._last_check_attempt = time.time()

            if info.valid:
                self._cached_valid = True
                self._cached_tier = info.tier
                self._last_successful_check = time.time()
                _LOGGER.info(
                    "License validated: tier=%s, expires=%s",
                    info.tier,
                    info.expires,
                )
            else:
                self._cached_valid = False
                self._cached_tier = LicenseTier.DEMO
                _LOGGER.warning(
                    "License validation failed: %s", info.message
                )

            return info

        except AuthenticationError:
            self._cached_valid = False
            self._cached_tier = LicenseTier.DEMO
            raise
        except APIConnectionError as err:
            _LOGGER.warning(
                "Cannot reach license server, using cached state: %s", err
            )
            self._last_check_attempt = time.time()
            # Return cached info if available
            if self._license_info:
                return self._license_info
            # First run with no connection — default to demo
            return LicenseInfo(
                valid=False,
                tier=LicenseTier.DEMO,
                expires=None,
                email=None,
                max_installations=0,
                features=[],
                message="Cannot reach license server. Running in DEMO mode.",
            )

    async def periodic_check(self) -> None:
        """Perform periodic license re-validation.

        Called by the coordinator on schedule. Handles errors gracefully.
        FREE mode skips all server checks.
        """
        if self.is_free_mode:
            return  # No server checks for FREE mode

        if not self.needs_recheck:
            return

        _LOGGER.debug("Performing periodic license check")
        try:
            info = await self._api.check_status()
            self._license_info = info
            self._last_check_attempt = time.time()

            if info.valid:
                self._cached_valid = True
                self._cached_tier = info.tier
                self._last_successful_check = time.time()
            else:
                self._cached_valid = False
                _LOGGER.warning(
                    "License check: license no longer active: %s",
                    info.message,
                )

        except APIConnectionError:
            self._last_check_attempt = time.time()
            if self.grace_period_remaining > 0:
                _LOGGER.info(
                    "License server unreachable. Grace period: %.1f hours remaining",
                    self.grace_period_remaining / 3600,
                )
            else:
                _LOGGER.error(
                    "License server unreachable and grace period expired. "
                    "Reverting to DEMO mode."
                )
                self._cached_valid = False
                self._cached_tier = LicenseTier.DEMO

        except Exception as err:
            _LOGGER.error("Unexpected error during license check: %s", err)
            self._last_check_attempt = time.time()

    async def _register_free_ping(self) -> None:
        """FREE installs: tell the license server and the installations webhook that
        this installation runs this version (HA instance id, HA and integration
        version, inverter brand, tariff, energy provider).

        Once per installed version, not on every start, and never when the owner
        switched it off (settings "telemetry": {"enabled": false}) — described in the
        panel (Settings → Privacy) and on smartinghome.pl/hems/. Fire-and-forget.
        """
        try:
            from .const import VERSION
            from .settings_io import read_async, write_async

            tel = (await read_async(self.hass)).get("telemetry") or {}
            if not tel.get("enabled", True) or tel.get("sent") == VERSION:
                return
            await write_async(self.hass, {"telemetry": {**tel, "sent": VERSION}})
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Telemetry settings unreadable — skipping ping: %s", err)
            return

        try:
            import aiohttp
            from .const import LICENSE_API_URL, DOMAIN, VERSION

            url = f"{LICENSE_API_URL}/register-free"

            # Get HA instance UUID as device_id
            device_id = self.hass.data.get("core.uuid", "unknown")
            ha_version = HA_VERSION

            payload = {
                "device_id": str(device_id),
                "ha_version": ha_version,
                "integration_version": VERSION,
            }

            session = self.hass.helpers.aiohttp_client.async_get_clientsession(
                self.hass
            )
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    _LOGGER.debug("FREE registration ping sent successfully")
                else:
                    _LOGGER.debug("FREE ping returned %s", resp.status)

        except Exception as err:
            # Silently ignore — telemetry should never affect operation
            _LOGGER.debug("FREE registration ping failed (non-critical): %s", err)

        # Notify installations webhook about new FREE install
        try:
            import aiohttp
            from .const import VERSION, DOMAIN

            device_id = self.hass.data.get("core.uuid", "unknown")
            ha_version = HA_VERSION

            # Extract config entry data (inverter brand, tariff, provider)
            entry_data = {}
            domain_data = self.hass.data.get(DOMAIN, {})
            for entry_id, entry_info in domain_data.items():
                coord = entry_info.get("coordinator") if isinstance(entry_info, dict) else None
                if coord and hasattr(coord, "entry"):
                    entry_data = coord.entry.data or {}
                    break
            # Fallback: scan config entries directly
            if not entry_data:
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    entry_data = entry.data or {}
                    break

            webhook_payload = {
                "event": "new_free_installation",
                "device_id": str(device_id),
                "ha_version": ha_version,
                "integration_version": VERSION,
                "inverter_brand": entry_data.get("inverter_brand", "unknown"),
                "tariff": entry_data.get("tariff", "unknown"),
                "energy_provider": entry_data.get("energy_provider", "unknown"),
                "license_tier": "free",
            }

            session = self.hass.helpers.aiohttp_client.async_get_clientsession(
                self.hass
            )
            async with session.post(
                "https://a.gregciupek.com/webhook/7cb899c0-5f2c-40f2-8d65-7dcbbbb28f01",
                json=webhook_payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                _LOGGER.debug(
                    "New install webhook: status=%s", resp.status
                )

        except Exception as err:
            _LOGGER.debug("Install webhook failed (non-critical): %s", err)


def any_entry_has_feature(hass: HomeAssistant, feature: str) -> bool:
    """True when any loaded Smarting HOME entry's license allows a feature.

    For per-Home-Assistant services (automatic updates, panel WebSocket commands).
    """
    for entry_data in (hass.data.get(DOMAIN) or {}).values():
        mgr = entry_data.get("license_manager") if isinstance(entry_data, dict) else None
        if mgr is not None and mgr.has_feature(feature):
            return True
    return False


async def async_device_id(hass: HomeAssistant) -> str:
    """The installation id the license server counts (same as at entry setup)."""
    try:
        from homeassistant.helpers.instance_id import async_get as async_get_instance_id

        return await async_get_instance_id(hass)
    except Exception:  # noqa: BLE001
        return str(hass.data.get("core.uuid", ""))


async def async_validate_license_key(hass: HomeAssistant, license_key: str) -> LicenseInfo:
    """Ask the license server about a key for this installation.

    One code path for the config flow, the options flow and the panel activation.
    Raises AuthenticationError (unknown key) and APIConnectionError (no server).
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    from .const import VERSION

    api = SmartingHomeAPI(
        async_get_clientsession(hass),
        license_key,
        device_id=await async_device_id(hass),
        ha_version=HA_VERSION,
        integration_version=VERSION,
    )
    return await api.validate_license()


async def async_check_license_key(hass: HomeAssistant, raw_key: Any) -> tuple[str, dict[str, Any]]:
    """Normalise and validate a pasted key → (key, {ok, code, tier, message, …})."""
    key, result = await check_license_key(
        raw_key,
        lambda k: async_validate_license_key(hass, k),
        auth_errors=(AuthenticationError,),
        connect_errors=(APIConnectionError,),
    )
    if result["ok"]:
        _LOGGER.info("License key %s accepted: tier=%s", mask_key(key), result["tier"])
    else:
        _LOGGER.warning("License key %s not accepted: %s", mask_key(key) or "(empty)", result["code"])
    return key, result
