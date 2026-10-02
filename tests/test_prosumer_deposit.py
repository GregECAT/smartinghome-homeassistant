"""Prosumer deposit: FIFO use, 12-month expiry, 30 % refund, value of an exported kWh.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
pd = importlib.import_module("smartinghome_pure.prosumer_deposit")


def test_hourly_rce_from_quarters():
    q = [(datetime(2026, 8, 15, 0, 15), 400.0), (datetime(2026, 8, 15, 0, 30), 500.0),
         (datetime(2026, 8, 15, 0, 45), 600.0), (datetime(2026, 8, 15, 1, 0), 700.0)]
    assert pd.hourly_rce(q) == {datetime(2026, 8, 15, 0): 550.0}


def test_monthly_flows_negative_price_is_zero():
    t = datetime(2026, 7, 1, 12)
    flows = pd.monthly_flows([(t, 2.0), (t.replace(hour=13), 1.0)], [(t, 1.0)],
                             {t: 500.0, t.replace(hour=13): -50.0}, lambda _: 0.5)
    m = flows["2026-07"]
    assert m["deposit"] == round(2.0 * 0.5 * 1.23, 2)
    assert m["energy_charge"] == 0.5 and m["export_kwh"] == 3.0


def test_deposit_credited_next_month_and_used_fully():
    flows = {"2026-04": {"deposit": 50, "energy_charge": 200},
             "2026-05": {"deposit": 60, "energy_charge": 200},
             "2026-06": {"deposit": 40, "energy_charge": 200}}
    r = pd.settle(flows, first_month="2026-04", current_month="2026-06",
                  projection={f"2026-{m:02d}": {"deposit": 40, "energy_charge": 200} for m in range(7, 13)})
    used = {m["month"]: m["deposit_used"] for m in r["months"]}
    assert used == {"2026-04": 0, "2026-05": 50, "2026-06": 60}
    assert r["balance_now"] == 0 and r["accruing_now"] == 40
    assert r["export_value_factor"] == 1.0


def test_surplus_expires_with_30_percent_refund():
    flows = {f"2025-{m:02d}": {"deposit": 100, "energy_charge": 10} for m in range(4, 13)}
    flows.update({f"2026-{m:02d}": {"deposit": 100, "energy_charge": 10} for m in range(1, 7)})
    r = pd.settle(flows, first_month="2025-04", current_month="2026-06",
                  projection=pd.project(flows, "2026-06"))
    assert r["lost_total"] > 0 and r["refund_total"] > 0
    # refund never above 30 % of a month's deposit
    assert r["refund_total"] <= 0.3 * 100 * 2 + 0.01
    assert r["export_value_factor"] < 1.0


def test_opening_balance_used_first():
    flows = {"2026-04": {"deposit": 0, "energy_charge": 30}}
    r = pd.settle(flows, first_month="2026-04", current_month="2026-04",
                  opening=[{"month": "2026-03", "amount": 50}])
    assert r["months"][0]["deposit_used"] == 30 and r["balance_now"] == 20


def test_reconcile():
    rows = [{"month": "2026-07", "deposit": 51.08}]
    assert pd.reconcile(rows, {"2026-07": 43.9})[0]["diff"] == 7.18
