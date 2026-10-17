# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""In-panel license activation: key clean-up, masking, server answers → messages.

Run: python3 -m pytest tests/  (no Home Assistant needed — license_key.py is pure)
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_lk")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_lk", _pkg)
lk = importlib.import_module("smartinghome_lk.license_key")
const = importlib.import_module("smartinghome_lk.const")

# Same shape as the keys the license server issues (random part in lower-case hex)
KEY = "SH-PRO--a1b2-c3d4-e5f6"


# ── normalisation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    KEY,
    f"  {KEY}  ",
    f"{KEY}\n",
    "SH-PRO--a1b2-\nc3d4-e5f6",           # wrapped by the mail client
    "SH-PRO--a1b2-c3d4-e5f6.",             # end of a sentence
    f"„{KEY}”",                            # Polish quotes
    f"<{KEY}>",
    f"Klucz: {KEY}",
    f"klucz licencji:{KEY}",
    "SH–PRO––a1b2–c3d4–e5f6",  # en dashes from a word processor
    "SH-PRO--a1b2​-c3d4-e5f6﻿",  # zero-width characters
    "SH-PRO--a1b2-c3d4 -e5f6",        # no-break space
    "sh-pro--a1b2-c3d4-e5f6",              # typed prefix in lower case
])
def test_normalize_key(raw):
    assert lk.normalize_key(raw) == KEY


def test_normalize_keeps_the_random_part_exact():
    # The server compares keys exactly: case and the double dash must survive
    assert lk.normalize_key("SH-PRO--AbCd-c3d4-e5f6") == "SH-PRO--AbCd-c3d4-e5f6"
    assert lk.normalize_key(None) == ""
    assert lk.normalize_key("   ") == ""


@pytest.mark.parametrize(("key", "ok"), [
    (KEY, True),
    ("SH-ENT-a1b2c3d4", True),
    ("", False),
    ("SH-PRO-", False),
    ("abc", False),
    ("PRO-a1b2-c3d4-e5f6", False),
    ("SH-PRO-a1b2 c3d4", False),
    ("SH-PRO-" + "a" * 80, False),
])
def test_is_plausible_key(key, ok):
    assert lk.is_plausible_key(key) is ok


# ── masking ──────────────────────────────────────────────────────────────────

def test_mask_key_hides_the_secret_part():
    masked = lk.mask_key(KEY)
    assert masked == "SH-PRO-••••…e5f6"
    assert "a1b2" not in masked and "c3d4" not in masked
    assert KEY not in masked


@pytest.mark.parametrize(("key", "expected"), [
    ("", ""),
    (None, ""),
    ("SH-PRO-abcd", "SH-PRO-••••"),       # too short to show a tail
    ("not-a-key-at-all-1234", "••••…1234"),
    ("sh-pro--a1b2-c3d4-e5f6", "SH-PRO-••••…e5f6"),
])
def test_mask_key_edge_cases(key, expected):
    assert lk.mask_key(key) == expected


# ── server answers → codes / messages ────────────────────────────────────────

@pytest.mark.parametrize(("message", "code"), [
    ("Maximum installation limit exceeded", lk.ERR_LIMIT),
    ("License has expired", lk.ERR_EXPIRED),
    ("License expired", lk.ERR_EXPIRED),
    ("License is suspended", lk.ERR_SUSPENDED),
    ("License key not found", lk.ERR_INVALID),
    ("No license key provided", lk.ERR_INVALID),
    ("Unexpected response: 500", lk.ERR_SERVER),
    ("Something new", lk.ERR_REJECTED),
    (None, lk.ERR_REJECTED),
])
def test_rejection_code(message, code):
    assert lk.rejection_code(message) == code


def test_every_code_has_a_polish_message_and_a_flow_error():
    codes = [v for k, v in vars(lk).items() if k.startswith("ERR_")]
    for code in codes:
        assert lk.ERROR_MESSAGES[code]
        assert lk.FLOW_ERRORS[code] in {
            "invalid_license", "cannot_connect", "install_limit", "license_inactive", "unknown",
        }
    assert "limit" in lk.ERROR_MESSAGES[lk.ERR_LIMIT].lower()
    assert "połączenia" in lk.ERROR_MESSAGES[lk.ERR_CONNECT]
    assert "nieprawidłowy" in lk.ERROR_MESSAGES[lk.ERR_INVALID].lower()


@dataclass
class _Info:
    valid: bool
    tier: str = "pro"
    expires: str | None = None
    message: str | None = None
    lifetime: bool = False


class _Auth(Exception):
    pass


class _Conn(Exception):
    pass


def _check(raw, outcome):
    calls: list[str] = []

    async def validate(key):
        calls.append(key)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    key, res = asyncio.run(lk.check_license_key(
        raw, validate, auth_errors=(_Auth,), connect_errors=(_Conn,),
    ))
    return key, res, calls


def test_check_accepts_a_lifetime_key():
    key, res, calls = _check(f" {KEY}\n", _Info(True, "pro", "lifetime", "Lifetime license active", True))
    assert calls == [KEY] and key == KEY
    assert res["ok"] is True and res["tier"] == "pro" and res["lifetime"] is True
    assert "dożywotnia" in res["message"]


def test_check_lifetime_from_expires_only():
    _, res, _ = _check(KEY, _Info(True, "enterprise", "lifetime"))
    assert res["lifetime"] is True and res["tier"] == "enterprise"
    assert "ENTERPRISE" in res["message"]


def test_check_dated_license_is_not_lifetime():
    _, res, _ = _check(KEY, _Info(True, "pro", "2027-03-21 20:57:00+00"))
    assert res["ok"] is True and res["lifetime"] is False


@pytest.mark.parametrize(("outcome", "code"), [
    (_Auth("Invalid license key"), lk.ERR_INVALID),
    (_Conn("timeout"), lk.ERR_CONNECT),
    (RuntimeError("boom"), lk.ERR_UNKNOWN),
    (_Info(False, "pro", message="Maximum installation limit exceeded"), lk.ERR_LIMIT),
    (_Info(False, "demo", message="License is suspended"), lk.ERR_SUSPENDED),
])
def test_check_failures(outcome, code):
    _, res, calls = _check(KEY, outcome)
    assert calls == [KEY]
    assert res["ok"] is False and res["code"] == code and res["tier"] is None
    assert res["message"] == lk.ERROR_MESSAGES[code]
    assert KEY not in res["message"]


def test_check_does_not_send_a_malformed_key():
    _, res, calls = _check("hello", _Info(True))
    assert calls == []
    assert res["ok"] is False and res["code"] == lk.ERR_FORMAT


# ── storing the key / reload ─────────────────────────────────────────────────

def test_entry_data_with_and_without_key():
    base = {"tariff": "g13", const.CONF_LICENSE_MODE: const.LICENSE_MODE_FREE, const.CONF_LICENSE_KEY: ""}
    pro = lk.entry_data_with_key(base, KEY)
    assert pro[const.CONF_LICENSE_MODE] == const.LICENSE_MODE_PRO
    assert pro[const.CONF_LICENSE_KEY] == KEY and pro["tariff"] == "g13"
    assert base[const.CONF_LICENSE_KEY] == ""  # input not mutated
    free = lk.entry_data_without_key(pro)
    assert free[const.CONF_LICENSE_MODE] == const.LICENSE_MODE_FREE
    assert free[const.CONF_LICENSE_KEY] == "" and free["tariff"] == "g13"


@pytest.mark.parametrize(("changed", "loaded", "explicit"), [
    (True, True, False),    # the update listener reloads
    (False, True, True),    # same key again (e.g. DEMO after an outage)
    (True, False, True),    # entry failed to set up → no listener
    (False, False, True),
])
def test_needs_explicit_reload(changed, loaded, explicit):
    assert lk.needs_explicit_reload(changed, loaded) is explicit


def test_status_payload_shows_the_masked_key_to_admins_only():
    common = dict(mode="pro", tier="pro", valid=True, key=KEY, lifetime=True,
                  expires="lifetime", last_check=1760000000.0, entry_state="loaded")
    admin = lk.status_payload(**common, is_admin=True)
    user = lk.status_payload(**common, is_admin=False)
    assert admin["key_masked"] == "SH-PRO-••••…e5f6" and admin["has_key"] is True
    assert user["key_masked"] is None and user["has_key"] is True
    assert admin["lifetime"] is True and admin["expires"] is None
    assert KEY not in repr(admin) and KEY not in repr(user)
