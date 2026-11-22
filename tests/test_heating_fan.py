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


def test_fan_follows_the_pump():
    assert hc.fan_follow("auto", True, False) == "on"
    assert hc.fan_follow("auto", False, True) == "off"
    assert hc.fan_follow("manual", True, False) == "on"        # the pump on HAND / manual: the fan too
    assert hc.fan_follow("summer", False, True) == "off"


def test_fan_left_alone_when_right_offline_unknown_or_mode_off():
    assert hc.fan_follow("auto", True, True) is None
    assert hc.fan_follow("auto", False, False) is None
    assert hc.fan_follow("auto", True, None) is None           # socket offline: nothing sent until it is back
    assert hc.fan_follow("auto", None, False) is None          # the pump's state unknown (Shelly offline)
    assert hc.fan_follow("off", True, False) is None           # "Wyłączone" = measurements only


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
