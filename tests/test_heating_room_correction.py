"""The house correction of the supply target: its memory (integral) and the pump's on / off by the house.

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
    return hm.normalize({"enabled": True, "mode": "auto", "valve_type": "cover", "valve_cover": "cover.v",
                         "room_target_c": 24.5, **kw})


def _inp(now, room, hour=12.0, pump_on=True):
    return hc.ControlInput(now=now, local_hour=hour, t_supply=50.0, t_top=70.0, t_boiler=40.0, t_room=room,
                           t_out=3.0, pump_on=pump_on, pump_configured=True)


def _run(c, start, seconds, room, **kw):
    out = None
    for t in range(0, int(seconds) + 1, 30):
        out = c.step(_inp(start + t, room, **kw))
    return out


def test_a_new_target_starts_the_memory_afresh():
    c = hc.HeatingController(_cfg(room_target_c=26))
    c.step(_inp(0, 24.0))
    c.room_i = 4.0                                    # learnt overnight: the house stayed below 26 °C
    c.configure(_cfg(room_target_c=24.5))             # the morning: 24.5 °C set by hand
    out = c.step(_inp(30, 25.3))
    assert abs(c.room_i) < 0.05                       # afresh (one 30 s step of the new error on top)
    assert out.room_corr < 0                          # warmer than wanted → a cooler supply


def test_day_night_switch_resets_the_memory():
    c = hc.HeatingController(_cfg(night_enabled=True, room_night_c=22, night_from=22, night_to=5))
    c.step(_inp(0, 24.0, hour=21.9))
    c.room_i = 3.0
    c.step(_inp(30, 24.0, hour=22.0))
    assert c.room_i == 0.0


def test_memory_against_the_house_fades_fast_while_heating():
    c = hc.HeatingController(_cfg())
    c.step(_inp(0, 25.3))
    c.needed = True                                   # heating is on (stops only at 24.5 + 1)
    c.room_i = 4.0                                    # +4 K pushing heat into a house 0.8 K too warm
    _run(c, 30, 3600, 25.3)
    assert c.room_i < 0.0                             # gone within the hour (was: ~13 h)


def test_memory_fades_while_the_pump_stands_but_not_past_zero():
    c = hc.HeatingController(_cfg())
    c.step(_inp(0, 26.0, pump_on=False))
    c.room_i = 4.0
    _run(c, 30, 4 * 3600, 26.0, pump_on=False)
    assert c.room_i == 0.0


def test_memory_with_the_house_keeps_its_normal_pace():
    c = hc.HeatingController(_cfg())
    c.step(_inp(0, 24.0))
    c.room_i = 1.0                                    # the house still below target: memory is right
    _run(c, 30, 3600, 24.0)
    assert 1.4 < c.room_i < 1.6                       # + gain·err·1 h / 2 h = +0.5


def test_pump_band_follows_the_house_closely():
    c = hc.HeatingController(_cfg())
    assert c.step(_inp(0, 24.9)).heating_needed is True       # below 24.5 + 0.5: start
    assert c.step(_inp(30, 25.4)).heating_needed is True      # still below 24.5 + 1: keep going
    assert c.step(_inp(60, 25.5)).heating_needed is False     # at 24.5 + 1: stop
    assert c.step(_inp(90, 25.2)).heating_needed is False     # not again until below 25.0
    assert c.step(_inp(120, 24.9)).heating_needed is True


def test_pump_band_centred_on_the_target():
    # bobrek 24.11.2026: target 24, hysteresis 0.2 K on the mains-powered parter sensor → on below 23.9, off at 24.1
    c = hc.HeatingController(_cfg(room_target_c=24.0, room_hyst_k=0.2))
    assert c.step(_inp(0, 24.7)).heating_needed is False      # above the target: no heating
    assert c.step(_inp(30, 23.9)).heating_needed is False     # not below 23.9 yet
    assert c.step(_inp(60, 23.8)).heating_needed is True      # start
    assert c.step(_inp(90, 24.0)).heating_needed is True      # keep going up to 24.1
    assert c.step(_inp(120, 24.1)).heating_needed is False    # stop
    assert c.step(_inp(150, 24.0)).heating_needed is False


def test_hysteresis_setting_is_optional_and_clamped():
    assert hm.normalize({})["room_hyst_k"] is None            # the band above the target, as before
    assert hm.normalize({"room_hyst_k": ""})["room_hyst_k"] is None
    assert hm.normalize({"room_hyst_k": 9})["room_hyst_k"] == 4.0
    assert hm.normalize({"room_hyst_k": 0.05})["room_hyst_k"] == 0.2
    assert hm.to_settings({"room_hyst_k": 0.2})["room_hyst_k"] == 0.2
    assert hm.to_settings({})["room_hyst_k"] is None
