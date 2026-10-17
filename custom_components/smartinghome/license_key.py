# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""License keys in the panel and the options flow — pure Python (no Home Assistant).

Keys arrive by e-mail and are pasted by hand, so they are cleaned up before they go
to the license server (normalize_key), never logged in full (mask_key), and the
server's answer is turned into one short Polish message (activation_result).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any

from .const import (
    CONF_LICENSE_KEY,
    CONF_LICENSE_MODE,
    LICENSE_MODE_FREE,
    LICENSE_MODE_PRO,
)

_LOGGER = logging.getLogger(__name__)

# Dashes that mail clients and word processors put in place of "-"
_DASHES = "‐‑‒–—―−﹘﹣－"
# Invisible characters a copy from an e-mail can carry
_INVISIBLE = "​‌‍⁠﻿­"
_WRAPPERS = "\"'`<>«»„“”‚‘’[](){}"
_TRAILING = ".,;:!"

# SH-PRO-…, SH-ENT-… — the random part is matched by the server exactly (it may
# contain lower-case letters and "--"), so only the prefix is case-folded
_PREFIX_RE = re.compile(r"^sh-[a-z]{2,12}-", re.IGNORECASE)
_KEY_RE = re.compile(r"^SH-[A-Z]{2,12}-[A-Za-z0-9-]{4,64}$")

# Error codes (WebSocket "code"; the options flow maps them to its error keys)
ERR_FORMAT = "invalid_format"
ERR_INVALID = "invalid_key"
ERR_CONNECT = "cannot_connect"
ERR_LIMIT = "install_limit"
ERR_EXPIRED = "expired"
ERR_SUSPENDED = "suspended"
ERR_SERVER = "server_error"
ERR_REJECTED = "rejected"
ERR_UNKNOWN = "unknown"

CONTACT = "kontakt@smartinghome.pl"

ERROR_MESSAGES: dict[str, str] = {
    ERR_FORMAT: (
        "To nie wygląda na klucz Smarting HOME — klucz zaczyna się od „SH-PRO-”. "
        "Skopiuj go w całości z e-maila z zamówieniem."
    ),
    ERR_INVALID: (
        "Nieprawidłowy klucz — serwer licencji go nie zna. Sprawdź, czy skopiowałeś "
        "cały klucz z e-maila (bez spacji i kropki na końcu)."
    ),
    ERR_CONNECT: (
        "Brak połączenia z serwerem licencji. Sprawdź, czy Home Assistant ma dostęp "
        "do internetu, i spróbuj ponownie za chwilę."
    ),
    ERR_LIMIT: (
        "Limit instalacji: ten klucz jest już aktywny w innej instalacji Home Assistant "
        f"(licencja obejmuje jedną instalację). Aby przenieść licencję, napisz na {CONTACT}."
    ),
    ERR_EXPIRED: f"Ta licencja wygasła. Napisz na {CONTACT}.",
    ERR_SUSPENDED: f"Ta licencja jest zawieszona. Napisz na {CONTACT}.",
    ERR_SERVER: "Serwer licencji zwrócił błąd. Spróbuj ponownie za kilka minut.",
    ERR_REJECTED: f"Serwer licencji odrzucił klucz. Napisz na {CONTACT}.",
    ERR_UNKNOWN: "Nie udało się sprawdzić klucza (nieoczekiwany błąd). Spróbuj ponownie.",
}

# Options / config flow error keys (strings.json "error")
FLOW_ERRORS: dict[str, str] = {
    ERR_FORMAT: "invalid_license",
    ERR_INVALID: "invalid_license",
    ERR_CONNECT: "cannot_connect",
    ERR_LIMIT: "install_limit",
    ERR_EXPIRED: "license_inactive",
    ERR_SUSPENDED: "license_inactive",
    ERR_REJECTED: "license_inactive",
    ERR_SERVER: "cannot_connect",
    ERR_UNKNOWN: "unknown",
}


def normalize_key(raw: Any) -> str:
    """The key as the license server stores it, from whatever the user pasted.

    Drops whitespace and invisible characters anywhere (an e-mail can wrap the key),
    quotes or brackets around it, a "Klucz:" label before it and punctuation after
    it; typographic dashes become "-". Only the SH-XXX- prefix is upper-cased.
    """
    if raw is None:
        return ""
    s = unicodedata.normalize("NFKC", str(raw))
    for ch in _DASHES:
        s = s.replace(ch, "-")
    s = "".join(ch for ch in s if ch not in _INVISIBLE and not ch.isspace())
    idx = s.lower().find("sh-")
    if idx > 0 and s[idx - 1] in ":=":
        s = s[idx:]
    s = s.strip(_WRAPPERS).rstrip(_TRAILING).strip(_WRAPPERS)
    m = _PREFIX_RE.match(s)
    if m:
        s = m.group(0).upper() + s[m.end():]
    return s


def is_plausible_key(key: str) -> bool:
    """Shape check before asking the server (SH-PRO-…, 4–64 key characters)."""
    return bool(key) and bool(_KEY_RE.match(key))


def mask_key(key: str | None) -> str:
    """SH-PRO-••••…last4 — for logs and the panel; never the whole key."""
    key = str(key or "")
    if not key:
        return ""
    m = _PREFIX_RE.match(key)
    prefix = m.group(0).upper() if m else ""
    rest = key[len(prefix):]
    tail = rest[-4:] if len(rest) >= 12 else ""
    return f"{prefix}••••…{tail}" if tail else f"{prefix}••••"


def rejection_code(message: str | None) -> str:
    """Error code for a key the server answered for but did not accept."""
    m = (message or "").lower()
    if "limit" in m and "install" in m:
        return ERR_LIMIT
    if "expired" in m:
        return ERR_EXPIRED
    if "suspended" in m or "revoked" in m:
        return ERR_SUSPENDED
    if "not found" in m or "invalid" in m or "no license key" in m:
        return ERR_INVALID
    if "unexpected response" in m or "server error" in m:
        return ERR_SERVER
    return ERR_REJECTED


def error_result(code: str) -> dict[str, Any]:
    """WebSocket result for a failed activation."""
    return {
        "ok": False,
        "code": code,
        "tier": None,
        "message": ERROR_MESSAGES.get(code, ERROR_MESSAGES[ERR_UNKNOWN]),
    }


def activation_result(
    valid: bool, tier: Any, message: str | None, lifetime: bool = False
) -> dict[str, Any]:
    """WebSocket result for an answer from the license server."""
    if not valid:
        return error_result(rejection_code(message))
    tier_s = str(getattr(tier, "value", tier) or "pro").lower()
    label = "ENTERPRISE" if tier_s == "enterprise" else "PRO"
    text = f"Licencja {label}{' dożywotnia' if lifetime else ''} aktywna. Przeładowuję integrację — to potrwa kilkanaście sekund."
    return {"ok": True, "code": None, "tier": tier_s, "lifetime": bool(lifetime), "message": text}


def is_lifetime(expires: Any, lifetime_flag: Any = None) -> bool:
    """The server marks perpetual licenses with lifetime=true / expires="lifetime"."""
    return bool(lifetime_flag) or str(expires or "").lower() == "lifetime"


async def check_license_key(
    raw_key: Any,
    validate: Callable[[str], Awaitable[Any]],
    *,
    auth_errors: tuple[type[BaseException], ...] = (),
    connect_errors: tuple[type[BaseException], ...] = (),
) -> tuple[str, dict[str, Any]]:
    """Normalise a pasted key and validate it → (key, activation result).

    validate(key) returns the server's LicenseInfo (valid, tier, expires, message,
    lifetime) and raises one of auth_errors for an unknown key and connect_errors when
    the license server cannot be reached. A key of the wrong shape is not sent.
    """
    key = normalize_key(raw_key)
    if not is_plausible_key(key):
        return key, error_result(ERR_FORMAT)
    try:
        info = await validate(key)
    except auth_errors:
        return key, error_result(ERR_INVALID)
    except connect_errors:
        return key, error_result(ERR_CONNECT)
    except Exception as err:  # noqa: BLE001 — reported to the user, not raised
        _LOGGER.warning("License check of %s failed: %s", mask_key(key), type(err).__name__)
        return key, error_result(ERR_UNKNOWN)
    return key, activation_result(
        bool(getattr(info, "valid", False)),
        getattr(info, "tier", None),
        getattr(info, "message", None),
        is_lifetime(getattr(info, "expires", None), getattr(info, "lifetime", False)),
    )


def needs_explicit_reload(data_changed: bool, entry_loaded: bool) -> bool:
    """Whether to schedule the entry reload ourselves after storing a key.

    A loaded entry whose data changed is reloaded by its update listener; reloading
    it here as well would run setup twice. An entry that failed to set up has no
    listener, and the same key stored again is no change — both need a reload.
    """
    return not (data_changed and entry_loaded)


def entry_data_with_key(data: dict[str, Any], key: str) -> dict[str, Any]:
    """Config entry data after activation (PRO mode + key)."""
    return {**data, CONF_LICENSE_MODE: LICENSE_MODE_PRO, CONF_LICENSE_KEY: key}


def entry_data_without_key(data: dict[str, Any]) -> dict[str, Any]:
    """Config entry data after removing the key (back to FREE)."""
    return {**data, CONF_LICENSE_MODE: LICENSE_MODE_FREE, CONF_LICENSE_KEY: ""}


def status_payload(
    *,
    mode: str,
    tier: str,
    valid: bool,
    key: str,
    lifetime: bool = False,
    expires: str | None = None,
    last_check: float | None = None,
    grace_hours: float | None = None,
    message: str | None = None,
    entry_state: str | None = None,
    is_admin: bool = False,
) -> dict[str, Any]:
    """smartinghome/license/status — what the panel's license card shows.

    The masked key goes to administrators only (they are the ones who can change it).
    """
    return {
        "mode": mode,
        "tier": str(tier or "").lower(),
        "valid": bool(valid),
        "has_key": bool(key),
        "key_masked": mask_key(key) if is_admin else None,
        "lifetime": bool(lifetime),
        "expires": None if lifetime else expires,
        "last_check": last_check or None,
        "grace_hours": grace_hours,
        "message": message,
        "entry_state": entry_state,
        "is_admin": bool(is_admin),
    }
