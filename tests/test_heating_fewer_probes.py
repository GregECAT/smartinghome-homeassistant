"""Fewer probes: the buffer bottom stands in for a missing top (hot water, the hot rule), nothing else changes.

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
    # bobrek after 21.11.2026: no boiler probe, no buffer-top probe — supply, buffer bottom, hot water, house
    base = dict(now=now, local_hour=12.0, t_supply=50.0, t_top=None, t_bottom=65.0, t_boiler=None, t_room=21.0,
                t_out=0.0, pump_on=True, pump_configured=True, valve_configured=False,
                t_dhw=48.0, dhw_pump_on=False, dhw_pump_configured=True)
    base.update(kw)
    return hc.ControlInput(**base)


def test_hot_water_charges_from_the_bottom_without_a_top_probe():
    out = hc.HeatingController(_cfg()).step(_inp(100.0))
    assert out.dhw.want and out.dhw.source_c == 65.0
    assert out.dhw_pump == ("on", "ładowanie CWU: bufor (dół) 65 °C, CWU 48 °C → 55 °C")


def test_cool_bottom_does_not_charge_and_says_why():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, t_bottom=50.0))
    assert not out.dhw.want
    assert out.dhw.reason == "bufor (dół) za chłodny (50 °C, potrzeba ≥ 53 °C)"


def test_the_top_wins_when_it_has_a_reading():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, t_top=78.0, t_bottom=40.0))
    assert out.dhw.source_c == 78.0 and "bufor 78 °C" in out.dhw.reason


def test_no_buffer_probe_at_all_keeps_the_pump_off():
    out = hc.HeatingController(_cfg()).step(_inp(100.0, t_bottom=None))
    assert not out.dhw.want and out.dhw.reason == "brak odczytu sondy bufora"


def test_hot_bottom_forces_the_heating_pump_without_a_top_probe():
    cfg = _cfg()
    hot = min(cfg["buffer_full_c"] + 4.0, 95.0)
    ctrl = hc.HeatingController(cfg)
    assert ctrl.step(_inp(100.0, t_bottom=hot - 10.0)).pump_forced == ""
    out = ctrl.step(_inp(200.0, t_bottom=hot, pump_on=False))
    assert out.pump_forced in ("hot", "dump") and out.pump and out.pump[0] == "on"


def test_heating_runs_on_the_house_alone():
    # no boiler, no top: the house decides, the supply cap from the top does not apply
    cfg = _cfg(room_target_c=22)
    out = hc.HeatingController(cfg).step(_inp(100.0, t_room=20.0, t_bottom=30.0, pump_on=False))
    assert out.heating_needed and out.pump_forced == "" and out.pump[0] == "on"
    uncapped = min(max(out.curve_c + out.room_corr, cfg["supply_min_c"]), cfg["supply_max_c"])
    assert out.supply_target == round(uncapped, 1)          # a cool bottom does not cap the supply
