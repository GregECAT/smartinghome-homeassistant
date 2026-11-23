"""Flue-gas sensor: its own probe filter, and the fire starting / ending by the flue (the water as the fallback).

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hm = importlib.import_module("smartinghome_pure.heating_model")

ENTS = {"boiler_out": "sensor.b", "buffer_top": "sensor.t", "buffer_bottom": "sensor.d"}
CFG = {"enabled": True, "entities": {**ENTS, "flue_gas": "sensor.flue"}}
CFG_WATER = {"enabled": True, "entities": ENTS}


def _run(model, t0, seconds, fn, step=10):
    """fn(t) → (boiler water, flue or None); returns [(t, state)] and the end time."""
    out, t = [], t0
    while t < t0 + seconds:
        water, flue = fn(t)
        raw = {"boiler_out": f"{water:.2f}", "buffer_top": "60", "buffer_bottom": "45"}
        if flue is not None:
            raw["flue_gas"] = f"{flue:.1f}"
        model.update(raw, now=t)
        model.evaluate_alarms(t)
        out.append((t, model.state))
        t += step
    return out, t


def _first(states, wanted):
    return next((t for t, s in states if s in wanted), None)


# ── probe filter ─────────────────────────────────────────────────────────────

def test_flue_probe_reads_far_beyond_a_ds18b20():
    p = hm.probe_for("flue_gas")
    p.update("85.0", 0)                  # a real flue temperature, not a DS18B20 reset value
    assert p.status == "ok" and p.value == 85.0
    p.update("140", 10)                  # +55 K between two samples: ignition, not a glitch
    assert p.value == 140.0
    p.update("420", 20)
    p.update("430", 30)
    p.update("445", 40)
    assert p.status == "ok" and p.value == 445.0
    p.update("650", 50)                  # beyond any flue: a broken thermocouple / transmitter
    assert p.status == "invalid" and p.value == 445.0
    ds = hm.probe_for("boiler_out")
    ds.update("130", 0)
    assert ds.status == "invalid"
    ds.update("60", 10)
    ds.update("85.0", 20)
    assert ds.status == "invalid" and ds.value == 60.0


def test_flue_role_is_a_temperature_with_an_offset():
    assert hm.ROLES["flue_gas"] == ("Spaliny", "temperature")
    assert "flue_gas" in hm.TEMP_ROLES and "flue_gas" in hm.PROBE_ROLES
    c = hm.normalize({"entities": {"flue_gas": " sensor.spaliny "}, "offsets": {"flue_gas": 4}})
    assert c["entities"]["flue_gas"] == "sensor.spaliny" and c["offsets"]["flue_gas"] == 4.0


# ── the fire by the flue ─────────────────────────────────────────────────────

def _lighting(t):
    """Lit at minute 10: the flue is hot within minutes, the water stays cold for half an hour."""
    m = t / 60.0
    flue = 25.0 if m < 10 else min(25.0 + (m - 10) * 25.0, 210.0)
    water = 30.0 if m < 40 else min(30.0 + (m - 40) * 1.5, 84.0)
    return water, flue


def test_fire_starts_by_the_flue_long_before_the_water():
    flue_model = hm.HeatingModel(CFG)
    states, _ = _run(flue_model, 0, 90 * 60, _lighting)
    t_flue = _first(states, hm.FIRE_STATES)
    assert t_flue is not None and 10 * 60 < t_flue <= 16 * 60
    assert dict(states)[t_flue] == "ignition"           # the water is still cold
    assert flue_model.state == "burning" and flue_model.session is not None
    assert flue_model.session["flue_max_c"] >= 200
    water_model = hm.HeatingModel(CFG_WATER)
    states_w, _ = _run(water_model, 0, 90 * 60, lambda t: (_lighting(t)[0], None))
    assert _first(states_w, hm.FIRE_STATES) >= 40 * 60   # the water alone sees it half an hour later


def test_water_warming_without_a_hot_flue_is_not_a_fire():
    # the boiler's water warms slowly (from the buffer, the sun on the pipe …) — the water alone calls that ignition
    fn = lambda t: (30.0 + min(t / 60.0, 40.0) * 0.5, 24.0)    # noqa: E731
    model = hm.HeatingModel(CFG)
    states, _ = _run(model, 0, 60 * 60, fn)
    assert {s for _, s in states} <= {"cold", "idle"} and model.session is None
    water_model = hm.HeatingModel(CFG_WATER)
    states_w, _ = _run(water_model, 0, 60 * 60, lambda t: (fn(t)[0], None))
    assert "ignition" in {s for _, s in states_w}


def test_warm_idle_boiler_with_a_cold_flue_is_not_a_fire():
    model = hm.HeatingModel(CFG)
    _run(model, 0, 3600, lambda t: (80.0, 60.0))
    assert model.state == "idle" and model.session is None


def _burn_out_hot_water(t):
    """Burning, then the wood is gone: the flue falls from 200 °C to the room, the insulated boiler stays ~70 °C."""
    m = t / 60.0
    if m < 20:
        flue = 25.0 + m * 10.0
    elif m < 120:
        flue = 200.0
    else:
        flue = max(200.0 - (m - 120) * 2.0, 45.0)        # below 80 °C at minute 180
    water = 30.0 + m * 2.0 if m < 25 else 80.0 if m < 140 else max(80.0 - (m - 140) * 0.1, 70.0)
    return water, flue


def test_fire_ends_by_the_flue_while_the_water_stays_hot():
    model = hm.HeatingModel(CFG)
    states, _ = _run(model, 0, 5 * 3600, _burn_out_hot_water)
    seq = [s for i, (_, s) in enumerate(states) if i == 0 or states[i - 1][1] != s]
    assert seq[0] == "cold" and "burning" in seq and "burnout" in seq
    assert len(model.sessions) == 1 and model.session is None
    end = model.sessions[0]["end"]
    # flue below 80 °C at minute 180, then FLUE_END_HOLD_S
    assert 180 * 60 + hm.FLUE_END_HOLD_S <= end <= 180 * 60 + hm.FLUE_END_HOLD_S + 120
    assert model.state == "idle"                          # water ~70 °C: warm, not a fire
    water_model = hm.HeatingModel(CFG_WATER)
    _run(water_model, 0, 5 * 3600, lambda t: (_burn_out_hot_water(t)[0], None))
    assert water_model.session is not None                # the water alone keeps the fire open for hours


def test_a_short_flue_dip_does_not_end_the_fire():
    # the exhaust fan paused at the boiler thermostat for 10 min: the flue dips below 80 °C, the fire goes on
    def fn(t):
        m = t / 60.0
        if m < 20:
            return 30.0 + m * 3.0, 25.0 + m * 10.0
        if 60 <= m < 70:
            return 88.0, 70.0
        return 88.0, 190.0
    model = hm.HeatingModel(CFG)
    states, _ = _run(model, 0, 100 * 60, fn)
    assert model.session is not None and not model.sessions
    assert all(s in hm.FIRE_STATES for t, s in states if t >= 20 * 60)


def test_reload_in_burnout_burns_again():
    def fn(t):
        m = t / 60.0
        if m < 20:
            return 30.0 + m * 3.0, 25.0 + m * 10.0
        if m < 60:
            return 88.0, 200.0
        if m < 90:                                          # burning out
            return 88.0 - (m - 60) * 0.5, 200.0 - (m - 60) * 3.5
        return 74.0, min(95.0 + (m - 90) * 15.0, 220.0)     # a new load catches
    model = hm.HeatingModel(CFG)
    states, _ = _run(model, 0, 110 * 60, fn)
    assert dict(states)[89 * 60] == "burnout"
    assert model.state == "burning"
    assert len(model.sessions) == 0                         # one fire, reloaded


def test_flue_sensor_stuck_low_falls_back_to_the_water():
    # the flue reads, but wrongly low: a clear water-side fire still opens a session, and the water closes it
    def fn(t):
        m = t / 60.0
        if m < 40:
            return 30.0 + m * 1.5, 22.0
        if m < 150:
            return 90.0 + (1.0 if int(m) % 20 < 10 else -1.0), 22.0
        return max(90.0 - (m - 150) * 0.4, 35.0), 22.0
    model = hm.HeatingModel(CFG)
    states, _ = _run(model, 0, 6 * 3600, fn)
    assert "burning" in {s for _, s in states}
    assert len(model.sessions) == 1 and model.state in ("idle", "cold")
    assert model.sessions[0]["flue_max_c"] < hm.FLUE_FIRE_C


def test_lost_flue_reading_keeps_the_water_logic():
    model = hm.HeatingModel(CFG)
    _, t = _run(model, 0, 40 * 60, _lighting)
    assert model.state in hm.FIRE_STATES
    _run(model, t, 30 * 60, lambda t: (84.0, None))         # flue sensor unassigned / gone
    assert model.state == "burning" and model.session is not None


def test_status_reports_the_flue():
    model = hm.HeatingModel(CFG)
    _, t = _run(model, 0, 15 * 60, _lighting)
    b = model.status(t)["boiler"]
    assert b["flue_c"] > hm.FLUE_FIRE_C and b["flue_trend_k_min"] > 10
    assert b["firing"] is True
    plain = hm.HeatingModel(CFG_WATER)
    _run(plain, 0, 60, lambda t: (30.0, None))
    pb = plain.status(60)["boiler"]
    assert pb["flue_c"] is None and pb["flue_trend_k_min"] is None
