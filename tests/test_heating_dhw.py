"""Hot-water charging pump: differential control, hysteresis, reverse flow, hot boiler, modes.

Run: python3 -m pytest tests/  (no Home Assistant needed)
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
hc = importlib.import_module("smartinghome_pure.heating_control")


def _cfg(**kw):
    return hm.normalize({"enabled": True, "mode": "auto", "dhw_target_c": 55, **kw})


def _inp(now, **kw):
    base = dict(now=now, local_hour=12.0, t_supply=50.0, t_top=70.0, t_boiler=40.0, t_room=21.0, t_out=0.0,
                pump_on=True, pump_configured=True, valve_configured=False,
                t_dhw=48.0, dhw_pump_on=False, dhw_pump_configured=True)
    base.update(kw)
    return hc.ControlInput(**base)


# ── configuration ────────────────────────────────────────────────────────────

def test_normalize_dhw_settings():
    c = hm.normalize({})
    assert (c["dhw_source"], c["dhw_on_diff_k"], c["dhw_off_diff_k"], c["dhw_hyst_k"]) == ("buffer_top", 5.0, 2.0, 5.0)
    assert c["dhw_dump"] is False and c["dhw_max_c"] == 80.0
    assert {"dhw_pump_switch", "dhw_pump_power"} <= set(c["entities"])
    odd = hm.normalize({"dhw_source": "nope", "dhw_on_diff_k": 4, "dhw_off_diff_k": 9, "dhw_target_c": 60,
                        "dhw_max_c": 50})
    assert odd["dhw_source"] == "buffer_top"
    assert odd["dhw_off_diff_k"] == 3.0                     # always below the start difference
    assert odd["dhw_max_c"] == 60.0                         # never below the target
    assert hm.to_settings({"dhw_source": "boiler_out"})["dhw_source"] == "boiler_out"


# ── charging ─────────────────────────────────────────────────────────────────

def test_charges_from_buffer_and_stops_at_target():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(100.0, t_dhw=48.0))
    assert out.dhw.want and out.dhw_pump == ("on", "ładowanie CWU: bufor 70 °C, CWU 48 °C → 55 °C")
    ctrl.dhw_pump.note(True, 100.0)
    out = ctrl.step(_inp(700.0, t_dhw=54.0, dhw_pump_on=True))
    assert out.dhw.want and out.dhw_pump is None
    out = ctrl.step(_inp(800.0, t_dhw=55.0, dhw_pump_on=True))
    assert not out.dhw.want and out.dhw_pump == ("off", "CWU naładowana (55 °C)")


def test_waits_for_the_hysteresis_before_recharging():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(100.0, t_dhw=52.0))
    assert not out.dhw.want and out.dhw_pump is None and out.dhw.reason == "CWU 52 °C — ładowanie od 50 °C"
    out = ctrl.step(_inp(110.0, t_dhw=50.0))
    assert out.dhw_pump and out.dhw_pump[0] == "on"


def test_source_too_cold_to_start_but_charging_continues_down_to_off_diff():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(100.0, t_top=52.0, t_dhw=48.0))
    assert not out.dhw.want and out.dhw.reason == "bufor za chłodny (52 °C, potrzeba ≥ 53 °C)"
    # already charging: 3 K is still enough (off below 2 K)
    ctrl.dhw_charging = True
    assert ctrl.step(_inp(110.0, t_top=52.0, t_dhw=49.0, dhw_pump_on=True)).dhw.want
    out = ctrl.step(_inp(120.0, t_top=51.0, t_dhw=49.5, dhw_pump_on=True))
    assert not out.dhw.want and not out.dhw.urgent


def test_reverse_flow_stops_at_once_despite_minimum_run_time():
    ctrl = hc.HeatingController(_cfg())
    ctrl.dhw_pump.note(True, 100.0)                         # just switched on
    out = ctrl.step(_inp(130.0, t_top=45.0, t_dhw=48.0, dhw_pump_on=True))
    assert out.dhw.urgent and out.dhw_pump == ("off", "bufor (45 °C) chłodniejszy niż CWU (48 °C)")
    # a small difference only waits for the minimum run time
    ctrl2 = hc.HeatingController(_cfg())
    ctrl2.dhw_pump.note(True, 100.0)
    ctrl2.dhw_charging = True
    assert ctrl2.step(_inp(130.0, t_top=49.0, t_dhw=48.0, dhw_pump_on=True)).dhw_pump is None


def test_missing_probe_stops_the_pump():
    ctrl = hc.HeatingController(_cfg())
    ctrl.dhw_pump.note(True, 100.0)
    out = ctrl.step(_inp(130.0, t_dhw=None, dhw_pump_on=True))
    assert out.dhw_pump == ("off", "brak odczytu sondy CWU")
    out = hc.HeatingController(_cfg()).step(_inp(130.0, t_top=None, dhw_pump_on=True))
    assert out.dhw_pump == ("off", "brak odczytu sondy bufora")


def test_boiler_as_the_source():
    ctrl = hc.HeatingController(_cfg(dhw_source="boiler_out"))
    out = ctrl.step(_inp(100.0, t_boiler=75.0, t_top=40.0, t_dhw=45.0))
    assert out.dhw.source_c == 75.0 and out.dhw_pump == ("on", "ładowanie CWU: kocioł 75 °C, CWU 45 °C → 55 °C")


def test_summer_charges_too():
    ctrl = hc.HeatingController(_cfg(mode="summer"))
    out = ctrl.step(_inp(100.0, t_dhw=45.0, pump_on=False))
    assert out.dhw_pump and out.dhw_pump[0] == "on"


# ── modes and the hot boiler ─────────────────────────────────────────────────

def test_off_mode_never_switches_the_pump():
    ctrl = hc.HeatingController(_cfg(mode="off"))
    out = ctrl.step(_inp(100.0, t_dhw=40.0, t_boiler=94.0, t_top=90.0, overheat=True))
    assert out.dhw.want and out.dhw_pump is None


def test_manual_mode_leaves_the_pump_to_the_user():
    ctrl = hc.HeatingController(_cfg(mode="manual"))
    assert ctrl.step(_inp(100.0, t_dhw=40.0)).dhw_pump is None
    ctrl.dhw_pump.note(True, 100.0)
    assert ctrl.step(_inp(130.0, t_top=40.0, t_dhw=48.0, dhw_pump_on=True)).dhw_pump is None


def test_hot_boiler_tops_up_to_the_target_in_every_mode_but_off():
    for mode in ("manual", "auto", "summer"):
        ctrl = hc.HeatingController(_cfg(mode=mode))
        ctrl.dhw_pump.note(False, 90.0)                     # min-off running: safety ignores it
        out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=86.0, overheat=True, t_dhw=52.0))
        assert out.dhw.force and out.dhw_pump == ("on", "kocioł gorący — CWU dogrzewana do zadanej"), mode
    # at the target, without dhw_dump: no more
    out = hc.HeatingController(_cfg()).step(_inp(100.0, t_boiler=93.0, t_top=86.0, overheat=True, t_dhw=55.0))
    assert not out.dhw.force and out.dhw_pump is None


def test_dhw_dump_up_to_the_cap_only():
    ctrl = hc.HeatingController(_cfg(dhw_dump=True, dhw_max_c=75))
    out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=86.0, overheat=True, t_dhw=60.0))
    assert out.dhw.force and out.dhw.dump and out.dhw_pump == ("on", "zrzut ciepła do CWU — kocioł gorący")
    ctrl.dhw_pump.note(True, 100.0)
    out = ctrl.step(_inp(150.0, t_boiler=93.0, t_top=86.0, overheat=True, t_dhw=75.0, dhw_pump_on=True))
    assert not out.dhw.force and out.dhw.urgent and out.dhw_pump == ("off", "CWU 75 °C — górny limit 75 °C")


def test_hot_needs_a_warmer_source():
    ctrl = hc.HeatingController(_cfg(dhw_source="buffer_top"))
    out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=53.0, overheat=True, t_dhw=52.0))
    assert not out.dhw.force                                 # 1 K: the coil would hardly take anything


# ── weekly run, persistence ──────────────────────────────────────────────────

def test_weekly_run_counts_from_the_first_reading():
    ctrl = hc.HeatingController(_cfg())
    now = 1_000_000.0
    assert ctrl.step(_inp(now, t_dhw=56.0)).dhw_pump is None           # no run right after enabling
    now += hc.EXERCISE_EVERY_S + 10
    out = ctrl.step(_inp(now, t_dhw=56.0))                             # tank charged: nothing to do but the run
    assert out.dhw_pump == ("on", "przepłukanie pompy CWU (raz w tygodniu)")
    ctrl.dhw_pump.note(True, now)
    assert ctrl.step(_inp(now + 10, t_dhw=56.0, dhw_pump_on=True)).dhw_pump is None
    out = ctrl.step(_inp(now + hc.DHW_EXERCISE_S + 1, t_dhw=56.0, dhw_pump_on=True))
    assert out.dhw_pump == ("off", "koniec przepłukania pompy CWU")


def test_weekly_run_turns_into_charging_when_there_is_heat():
    ctrl = hc.HeatingController(_cfg())
    now = 1_000_000.0
    ctrl.dhw_pump.last_on = now - hc.EXERCISE_EVERY_S - 10
    assert ctrl.step(_inp(now, t_dhw=54.0)).dhw_pump[0] == "on"
    ctrl.dhw_pump.note(True, now)
    out = ctrl.step(_inp(now + hc.DHW_EXERCISE_S + 1, t_dhw=54.0, dhw_pump_on=True))
    assert out.dhw.want and out.dhw_pump is None                      # buffer 70 °C, tank below target: keep going


def test_state_roundtrip():
    ctrl = hc.HeatingController(_cfg())
    ctrl.dhw_pump.note(True, 500.0)
    fresh = hc.HeatingController(_cfg())
    fresh.restore(ctrl.dump_state())
    assert fresh.dhw_pump.last_on == 500.0 and fresh.dhw_pump.last_change == 500.0


def test_unconfigured_pump_is_never_commanded():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, t_dhw=40.0, dhw_pump_configured=False))
    assert out.dhw.want and out.dhw_pump is None


# ── model: the pump's power ──────────────────────────────────────────────────

def test_dhw_pump_fault_alarm_and_status():
    cfg = {"enabled": True, "entities": {"boiler_out": "sensor.k", "buffer_top": "sensor.g",
                                         "dhw_pump_switch": "switch.cwu", "dhw_pump_power": "sensor.cwu_w"}}
    model = hm.HeatingModel(cfg)
    raw = {"boiler_out": "30", "buffer_top": "60", "dhw_pump_switch": "on", "dhw_pump_power": "0.2"}
    for t in range(0, 100, 10):
        model.update(raw, t)
        model.evaluate_alarms(t)
    assert not model.active_alarms()
    for t in range(100, 170, 10):
        model.update(raw, t)
        model.evaluate_alarms(t)
    assert [a.id for a in model.active_alarms()] == ["heating_dhw_pump_fault"]
    assert model.status(170)["dhw_pump"] == {"on": True, "power_w": 0.2, "off_min": None}
    model.update({**raw, "dhw_pump_power": "28"}, 180)
    model.evaluate_alarms(180)
    assert not model.active_alarms()
