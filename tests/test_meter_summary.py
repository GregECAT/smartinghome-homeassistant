"""Energia i koszty: summary on a meter that has just started reporting.

Business site (C13) whose eLicznik access began on 2026-09-30: one day of
September, five hours of October. Seen live on 2026-10-02 — the panel showed a
"previous month" of 118.87 zł (one day of kWh + a whole month of fixed fees)
and used it as the forecast baseline.

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"


def _stub(name: str, **attrs) -> None:
    mod = sys.modules.get(name) or types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


# meter_ws imports Home Assistant only for its WebSocket handlers
_passthrough = lambda *a, **k: (lambda f: f)
_stub("voluptuous", Required=lambda *a, **k: a[0], Optional=lambda *a, **k: a[0])
_stub("homeassistant")
_stub("homeassistant.components")
_stub("homeassistant.components.websocket_api", websocket_command=_passthrough,
      async_response=lambda f: f, async_register_command=lambda *a, **k: None)
sys.modules["homeassistant.components"].websocket_api = sys.modules["homeassistant.components.websocket_api"]
_stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)

_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
mw = importlib.import_module("smartinghome_pure.meter_ws")

NOW = datetime(2026, 10, 2, 12, 0)  # noqa: DTZ001 — local naive, as summarize()
CFG = {"tariff": "C13", "contract_kw": 10.3,
       "prices": {"fixed": {"handlowa": 35, "abonament": 2.28, "mocowa": 10.31}}}


def _hours() -> list:
    start = datetime(2026, 9, 30, 0)  # noqa: DTZ001
    return [(start + timedelta(hours=i), 0.4) for i in range(24 + 5)]


def test_partial_previous_month_carries_fixed_fees_of_its_days_only():
    s = mw.summarize(_hours(), CFG, NOW)
    pm = s["previous_month"]
    assert pm["complete"] is False and pm["days_with_data"] == 1 and pm["days"] == 30
    assert abs(pm["fixed"] - s["fixed_total"] / 30) < 0.01
    assert pm["fixed_month"] == s["fixed_total"]


def test_unfinished_day_is_marked_by_its_hours():
    daily = {d["date"]: d for d in mw.summarize(_hours(), CFG, NOW)["daily"]}
    assert daily["2026-09-30"]["hours"] == 24
    assert daily["2026-10-01"]["hours"] == 5


def test_reactive_energy_from_invoice_is_a_monthly_fee():
    cfg = {**CFG, "prices": {"fixed": {**CFG["prices"]["fixed"], "bierna": 290.28}}}
    s = mw.summarize(_hours(), cfg, NOW)
    assert s["fixed_breakdown"]["bierna"] == 290.28
    # invoice 05.07–04.09.2026: fixed part without reactive was 106.61 zł / month
    assert abs(s["fixed_total"] - (106.61 + 290.28)) < 0.01
    assert "bierna" not in mw.summarize(_hours(), CFG, NOW)["fixed_breakdown"]
