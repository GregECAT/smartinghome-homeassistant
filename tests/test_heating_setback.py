"""Setback periods of the house target (nobody at home, e.g. 7–15 on workdays) and the wood each fire burnt
(the fire's energy balance: what the tanks gained + what the house took meanwhile).

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

WORKDAYS = [0, 1, 2, 3, 4]


def _cfg(**kw):
    return hm.normalize({"enabled": True, "mode": "auto", "valve_type": "cover", "valve_cover": "cover.v", **kw})


def _away(**kw):
    return {"on": True, "from": 7, "to": 15, "temp_c": 22.0, "days": WORKDAYS, **kw}


# ── settings ─────────────────────────────────────────────────────────────────

def test_no_setbacks_by_default_and_they_are_stored():
    assert _cfg()["setbacks"] == []
    stored = hm.to_settings({"room_target_c": 25, "setbacks": [_away()]})
    assert stored["setbacks"] == [{"on": True, "from": 7, "to": 15, "temp_c": 22.0, "days": WORKDAYS}]


def test_setbacks_are_cleaned_and_clamped():
    cfg = _cfg(room_target_c=25, setbacks=[
        "nonsense",
        {"from": 30, "to": -2, "temp_c": 3, "days": [6, 0, 0, 9, True, "x", 2.0]},
        {"on": False},
    ] + [_away()] * 5)
    sb = cfg["setbacks"]
    assert len(sb) == hm.MAX_SETBACKS
    assert sb[0] == {"on": True, "from": 23, "to": 0, "temp_c": 10.0, "days": [0, 2, 6]}
    assert sb[1] == {"on": False, "from": 7, "to": 15, "temp_c": 23.0, "days": [0, 1, 2, 3, 4, 5, 6]}
    assert _cfg(setbacks="7-15")["setbacks"] == []


# ── which target applies ─────────────────────────────────────────────────────

def test_a_workday_period_lowers_the_target_only_then():
    cfg = _cfg(room_target_c=25, setbacks=[_away()])
    assert hc.room_target_now(cfg, 6.9, 0) == 25.0
    assert hc.room_target_now(cfg, 7.0, 0) == 22.0
    assert hc.room_target_now(cfg, 14.9, 4) == 22.0
    assert hc.room_target_now(cfg, 15.0, 4) == 25.0
    assert hc.room_target_now(cfg, 10.0, 5) == 25.0              # Saturday: people are at home
    assert hc.room_target_now(cfg, 10.0) == 22.0                 # no weekday given: every day


def test_a_switched_off_period_or_one_without_days_never_applies():
    cfg = _cfg(room_target_c=25, setbacks=[_away(on=False), _away(days=[])])
    assert hc.setback_now(cfg, 10.0, 0) is None
    assert hc.room_target_now(cfg, 10.0, 0) == 25.0


def test_a_period_across_midnight_belongs_to_the_day_it_started():
    cfg = _cfg(room_target_c=25, setbacks=[_away(**{"from": 22, "to": 6, "days": [4]})])   # Friday night
    assert hc.room_target_now(cfg, 23.0, 4) == 22.0               # Friday 23:00
    assert hc.room_target_now(cfg, 3.0, 5) == 22.0                # Saturday 3:00 — still Friday's period
    assert hc.room_target_now(cfg, 3.0, 4) == 25.0                # Friday 3:00 belongs to Thursday
    assert hc.room_target_now(cfg, 23.0, 5) == 25.0


def test_the_lowest_target_wins_and_never_above_the_day():
    cfg = _cfg(room_target_c=25, night_enabled=True, room_night_c=21, night_from=22, night_to=5,
               setbacks=[_away(temp_c=23), _away(**{"from": 12, "to": 14, "temp_c": 20}),
                         _away(**{"from": 0, "to": 23, "temp_c": 27})])
    assert hc.target_phase(cfg, 8.0, 0)[:2] == (23.0, "setback")
    t, phase, sb = hc.target_phase(cfg, 12.5, 0)
    assert (t, phase, sb["index"]) == (20.0, "setback", 1)
    assert hc.target_phase(cfg, 23.0, 0)[:2] == (21.0, "night")   # the 27 °C one is no setback
    assert hc.target_phase(cfg, 16.0, 0)[:2] == (25.0, "day")
    night_low = _cfg(room_target_c=25, night_enabled=True, room_night_c=19, night_from=22, night_to=8,
                     setbacks=[_away()])
    assert hc.target_phase(night_low, 7.5, 0)[:2] == (19.0, "night")


# ── the controller ───────────────────────────────────────────────────────────

def _inp(hour, weekday=0, room=24.0):
    return hc.ControlInput(now=1000.0 + hour * 3600, local_hour=hour % 24, weekday=weekday, t_supply=45.0,
                           t_top=70.0, t_boiler=40.0, t_room=room, t_out=0.0, pump_on=True, pump_configured=True)


def test_controller_logs_the_setback_and_lowers_the_curve():
    c = hc.HeatingController(_cfg(room_target_c=25, setbacks=[_away()]))
    morning = c.step(_inp(6.5))
    assert morning.setback is None and morning.room_target == 25.0
    away = c.step(_inp(7.5))
    assert away.room_target == 22.0 and not away.night
    assert away.setback == {"index": 0, "from": 7, "to": 15, "temp_c": 22.0}
    assert any("Obniżenie 7–15" in line and "22.0" in line for line in away.log)
    assert away.curve_c < morning.curve_c
    assert away.heating_needed is False                          # 24 °C in the house ≥ 22 + 1: no heating
    back = c.step(_inp(15.5))
    assert back.setback is None and back.room_target == 25.0
    assert any("Dzień" in line and "25.0" in line for line in back.log)


def test_advisor_schedule_sees_the_weekday():
    cfg = _cfg(room_target_c=25, setbacks=[_away()])
    # what heating_ha._target_at feeds the advisor: local hour and weekday of each forecast moment
    assert hc.room_target_now(cfg, 10.0, 2) == 22.0
    assert hc.room_target_now(cfg, 10.0, 6) == 25.0


# ── wood: the fire's energy balance ──────────────────────────────────────────

def test_session_heat_adds_the_house_to_what_the_tanks_gained():
    s = {"stored_start_kwh": 10.0, "stored_max_kwh": 60.0, "stored_kwh": 50.0, "house_kwh": 120.0}
    assert hm.session_heat(s, 40.0) == 150.0                     # 40 − 10 + 120
    assert hm.session_heat({**s, "house_kwh": 5.0}, 12.0) == 50.0  # never below what the tanks took at the peak
    assert hm.session_heat({**s, "house_kwh": None}, 40.0) is None
    assert hm.session_heat(s, None) is None


CFG = {"enabled": True,
       "entities": {"boiler_out": "sensor.b", "buffer_top": "sensor.t", "buffer_bottom": "sensor.d"}}


def _fire(model, t0, minutes, boiler, top, bottom, house_kw=None, step=10):
    t = t0
    while t < t0 + minutes * 60:
        model.update({"boiler_out": f"{boiler(t - t0):.2f}", "buffer_top": f"{top(t - t0):.2f}",
                      "buffer_bottom": f"{bottom(t - t0):.2f}"}, now=t)
        if house_kw is not None and model.session is not None:
            model.session["house_kwh"] = float(model.session.get("house_kwh") or 0.0) + house_kw * step / 3600.0
        t += step
    return t


def test_a_fire_books_the_heat_the_house_took_in_the_wood():
    model = hm.HeatingModel(CFG)
    # a 4 h fire that only keeps the buffer where it was — the house takes all the heat (8 kW)
    t = _fire(model, 0, 30, lambda m: 30.0, lambda m: 60.0, lambda m: 50.0)
    boiler = lambda m: 30.0 + min(m / 60.0, 1.0) * 55.0 if m < 4 * 3600 else max(85.0 - (m - 4 * 3600) / 60.0, 30.0)  # noqa: E731
    _fire(model, t, 6 * 60, boiler, lambda m: 60.0, lambda m: 50.0, house_kw=8.0)
    assert len(model.sessions) == 1
    s = model.sessions[0]
    assert s["stored_kwh"] == pytest.approx(0.0, abs=0.5)        # the tanks hold what they held …
    assert s["house_kwh"] == pytest.approx(8.0 * s["duration_h"], rel=0.02)
    assert s["heat_kwh"] == pytest.approx(s["house_kwh"], abs=0.5)  # … all the heat went to the house
    per_kg = model.cfg["wood_kwh_per_kg"] * model.cfg["efficiency"]
    assert s["wood_est_kg"] == pytest.approx(s["heat_kwh"] / per_kg, abs=0.1)
    assert s["wood_est_kg"] > 8


def test_without_the_house_share_the_wood_is_what_the_tanks_took():
    model = hm.HeatingModel(CFG)
    t = _fire(model, 0, 30, lambda m: 30.0, lambda m: 50.0, lambda m: 40.0)
    boiler = lambda m: 30.0 + min(m / 60.0, 1.0) * 55.0 if m < 3 * 3600 else max(85.0 - (m - 3 * 3600) / 60.0, 30.0)  # noqa: E731
    top = lambda m: 50.0 + min(m / (3 * 3600), 1.0) * 35.0       # noqa: E731
    bottom = lambda m: 40.0 + min(m / (3 * 3600), 1.0) * 35.0    # noqa: E731
    _fire(model, t, 5 * 60, boiler, top, bottom)
    s = model.sessions[0]
    assert s["heat_kwh"] is None and s["stored_kwh"] > 20
    per_kg = model.cfg["wood_kwh_per_kg"] * model.cfg["efficiency"]
    assert s["wood_est_kg"] == pytest.approx(s["stored_kwh"] / per_kg, abs=0.1)


def test_the_open_fire_shows_its_heat_so_far():
    model = hm.HeatingModel(CFG)
    t = _fire(model, 0, 30, lambda m: 30.0, lambda m: 60.0, lambda m: 50.0)
    _fire(model, t, 90, lambda m: 30.0 + min(m / 60.0, 1.0) * 55.0, lambda m: 60.0, lambda m: 50.0, house_kw=6.0)
    st = model.status(t + 90 * 60)
    assert st["session"] is not None
    assert st["session"]["heat_kwh"] == pytest.approx(st["session"]["house_kwh"], abs=0.5)


def test_month_sums_the_fires_heat():
    sessions = [{"start": 0.0, "duration_h": 20, "stored_kwh": 50.0, "heat_kwh": 160.0, "wood_est_kg": 50.0},
                {"start": 3600.0, "duration_h": 3, "stored_kwh": 30.0, "wood_est_kg": 9.4}]   # booked before v1.100
    m = hm.monthly_summary(sessions)[0]
    assert m["stored_kwh"] == 80 and m["heat_kwh"] == 190 and m["wood_kg"] == 59


# ── the house's draw before the hourly model is fitted ───────────────────────

def test_house_draw_from_the_buffer_discharge():
    b = ha.BuildingModel()
    assert b.house_kw(25.0, 0.0) is None
    b.observe_draw(6.5, 25.0, 3600)                               # 6.5 kW at 25 K → 0.26 kW/K
    assert b.house_kw(25.0, 0.0) is None                          # not yet two hours of it
    b.observe_draw(6.5, 25.0, 3600)
    assert b.house_kw(25.0, 5.0) == pytest.approx(0.26 * 20, rel=0.01)
    b.observe_draw(-3.0, 25.0, 3600)                              # a buffer gaining heat is no draw
    b.observe_draw(9.0, 2.0, 3600)                                # too mild to tell
    assert b.draw_obs_s == 7200
    restored = ha.BuildingModel()
    restored.restore(b.dump())
    assert restored.house_kw(25.0, 5.0) == pytest.approx(b.house_kw(25.0, 5.0))
    b.ua, b.base = 0.3, 0.5                                       # the fitted model takes over
    assert b.house_kw(25.0, 5.0) == pytest.approx(0.3 * 20 + 0.5)


def test_learned_heat_per_kg_uses_the_balance():
    sessions = [{"wood_kg": 40.0, "stored_kwh": 50.0, "house_kwh": 110.0, "heat_kwh": 128.0},
                {"wood_kg": 30.0, "stored_kwh": 40.0, "house_kwh": 60.0, "heat_kwh": 96.0}]
    assert ha.learned_kwh_per_kg(sessions) == pytest.approx(3.2)
