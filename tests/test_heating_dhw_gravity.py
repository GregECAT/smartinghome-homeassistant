"""Gravity flow into the hot-water coil: the coil's supply pipe at the tank stays warm with the pump stopped.

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

ENTS = {"boiler_out": "sensor.b", "buffer_top": "sensor.t", "buffer_bottom": "sensor.d", "dhw": "sensor.cwu",
        "dhw_coil_supply": "sensor.coil", "dhw_pump_switch": "switch.cwu"}
GID = "heating_dhw_gravity"


def _model(**ents):
    return hm.HeatingModel({"enabled": True, "entities": {**ENTS, **ents}})


def _raw(top=80.0, dhw=50.0, coil=62.0, pump="off"):
    raw = {"boiler_out": "40", "buffer_top": f"{top}", "buffer_bottom": "60", "dhw_pump_switch": pump}
    if dhw is not None:
        raw["dhw"] = f"{dhw}"
    if coil is not None:
        raw["dhw_coil_supply"] = f"{coil}"
    return raw


def _run(model, t0, seconds, raw, step=60):
    t = t0
    while t < t0 + seconds:
        model.update(raw(t) if callable(raw) else raw, now=t)
        model.evaluate_alarms(t)
        t += step
    return t


def _ids(model):
    return [a.id for a in model.active_alarms()]


def test_role_is_a_temperature_probe():
    assert hm.ROLES["dhw_coil_supply"][1] == "temperature"
    assert "dhw_coil_supply" in hm.TEMP_ROLES and "dhw_coil_supply" in hm.PROBE_ROLES


def test_warm_coil_with_the_pump_off_for_an_hour_raises_the_alarm_after_the_hold():
    model = _model()
    t = _run(model, 0, hm.DHW_GRAVITY_OFF_S, _raw())           # pump off for the first hour: nothing yet
    assert GID not in _ids(model)
    assert model.status(t)["dhw_coil"]["suspected"] is False
    t = _run(model, t, 10 * 60, _raw())                         # condition holds, still within the hold
    assert GID not in _ids(model) and model.status(t)["dhw_coil"]["suspected"] is True
    t = _run(model, t, hm.DHW_GRAVITY_HOLD_S, _raw())
    assert GID in _ids(model)
    alarm = next(a for a in model.active_alarms() if a.id == GID)
    assert alarm.level == "warning"
    assert "62 °C" in alarm.message and "góra bufora 80 °C" in alarm.message and "zasobnik 50 °C" in alarm.message
    assert "zawór zwrotny" in alarm.action and "syfon termiczny" in alarm.action
    st = model.status(t)
    assert st["dhw_coil"] == {"temp_c": 62.0, "gravity": True, "suspected": True}
    assert st["dhw_pump"]["off_min"] >= 90


def test_coil_that_cooled_down_is_no_gravity():
    model = _model()
    _run(model, 0, 3 * 3600, _raw(coil=24.0))
    assert GID not in _ids(model)


def test_conduction_from_a_hot_tank_is_no_gravity():
    # the pipe is warmed by the tank it enters, but never above it
    model = _model()
    _run(model, 0, 3 * 3600, _raw(dhw=55.0, coil=45.0))
    assert GID not in _ids(model)


def test_cold_buffer_drives_no_gravity_flow():
    model = _model()
    _run(model, 0, 3 * 3600, _raw(top=45.0, dhw=30.0, coil=38.0))
    assert GID not in _ids(model)


def test_no_alarm_during_a_heat_dump():
    model = _model()
    model.dump_active = True
    _run(model, 0, 3 * 3600, _raw())
    assert GID not in _ids(model)
    model.dump_active = False
    _run(model, 3 * 3600, hm.DHW_GRAVITY_HOLD_S + 60, _raw())
    assert GID in _ids(model)


def test_without_a_tank_probe_the_coil_is_compared_with_the_buffer():
    near = _model()
    _run(near, 0, 3 * 3600, _raw(dhw=None, coil=70.0))         # within 15 K of the buffer top
    assert GID in _ids(near)
    alarm = next(a for a in near.active_alarms() if a.id == GID)
    assert ", zasobnik" not in alarm.message
    far = _model()
    _run(far, 0, 3 * 3600, _raw(dhw=None, coil=45.0))          # warm, but far below the buffer
    assert GID not in _ids(far)


def test_buffer_bottom_stands_in_for_a_missing_top():
    model = _model(buffer_top="")
    raw = {**_raw(dhw=None, coil=58.0), "buffer_bottom": "66"}
    raw.pop("buffer_top")
    _run(model, 0, 3 * 3600, raw)
    assert GID in _ids(model)
    assert "dół bufora 66 °C" in next(a for a in model.active_alarms() if a.id == GID).message


def test_pump_run_restarts_the_clock_and_a_wifi_drop_does_not():
    model = _model()
    t = _run(model, 0, 50 * 60, _raw())
    t = _run(model, t, 5 * 60, _raw(pump="unavailable"))       # the relay off Wi-Fi: still off, clock runs
    assert model.dhw_pump_off_s(t) >= 54 * 60
    t = _run(model, t, 60, _raw(pump="on"))                    # it ran: the pipe carried water, start again
    assert model.dhw_pump_off_s(t) is None
    t = _run(model, t, 60 * 60, _raw())
    assert GID not in _ids(model)
    t = _run(model, t, hm.DHW_GRAVITY_HOLD_S + 2 * 60, _raw())
    assert GID in _ids(model)


def test_alarm_clears_with_hysteresis_and_when_the_pump_starts():
    model = _model()
    t = _run(model, 0, 2 * 3600, _raw())
    assert GID in _ids(model)
    t = _run(model, t, 10 * 60, _raw(coil=52.0))               # tank + 3 − 2 K hysteresis = 51 — still warm
    assert GID in _ids(model)
    t = _run(model, t, 60, _raw(coil=49.0))
    assert GID not in _ids(model)
    t = _run(model, t, 3 * 3600, _raw())
    assert GID in _ids(model)
    _run(model, t, 60, _raw(pump="on"))
    assert GID not in _ids(model)


def test_no_pump_relay_no_alarm():
    model = _model(dhw_pump_switch="")
    raw = _raw()
    raw.pop("dhw_pump_switch")
    _run(model, 0, 3 * 3600, raw)
    assert GID not in _ids(model) and model.status(3 * 3600)["dhw_pump"]["off_min"] is None


def test_source_follows_dhw_source_boiler():
    model = hm.HeatingModel({"enabled": True, "dhw_source": "boiler_out", "entities": ENTS})
    raw = {**_raw(top=40.0, dhw=50.0, coil=66.0), "boiler_out": "78"}
    _run(model, 0, 3 * 3600, raw)
    assert GID in _ids(model)
    assert "kocioł 78 °C" in next(a for a in model.active_alarms() if a.id == GID).message
