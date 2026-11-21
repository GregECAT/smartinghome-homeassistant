"""Day and night targets of the house: settings (also the older setback in K), the controller, the advisor.

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
hc = importlib.import_module("smartinghome_pure.heating_control")
ha = importlib.import_module("smartinghome_pure.heating_advisor")


def _cfg(**kw):
    return hm.normalize({"enabled": True, "mode": "auto", "valve_type": "cover", "valve_cover": "cover.v", **kw})


# ── settings ─────────────────────────────────────────────────────────────────

def test_older_setback_in_k_becomes_a_night_temperature():
    cfg = _cfg(room_target_c=24, night_setback_k=2.5)
    assert cfg["night_enabled"] is True
    assert cfg["room_night_c"] == 21.5
    assert cfg["night_setback_k"] == 2.5


def test_no_setback_means_night_off_with_a_suggested_value():
    cfg = _cfg(room_target_c=24, night_setback_k=0)
    assert cfg["night_enabled"] is False
    assert cfg["room_night_c"] == 22.0              # day − 2 K, ready when switched on
    assert cfg["night_setback_k"] == 0.0
    assert hc.room_target_now(cfg, 23.0) == 24.0


def test_night_temperature_is_absolute_and_survives_a_day_change():
    stored = hm.to_settings({"room_target_c": 24, "night_enabled": True, "room_night_c": 21, "night_setback_k": 0})
    assert stored["room_night_c"] == 21.0 and stored["night_enabled"] is True
    changed = hm.normalize({**stored, "room_target_c": 23})     # the day target moved with the panel's +/−
    assert changed["room_night_c"] == 21.0
    assert changed["night_setback_k"] == 2.0


def test_switching_night_off_keeps_its_value():
    stored = hm.to_settings({"room_target_c": 24, "night_enabled": True, "room_night_c": 21})
    off = hm.normalize({**stored, "night_enabled": False})
    assert off["night_enabled"] is False and off["room_night_c"] == 21.0
    assert hc.room_target_now(off, 2.0) == 24.0


def test_switching_night_on_over_older_settings():
    older = {"room_target_c": 24, "night_setback_k": 0, "night_from": 22, "night_to": 5}   # stored before v1.87
    on = hm.to_settings({**older, "night_enabled": True})                                 # the panel's switch
    assert on["night_enabled"] is True and on["room_night_c"] == 22.0 and on["night_setback_k"] == 2.0
    assert hc.room_target_now(hm.normalize(on), 23.0) == 22.0


def test_night_range_clamped():
    assert _cfg(night_enabled=True, room_night_c=3)["room_night_c"] == 10.0
    assert _cfg(night_enabled=True, room_night_c=40)["room_night_c"] == 28.0


# ── controller ───────────────────────────────────────────────────────────────

def test_night_window_wraps_midnight_and_never_exceeds_the_day_target():
    cfg = _cfg(room_target_c=24, night_enabled=True, room_night_c=21.5, night_from=22, night_to=5)
    assert hc.room_target_now(cfg, 22.0) == 21.5
    assert hc.room_target_now(cfg, 4.9) == 21.5
    assert hc.room_target_now(cfg, 5.0) == 24.0
    assert hc.room_target_now(cfg, 12.0) == 24.0
    warmer = _cfg(room_target_c=21, night_enabled=True, room_night_c=23)
    assert hc.room_target_now(warmer, 23.0) == 21.0


def test_day_time_window_without_midnight():
    cfg = _cfg(room_target_c=22, night_enabled=True, room_night_c=19, night_from=1, night_to=6)
    assert hc.night_now(cfg, 0.5) is False
    assert hc.night_now(cfg, 3.0) is True
    assert hc.room_target_now(cfg, 3.0) == 19.0


def _inp(hour, room=22.0):
    return hc.ControlInput(now=1000.0 + hour * 3600, local_hour=hour % 24, t_supply=45.0, t_top=70.0,
                           t_boiler=40.0, t_room=room, t_out=0.0, pump_on=True, pump_configured=True)


def test_controller_logs_the_switch_and_lowers_the_curve_at_night():
    cfg = _cfg(room_target_c=22, night_enabled=True, room_night_c=19, night_from=22, night_to=5)
    c = hc.HeatingController(cfg)
    day = c.step(_inp(21.5))
    assert not day.night and day.room_target == 22.0 and day.log == []
    night = c.step(_inp(22.5))
    assert night.night and night.room_target == 19.0
    assert any("Noc" in line and "19.0" in line for line in night.log)
    assert night.curve_c < day.curve_c
    morning = c.step(_inp(29.0))                       # 5:00 the next day
    assert not morning.night and any("Dzień" in line for line in morning.log)


def test_warm_house_at_night_needs_no_heating():
    cfg = _cfg(room_target_c=22, night_enabled=True, room_night_c=19, night_from=22, night_to=5)
    c = hc.HeatingController(cfg)
    assert c.step(_inp(12.0, room=21.5)).heating_needed is True
    assert c.step(_inp(23.0, room=21.5)).heating_needed is False     # 21.5 ≥ 19 + 2


# ── advisor ──────────────────────────────────────────────────────────────────

def test_depletion_follows_the_schedule():
    m = ha.BuildingModel()
    m.ua, m.base = 0.25, 0.0
    flat = [(h * 3600.0, 1.0) for h in range(0, 100)]
    _, day_only = ha.depletion(100.0, 0.0, m, 21.0, flat, None)
    sched = lambda ts: 18.0 if (ts // 3600) % 24 < 8 else 21.0  # noqa: E731
    _, with_night = ha.depletion(100.0, 0.0, m, sched, flat, None)
    assert with_night == pytest.approx(day_only - 0.25 * 3 * 8, rel=0.01)     # 3 K less for 8 h
