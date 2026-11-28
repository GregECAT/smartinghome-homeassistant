# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
""""Najpierw bufor": during a fire the house (CH pump) and the hot water wait for a warm buffer bottom; let go for
the rest of the fire by the bottom, the time limit or a cold house; safety (heat dump) is never held back.

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
hc = importlib.import_module("smartinghome_pure.heating_control")

START = 1_000_000.0
H = 3600.0


def _cfg(**kw):
    return hm.normalize({"enabled": True, "mode": "auto", "dhw_target_c": 55, "buffer_first": True,
                         "buffer_first_c": 60, "buffer_first_max_h": 3, "room_target_c": 24, **kw})


def _inp(now, **kw):
    base = dict(now=now, local_hour=12.0, t_supply=50.0, t_top=78.0, t_bottom=40.0, t_boiler=80.0, t_room=23.5,
                t_out=0.0, pump_on=False, pump_configured=True, valve_configured=False,
                t_dhw=45.0, dhw_pump_on=False, dhw_pump_configured=True, firing=True, fire_start=START)
    base.update(kw)
    return hc.ControlInput(**base)


def test_settings():
    c = hm.normalize({})
    assert c["buffer_first"] is False and c["buffer_first_c"] == 60 and c["buffer_first_max_h"] == 3
    c = hm.normalize({"buffer_first": True, "buffer_first_c": 120, "buffer_first_max_h": 0})
    assert c["buffer_first"] is True and c["buffer_first_c"] == 90 and c["buffer_first_max_h"] == 0.5


def test_off_by_default_the_house_heats_during_a_fire():
    ctrl = hc.HeatingController(_cfg(buffer_first=False))
    out = ctrl.step(_inp(START + 600))
    assert out.buffer_first == "" and out.pump == ("on", "jest zapotrzebowanie na ciepło")
    assert out.dhw.want


def test_holds_the_house_and_the_hot_water_until_the_bottom_is_warm():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(START + 600))
    assert "dół bufora dojdzie do 60 °C" in out.buffer_first and "teraz 40 °C" in out.buffer_first
    assert out.pump is None                                  # the pump stands and stays standing
    assert not out.dhw.want and out.dhw_pump is None
    assert any(line.startswith("🔒 Najpierw bufor") for line in out.log)
    # the bottom warms up: let go — and for the rest of this fire, even when the house cools the bottom again
    out = ctrl.step(_inp(START + 2 * H, t_bottom=61.0))
    assert out.buffer_first == "" and out.pump == ("on", "jest zapotrzebowanie na ciepło")
    assert any("🔓 Najpierw bufor: dom i CWU dostają ciepło — dół bufora 61 °C" in line for line in out.log)
    ctrl.pump.note(True, START + 2 * H)
    out = ctrl.step(_inp(START + 2.5 * H, t_bottom=50.0, pump_on=True))
    assert out.buffer_first == "" and out.pump is None


def test_a_new_fire_is_held_again_and_the_latch_survives_a_restart():
    ctrl = hc.HeatingController(_cfg())
    ctrl.step(_inp(START + 600))
    ctrl.step(_inp(START + H, t_bottom=65.0))                # let go for this fire
    again = hc.HeatingController(_cfg())
    again.restore(ctrl.dump_state())
    assert again.step(_inp(START + 1.5 * H, t_bottom=45.0)).buffer_first == ""
    again.step(_inp(START + 20 * H, firing=False, fire_start=None))   # the fire went out
    nxt = START + 24 * H
    assert again.step(_inp(nxt + 600, t_bottom=35.0, fire_start=nxt)).buffer_first


def test_let_go_after_the_time_limit():
    ctrl = hc.HeatingController(_cfg(buffer_first_max_h=2))
    assert ctrl.step(_inp(START + 1.9 * H)).buffer_first
    out = ctrl.step(_inp(START + 2 * H))
    assert out.buffer_first == "" and any("minęło 2 h od rozpalenia" in line for line in out.log)


def test_a_cold_house_gets_heat_at_once():
    ctrl = hc.HeatingController(_cfg())
    assert ctrl.step(_inp(START + 600, t_room=23.1)).buffer_first
    out = ctrl.step(_inp(START + 900, t_room=23.0))          # 1 K under the target of 24
    assert out.buffer_first == "" and out.pump == ("on", "jest zapotrzebowanie na ciepło")


def test_no_fire_or_unknown_never_holds():
    ctrl = hc.HeatingController(_cfg())
    assert ctrl.step(_inp(START, firing=False, fire_start=None)).buffer_first == ""
    assert ctrl.step(_inp(START + 60, firing=None, fire_start=None)).buffer_first == ""


def test_only_in_auto():
    ctrl = hc.HeatingController(_cfg(mode="manual"))
    assert ctrl.step(_inp(START + 600)).buffer_first == ""


def test_without_a_bottom_probe_the_top_decides():
    ctrl = hc.HeatingController(_cfg())
    out = ctrl.step(_inp(START + 600, t_bottom=None, t_top=55.0))
    assert "góra bufora dojdzie do 60 °C" in out.buffer_first
    assert ctrl.step(_inp(START + 900, t_bottom=None, t_top=62.0)).buffer_first == ""


def test_a_heat_dump_is_never_held_back():
    ctrl = hc.HeatingController(_cfg(dump_top_c=93))
    out = ctrl.step(_inp(START + 600, t_top=94.0, t_bottom=40.0))
    assert out.dump and out.pump == ("on", "zrzut ciepła — " + ctrl._hot_why)
