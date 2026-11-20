"""Panel emergency switches (lockout) and the reported reason a safety rule holds the CH pump on.

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
    return hm.normalize({"enabled": True, "mode": "auto", "valve_type": "cover", "valve_cover": "cover.v", **kw})


def _inp(now, **kw):
    base = dict(now=now, local_hour=12.0, t_supply=50.0, t_top=70.0, t_boiler=40.0, t_room=21.0, t_out=0.0,
                pump_on=False, pump_configured=True, valve_configured=True,
                t_dhw=48.0, dhw_pump_on=False, dhw_pump_configured=True)
    base.update(kw)
    return hc.ControlInput(**base)


def test_normalize_lockout():
    assert hm.normalize({})["lockout"] == {"pump": False, "dhw_pump": False, "valve": False}
    got = hm.normalize({"lockout": {"pump": True, "valve": "yes", "boiler": True}})["lockout"]
    assert got == {"pump": True, "dhw_pump": False, "valve": False}
    assert hm.to_settings({"lockout": {"dhw_pump": True}})["lockout"]["dhw_pump"] is True


def test_forced_reason_reported_in_every_mode():
    for mode in ("auto", "manual", "summer"):
        out = hc.HeatingController(_cfg(mode=mode)).step(_inp(100.0, probe_fault=True))
        assert out.pump_forced == "probe" and out.pump == ("on", "brak odczytu sondy kotła/bufora"), mode
    off = hc.HeatingController(_cfg(mode="off")).step(_inp(100.0, probe_fault=True))
    assert off.pump_forced == "probe" and off.pump is None           # reported, not acted on
    hot = hc.HeatingController(_cfg()).step(_inp(100.0, t_boiler=95.0, t_top=92.0, overheat=True))
    assert hot.pump_forced == "dump"
    assert hc.HeatingController(_cfg()).step(_inp(100.0)).pump_forced == ""


def test_locked_pump_is_never_switched_on_even_by_safety():
    ctrl = hc.HeatingController(_cfg(lockout={"pump": True}))
    out = ctrl.step(_inp(100.0, probe_fault=True))
    assert out.pump is None
    assert [a.id for a in out.alarms] == ["heating_lock_blocks_safety"] and out.alarms[0].level == "warning"
    running = ctrl.step(_inp(110.0, probe_fault=True, pump_on=True))   # turned on elsewhere → off again
    assert running.pump == ("off", hc.LOCK_REASON)
    hot = hc.HeatingController(_cfg(lockout={"pump": True})).step(
        _inp(100.0, t_boiler=95.0, t_top=92.0, overheat=True, pump_on=True))
    assert hot.pump == ("off", hc.LOCK_REASON)
    assert any(a.id == "heating_lock_blocks_safety" and a.level == "critical" for a in hot.alarms)


def test_lock_wins_in_off_mode_without_alarm():
    out = hc.HeatingController(_cfg(mode="off", lockout={"pump": True, "dhw_pump": True})).step(
        _inp(100.0, probe_fault=True, pump_on=True, dhw_pump_on=True))
    assert out.pump == ("off", hc.LOCK_REASON) and out.dhw_pump == ("off", hc.LOCK_REASON)
    assert not out.alarms


def test_locked_dhw_pump_and_valve():
    ctrl = hc.HeatingController(_cfg(lockout={"dhw_pump": True, "valve": True}, dhw_dump=True))
    ctrl.valve.known = True
    out = ctrl.step(_inp(100.0, t_top=70.0, t_dhw=40.0))              # charging would start
    assert out.dhw_pump is None and out.valve is None
    hot = ctrl.step(_inp(200.0 + hc.DUMP_REDRIVE_S, t_boiler=95.0, t_top=92.0, overheat=True, t_dhw=60.0))
    assert hot.valve is None and hot.dhw_pump is None
    blocked = [a for a in hot.alarms if a.id == "heating_lock_blocks_safety"]
    assert blocked and "zawór" in blocked[0].message and blocked[0].level == "critical"


def test_unlocked_controller_unchanged():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(100.0, t_top=70.0, t_dhw=40.0))
    assert out.dhw_pump and out.dhw_pump[0] == "on"
