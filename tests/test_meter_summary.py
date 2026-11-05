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


# ── Twoja taryfa a Pstryk (compare_pstryk) ──────────────────────────────────

def _prices(rows, price):
    return {t: price for t, _ in rows}


def test_pstryk_compare_uses_the_same_hours_and_fixed_fees():
    hours = _hours()
    prices = _prices(hours[24:], 1.0)  # Pstryk only for the last 5 hours (01.10)
    cfg = {**CFG, "pstryk_monthly_fee": 30}
    c = mw.compare_pstryk(hours, cfg, NOW, prices)
    assert c["all"]["hours"] == 5 and abs(c["all"]["kwh"] - 2.0) < 1e-9
    # 01.10 00–05 is off-peak: tariff 0.932 + 0.1527 + fees; Pstryk all-in 1.0
    fees = mw.mt.PER_KWH_FEES_NETTO
    assert abs(c["all"]["tariff"] - round(2.0 * (0.932 + 0.1527 + fees), 2)) < 0.011
    assert abs(c["all"]["pstryk"] - 2.0) < 0.011
    # fixed: seller's handlowa 35 replaced by the Pstryk fee 30
    assert abs(c["fixed_tariff"] - c["fixed_pstryk"] - 5.0) < 0.01
    assert c["daily"][0]["date"] == "2026-10-01" and c["daily"][0]["hours"] == 5
    assert "month" not in c  # fewer than 24 compared hours


def test_pstryk_compare_without_distribution_adds_the_tariffs():
    hours = _hours()
    prices = _prices(hours, 0.5)
    c = mw.compare_pstryk(hours, {**CFG, "pstryk_includes_distribution": False}, NOW, prices)
    fees = mw.mt.PER_KWH_FEES_NETTO
    # energy 0.5 + the zone's distribution → more than 0.5 per kWh
    assert c["all"]["pstryk"] > c["all"]["kwh"] * (0.5 + 0.1527 + fees) - 0.01
    assert c["includes_distribution"] is False
    # 29 compared hours → a month estimate from last month's kWh (Sept: 24 × 0.4)
    m = c["month"]
    assert abs(m["kwh"] - 9.6) < 0.01
    assert abs(m["diff"] - (m["pstryk_total"] - m["tariff_total"])) < 0.02


def test_pstryk_compare_hourly_series_from_today():
    now = datetime(2026, 10, 1, 12, 0)  # noqa: DTZ001
    up = {datetime(2026, 10, 1, h): 1.2 for h in range(24)}  # noqa: DTZ001
    up[datetime(2026, 9, 30, 23)] = 9.9  # noqa: DTZ001 — yesterday: dropped
    c = mw.compare_pstryk(_hours(), CFG, now, {}, up)
    assert len(c["hourly"]) == 24
    h8 = next(r for r in c["hourly"] if r["t"].endswith("08:00"))
    assert h8["zone"] == "morning" and abs(h8["tariff"] - (1.01 + 0.2155 + mw.mt.PER_KWH_FEES_NETTO)) < 1e-6
    assert h8["pstryk"] == 1.2


def test_pstryk_compare_none_without_prices_and_needs_input_for_c11():
    assert mw.compare_pstryk(_hours(), CFG, NOW, {}) is None
    c = mw.compare_pstryk(_hours(), {"tariff": "C11"}, NOW, _prices(_hours(), 1.0))
    assert c == {"needs_input": True}
