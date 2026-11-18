"""Wood boiler + heat buffer model: probe filter, tank energy, boiler state, sessions, alarms.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import math
import sys
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hm = importlib.import_module("smartinghome_pure.heating_model")

CFG = {
    "enabled": True,
    "entities": {"boiler_out": "sensor.b", "buffer_top": "sensor.t", "buffer_bottom": "sensor.d",
                 "dhw": "sensor.c", "room": "sensor.r"},
}


def _feed(model, t0, seconds, fn, step=10):
    """fn(t) → raw states dict; returns list of (t, state) and the end time."""
    states = []
    t = t0
    while t < t0 + seconds:
        model.update(fn(t), now=t)
        model.evaluate_alarms(t)
        states.append((t, model.state))
        t += step
    return states, t


# ── configuration ────────────────────────────────────────────────────────────

def test_normalize_defaults_and_clamps():
    c = hm.normalize({"buffer_l": "abc", "warn_c": 200, "offsets": {"buffer_top": 9}, "profile": "nope"})
    assert c["buffer_l"] == 1000.0
    assert c["profile"] == "atmos_dc"
    assert c["warn_c"] == 94.0 and c["alarm_c"] > c["warn_c"]
    assert c["offsets"]["buffer_top"] == 5.0
    assert c["buffer_min_c"] == 40.0 and c["supply_max_c"] == 75.0
    floor = hm.normalize({"emitters": "floor"})
    assert floor["buffer_min_c"] == 30.0 and floor["supply_max_c"] == 45.0
    assert set(c["entities"]) == set(hm.ROLES)


def test_probe_heights_too_close_fall_back():
    c = hm.normalize({"probe_top_h": 0.55, "probe_bottom_h": 0.5})
    assert (c["probe_top_h"], c["probe_bottom_h"]) == (0.85, 0.15)


# ── probe filter ─────────────────────────────────────────────────────────────

def test_probe_rejects_minus_127_and_reports_fault_after_hold():
    p = hm.Probe()
    p.update("50.0", 0)
    p.update("-127", 10)
    assert p.value == 50.0 and p.status == "invalid"
    assert p.bad_for(100) == 90
    p.update("51.0", 200)
    assert p.status == "ok" and p.bad_since is None and p.value == 51.0


def test_probe_85_is_reset_value_unless_it_fits_the_trend():
    p = hm.Probe()
    p.update("60.0", 0)
    p.update("85.0", 10)
    assert p.value == 60.0 and p.status == "invalid"
    q = hm.Probe()
    q.update("84.5", 0)
    q.update("85.0", 10)
    assert q.value == 85.0 and q.status == "ok"


def test_probe_single_jump_dropped_repeated_jump_accepted():
    p = hm.Probe()
    p.update("50", 0)
    p.update("70", 10)
    assert p.value == 50.0
    p.update("71", 20)
    p.update("72", 30)
    assert p.value == 72.0


def test_probe_offset_and_unavailable():
    p = hm.Probe()
    p.update("40", 0, offset=-0.5)
    assert p.value == 39.5
    p.update("unavailable", 10, offset=-0.5)
    assert p.status == "unavailable" and p.fault and p.value == 39.5
    p.update(None, 20)
    assert p.status == "missing" and not p.fault


# ── tanks ────────────────────────────────────────────────────────────────────

def test_buffer_full_and_empty():
    cfg = hm.normalize({})
    full = hm.buffer_state(cfg, 90.0, 90.0)
    assert full["energy_kwh"] == pytest.approx(57.0, abs=0.1)
    assert full["charge_pct"] == 100 and full["space_kwh"] == 0 and full["wood_max_kg"] == 0
    empty = hm.buffer_state(cfg, 40.0, 40.0)
    assert empty["energy_kwh"] == 0 and empty["charge_pct"] == 0
    assert empty["space_kwh"] == pytest.approx(57.0, abs=0.1)
    # 57 kWh / (4.0 kWh/kg × 0.8) ≈ 17.8 kg — the "empty buffer takes ~17 kg" of the plan
    assert empty["wood_max_kg"] == pytest.approx(17.8, abs=0.1)


def test_buffer_stratified_between_and_cold_layers_not_negative():
    cfg = hm.normalize({})
    s = hm.buffer_state(cfg, 80.0, 30.0)
    assert 0 < s["energy_kwh"] < 57.0
    assert s["stratification_k"] == 50.0
    # bottom layers below 40 °C give no useful heat, but count as space
    mixed = hm.buffer_state(cfg, 55.0, 55.0)
    assert s["space_kwh"] > mixed["space_kwh"] - 20


def test_buffer_one_probe_is_estimated():
    s = hm.buffer_state(hm.normalize({}), 70.0, None)
    assert s["estimated"] and s["stratification_k"] is None
    assert s["energy_kwh"] == pytest.approx(30 * 1.14, abs=0.1)


def test_dhw_energy():
    d = hm.dhw_state(hm.normalize({}), 55.0)
    assert d["charge_pct"] == 100
    assert d["energy_kwh"] == pytest.approx(45 * 1.14 * 0.5, abs=0.1)


# ── boiler state machine and sessions ────────────────────────────────────────

def _burn_profile(t):
    """One fire: cold → ignition → burning with thermostat wobble → burn-out → cooling."""
    m = t / 60.0
    if m < 10:
        boiler = 30.0
    elif m < 25:
        boiler = 30.0 + (m - 10) * 3.0
    elif m < 180:
        boiler = 84.0 + 2.5 * math.sin((m - 25) / 20.0 * 2 * math.pi)   # boiler thermostat pausing the fan
    elif m < 240:
        boiler = 84.0 - (m - 180) * 0.5
    else:
        boiler = max(54.0 - (m - 240) * 0.08, 40.0)
    charge = min(max((m - 25) / 155.0, 0.0), 1.0)
    return {"boiler_out": f"{boiler:.2f}", "buffer_top": f"{45 + 40 * charge:.2f}",
            "buffer_bottom": f"{35 + 35 * charge:.2f}", "dhw": "50", "room": "21"}


def test_burn_is_one_session_with_states_in_order():
    model = hm.HeatingModel(CFG)
    states, _ = _feed(model, 0, 6 * 3600, _burn_profile)
    seq = []
    for _, s in states:
        if not seq or seq[-1] != s:
            seq.append(s)
    assert seq[:2] == ["cold", "ignition"]
    assert "burning" in seq and "burnout" in seq
    assert seq[-1] in ("idle", "cold")
    # thermostat wobble (±2.5 K) during the burn does not end it
    burning_phase = [s for t, s in states if 40 * 60 <= t < 170 * 60]
    assert set(burning_phase) == {"burning"}
    assert len(model.sessions) == 1 and model.session is None
    s = model.sessions[0]
    assert 3.5 < s["duration_h"] < 4.5
    assert s["stored_kwh"] > 20 and s["wood_est_kg"] > 5
    assert s["max_c"] >= 86


def test_warm_idle_boiler_is_not_a_fire():
    model = hm.HeatingModel(CFG)
    _feed(model, 0, 3600, lambda t: {"boiler_out": "66.0", "buffer_top": "60", "buffer_bottom": "50"})
    assert model.state == "idle" and model.session is None


def test_restart_mid_burn_continues_session():
    model = hm.HeatingModel(CFG)
    _, t = _feed(model, 0, 60 * 60, _burn_profile)
    assert model.state == "burning"
    saved = model.dump()
    fresh = hm.HeatingModel(CFG)
    fresh.restore(saved)
    _feed(fresh, t, 5 * 3600, _burn_profile)
    assert len(fresh.sessions) == 1
    assert fresh.sessions[0]["start"] == saved["session"]["start"]


# ── buffer runtime ───────────────────────────────────────────────────────────

def test_runtime_from_discharge_rate():
    model = hm.HeatingModel(CFG)

    def cooling(t):
        temp = 80.0 - t / 3600.0 * 2.0    # whole tank −2 K/h ≈ 2.3 kW draw
        return {"boiler_out": "30", "buffer_top": f"{temp:.3f}", "buffer_bottom": f"{temp:.3f}"}

    _feed(model, 0, 2 * 3600, cooling, step=30)
    assert model.discharge_kw == pytest.approx(2.28, abs=0.15)
    expected = model.buffer["energy_kwh"] / model.discharge_kw
    assert model.runtime_h == pytest.approx(expected, rel=0.05)


# ── alarms ───────────────────────────────────────────────────────────────────

def test_overheat_hold_hysteresis_and_hot_hidden_under_it():
    model = hm.HeatingModel(CFG)
    raised_all, cleared_all = [], []

    def run(temp, seconds, t0):
        t = t0
        while t < t0 + seconds:
            model.update({"boiler_out": str(temp), "buffer_top": "70", "buffer_bottom": "50"}, now=t)
            r, c = model.evaluate_alarms(t)
            raised_all.extend(a.id for a in r)
            cleared_all.extend(a.id for a in c)
            t += 10
        return t

    t = run(86.0, 300, 0)
    t = run(93.0, 20, t)
    assert "heating_overheat" not in raised_all          # 30 s hold
    t = run(93.0, 60, t)
    assert "heating_overheat" in raised_all
    assert [a.id for a in model.active_alarms()] == ["heating_overheat"]
    t = run(91.0, 60, t)                                  # within 2 K hysteresis
    assert "heating_overheat" not in cleared_all
    t = run(89.0, 30, t)
    assert "heating_overheat" in cleared_all and "heating_hot" not in cleared_all
    assert [a.id for a in model.active_alarms()] == ["heating_hot"]
    run(84.0, 30, t)
    assert "heating_hot" in cleared_all


def test_probe_fault_alarm_critical_when_fire_is_on():
    model = hm.HeatingModel(CFG)
    _, t = _feed(model, 0, 60 * 60, _burn_profile)
    assert model.state == "burning"

    def broken(_t):
        raw = _burn_profile(_t)
        raw["boiler_out"] = "-127"
        return raw

    _feed(model, t, 150, broken)
    alarms = {a.id: a for a in model.active_alarms()}
    assert alarms["heating_probe_fault"].level == "critical"
    assert "Kocioł" in alarms["heating_probe_fault"].message


def test_no_flow_to_buffer():
    model = hm.HeatingModel(CFG)

    def stuck(t):
        m = t / 60.0
        boiler = 30.0 + m * 4.0 if m < 14 else 88.0
        return {"boiler_out": f"{boiler:.1f}", "buffer_top": "60", "buffer_bottom": "40"}

    _feed(model, 0, 30 * 60, stuck)
    assert "heating_no_flow" not in {a.id for a in model.active_alarms()}   # too early in the fire
    _feed(model, 30 * 60, 30 * 60, stuck)
    assert "heating_no_flow" in {a.id for a in model.active_alarms()}


def test_pump_fault_after_two_minutes():
    model = hm.HeatingModel({**CFG, "entities": {**CFG["entities"], "pump_switch": "switch.p",
                                                 "pump_power": "sensor.pw"}})
    raw = {"boiler_out": "30", "buffer_top": "60", "buffer_bottom": "40", "pump_switch": "on", "pump_power": "0.4"}
    _feed(model, 0, 100, lambda t: raw)
    assert not model.active_alarms()
    _feed(model, 100, 60, lambda t: raw)
    assert [a.id for a in model.active_alarms()] == ["heating_pump_fault"]
    _feed(model, 160, 20, lambda t: {**raw, "pump_power": "35"})
    assert not model.active_alarms()


def test_light_fire_hint_only_when_cold_and_buffer_empty():
    model = hm.HeatingModel(CFG)
    raw = {"boiler_out": "30", "buffer_top": "42", "buffer_bottom": "38", "room": "19.0"}
    _feed(model, 0, 1900, lambda t: raw)
    hint = [a for a in model.active_alarms() if a.id == "heating_light_fire"]
    assert hint and hint[0].level == "info" and "kg" in hint[0].action
    model2 = hm.HeatingModel(CFG)
    _feed(model2, 0, 1900, lambda t: {**raw, "buffer_top": "80", "buffer_bottom": "70"})
    assert not [a for a in model2.active_alarms() if a.id == "heating_light_fire"]


def test_status_shape():
    model = hm.HeatingModel(CFG)
    _feed(model, 0, 600, _burn_profile)
    st = model.status(600)
    assert st["boiler"]["state"] in hm.STATE_LABELS
    assert st["buffer"]["charge_pct"] is not None
    assert st["probes"]["buffer_top"]["status"] == "ok"
    assert st["probes"]["heating_supply"]["status"] == "missing"


def test_no_probe_fault_during_startup_grace():
    model = hm.HeatingModel(CFG)
    raw = {"boiler_out": "unavailable", "buffer_top": "60", "buffer_bottom": "40"}
    _feed(model, 0, 280, lambda t: raw)
    assert not model.active_alarms()          # integrations still starting after an HA restart
    _feed(model, 280, 60, lambda t: raw)
    assert [a.id for a in model.active_alarms()] == ["heating_probe_fault"]


def test_to_settings_keeps_profile_defaults_empty():
    stored = hm.to_settings({"enabled": True, "warn_c": "", "alarm_c": 90, "entities": {"buffer_top": " sensor.t "}})
    assert stored["warn_c"] is None and stored["alarm_c"] == 90.0
    assert stored["entities"]["buffer_top"] == "sensor.t"
    assert stored["supply_max_c"] is None and stored["safety_dump"] is None and stored["curve_slope"] is None
    assert "tzb_c" not in stored                      # derived from the profile, never stored
    norm = hm.normalize(stored)
    assert norm["warn_c"] == 88.0 and norm["supply_max_c"] == 75.0 and norm["safety_dump"] is True
    floor = hm.normalize({**stored, "emitters": "floor"})
    assert floor["supply_max_c"] == 45.0 and floor["safety_dump"] is False and floor["curve_slope"] == 0.5
