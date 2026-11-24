"""Deye Time of Use preview: active slot, targets per intent, sticky targets, write counting.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
tou = importlib.import_module("smartinghome_pure.deye_tou")

D, G = tou.CHARGING_DISABLED, tou.CHARGING_GRID


def _table(**kw):
    """A typical G12w installer table: grid charging 22–06 to 80 %, reserve 20 %."""
    slots = kw.pop("slots", None) or (
        tou.Slot(0, 80, G),      # 00:00–06:00 cheap night, grid charge to 80 %
        tou.Slot(360, 20, D),    # 06:00–13:00 expensive
        tou.Slot(780, 30, D),    # 13:00–15:00 cheap afternoon
        tou.Slot(900, 20, D),    # 15:00–22:00 expensive
        tou.Slot(1320, 80, G),   # 22:00–23:00 cheap night
        tou.Slot(1380, 80, G),   # 23:00–00:00 cheap night
    )
    defaults = dict(enabled=True, grid_charging=True, low_soc=10, clock_drift_s=0.0)
    defaults.update(kw)
    return tou.TouTable(slots=tuple(slots), **defaults)


def _at(hour, minute=0, day=24):
    return datetime(2026, 11, day, hour, minute)


# ── parsing ──


def test_parse_states():
    assert tou.parse_time("13:30:00") == 810
    assert tou.parse_time("06:00") == 360
    assert tou.parse_time("25:00") is None
    assert tou.parse_time("unavailable") is None
    assert tou.parse_int("40.0") == 40
    assert tou.parse_int("unknown") is None
    assert tou.parse_tou_enabled("Disabled") is False
    assert tou.parse_tou_enabled("Week") is True
    assert tou.parse_tou_enabled("unavailable") is None
    assert tou.parse_switch("on") is True and tou.parse_switch("off") is False
    assert tou.parse_switch("unknown") is None


# ── geometry ──


def test_active_slot_follows_start_times_and_wraps():
    t = _table()
    assert tou.active_slot(t, 0) == 0
    assert tou.active_slot(t, 5 * 60 + 59) == 0
    assert tou.active_slot(t, 6 * 60) == 1
    assert tou.active_slot(t, 14 * 60) == 2
    assert tou.active_slot(t, 23 * 60 + 30) == 5
    assert tou.slot_window(t, 2) == "13:00–15:00"
    assert tou.slot_window(t, 5) == "23:00–00:00"


def test_table_starting_after_midnight_wraps_through_slot_six():
    slots = (
        tou.Slot(360, 20, D), tou.Slot(780, 30, D), tou.Slot(900, 20, D),
        tou.Slot(1320, 80, G), tou.Slot(1380, 80, G), tou.Slot(120, 80, G),  # 02:00–06:00
    )
    t = _table(slots=slots)
    assert tou.slots_valid(t)
    assert tou.active_slot(t, 30) == 4  # 00:30 still in 23:00–02:00
    assert tou.active_slot(t, 3 * 60) == 5


def test_invalid_tables_have_no_active_slot():
    unreadable = _table(slots=(tou.Slot(None, 20, D),) + (tou.Slot(60, 20, D),) * 5)
    assert not tou.slots_valid(unreadable)
    assert tou.active_slot(unreadable, 100) is None
    zigzag = _table(slots=(
        tou.Slot(0, 20, D), tou.Slot(600, 20, D), tou.Slot(300, 20, D),
        tou.Slot(900, 20, D), tou.Slot(1000, 20, D), tou.Slot(1100, 20, D),
    ))
    assert not tou.slots_valid(zigzag)


# ── targets ──


def test_reserve_is_lowest_floor_of_slots_without_grid_charging():
    assert tou.reserve_soc(_table()) == 20
    assert tou.reserve_soc(_table(low_soc=25)) == 25  # never below Battery Low SOC
    all_grid = _table(slots=tuple(tou.Slot(i * 240, 90, G) for i in range(6)), low_soc=15)
    assert tou.reserve_soc(all_grid) == 15


def test_targets_per_intent():
    base = tou.Slot(360, 20, D)
    kw = dict(reserve=20, low_soc=10, soc_now=55.2)
    assert tou.slot_target("charge_grid", base, **kw)[:2] == (100, G)
    assert tou.slot_target("charge_grid", base, charge_target=85, **kw)[:2] == (85, G)
    assert tou.slot_target("hold", base, **kw)[:2] == (56, D)  # rounded up: no discharge at all
    assert tou.slot_target("home", base, **kw)[:2] == (20, D)
    assert tou.slot_target("general", base, **kw)[:2] == (20, D)


def test_sell_and_pv_export_fall_back_to_home_with_a_note():
    base = tou.Slot(900, 30, D)
    for intent in ("sell", "pv_export"):
        soc, charging, note = tou.slot_target(intent, base, reserve=20, low_soc=10, soc_now=70)
        assert (soc, charging) == (20, D)
        assert "Deye" in note


def test_hold_without_soc_keeps_the_slot():
    base = tou.Slot(900, 30, D)
    soc, charging, note = tou.slot_target("hold", base, reserve=20, low_soc=10, soc_now=None)
    assert (soc, charging) == (30, D)
    assert note


# ── shadow controller ──


def test_general_writes_nothing():
    sh = tou.TouShadow()
    r = sh.update(_table(), intent="general", soc_now=50, now=_at(10))
    assert r.available and r.slot == 2 and r.writes == []
    assert r.writes_today == 0


def test_charge_grid_changes_only_the_running_slot():
    sh = tou.TouShadow()
    r = sh.update(_table(), intent="charge_grid", soc_now=40, now=_at(14))
    assert r.slot == 3 and r.window == "13:00–15:00"
    assert [(w.slot, w.field, w.old, w.new) for w in r.writes] == [(3, "soc", 30, 100), (3, "charging", D, G)]
    assert r.writes_today == 2
    assert "slot 3" in tou.describe_writes(r)


def test_re_assert_and_changing_soc_write_nothing_more():
    sh = tou.TouShadow()
    sh.update(_table(), intent="hold", soc_now=61.0, now=_at(10))
    first = sh.writes_today
    for minute in range(15, 60, 15):
        r = sh.update(_table(), intent="hold", soc_now=61.0 - minute / 10, now=_at(10, minute))
        assert r.writes == []
        assert r.target_soc == 61
    assert sh.writes_today == first


def test_slot_end_restores_previous_slot():
    sh = tou.TouShadow()
    sh.update(_table(), intent="charge_grid", soc_now=40, now=_at(14, 30))
    r = sh.update(_table(), intent="charge_grid", soc_now=60, now=_at(15, 0))
    pairs = {(w.slot, w.field): (w.old, w.new) for w in r.writes}
    assert pairs[(3, "soc")] == (100, 30)  # 13:00–15:00 back to the installer's value
    assert pairs[(3, "charging")] == (G, D)
    assert pairs[(4, "soc")] == (20, 100)  # 15:00–22:00 now charges


def test_back_to_general_restores_the_slot():
    sh = tou.TouShadow()
    sh.update(_table(), intent="charge_grid", soc_now=40, now=_at(14))
    r = sh.update(_table(), intent="general", soc_now=70, now=_at(14, 20))
    assert [(w.slot, w.field, w.new) for w in r.writes] == [(3, "soc", 30), (3, "charging", D)]


def test_blocking_problems_make_preview_unavailable():
    sh = tou.TouShadow()
    for table in (_table(enabled=False), _table(clock_drift_s=900.0)):
        r = sh.update(table, intent="charge_grid", soc_now=40, now=_at(14))
        assert not r.available and r.problems and r.writes == []
    assert sh.writes_today == 0


def test_grid_charging_switch_off_is_reported():
    r = tou.TouShadow().update(_table(grid_charging=False), intent="charge_grid", soc_now=40, now=_at(14))
    assert r.available
    assert any("Battery Grid Charging" in p for p in r.problems)


def test_budget_counts_per_hour_and_resets_daily():
    sh = tou.TouShadow(day_budget=48, hour_budget=6)
    now = _at(14)
    intents = ["charge_grid", "hold", "charge_grid", "hold"]
    flags = []
    for i, intent in enumerate(intents):
        r = sh.update(_table(), intent=intent, soc_now=50 + i, now=now + timedelta(minutes=i))
        flags.append(r.over_budget)
    assert flags[:3] == [False, False, False]  # 2 + 2 + 2 writes = 6 in the hour
    assert flags[3] is True
    r = sh.update(_table(), intent="general", soc_now=60, now=_at(0, 5, day=25))
    # New day: the counter starts again with the one write that restores yesterday's slot 3
    assert [(w.slot, w.field, w.new) for w in r.writes] == [(3, "soc", 30)]
    assert r.writes_today == 1 and not r.over_budget


def test_as_dict_is_panel_ready():
    r = tou.TouShadow().update(_table(), intent="hold", soc_now=44.5, now=_at(8))
    d = r.as_dict()
    assert d["intent_label"] == "trzymanie baterii"
    assert d["target_soc"] == 45 and d["target_charging_label"] == "bez ładowania z sieci"
    assert d["writes"] and all(isinstance(w, str) for w in d["writes"])
