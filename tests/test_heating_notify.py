"""Heating notifications: "the boiler is lit" at 100 °C flue, the heating's own recipients, the wood reminder.

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


def run(readings, fire_from=None, armed=None):
    """Feed flue readings; a fire (session) is booked from index fire_from on. → indexes that notified."""
    prev, out = None, []
    for i, f in enumerate(readings):
        armed, say = hc.lit_watch(armed, fire_from is not None and i >= fire_from, prev, f)
        if say:
            out.append(i)
        prev = f if f is not None else prev
    return out


def test_lit_once_when_the_flue_crosses_100():
    # bobrek 26.11.2026: "at 100 °C in the chimney tell me to switch the boiler to automatic"
    assert run([20, 45, 80, 99, 104, 130, 160], fire_from=4) == [4]


def test_lit_not_again_while_the_fire_burns():
    # the exhaust fan pausing at the boiler thermostat: the flue dips under 100 and rises again — same fire
    assert run([20, 105, 150, 95, 70, 98, 120], fire_from=1) == [1]


def test_lit_again_for_the_next_fire():
    readings = [20, 110, 150, 60, 40, 30, 50, 101, 140]
    assert run(readings, fire_from=1) == [1]                # one long session: once
    prev, armed, said = None, None, []
    for i, f in enumerate(readings):
        fire = 1 <= i <= 3 or i >= 7                         # the first fire ended at index 4
        armed, say = hc.lit_watch(armed, fire, prev, f)
        said += [i] if say else []
        prev = f
    assert said == [1, 7]


def test_lit_silent_after_a_restart_in_the_middle_of_a_fire():
    assert run([150, 140, 95, 120], fire_from=0) == []      # first look: fire burning → not armed
    assert run([30, 120], fire_from=None) == [1]            # no fire booked yet: the crossing still counts


def test_lit_needs_two_readings_and_no_gap():
    assert run([None, 120, 130]) == []
    assert run([90, None, 110], fire_from=2) == [2]         # a missing reading keeps the last one


def test_wood_reminder():
    assert "Zważ drewno przed załadunkiem" in hc.wood_reminder(None, before_fire=True)
    assert "Nie wpisano jeszcze drewna" in hc.wood_reminder(0.0)
    assert "14,7 kg" in hc.wood_reminder(14.7) and "Dokładane drewno też zważ" in hc.wood_reminder(14.7)


def test_push_recipients_heating_extra():
    base = ["notify.mobile_app_iphone_grzegorz", "notify.mobile_app_iphone_henryk"]
    extra = ["notify.mobile_app_iphone", "notify.mobile_app_sm_s931b", "notify.mobile_app_iphone_grzegorz"]
    assert hc.push_recipients(base, extra, True) == [*base, "notify.mobile_app_iphone", "notify.mobile_app_sm_s931b"]
    assert hc.push_recipients(base, extra, False) == extra    # push off in Smarting HOME: the heating's own only
    assert hc.push_recipients(base, [], True) == base


def test_notify_extra_setting():
    cfg = hm.normalize({})
    assert cfg["notify_extra"] == [] and cfg["lit_notify"] is True
    cfg = hm.normalize({"notify_extra": ["notify.mobile_app_iphone", "mobile_app_sm_s931b", "notify.mobile_app_iphone",
                                         "", None, "notify.bad name", "light.kitchen", 5], "lit_notify": False})
    # a bare service name gets the prefix; duplicates, blanks and names with spaces are dropped
    assert cfg["notify_extra"] == ["notify.mobile_app_iphone", "notify.mobile_app_sm_s931b", "notify.5"]
    assert cfg["lit_notify"] is False
    assert hm.normalize({"notify_extra": "notify.x"})["notify_extra"] == []
    assert len(hm.normalize({"notify_extra": [f"notify.p{i}" for i in range(20)]})["notify_extra"]) == hm.NOTIFY_EXTRA_MAX
    saved = hm.to_settings({"notify_extra": ["notify.mobile_app_iphone"], "lit_notify": True})
    assert saved["notify_extra"] == ["notify.mobile_app_iphone"] and saved["lit_notify"] is True


def test_reload_once_the_buffer_has_room_again():
    # a fire burning, the buffer full (no room for a full load) → later room for one: say "dołóż" once
    full = {"firing": True, "basis": "history", "full_now": False, "full_at": 5400.0, "full_kg": 17}
    ready = {**full, "full_now": True, "full_at": 0.0}
    waiting, said = False, []
    for i, a in enumerate([full, full, ready, ready, full, ready]):
        waiting, say = hc.reload_watch(waiting, a)
        said += [i] if say else []
    assert said == [2, 5]


def test_reload_not_right_after_lighting_nor_without_a_fire():
    ready = {"firing": True, "basis": "history", "full_now": True, "full_at": 0.0, "full_kg": 30}
    assert hc.reload_watch(False, ready) == (False, False)          # an empty buffer at lighting: no ping
    assert hc.reload_watch(True, {**ready, "firing": False}) == (False, False)
    assert hc.reload_watch(False, {"firing": True, "basis": None}) == (False, False)
