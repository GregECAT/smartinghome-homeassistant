# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""License API client: POST /release (free this installation's slot), with a mocked
HTTP session — and the error mapping of /validate the panel activation relies on.

Run: python3 -m pytest tests/  (no Home Assistant or network needed; aiohttp is
stubbed when it is not installed)
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"

try:
    import aiohttp  # noqa: F401
except ImportError:  # minimal stand-in: only what api.py touches
    aiohttp = types.ModuleType("aiohttp")

    class _ClientError(Exception):
        pass

    class _ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    aiohttp.ClientError = _ClientError
    aiohttp.ClientTimeout = _ClientTimeout
    aiohttp.ContentTypeError = type("ContentTypeError", (_ClientError,), {})
    sys.modules["aiohttp"] = aiohttp

_pkg = types.ModuleType("smartinghome_api")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_api", _pkg)
api = importlib.import_module("smartinghome_api.api")
const = importlib.import_module("smartinghome_api.const")
lk = importlib.import_module("smartinghome_api.license_key")

KEY = "SH-PRO--a1b2-c3d4-e5f6"
DEVICE = "0123456789abcdef0123456789abcdef"


class _Response:
    def __init__(self, status, data=None, raise_json=False):
        self.status = status
        self._data = data
        self._raise_json = raise_json

    async def json(self):
        if self._raise_json:
            raise ValueError("not JSON")
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """aiohttp.ClientSession stand-in: records calls, answers or raises."""

    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if self.exc is not None:
            raise self.exc
        return self.response


def _client(session, key=KEY, device=DEVICE):
    return api.SmartingHomeAPI(session, key, device_id=device, ha_version="2026.10.0", integration_version="1.76.0")


def _run(coro):
    return asyncio.run(coro)


# ── /release ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("released", [True, False])
def test_release_ok(released):
    session = _Session(_Response(200, {"ok": True, "released": released}))
    assert _run(_client(session).release_device()) is released
    (call,) = session.calls
    assert call["url"] == const.LICENSE_API_URL + "/release"
    assert call["json"] == {"license_key": KEY, "device_id": DEVICE}
    assert call["headers"]["X-License-Key"] == KEY


def test_release_unknown_key_is_authentication_error():
    session = _Session(_Response(401, {"ok": False}))
    with pytest.raises(api.AuthenticationError):
        _run(_client(session).release_device())


@pytest.mark.parametrize("response", [
    _Response(500, {"ok": False, "message": "Server error"}),
    _Response(502, raise_json=True),          # HTML error page
    _Response(200, {"ok": False}),
])
def test_release_server_error(response):
    with pytest.raises(api.SmartingHomeAPIError) as err:
        _run(_client(_Session(response)).release_device())
    assert not isinstance(err.value, (api.AuthenticationError, api.ConnectionError))


@pytest.mark.parametrize("exc", [aiohttp.ClientError("refused"), asyncio.TimeoutError()])
def test_release_network_failure_is_connection_error(exc):
    with pytest.raises(api.ConnectionError):
        _run(_client(_Session(exc=exc)).release_device())


@pytest.mark.parametrize(("key", "device"), [("", DEVICE), (KEY, "")])
def test_release_needs_key_and_device(key, device):
    session = _Session(_Response(200, {"ok": True, "released": True}))
    with pytest.raises(api.SmartingHomeAPIError):
        _run(_client(session, key, device).release_device())
    assert session.calls == []


def test_release_through_the_deactivation_glue():
    """The panel's "Usuń klucz": released / not held / unknown key → slot free;
    no connection or server error → key removed anyway, user warned."""
    cases = [
        (_Session(_Response(200, {"ok": True, "released": True})), True),
        (_Session(_Response(200, {"ok": True, "released": False})), True),
        (_Session(_Response(401, {"ok": False})), True),
        (_Session(exc=aiohttp.ClientError("refused")), False),
        (_Session(_Response(500, {"ok": False})), False),
    ]
    for session, freed in cases:
        got = _run(lk.release_license_key(
            KEY, lambda k, s=session: _client(s, k).release_device(),
            auth_errors=(api.AuthenticationError,),
        ))
        assert got is freed
        res = lk.deactivation_result(got)
        assert res["ok"] is True and res["tier"] == "free" and res["slot_released"] is freed
        if not freed:
            assert "30 dni" in res["message"] and lk.CONTACT in res["message"]


# ── /validate error mapping (activation messages depend on it) ───────────────

def test_validate_unknown_key_raises_authentication_error():
    # Used to be swallowed by the generic handler → "unknown" in the config flow
    session = _Session(_Response(401, {"valid": False, "message": "License key not found"}))
    with pytest.raises(api.AuthenticationError):
        _run(_client(session).validate_license())


def test_validate_timeout_is_connection_error():
    with pytest.raises(api.ConnectionError):
        _run(_client(_Session(exc=asyncio.TimeoutError())).validate_license())


def test_validate_lifetime_and_limit():
    ok = _run(_client(_Session(_Response(200, {
        "valid": True, "tier": "pro", "lifetime": True, "expires": "lifetime",
        "max_installations": 1, "features": [], "message": "Lifetime license active",
    }))).validate_license())
    assert ok.valid and ok.lifetime is True
    limit = _run(_client(_Session(_Response(403, {
        "valid": False, "tier": "pro", "message": "Maximum installation limit exceeded",
    }))).validate_license())
    assert limit.valid is False
    assert lk.rejection_code(limit.message) == lk.ERR_LIMIT
