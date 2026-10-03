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
    p = arb.ArbitrageParams(slot_minutes=60)
    plan = arb.optimize(77, _inputs(18, light), p)
    by_hour = {hp.start[11:13]: hp for hp in plan.hours}
    kwh_per_pct = p.capacity_kwh / 100
    # Three hours before the end the sale keeps the rest of the peak × margin + the buffer
    assert (by_hour["18"].soc_end - 5.0) * kwh_per_pct >= 2 * 1.0 * 1.1 + 0.9
    # 19:00 still keeps the last hour's house load
    assert (by_hour["19"].soc_end - 5.0) * kwh_per_pct >= 1.0
    # Endgame (2026-10-02, owner's rule): the last hour sells everything the house
    # won't need — the peak ends at the floor, not with a spare kWh
    assert by_hour["20"].soc_end <= 6.0
    assert by_hour["21"].soc_start <= 6.0


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


def test_flat_price_day_morning_goes_to_the_floor():
    # Saturday 2026-10-03 8:00, SOC 32 %, sunny forecast, one buy price all day:
    # empty the battery to its floor selling at the morning RCE, PV refills it by noon
    pv = {8: 1.8, 9: 2.9, 10: 3.7, 11: 4.2, 12: 4.3, 13: 4.1, 14: 3.5, 15: 2.8, 16: 1.9, 17: 0.8}
    sell = {8: 0.906, 9: 0.752, 10: 0.528, 11: 0.356, 12: 0.172, 13: 0.125, 14: 0.202,
            15: 0.51, 16: 0.805, 17: 1.05, 18: 1.231, 19: 1.309, 20: 1.238, 21: 1.166}
    inputs = []
    for k in range(8, 32):
        h = k % 24
        x = arb.HourInput(datetime(2026, 10, 3 + k // 24, h), 1.0, 0.626, sell.get(h, 0.9),
                          1.2, pv.get(h, 0.0), "")
        x.no_import = False
        inputs.append(x)
    p = arb.ArbitrageParams(slot_minutes=60, peak_floor_soc=9, reserve_soc=15)
    plan = arb.optimize(32, inputs, p)
    assert plan.hours[0].action == arb.ACT_DISCHARGE
    assert min(hp.soc_end for hp in plan.hours[:4]) < 12  # below the 15 % reserve
    assert max(hp.soc_end for hp in plan.hours[:10]) > 90  # the sun refills it


def _day(start, end, buy, sell, pv, load=1.2, peak=()):
    out = []
    for k in range(start, end):
        h = k % 24
        x = arb.HourInput(datetime(2026, 10, 2 + k // 24, h), 1.0, buy(h), sell.get(h, 0.9) if isinstance(sell, dict) else sell,
                          load, pv.get(h, 0.0), "")
        x.no_import = h in peak and k < 24
        out.append(x)
    arb.mark_daylight(out, 6.8, 18.3)
    return out


def test_no_peak_ahead_daylight_never_charges_from_grid():
    # Saturday 11:00 (flat price, no peak): buying at 0.63 to sell at 1.31 in the evening
    # pays on paper, but in daylight the grid never charges the battery (owner's rule)
    pv = {11: 2.0, 12: 2.1, 13: 2.0, 14: 1.8, 15: 1.4, 16: 0.9, 17: 0.3}
    sell = {11: 0.356, 12: 0.172, 13: 0.125, 14: 0.202, 15: 0.51, 16: 0.805, 17: 1.05,
            18: 1.231, 19: 1.309, 20: 1.238, 21: 1.166}
    p = arb.ArbitrageParams(slot_minutes=60, peak_floor_soc=9, reserve_soc=15)
    plain = arb.optimize(22, _day(11, 35, lambda h: 0.626, sell, pv), arb.ArbitrageParams(
        slot_minutes=60, peak_floor_soc=9, reserve_soc=15, daylight_pv_first=0))
    assert any(hp.battery_kwh > 0 and hp.grid_import > 0.01 for hp in plain.hours[:7])  # the old behaviour
    plan = arb.optimize(22, _day(11, 35, lambda h: 0.626, sell, pv), p)
    for hp in plan.hours[:7]:
        assert hp.action != arb.ACT_CHARGE_GRID, hp
        assert hp.battery_kwh <= 0 or hp.grid_import < 0.01, hp
        assert hp.no_grid_charge


def _g13(h):
    return 1.35 if 16 <= h < 21 or 7 <= h < 13 else 0.626


def test_weekday_cloudy_grid_tops_up_only_the_shortfall_before_the_peak():
    # Weekday 13:00, cloudy: PV can't fill the battery by 16:00 → the grid adds the
    # missing part only, so the battery is full when the peak starts
    pv = {13: 1.6, 14: 1.5, 15: 1.4}
    p = arb.ArbitrageParams(slot_minutes=60, peak_floor_soc=9, reserve_soc=15, wear_cost=0.16)
    ins = _day(13, 37, _g13, 0.9, pv, load=0.8, peak=range(16, 21))
    plan = arb.optimize(30, ins, p)
    first3 = plan.hours[:3]
    assert all(hp.grid_import < 0.01 for hp in plan.hours[3:8])     # the peak runs on the battery
    assert all(hp.grid_export < 0.01 for hp in first3)              # PV goes into the battery first
    grid_in = sum(hp.grid_import for hp in first3)
    pv_surplus = sum(max(pv[13 + i] - 0.8, 0) for i in range(3))
    need = (plan.hours[2].soc_end - 30) / 100 * p.capacity_kwh / p.eff_charge
    assert grid_in <= need - pv_surplus + 0.3                       # no more than the shortfall
    assert grid_in > 0.5


def test_weekday_sunny_no_grid_before_the_peak():
    # Weekday 13:00, sunny: the sun fills the battery before 16:00 → no grid at all
    pv = {13: 4.5, 14: 4.0, 15: 3.2}
    p = arb.ArbitrageParams(slot_minutes=60, peak_floor_soc=9, reserve_soc=15, wear_cost=0.16)
    plan = arb.optimize(40, _day(13, 37, _g13, 0.9, pv, load=0.8, peak=range(16, 21)), p)
    for hp in plan.hours[:3]:
        assert hp.battery_kwh <= 0 or hp.grid_import < 0.01, hp
