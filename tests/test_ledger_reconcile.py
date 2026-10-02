"""Ledger gap fill: the battery's share comes from the energy balance, not its counters.

2026-10-02: during a 10:02–12:58 outage PV charged the battery 12 → 99 % but the
inverter's charge counter barely moved, so the gap's PV was booked to the grid.

Run: python3 -m pytest tests/  (Home Assistant modules are stubbed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import date, datetime
from pathlib import Path

for name in ("homeassistant", "homeassistant.core", "homeassistant.helpers",
             "homeassistant.helpers.storage", "homeassistant.util"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["homeassistant.core"].HomeAssistant = object
sys.modules["homeassistant.helpers.storage"].Store = lambda *a, **k: None
_dt = types.ModuleType("homeassistant.util.dt")
_dt.now = datetime.now
sys.modules["homeassistant.util.dt"] = _dt
sys.modules["homeassistant.util"].dt = _dt

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
el = importlib.import_module("smartinghome_pure.energy_ledger")


def _ledger():
    led = el.EnergyLedger.__new__(el.EnergyLedger)
    led._days, led._pool_pv, led._pool_grid, led._dirty = {}, 0.0, 0.0, False
    return led


def test_gap_pv_charges_battery_not_grid():
    led = _ledger()
    day = date(2026, 10, 2)
    # Gap: PV 20 kWh, house 3, meter export 8.3, import 0 → the battery took 8.7 kWh.
    # The inverter's charge counter shows only 3.6 (it undercounts).
    added = led.reconcile(day, {"pv": 20.0, "load": 3.0, "charge": 3.6, "discharge": 0.0,
                                "import": 0.0, "export": 8.3},
                          buy_price=0.57, sell_price=0.5, is_peak=False, elapsed_min=780)
    v = led.day(day)
    assert abs(added["charge"] - 8.7) < 0.01
    assert abs(v["pv_bat"] - 8.7) < 0.01
    assert abs(v["pv_grid"] + v["bat_grid"] - 8.3) < 0.01  # flows agree with the meter
    assert v["export"] == 8.3


def test_without_meter_counters_falls_back_to_battery_counters():
    led = _ledger()
    day = date(2026, 10, 2)
    added = led.reconcile(day, {"pv": 5.0, "load": 2.0, "charge": 1.0, "discharge": 0.0},
                          buy_price=0.57, sell_price=0.5, is_peak=False, elapsed_min=600)
    assert added["charge"] == 1.0
