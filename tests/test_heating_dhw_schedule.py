"""Hot water by the clock: hour windows, a hot-enough source, with and without the tank probe.

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
    return hm.normalize({"enabled": True, "mode": "auto", "dhw_target_c": 55, "dhw_mode": "schedule", **kw})


def _inp(now, hour, **kw):
    base = dict(now=now, local_hour=hour, t_supply=50.0, t_top=80.0, t_bottom=60.0, t_boiler=None, t_room=21.0,
                t_out=0.0, pump_on=False, pump_configured=True, valve_configured=False,
                t_dhw=None, dhw_pump_on=False, dhw_pump_configured=True)
    base.update(kw)
    return hc.ControlInput(**base)


def test_settings_default_to_the_probe_and_keep_the_windows():
    c = hm.normalize({})
    assert c["dhw_mode"] == "auto"
    assert (c["dhw_win1_from"], c["dhw_win1_to"], c["dhw_win2_from"], c["dhw_win2_to"]) == (5, 6, 17, 18)
    stored = hm.to_settings({"dhw_mode": "schedule", "dhw_win1_from": 6, "dhw_win1_to": 7.6, "dhw_win2_to": 40})
    assert stored["dhw_mode"] == "schedule" and stored["dhw_win1_to"] == 7 and stored["dhw_win2_to"] == 23
    assert hm.normalize({"dhw_mode": "nope"})["dhw_mode"] == "auto"


def test_charges_in_the_window_without_a_tank_probe():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, 5.5))
    assert out.dhw.want and out.dhw_pump == ("on", "ładowanie CWU według godzin (5–6, 17–18): bufor 80 °C")


def test_stays_off_outside_the_windows():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, 12.0))
    assert not out.dhw.want and out.dhw.reason == "CWU według godzin (5–6, 17–18) — poza oknem ładowania"


def test_window_end_stops_the_pump():
    ctrl = hc.HeatingController(_cfg())
    assert ctrl.step(_inp(100.0, 17.9)).dhw.want
    ctrl.dhw_pump.note(True, 100.0)
    out = ctrl.step(_inp(4000.0, 18.0, dhw_pump_on=True))
    assert not out.dhw.want and out.dhw_pump and out.dhw_pump[0] == "off"


def test_a_cool_source_never_charges_a_tank_it_cannot_see():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, 5.5, t_top=58.0))
    assert not out.dhw.want
    assert out.dhw.reason == "bufor za chłodny na ładowanie CWU (58 °C, potrzeba ≥ 60 °C)"


def test_bottom_stands_in_for_a_missing_top():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, 17.5, t_top=None, t_bottom=70.0))
    assert out.dhw.want and "bufor (dół) 70 °C" in out.dhw.reason


def test_with_a_tank_probe_it_stops_at_the_target():
    ctrl = hc.HeatingController(_cfg())
    assert ctrl.step(_inp(100.0, 5.2, t_dhw=45.0)).dhw.want
    out = ctrl.step(_inp(200.0, 5.3, t_dhw=55.0, dhw_pump_on=True))
    assert not out.dhw.want and out.dhw.reason == "CWU naładowana (55 °C)"


def test_window_across_midnight_and_an_empty_window():
    ctrl = hc.HeatingController(_cfg(dhw_win1_from=23, dhw_win1_to=1, dhw_win2_from=12, dhw_win2_to=12))
    assert ctrl.step(_inp(100.0, 0.5)).dhw.want
    assert not ctrl.step(_inp(200.0, 12.5)).dhw.want
    assert "(23–1)" in ctrl.step(_inp(300.0, 12.5)).dhw.reason


def test_manual_mode_leaves_the_pump_to_its_switch():
    out = hc.HeatingController(_cfg(mode="manual")).step(_inp(100.0, 5.5))
    assert out.dhw.want and out.dhw_pump is None


def _hot(ctrl):
    ctrl.hot = True                       # the boiler's hot state (the same one that starts the heat dump)
    return ctrl


def test_hot_boiler_dumps_into_a_tank_without_probe_when_dhw_dump_is_on():
    for mode in ("schedule", "auto"):
        ctrl = _hot(hc.HeatingController(_cfg(dhw_mode=mode, dhw_dump=True)))
        plan = ctrl._dhw_plan(_inp(1000.0, 14.0, t_top=92.0, t_dhw=None))        # 14:00 — outside the windows
        assert plan.want and plan.force and plan.dump
        assert "bez sondy CWU" in plan.reason


def test_no_blind_dump_without_the_option_or_when_the_boiler_is_not_hot():
    ctrl = _hot(hc.HeatingController(_cfg(dhw_dump=False)))
    assert not ctrl._dhw_plan(_inp(1000.0, 14.0, t_top=92.0, t_dhw=None)).want   # nothing would stop it at 55 °C
    ctrl = hc.HeatingController(_cfg(dhw_dump=True))
    assert not ctrl._dhw_plan(_inp(1000.0, 14.0, t_top=85.0, t_dhw=None)).want   # not hot: the clock rules


def test_with_a_tank_probe_the_limit_still_applies():
    ctrl = _hot(hc.HeatingController(_cfg(dhw_dump=True, dhw_max_c=80)))
    assert not ctrl._dhw_plan(_inp(1000.0, 14.0, t_top=92.0, t_dhw=81.0)).want
