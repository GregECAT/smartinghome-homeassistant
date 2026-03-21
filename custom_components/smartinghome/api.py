"""API client for Smarting HOME license server."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import (
    LICENSE_API_URL,
    LICENSE_VALIDATE_ENDPOINT,
    LICENSE_STATUS_ENDPOINT,
    LicenseTier,
)

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)


@dataclass
class LicenseInfo:
    """License information returned from API."""

    valid: bool
    tier: LicenseTier
    expires: str | None
    email: str | None
    max_installations: int
    features: list[str]
    message: str | None = None


class SmartingHomeAPIError(Exception):
    """Base exception for API errors."""


class AuthenticationError(SmartingHomeAPIError):
    """Invalid license key."""


class ConnectionError(SmartingHomeAPIError):
    """Cannot reach license server."""


class SmartingHomeAPI:
    """Client for Smarting HOME license API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        license_key: str,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._license_key = license_key
        self._base_url = LICENSE_API_URL

    async def validate_license(self) -> LicenseInfo:
        """Validate license key against the server.

        Returns LicenseInfo with validation result.
        Raises AuthenticationError for invalid keys.
        Raises ConnectionError for network issues.
        """
        url = f"{self._base_url}{LICENSE_VALIDATE_ENDPOINT}"
        headers = {
            "Content-Type": "application/json",
            "X-License-Key": self._license_key,
            "User-Agent": "SmartingHOME-HA/1.0.0",
        }
        payload = {
            "license_key": self._license_key,
            "product": "smartinghome-ha",
            "version": "1.0.0",
        }

        try:
            async with self._session.post(
                url, json=payload, headers=headers, timeout=TIMEOUT
            ) as response:
                data: dict[str, Any] = await response.json()

                if response.status == 200 and data.get("valid"):
                    return LicenseInfo(
                        valid=True,
                        tier=LicenseTier(data.get("tier", "pro")),
                        expires=data.get("expires"),
                        email=data.get("email"),
                        max_installations=data.get("max_installations", 1),
                        features=data.get("features", []),
                        message=data.get("message"),
                    )

                if response.status == 401:
                    raise AuthenticationError(
                        data.get("message", "Invalid license key")
                    )

                if response.status == 403:
                    return LicenseInfo(
                        valid=False,
                        tier=LicenseTier.DEMO,
                        expires=data.get("expires"),
                        email=data.get("email"),
                        max_installations=0,
                        features=[],
                        message=data.get(
                            "message", "License expired or inactive"
                        ),
                    )

                _LOGGER.warning(
                    "Unexpected API response: %s - %s",
                    response.status,
                    data,
                )
                return LicenseInfo(
                    valid=False,
                    tier=LicenseTier.DEMO,
                    expires=None,
                    email=None,
                    max_installations=0,
                    features=[],
                    message=f"Unexpected response: {response.status}",
                )

        except aiohttp.ClientError as err:
            _LOGGER.error("Cannot connect to Smarting HOME API: %s", err)
            raise ConnectionError(
                f"Cannot connect to license server: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("License validation error: %s", err)
            raise SmartingHomeAPIError(
                f"License validation failed: {err}"
            ) from err

    async def check_status(self) -> LicenseInfo:
        """Check current license status (periodic re-validation).

        This is a lighter endpoint for periodic checks.
        """
        url = f"{self._base_url}{LICENSE_STATUS_ENDPOINT}"
        headers = {
            "X-License-Key": self._license_key,
            "User-Agent": "SmartingHOME-HA/1.0.0",
        }

        try:
            async with self._session.get(
                url, headers=headers, timeout=TIMEOUT
            ) as response:
                data: dict[str, Any] = await response.json()

                if response.status == 200:
                    return LicenseInfo(
                        valid=data.get("valid", False),
                        tier=LicenseTier(data.get("tier", "demo")),
                        expires=data.get("expires"),
                        email=data.get("email"),
                        max_installations=data.get("max_installations", 1),
                        features=data.get("features", []),
                        message=data.get("message"),
                    )

                return LicenseInfo(
                    valid=False,
                    tier=LicenseTier.DEMO,
                    expires=None,
                    email=None,
                    max_installations=0,
                    features=[],
                    message=f"Status check failed: {response.status}",
                )

        except aiohttp.ClientError as err:
            _LOGGER.warning(
                "License status check network error (grace period applies): %s",
                err,
            )
            raise ConnectionError(
                f"Cannot reach license server: {err}"
            ) from err
