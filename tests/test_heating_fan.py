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


def test_fan_starts_at_once_during_a_heat_dump():
    assert hc.fan_follow("auto", True, 5.0, False, dump=True) == ("on", 0.0)      # no 2 min wait
    assert hc.fan_follow("auto", True, 5.0, True, dump=True) == (None, 0.0)
    assert hc.fan_follow("auto", False, 5.0, True, dump=True) == (None, 115.0)   # pump off: the usual run-on
    assert hc.fan_follow("off", True, 5.0, False, dump=True) == (None, 0.0)
    assert hc.fan_follow("auto", True, 5.0, None, dump=True) == (None, 0.0)      # socket offline


def test_fan_off_at_night_whatever_the_pump_does():
    assert hc.fan_follow("auto", True, LATE, True, quiet=True) == ("off", 0.0)       # 23:00: off at once
    assert hc.fan_follow("auto", True, LATE, False, quiet=True) == (None, 0.0)       # the pump runs: the fan stays off
    assert hc.fan_follow("auto", True, 5.0, False, quiet=True) == (None, 0.0)        # no countdown at night
    assert hc.fan_follow("manual", True, LATE, True, quiet=True) == ("off", 0.0)
    assert hc.fan_follow("auto", None, LATE, True, quiet=True) == ("off", 0.0)       # the pump's Shelly offline
    assert hc.fan_follow("auto", True, LATE, None, quiet=True) == (None, 0.0)        # socket offline
    assert hc.fan_follow("off", True, LATE, True, quiet=True) == (None, 0.0)         # "Wyłączone" = hands off


def test_heat_dump_runs_the_fan_at_night_too():
    assert hc.fan_follow("auto", True, 5.0, False, dump=True, quiet=True) == ("on", 0.0)
    assert hc.fan_follow("auto", True, 5.0, True, dump=True, quiet=True) == (None, 0.0)
    assert hc.fan_follow("auto", False, 5.0, True, dump=True, quiet=True) == ("off", 0.0)   # dump over: off, no run-on


def test_fan_follows_again_in_the_morning():
    assert hc.fan_follow("auto", True, 7200.0, False, quiet=False) == ("on", 0.0)     # 07:00, pump on since 05:00
    assert hc.fan_follow("auto", True, 30.0, False, quiet=False) == (None, 90.0)
    assert hc.in_hours(23, 7, 23.0) and hc.in_hours(23, 7, 3.5) and hc.in_hours(23, 7, 6.99)
    assert not hc.in_hours(23, 7, 7.0) and not hc.in_hours(23, 7, 22.99)


def test_fan_quiet_settings():
    cfg = hm.normalize({})
    assert cfg["fan_quiet"] is False and (cfg["fan_quiet_from"], cfg["fan_quiet_to"]) == (23, 7)
    cfg = hm.normalize({"fan_quiet": True, "fan_quiet_from": "22", "fan_quiet_to": 99})
    assert cfg["fan_quiet"] is True and cfg["fan_quiet_from"] == 22 and cfg["fan_quiet_to"] == 23
    assert hm.normalize({"fan_quiet": "yes"})["fan_quiet"] is False
    saved = hm.to_settings({"fan_quiet": True, "fan_quiet_from": 23, "fan_quiet_to": 7})
    assert (saved["fan_quiet"], saved["fan_quiet_from"], saved["fan_quiet_to"]) == (True, 23, 7)


def test_fan_switched_off_in_the_panel_stays_off():
    # bobrek 26.11.2026: the garage heater switched off from the drawing — it no longer starts with the CH pump
    assert hc.fan_follow("auto", True, LATE, True, off=True) == ("off", 0.0)          # off at once
    assert hc.fan_follow("auto", True, LATE, False, off=True) == (None, 0.0)          # the pump runs: stays off
    assert hc.fan_follow("auto", True, 5.0, False, off=True) == (None, 0.0)           # no countdown
    assert hc.fan_follow("manual", False, LATE, True, off=True) == ("off", 0.0)
    assert hc.fan_follow("auto", True, LATE, None, off=True) == (None, 0.0)           # socket offline
    assert hc.fan_follow("off", True, LATE, True, off=True) == (None, 0.0)            # "Wyłączone" = hands off


def test_heat_dump_runs_a_switched_off_fan_too():
    assert hc.fan_follow("auto", True, 5.0, False, dump=True, off=True) == ("on", 0.0)
    assert hc.fan_follow("auto", False, 5.0, True, dump=True, off=True) == ("off", 0.0)  # dump over: off again


def test_fan_switched_back_on_follows_the_pump_at_once():
    assert hc.fan_follow("auto", True, 7200.0, False, off=False) == ("on", 0.0)
    assert hc.fan_follow("auto", False, 7200.0, False, off=False) == (None, 0.0)


def test_fan_off_setting():
    assert hm.normalize({})["fan_off"] is False
    assert hm.normalize({"fan_off": True})["fan_off"] is True
    assert hm.normalize({"fan_off": "true"})["fan_off"] is False
    assert hm.to_settings({"fan_off": True})["fan_off"] is True


def test_the_heater_room_temperature_is_read_and_shown_only():
    # bobrek 25.11.2026: the garage temperature from the PV battery standing there
    m = hm.HeatingModel({"enabled": True, "entities": {"fan_room_temp": "sensor.battery_temperature"}})
    assert m.cfg["entities"]["fan_room_temp"] == "sensor.battery_temperature"
    m.update({"fan_room_temp": "14.0"}, now=1000.0)
    assert m.status(1000.0)["probes"]["fan_room_temp"]["value"] == 14.0
    assert "fan_room_temp" not in hm.PROBE_ROLES          # no probe-fault alarm, no part in the control
