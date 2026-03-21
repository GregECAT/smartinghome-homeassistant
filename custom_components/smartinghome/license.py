"""License management for Smarting HOME integration."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant

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
    LICENSE_MODE_PRO,
    LicenseTier,
    ATTR_LICENSE_VALID,
    ATTR_LICENSE_TIER,
    ATTR_LICENSE_EXPIRES,
    ATTR_LICENSE_LAST_CHECK,
)

_LOGGER = logging.getLogger(__name__)


class LicenseManager:
    """Manages license validation and caching."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: SmartingHomeAPI,
        license_mode: str = LICENSE_MODE_FREE,
    ) -> None:
        """Initialize the license manager."""
        self.hass = hass
        self._api = api
        self._license_mode = license_mode
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
            "grace_period_remaining_hours": round(
                self.grace_period_remaining / 3600, 1
            ) if not self.is_free_mode else None,
        }

    async def validate(self) -> LicenseInfo:
        """Perform initial license validation.

        FREE mode returns a synthetic valid LicenseInfo immediately.
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
                    "License check: subscription no longer active: %s",
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
