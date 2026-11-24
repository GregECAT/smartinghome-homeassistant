# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
"""Weighed wood: entries per fire (reloads add up), wood weighed before lighting waiting for its fire, undo,
and the house's draw learnt from weighed fire cycles — what "how long the fire and the buffer last" goes by.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hm = importlib.import_module("smartinghome_pure.heating_model")
ha = importlib.import_module("smartinghome_pure.heating_advisor")

CFG = {"enabled": True, "buffer_l": 1000}
H = 3600.0


def _start_fire(model, now, energy=None):
    model.state = "ignition"
    model._track_session(40.0, now)
    if energy is not None:
        model.session["stored_start_kwh"] = energy


def _end_fire(model, now):
    model.state = "idle"
    model._track_session(40.0, now)


def test_reloads_during_a_fire_add_up():
    m = hm.HeatingModel(CFG)
    _start_fire(m, 1000.0)
    assert m.log_load(12.0, 1100.0, "current")[0] == "current"
    m.log_load(8.5, 5000.0, "current")
    assert m.session["wood_kg"] == 20.5
    assert [x["kg"] for x in m.session["loads"]] == [12.0, 8.5]


def test_weighed_before_lighting_waits_for_the_fire():
    m = hm.HeatingModel(CFG)
    assert m.log_load(15.0, 0.0, "next") == ("next", None)
    assert m.status(10.0)["wood"]["pending_kg"] == 15.0
    _start_fire(m, 20 * 60.0)                      # the water shows the fire 20 min later
    assert m.session["wood_kg"] == 15.0 and m.pending_loads == []
    assert any("przed rozpaleniem" in n for n in m.notes)


def test_wood_weighed_for_a_fire_that_never_came_is_dropped_with_a_note():
    m = hm.HeatingModel(CFG)
    m.log_load(10.0, 0.0, "next")
    m.state = "idle"
    m._track_session(30.0, hm.PENDING_LOAD_S + 60.0)
    assert m.pending_loads == [] and any("nie trafiło" in n for n in m.notes)


def test_the_last_fire_takes_wood_for_twelve_hours():
    m = hm.HeatingModel(CFG)
    _start_fire(m, 0.0)
    _end_fire(m, 4 * H)
    assert m.log_load(10.0, 5 * H, "current") is None        # nothing burns now
    assert m.log_load(10.0, 5 * H, "last")[0] == "last"
    assert m.sessions[-1]["wood_kg"] == 10.0
    assert m.log_load(5.0, 4 * H + hm.LAST_FIRE_LOAD_S + 60, "last") is None
    assert m.log_load(5.0, 5 * H, "auto")[0] == "last"       # older panels: current, else the last fire


def test_undo_takes_back_the_latest_entry():
    m = hm.HeatingModel(CFG)
    _start_fire(m, 0.0)
    m.log_load(12.0, 100.0, "current")
    m.log_load(6.0, 200.0, "current")
    assert m.undo_load(300.0) == (6.0, "current") and m.session["wood_kg"] == 12.0
    assert m.undo_load(400.0) == (12.0, "current") and m.session["wood_kg"] is None
    assert m.undo_load(500.0) is None
    m.log_load(9.0, 600.0, "next")
    assert m.undo_load(700.0) == (9.0, "next") and m.pending_loads == []


def test_a_total_booked_before_entries_becomes_one_entry():
    m = hm.HeatingModel(CFG)
    _start_fire(m, 0.0)
    m.session["wood_kg"] = 20.0                    # booked by an older version: a total, no entries
    m.log_load(5.0, 100.0, "current")
    assert m.session["wood_kg"] == 25.0 and len(m.session["loads"]) == 2


def test_waiting_wood_survives_a_restart():
    m = hm.HeatingModel(CFG)
    m.log_load(11.0, 50.0, "next")
    m2 = hm.HeatingModel(CFG)
    m2.restore(m.dump())
    assert m2.pending_loads == [{"ts": 50.0, "kg": 11.0}]


def _cycle(b, start, hours, dt_k):
    b.open_cycle(start)
    for _ in range(int(hours * 60)):
        b.cycle_add(dt_k, 60.0)


def test_a_weighed_cycle_gives_the_house_draw():
    b = ha.BuildingModel()
    _cycle(b, 0.0, 20.0, 24.0)
    prev = {"start": 0.0, "stored_start_kwh": 20.0, "wood_kg": 60.0, "loads": [{"ts": 0.0, "kg": 60.0}]}
    new = {"start": 20 * H, "stored_start_kwh": 26.0}
    note = b.close_cycle(prev, new, 3.2)
    # 60 kg × 3.2 = 192 kWh, the tanks kept 6 → the house took 186 kWh in 20 h = 9.3 kW at 24 K
    assert note and "60.0 kg" in note
    assert b.cycle_ua() == pytest.approx(9.3 / 24.0, rel=0.01)
    assert b.demand_basis() == "history"
    assert b.house_kw(24.0, 0.0) == pytest.approx(9.3, rel=0.01)
    assert b.demand_kw(24.0, 4.0) == pytest.approx(9.3 / 24.0 * 20.0, rel=0.01)


def test_a_cycle_without_full_weighing_or_too_short_is_not_used():
    b = ha.BuildingModel()
    _cycle(b, 0.0, 20.0, 24.0)
    est = {"start": 0.0, "stored_start_kwh": 20.0, "wood_est_kg": 60.0}         # estimated, not weighed
    assert b.close_cycle(est, {"start": 20 * H, "stored_start_kwh": 26.0}, 3.2) is None
    _cycle(b, 0.0, 2.0, 24.0)
    short = {"start": 0.0, "stored_start_kwh": 20.0, "wood_kg": 10.0, "loads": [{"ts": 0.0, "kg": 10.0}]}
    assert b.close_cycle(short, {"start": 2 * H, "stored_start_kwh": 20.0}, 3.2) is None
    assert b.cycles == []


def test_cycles_survive_a_restart():
    b = ha.BuildingModel()
    _cycle(b, 0.0, 20.0, 24.0)
    b.close_cycle({"start": 0.0, "stored_start_kwh": 20.0, "wood_kg": 60.0, "loads": [{"ts": 0.0, "kg": 60.0}]},
                  {"start": 20 * H, "stored_start_kwh": 26.0}, 3.2)
    b.open_cycle(20 * H)
    b.cycle_add(24.0, 600.0)
    b2 = ha.BuildingModel()
    b2.restore(b.dump())
    assert b2.cycle_ua() == pytest.approx(b.cycle_ua()) and b2.cycle_start == 20 * H


def test_advice_counts_the_wood_still_in_the_firebox():
    b = ha.BuildingModel()
    _cycle(b, 0.0, 20.0, 24.0)
    b.close_cycle({"start": 0.0, "stored_start_kwh": 20.0, "wood_kg": 60.0, "loads": [{"ts": 0.0, "kg": 60.0}]},
                  {"start": 20 * H, "stored_start_kwh": 26.0}, 3.2)
    cfg = hm.normalize({"enabled": True, "buffer_l": 1000})
    status = {"buffer": {"energy_kwh": 30.0, "space_kwh": 20.0}, "boiler": {"firing": True}}
    only_buffer = ha.advise(cfg, status, b, 0.0, lambda ts: 24.0, [], 0.0, 3.2)
    with_fire = ha.advise(cfg, status, b, 0.0, lambda ts: 24.0, [], 0.0, 3.2, fire_left_kwh=46.5)
    assert only_buffer["basis"] == "history" and with_fire["heat_left_kwh"] == 76.5
    # 9.3 kW: the buffer alone ~3.2 h, with the wood in the firebox ~8.2 h
    assert (only_buffer["empty_at"] / H) == pytest.approx(30.0 / 9.3, rel=0.03)
    assert (with_fire["empty_at"] / H) == pytest.approx(76.5 / 9.3, rel=0.03)


# ── heating costs: the kind of wood and the price as bought → zł/kg ─────────

def test_price_per_stacked_metre_of_hardwood():
    c = hm.normalize({"wood_kind": "hard", "wood_price": 300, "wood_price_unit": "mp"})   # bobrek: 300 zł/mp, hard
    assert c["wood_kg_per_unit"] == pytest.approx(700 * 0.65, abs=0.1)
    assert c["wood_price_kg"] == pytest.approx(300 / 455.0, abs=0.001)
    months = hm.monthly_summary([{"start": 1_795_000_000, "duration_h": 5, "stored_kwh": 40, "wood_kg": 45.5}],
                                3600, price_kg=c["wood_price_kg"])
    assert months[0]["cost"] == pytest.approx(30, abs=1)          # 45.5 kg ≈ 0.1 mp ≈ 30 zł


def test_price_per_solid_metre_and_per_kg_and_kinds():
    assert hm.normalize({"wood_kind": "soft", "wood_price": 240, "wood_price_unit": "m3"})["wood_price_kg"] \
        == pytest.approx(0.5, abs=0.001)
    assert hm.normalize({"wood_price": 0.7, "wood_price_unit": "kg"})["wood_price_kg"] == 0.7
    c = hm.normalize({"wood_kind": "oak?", "wood_price_unit": "ton"})
    assert c["wood_kind"] == "mixed" and c["wood_price_unit"] == "mp" and c["wood_price_kg"] is None


def test_older_zl_per_kg_setting_still_counts():
    assert hm.normalize({"wood_price_kg": 0.8})["wood_price_kg"] == 0.8
    stored = hm.to_settings({"wood_kind": "hard", "wood_price": 300})
    assert stored["wood_price"] == 300 and stored["wood_kind"] == "hard" and "wood_kg_per_unit" not in stored
