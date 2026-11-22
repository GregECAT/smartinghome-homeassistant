"""A unit heater on the CH circuit (e.g. the garage): its fan runs exactly when the CH pump runs.

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


LATE = hc.FAN_DELAY_S + 1


def test_fan_follows_the_pump_two_minutes_late():
    assert hc.FAN_DELAY_S == 120.0
    assert hc.fan_follow("auto", True, LATE, False) == ("on", 0.0)
    assert hc.fan_follow("auto", False, LATE, True) == ("off", 0.0)
    assert hc.fan_follow("manual", True, LATE, False) == ("on", 0.0)    # the pump on HAND / manual: the fan too
    assert hc.fan_follow("summer", False, LATE, True) == ("off", 0.0)


def test_fan_waits_for_a_warm_coil_and_runs_on_after_the_pump_stops():
    assert hc.fan_follow("auto", True, 30.0, False) == (None, 90.0)     # cold coil: starts 2 min after the pump
    assert hc.fan_follow("auto", False, 100.0, True) == (None, 20.0)    # coil still hot: runs on 2 min
    assert hc.fan_follow("auto", True, -5.0, False) == (None, hc.FAN_DELAY_S)   # clock skew counts as just now


def test_fan_left_alone_when_right_offline_unknown_or_mode_off():
    assert hc.fan_follow("auto", True, LATE, True) == (None, 0.0)
    assert hc.fan_follow("auto", False, 5.0, False) == (None, 0.0)      # already right: no countdown either
    assert hc.fan_follow("auto", True, LATE, None) == (None, 0.0)       # socket offline: nothing until it is back
    assert hc.fan_follow("auto", None, LATE, False) == (None, 0.0)      # the pump's state unknown (Shelly offline)
    assert hc.fan_follow("off", True, LATE, False) == (None, 0.0)       # "Wyłączone" = measurements only


def test_fan_roles_and_name_in_settings():
    cfg = hm.normalize({"entities": {"fan_switch": " switch.wlacznik_gniazdko_socket_1 "}, "fan_name": "  Garaż  "})
    assert cfg["entities"]["fan_switch"] == "switch.wlacznik_gniazdko_socket_1"
    assert cfg["entities"]["fan_power"] == ""
    assert cfg["fan_name"] == "Garaż"
    assert hm.to_settings({"fan_name": "Garaż"})["fan_name"] == "Garaż"
    assert hm.normalize({})["fan_name"] == hm.FAN_NAME
    assert hm.normalize({"fan_name": "   "})["fan_name"] == hm.FAN_NAME
    assert len(hm.normalize({"fan_name": "x" * 60})["fan_name"]) == 24
    assert hm.ROLES["fan_switch"][1] == "switch" and hm.ROLES["fan_power"][1] == "power"


def test_fan_state_in_the_model_status():
    m = hm.HeatingModel()
    m.configure(hm.normalize({"enabled": True}))
    m.update({"fan_switch": "on", "fan_power": "48.5"}, 1000.0)
    assert m.status(1000.0)["fan"] == {"on": True, "power_w": 48.5}
    m.update({"fan_switch": "unavailable", "fan_power": "unavailable"}, 1010.0)
    assert m.status(1010.0)["fan"] == {"on": None, "power_w": None}
