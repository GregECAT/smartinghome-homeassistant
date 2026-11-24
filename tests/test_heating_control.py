"""Heating control: curve, valve PI on a simulated plant, pump, modes, safety overrides.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from collections import deque
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hm = importlib.import_module("smartinghome_pure.heating_model")
hc = importlib.import_module("smartinghome_pure.heating_control")


def _cfg(**kw):
    return hm.normalize({"enabled": True, "mode": "auto", "valve_type": "cover", "valve_cover": "cover.v", **kw})


# ── curve / targets ──────────────────────────────────────────────────────────

def test_curve_and_limits():
    cfg = _cfg()
    assert hc.curve_target(cfg, -5.0, 21.0) == pytest.approx(21 + 1.4 * 26)
    assert hc.curve_target(cfg, -30.0, 21.0) == 75.0          # radiators max
    assert hc.curve_target(cfg, 20.0, 21.0) == 25.0           # min
    floor = _cfg(emitters="floor")
    assert hc.curve_target(floor, -20.0, 21.0) == 41.5
    assert hc.curve_target(_cfg(curve_shift=3), -5.0, 21.0) == pytest.approx(21 + 1.4 * 26 + 3)


def test_night_setback_wraps_midnight():
    cfg = _cfg(night_setback_k=2, night_from=22, night_to=5)
    assert hc.room_target_now(cfg, 23.5) == 19.0
    assert hc.room_target_now(cfg, 3.0) == 19.0
    assert hc.room_target_now(cfg, 12.0) == 21.0


# ── simulated plant ──────────────────────────────────────────────────────────

class Plant:
    """Buffer top → 3-way valve → radiators; transport delay and a probe in a pocket."""

    def __init__(self, t_top=70.0, t_room=21.0, pos=30.0, loss=0.0):
        self.t_top, self.t_room, self.loss = t_top, t_room, loss
        self.pos = pos                       # real valve position %
        self.t_ret = 35.0
        self.pipe = deque([35.0] * 20)       # 20 s transport delay
        self.t_meas = 35.0
        self.drive = None                    # (direction, until)

    def mix(self):
        return self.t_ret + (self.t_top - self.t_ret) * self.pos / 100.0

    def tick(self, now, dt=1.0, run_s=120.0):
        if self.drive and now < self.drive[1]:
            step = 100.0 / run_s * dt
            self.pos = min(self.pos + step, 100.0) if self.drive[0] == "open" else max(self.pos - step, 0.0)
        t_mix = self.mix()
        self.t_ret += (self.t_room + 0.55 * (t_mix - self.t_room) - self.t_ret) * dt / 600.0
        self.pipe.append(t_mix)
        delayed = self.pipe.popleft()
        self.t_meas += (delayed - self.loss - self.t_meas) * dt / 40.0


def _run(ctrl, plant, t0, seconds, t_out, pulses):
    t = t0
    errors = []
    while t < t0 + seconds:
        plant.tick(t)
        if int(t) % 10 == 0:
            out = ctrl.step(hc.ControlInput(now=t, local_hour=12.0, t_supply=round(plant.t_meas, 2),
                                            t_top=plant.t_top, t_boiler=40.0, t_out=t_out,
                                            valve_configured=True))
            if out.valve:
                ctrl.valve.apply(out.valve, t)
                plant.drive = (out.valve.direction, t + out.valve.seconds)
                pulses.append((t, out.valve))
            errors.append((t, plant.t_meas - out.supply_target))
        t += 1.0
    return t, errors


def test_valve_homes_then_holds_supply_without_oscillation():
    ctrl = hc.HeatingController(_cfg())
    plant = Plant()
    pulses = []
    t, errors = _run(ctrl, plant, 0.0, 3600.0, -5.0, pulses)
    assert pulses[0][1].kind == "home" and ctrl.valve.known
    late = [abs(e) for ts, e in errors if ts >= 1800]
    assert sum(late) / len(late) < 1.0
    assert max(late) < 2.5
    auto = [p for ts, p in pulses if p.kind == "auto" and ts >= 1800]
    assert len(auto) <= 15                     # settled: few corrections in the second half hour
    # the estimate tracks the real valve
    assert abs(ctrl.valve.position - plant.pos) < 10


def test_valve_follows_a_warmer_day():
    ctrl = hc.HeatingController(_cfg())
    plant = Plant()
    pulses = []
    t, _ = _run(ctrl, plant, 0.0, 2400.0, -5.0, pulses)
    t, errors = _run(ctrl, plant, t, 2400.0, 8.0, pulses)          # target 57.4 → 39.2 °C
    late = [abs(e) for ts, e in errors if ts >= t - 900]
    assert sum(late) / len(late) < 1.0


def test_valve_saturates_open_when_buffer_too_cold():
    cfg = _cfg()
    ctrl = hc.HeatingController(cfg)
    plant = Plant(t_top=40.0, loss=5.0)        # target top − 2 = 38 °C, the pipe loses 5 K: unreachable
    pulses = []
    _run(ctrl, plant, 0.0, 3600.0, -15.0, pulses)
    assert ctrl.valve.position == 100.0
    assert ctrl.valve.saturated == "open"
    assert ctrl.valve.run_last_hour(3600.0) <= hc.MAX_RUN_PER_H


def test_valve_holds_without_supply_probe():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known, ctrl.valve.position = True, 40.0
    out = ctrl.step(hc.ControlInput(now=1000.0, local_hour=12, t_supply=None, t_top=70, t_out=0,
                                    valve_configured=True))
    assert out.valve is None and out.supply_target is not None


# ── modes / pump / safety ────────────────────────────────────────────────────

def _inp(now, **kw):
    base = dict(now=now, local_hour=12.0, t_supply=50.0, t_top=70.0, t_boiler=40.0, t_room=21.0, t_out=0.0,
                pump_on=True, pump_configured=True, valve_configured=True)
    base.update(kw)
    return hc.ControlInput(**base)


def test_off_mode_never_commands_even_when_overheating():
    ctrl = hc.HeatingController(_cfg(mode="off"))
    out = ctrl.step(_inp(100.0, t_boiler=95.0, t_top=92.0, overheat=True, pump_on=False))
    assert out.valve is None and out.pump is None and out.dump is True and out.log == []


def test_dump_opens_valve_and_starts_pump_in_manual_and_auto():
    for mode in ("manual", "auto", "summer"):
        ctrl = hc.HeatingController(_cfg(mode=mode))
        ctrl.valve.known, ctrl.valve.position = True, 20.0
        out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=85.0, overheat=True, pump_on=False))
        assert out.dump and out.valve.direction == "open" and out.valve.kind == "safety", mode
        assert out.valve.seconds == ctrl.valve.run_s + hc.OVERRUN_S, mode       # full run, not the estimate
        assert out.pump == ("on", "zrzut ciepła — kocioł gorący"), mode


def test_no_dump_for_floor_heating():
    ctrl = hc.HeatingController(_cfg(emitters="floor"))
    ctrl.valve.known = True
    out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=89.0, overheat=True, t_supply=40.0))
    assert not out.dump
    assert out.valve is None or out.valve.direction != "open" or out.valve.kind != "safety"


def test_dump_hysteresis():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    ctrl.step(_inp(100.0, t_boiler=89.0, t_top=89.0))
    assert ctrl.dumping                                  # boiler ≥ warn and buffer full
    ctrl.step(_inp(200.0, t_boiler=87.0, t_top=87.0))
    assert ctrl.dumping                                  # within 3 K
    ctrl.step(_inp(300.0, t_boiler=84.0, t_top=86.0))
    assert not ctrl.dumping


def test_supply_limiter_closes_and_raises_alarm():
    ctrl = hc.HeatingController(_cfg(emitters="floor", safety_dump=False))
    ctrl.valve.known, ctrl.valve.position = True, 60.0
    out = ctrl.step(_inp(100.0, t_supply=50.0))          # floor max 45 + 3
    assert out.limiter and out.valve.direction == "close" and out.valve.kind == "safety"
    assert [a.id for a in out.alarms] == ["heating_supply_hot"]


def test_probe_lost_during_fire_starts_pump_even_in_manual():
    ctrl = hc.HeatingController(_cfg(mode="manual"))
    out = ctrl.step(_inp(100.0, pump_on=False, probe_fault=True))
    assert out.pump and out.pump[0] == "on"


def test_auto_pump_off_when_warm_outside_with_min_times():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    out = ctrl.step(_inp(1000.0, t_out=20.0))            # damped starts at 20 → above the 16 °C limit
    assert out.pump == ("off", "ciepło na zewnątrz (średnio 20.0 °C)")
    ctrl.pump.note(False, 1000.0)
    out = ctrl.step(_inp(1100.0, t_out=-5.0, pump_on=False))
    assert out.pump is None                              # damped still warm and min-off running
    ctrl2 = hc.HeatingController(_cfg())
    ctrl2.valve.known = True
    out = ctrl2.step(_inp(1000.0, t_out=-5.0, pump_on=False))
    assert out.pump == ("on", "jest zapotrzebowanie na ciepło")


def test_auto_pump_off_when_room_overheats_and_buffer_cold():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    assert ctrl.step(_inp(1000.0, t_room=23.5)).pump[0] == "off"
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    out = ctrl.step(_inp(1000.0, t_top=24.0))
    assert out.pump == ("off", "bufor za zimny (24 °C)")


def test_summer_closes_valve_and_exercises_pump_weekly():
    ctrl = hc.HeatingController(_cfg(mode="summer"))
    ctrl.valve.known, ctrl.valve.position = True, 50.0
    ctrl.pump.last_on = 0.0
    now = 8 * 86400.0
    out = ctrl.step(_inp(now, pump_on=False))
    assert out.valve.direction == "close"
    assert out.pump == ("on", "przepłukanie pompy (raz w tygodniu, latem)")
    ctrl.valve.apply(out.valve, now)
    out = ctrl.step(_inp(now + 70, pump_on=True))
    assert out.pump == ("off", "koniec przepłukania pompy")


def test_room_correction_pro_only_and_bounded():
    cfg = _cfg()
    ctrl = hc.HeatingController(cfg)
    ctrl.valve.known = True
    a = ctrl.step(_inp(100.0, t_room=19.0, room_licensed=True))
    assert a.room_corr == pytest.approx(4.0)            # 2 K/K × 2 K
    b = hc.HeatingController(cfg).step(_inp(100.0, t_room=19.0, room_licensed=False))
    assert b.room_corr == 0.0 and b.supply_target < a.supply_target
    c = hc.HeatingController(cfg).step(_inp(100.0, t_room=10.0))
    assert c.room_corr == hc.ROOM_CORR_MAX


def test_target_capped_by_buffer_top():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, t_out=-15.0, t_top=50.0))
    assert out.supply_target == 48.0


def test_manual_valve_and_undo():
    ctrl = hc.HeatingController(_cfg(mode="manual"))
    ctrl.valve.known, ctrl.valve.position = True, 50.0
    cmd = ctrl.manual_valve("open", 12.0, 100.0)
    ctrl.valve.apply(cmd, 100.0)
    assert ctrl.valve.position == pytest.approx(60.0)
    ctrl.valve.undo(cmd)
    assert ctrl.valve.position == pytest.approx(50.0)
    assert ctrl.manual_valve("close", 5.0, 100.0) is not None


def test_state_roundtrip_and_valve_change_rehomes():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known, ctrl.valve.position = True, 33.0
    ctrl.pump.note(True, 500.0)
    saved = ctrl.dump_state()
    fresh = hc.HeatingController(_cfg())
    fresh.restore(saved)
    assert fresh.valve.known and fresh.valve.position == 33.0 and fresh.pump.last_on == 500.0
    log = fresh.configure(_cfg(valve_run_s=60, mode="manual"))
    assert not fresh.valve.known and fresh.valve.run_s == 60 and log == ["Tryb: Ręczne"]


# ── review fixes: never against the Shelly guard, dump robust, restarts, retries ──

def test_hot_is_a_superset_of_the_shelly_guard():
    """Guard: boiler ≥ alarm − 2 (90) or buffer top ≥ full + 4 (92), released 3 K lower."""
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    out = ctrl.step(_inp(100.0, t_boiler=90.0, t_top=80.0, pump_on=False))
    assert ctrl.hot and out.dump and out.pump[0] == "on"
    ctrl.step(_inp(110.0, t_boiler=88.0, t_top=80.0))
    assert ctrl.hot                                     # hysteresis like the guard
    ctrl.step(_inp(120.0, t_boiler=86.5, t_top=80.0))
    assert not ctrl.hot
    ctrl2 = hc.HeatingController(_cfg())
    ctrl2.valve.known = True
    ctrl2.step(_inp(100.0, t_boiler=60.0, t_top=92.0))
    assert ctrl2.hot                                    # buffer top alone


def test_hot_pump_never_switched_off_by_auto():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    for i in range(40):                                 # warm outside: auto would want the pump off
        out = ctrl.step(_inp(100.0 + i * 10, t_out=25.0, t_boiler=90.5, t_top=80.0, pump_on=True))
        assert out.pump is None or out.pump[0] == "on"


def test_floor_hot_forces_pump_but_never_opens_the_valve():
    ctrl = hc.HeatingController(_cfg(emitters="floor", safety_dump=True))   # True is ignored for floor
    ctrl.valve.known, ctrl.valve.position = True, 30.0
    out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=89.0, overheat=True, t_supply=40.0, pump_on=False))
    assert ctrl.hot and not out.dump
    assert out.pump == ("on", "kocioł gorący — odbiór ciepła")
    assert out.valve is None or out.valve.direction == "close"


def test_safety_dump_forced_off_for_floor_and_mixed():
    assert hm.normalize({"emitters": "floor", "safety_dump": True})["safety_dump"] is False
    assert hm.normalize({"emitters": "mixed", "safety_dump": True})["safety_dump"] is False
    assert hm.normalize({"emitters": "radiators", "safety_dump": False})["safety_dump"] is False
    assert hm.normalize({"emitters": "radiators"})["safety_dump"] is True


def test_dump_redrives_and_preempts_a_running_close():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known, ctrl.valve.position = True, 100.0
    ctrl.valve.over_open_s = 99.0                        # the estimate says: fully open
    ctrl.valve.busy_until = 500.0                        # a close (homing) is running
    out = ctrl.step(_inp(100.0, t_boiler=93.0, t_top=85.0, overheat=True))
    assert out.valve and out.valve.direction == "open"
    ctrl.valve.apply(out.valve, 100.0)
    assert ctrl.step(_inp(200.0, t_boiler=93.0, t_top=85.0, overheat=True)).valve is None
    again = ctrl.step(_inp(100.0 + hc.DUMP_REDRIVE_S, t_boiler=93.0, t_top=85.0, overheat=True))
    assert again.valve and again.valve.direction == "open"


def test_probe_fault_forces_pump_in_summer_and_auto():
    for mode in ("summer", "auto"):
        ctrl = hc.HeatingController(_cfg(mode=mode))
        ctrl.valve.known = True
        out = ctrl.step(_inp(100.0, pump_on=False, probe_fault=True, t_out=25.0))
        assert out.pump == ("on", "brak odczytu sondy kotła/bufora"), mode


def test_no_limiter_log_spam_during_dump():
    ctrl = hc.HeatingController(_cfg())
    ctrl.valve.known = True
    logs = []
    for i in range(10):
        logs += ctrl.step(_inp(100.0 + i * 10, t_boiler=93.0, t_top=85.0, overheat=True, t_supply=85.0)).log
    assert not any("Ogranicznik" in line for line in logs)
    assert sum("Zrzut" in line for line in logs) == 1


def test_restart_with_inverted_valve_does_not_rehome():
    cfg = _cfg(valve_invert=True, valve_run_s=90)
    ctrl = hc.HeatingController(cfg)
    ctrl.configure(cfg)
    ctrl.valve.known, ctrl.valve.position = True, 40.0
    saved = ctrl.dump_state()
    fresh = hc.HeatingController(hm.normalize(None))      # built from defaults, as at HA start
    fresh.restore(saved)
    fresh.configure(cfg)
    assert fresh.valve.known and fresh.valve.run_s == 90 and fresh.valve.position == 40.0


def test_failed_actuator_is_not_hammered():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(100.0))
    assert out.valve.kind == "home"
    ctrl.valve.failed(100.0)                              # the actuator didn't take it
    assert ctrl.step(_inp(130.0)).valve is None
    assert ctrl.step(_inp(100.0 + hc.RETRY_S + 1)).valve.kind == "home"


# ── valve parked while the CH pump stands ────────────────────────────────────

def _warm_house(now, **kw):
    # warm inside: no heating needed, the pump stays off
    return _inp(now, t_room=26.0, pump_on=False, **kw)


def test_valve_closes_after_the_pump_stood_two_minutes():
    assert hc.VALVE_PARK_AFTER_S == 120.0        # 10 min let the supply climb 49 → 62 °C by gravity (bobrek, 22.11)
    ctrl = hc.HeatingController(_cfg(room_target_c=22))
    ctrl.valve.known, ctrl.valve.position = True, 100.0
    ctrl.pump.last_on = 1000.0
    assert ctrl.step(_warm_house(1000.0 + hc.VALVE_PARK_AFTER_S - 30)).valve is None
    out = ctrl.step(_warm_house(1000.0 + hc.VALVE_PARK_AFTER_S + 1))
    assert out.valve.direction == "close" and out.valve.kind == "park"
    assert out.valve.seconds == ctrl.valve.run_s + hc.OVERRUN_S           # from 100 %: the whole stroke
    assert "bez przepływu grawitacyjnego" in out.valve.reason
    ctrl.valve.apply(out.valve, 1000.0 + hc.VALVE_PARK_AFTER_S + 1)
    assert ctrl.valve.at_end("close")
    assert ctrl.step(_warm_house(5000.0)).valve is None                     # parked once, not every tick


def test_valve_not_parked_while_the_pump_runs_or_in_manual():
    ctrl = hc.HeatingController(_cfg(room_target_c=22))
    ctrl.valve.known, ctrl.valve.position = True, 60.0
    out = ctrl.step(_inp(5000.0, t_room=26.0, pump_on=True))              # running (minimum run time)
    assert out.valve is None or out.valve.kind != "park"
    manual = hc.HeatingController(_cfg(mode="manual", room_target_c=22))
    manual.valve.known, manual.valve.position = True, 60.0
    assert manual.step(_warm_house(5000.0)).valve is None


def test_after_parking_heating_opens_the_valve_step_by_step():
    ctrl = hc.HeatingController(_cfg(room_target_c=22))
    ctrl.valve.known, ctrl.valve.position = True, 0.0
    ctrl.valve.over_close_s = hc.OVERRUN_S
    out = ctrl.step(_inp(5000.0, t_room=20.0, t_supply=30.0, pump_on=True))
    assert out.valve.direction == "open" and out.valve.kind == "auto"
    assert out.valve.seconds <= hc.MAX_PULSE_S                              # a pulse, not the full stroke


# ── heat dump by the buffer's top (dump_top_c) — bobrek 24.11.2026 ───────────

def test_dump_by_buffer_top_needs_a_fire_and_ignores_the_boiler():
    ctrl = hc.HeatingController(_cfg(mode="auto", dump_top_c=93.0, alarm_c=79.0, warn_c=77.0))
    # the boiler probe at its working temperature (and even at the alarm) starts nothing while the top is below 93
    out = ctrl.step(_inp(100.0, t_boiler=80.0, t_top=86.3, overheat=True, firing=True))
    assert out.dump is False and out.pump_forced == ""
    out = ctrl.step(_inp(110.0, t_boiler=80.0, t_top=93.0, firing=True, pump_on=False))
    assert out.dump is True and out.pump_forced == "dump"
    assert any("góra bufora 93.0" in line for line in out.log)
    assert out.pump == ("on", "zrzut ciepła — bufor pełny")
    assert ctrl.step(_inp(120.0, t_top=90.5, firing=True)).dump is True          # held 3 K lower …
    assert ctrl.step(_inp(130.0, t_top=89.9, firing=True)).dump is False         # … then released


def test_dump_by_buffer_top_stops_when_the_fire_is_out():
    ctrl = hc.HeatingController(_cfg(mode="auto", dump_top_c=93.0))
    assert ctrl.step(_inp(100.0, t_top=94.0, firing=True)).dump is True
    out = ctrl.step(_inp(110.0, t_top=94.0, firing=False))                     # nothing adds heat any more
    assert out.dump is False and any("wygasł" in line for line in out.log)
    assert ctrl.step(_inp(120.0, t_top=95.0, firing=False)).dump is False
    assert ctrl.step(_inp(130.0, t_top=95.0, firing=None)).dump is True        # fire unknown: safety first


def test_dump_top_setting_is_optional_and_clamped():
    assert hm.normalize({})["dump_top_c"] is None
    assert hm.normalize({"dump_top_c": 93})["dump_top_c"] == 93.0
    assert hm.normalize({"dump_top_c": 120})["dump_top_c"] == 98.0
    assert hm.normalize({"dump_top_c": ""})["dump_top_c"] is None
    assert hm.to_settings({"dump_top_c": 93})["dump_top_c"] == 93.0
