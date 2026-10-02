"""RCE PSE options → market price scale (bobrek: gross prices were × 1.23 twice).

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
ru = importlib.import_module("smartinghome_pure.rce_units")


class _Entry:
    def __init__(self, data=None, options=None):
        self.data, self.options = data or {}, options or {}


class _Hass:
    def __init__(self, *entries):
        self.config_entries = types.SimpleNamespace(async_entries=lambda domain: list(entries))


def test_defaults_net_mwh():
    assert ru.market_scale(_Hass(_Entry())) == 1.0
    assert ru.market_scale(_Hass()) == 1.0


def test_gross_in_section_and_kwh():
    h = _Hass(_Entry(options={"pricing": {"use_gross_prices": True, "price_unit": "PLN/MWh"}}))
    assert abs(996.8 * ru.market_scale(h) - 810.4) < 0.1  # 22:00 on 2026-10-02
    h = _Hass(_Entry(options={"use_gross_prices": True, "price_unit": "PLN/kWh"}))
    assert abs(0.9968 * ru.market_scale(h) - 810.4) < 0.1
    assert ru.unit_scale(h) == 1000.0


def test_scale_prices():
    out = ru.scale_prices([{"rce_pln": 123.0, "period": "12:00 - 12:15"}], 1 / 1.23)
    assert abs(out[0]["rce_pln"] - 100.0) < 1e-9 and out[0]["period"] == "12:00 - 12:15"
