"""Manual mode: the valve driven to a chosen opening (%) from the panel slider.

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


def _ctrl(position=0.0, known=True):
    ctrl = hc.HeatingController(hm.normalize({"enabled": True, "mode": "manual", "valve_run_s": 127}))
    ctrl.valve.known, ctrl.valve.position = known, position
    return ctrl


def test_opens_and_closes_by_the_difference():
    ctrl = _ctrl(0.0)
    cmd = ctrl.manual_valve_to(40, 1000.0)
    assert cmd.direction == "open" and cmd.kind == "manual"
    assert cmd.seconds == pytest.approx(0.40 * 127, abs=0.1)
    assert "40 %" in cmd.reason
    ctrl.valve.apply(cmd, 1000.0)
    assert ctrl.valve.position == pytest.approx(40.0, abs=0.1)
    cmd = ctrl.manual_valve_to(25, 2000.0)
    assert cmd.direction == "close" and cmd.seconds == pytest.approx(0.15 * 127, abs=0.1)


def test_the_ends_get_the_overrun():
    ctrl = _ctrl(30.0)
    cmd = ctrl.manual_valve_to(0, 1000.0)
    assert cmd.direction == "close" and cmd.seconds == pytest.approx(0.30 * 127 + hc.OVERRUN_S, abs=0.1)
    ctrl.valve.apply(cmd, 1000.0)
    assert ctrl.valve.at_end("close")
    assert ctrl.manual_valve_to(0, 2000.0) is None                      # already shut
    ctrl = _ctrl(70.0)
    cmd = ctrl.manual_valve_to(100, 1000.0)
    assert cmd.direction == "open" and cmd.seconds == pytest.approx(0.30 * 127 + hc.OVERRUN_S, abs=0.1)


def test_nothing_while_moving_or_already_there():
    ctrl = _ctrl(50.0)
    assert ctrl.manual_valve_to(50.5, 1000.0) is None
    ctrl.valve.apply(ctrl.manual_valve_to(80, 1000.0), 1000.0)
    assert ctrl.manual_valve_to(20, 1010.0) is None                     # still driving the 38 s move


def test_unknown_position_is_homed_first():
    cmd = _ctrl(50.0, known=False).manual_valve_to(40, 1000.0)
    assert cmd.kind == "home" and cmd.direction == "close"
