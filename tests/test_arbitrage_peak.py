"""Regression: Max Zysk planner on the home install, 2026-10-01 (G13 winter).

That day the battery sold at 18–19 h inside the afternoon peak (RCE 1.14) and,
empty at 20:00, the house bought from the grid at the same 1.40 zł/kWh. In the
morning (09–10 h, RCE 0.58–0.88) the plan kept the battery level but general
mode let the GoodWe charge it from PV anyway, so the midday surplus was later
exported at RCE ≈ 0.

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
# Package without running __init__.py (it imports Home Assistant)
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
arb = importlib.import_module("smartinghome_pure.arbitrage")

# Hourly data of 2026-10-01 (kWh, RCE zł/kWh) and the home G13 prices (brutto, all-in)
PV = [0, 0, 0, 0, 0, 0, .1, .5, 1.7, 2.0, 3.0, 3.6, 4.6, 3.6, 4.4, 4.5, 2.9, .8, 0, 0, 0, 0, 0, 0]
LOAD = [1.1, .7, .8, .7, .8, .7, 1.3, 1.4, .8, 1.1, 1.9, 1.0, 1.4, 1.3, 1.1, 1.7, 3.6, 3.4, 1.5, 1.6, 2.2, 1.6, 1.2, .8]
RCE = [.755, .742, .727, .751, .757, .841, 1.029, 1.124, .939, .718, .475, .419, -.013, .183,
       .401, .638, .856, .910, 1.141, 1.121, .914, .875, .950, .902]


def _buy(h: int) -> float:
    if 7 <= h < 13:
        return 0.847
    if 16 <= h < 21:
        return 1.400
    return 0.573


def _inputs(start_h: int, load: list[float] | None = None) -> list:
    load = load or LOAD
    out = []
    for k in range(start_h, 30):
        h = k % 24
        out.append(arb.HourInput(datetime(2026, 10, 1 + k // 24, h), 1.0, _buy(h),  # noqa: DTZ001 — local naive, as the planner
                                 max(RCE[h], 0) * 1.23, load[h], PV[h], ""))
    for x in out:
        x.no_import = x.buy > 0.6
    return out


def test_evening_peak_keeps_energy_for_the_rest_of_the_peak():
    # 18:00, SOC 77 %, the profile expects a light evening (1 kWh/h; reality 1.5–2.2)
    light = [1.0 if 18 <= h <= 20 else LOAD[h] for h in range(24)]
    plan = arb.optimize(77, _inputs(18, light), arb.ArbitrageParams(slot_minutes=60))
    by_hour = {hp.start[11:13]: hp for hp in plan.hours}
    # 19:00 must not sell any more: what is left covers 19–20 h × 1.25 + 1 kWh
    assert by_hour["19"].action != arb.ACT_DISCHARGE
    assert by_hour["19"].grid_export < 0.1
    # the peak ends with the buffer still in the battery (≥ ~1 kWh above the 5 % floor)
    assert by_hour["20"].soc_end >= 14.0


def test_peak_sell_buffer_zero_restores_old_behaviour():
    light = [1.0 if 18 <= h <= 20 else LOAD[h] for h in range(24)]
    p = arb.ArbitrageParams(slot_minutes=60, peak_sell_buffer_kwh=0.0)
    plan = arb.optimize(77, _inputs(18, light), p)
    assert plan.hours[0].action == arb.ACT_DISCHARGE  # still sells the sure surplus at 18:00


def test_morning_surplus_not_stored_is_exported():
    plan = arb.optimize(13, _inputs(0), arb.ArbitrageParams(slot_minutes=60, pv_confidence=1.0))
    by_hour = {hp.start[11:13]: hp for hp in plan.hours[:24]}
    # 09–10 h: the plan keeps the battery level (it charges later, RCE ≈ 0 at 12 h) —
    # executed as PV export with charging blocked, not general mode
    for h in ("09", "10"):
        hp = by_hour[h]
        assert abs(hp.battery_kwh) < 0.05
        assert hp.grid_export > 0.3
        assert hp.action == arb.ACT_PV_EXPORT
    # a full battery with PV surplus stays in general mode
    assert by_hour["15"].soc_start >= 99 and by_hour["15"].action == arb.ACT_PV_CHARGE


def test_classify_pv_export_needs_room_and_surplus():
    p = arb.ArbitrageParams()
    assert arb.classify(0.0, -1.0, 1.0, p, sell=0.5, room=True)[0] == arb.ACT_PV_EXPORT
    assert arb.classify(0.0, -1.0, 1.0, p, sell=0.5, room=False)[0] == arb.ACT_PV_CHARGE
    assert arb.classify(0.0, 1.0, 1.0, p, sell=0.5, room=True)[0] == arb.ACT_HOLD


def test_peak_never_charges_from_grid():
    # 2026-10-02 16:41: SOC 99 %, a 5-minute first slot in the afternoon peak, PV ≈ load.
    # The 99 → 100 % top-up was planned as "charge from grid" and ran at full power.
    p = arb.ArbitrageParams(slot_minutes=15)
    inputs = _inputs(16)
    first = inputs[0]
    inputs[0] = arb.HourInput(first.start.replace(minute=40), 5 / 60, first.buy, first.sell,
                              2.8 * 5 / 60, 3.0 * 5 / 60, "")
    inputs[0].no_import = True
    plan = arb.optimize(99, inputs, p)
    for hp in plan.hours:
        if hp.no_import:
            assert hp.action != arb.ACT_CHARGE_GRID, hp
            assert hp.grid_import < 0.01 or hp.battery_kwh <= 0.0, hp
    assert arb.classify(0.1, -0.02, 5 / 60, p, sell=1.1, no_import=True)[0] == arb.ACT_PV_CHARGE
