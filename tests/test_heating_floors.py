"""The house storey by storey: settings, the reference room standing in for its storey, area-weighted mean.

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


def test_normalize_floors():
    c = hm.normalize({})
    assert c["floors"] == 1 and c["room_floor"] == 0
    assert c["floor_names"] == ["Parter", "1. piętro", "Poddasze"] and c["floor_areas"] == [None, None, None]
    c = hm.normalize({"floors": 7, "room_floor": 5, "floor_names": ["  Piwnica ", "", None], "floor_areas": [90, "x", -3]})
    assert c["floors"] == 3 and c["room_floor"] == 2
    assert c["floor_names"] == ["Piwnica", "1. piętro", "Poddasze"]
    assert c["floor_areas"] == [90.0, None, None]
    assert hm.normalize({"floors": 2, "room_floor": 2})["room_floor"] == 1
    stored = hm.to_settings({"floors": 3, "room_floor": 1, "floor_areas": [100, 100, 50]})
    assert stored["floors"] == 3 and stored["room_floor"] == 1 and stored["floor_areas"] == [100.0, 100.0, 50.0]


def test_reference_room_stands_in_for_its_storey():
    cfg = hm.normalize({"floors": 3, "room_floor": 1})
    floors, mean = hm.floors_state(cfg, [None, None, None], [None, None, None], 21.4, 55.0)
    assert [f["temp_c"] for f in floors] == [None, 21.4, None]
    assert floors[1]["ref"] and floors[1]["source"] == "room" and floors[1]["humidity"] == 55
    assert mean == 21.4
    # its own sensor wins over the reference room
    floors, _ = hm.floors_state(cfg, [None, 20.0, None], [None, None, None], 21.4, None)
    assert floors[1]["temp_c"] == 20.0 and floors[1]["source"] == "probe"


def test_mean_weighted_by_area_or_equal():
    eq = hm.normalize({"floors": 3, "room_floor": 1})
    _, mean = hm.floors_state(eq, [20.0, 22.0, 18.0], [None] * 3, None, None)
    assert mean == 20.0
    w = hm.normalize({"floors": 3, "room_floor": 1, "floor_areas": [100, 100, 50]})
    _, mean = hm.floors_state(w, [20.0, 22.0, 18.0], [None] * 3, None, None)
    assert mean == pytest.approx((2000 + 2200 + 900) / 250, abs=0.05)
    # an area missing on a measured storey → equal weights (no half-weighted mix)
    p = hm.normalize({"floors": 3, "floor_areas": [100, None, 50]})
    _, mean = hm.floors_state(p, [20.0, 22.0, 18.0], [None] * 3, None, None)
    assert mean == 20.0
    _, none = hm.floors_state(eq, [None] * 3, [None] * 3, None, None)
    assert none is None


def test_model_status_and_house_temp():
    ents = {"room": "sensor.ht", "room_humidity": "sensor.hh", "floor0_temp": "sensor.p0", "floor2_temp": "sensor.p2"}
    model = hm.HeatingModel({"enabled": True, "floors": 3, "room_floor": 1, "floor_areas": [100, 100, 50], "entities": ents})
    model.update({"room": "21.0", "room_humidity": "50", "floor0_temp": "20.0", "floor2_temp": "19.0"}, 100)
    st = model.status(100)
    assert [f["temp_c"] for f in st["floors"]] == [20.0, 21.0, 19.0]
    assert st["house_c"] == pytest.approx((2000 + 2100 + 950) / 250, abs=0.05)
    assert model.house_temp() == st["house_c"]
    single = hm.HeatingModel({"enabled": True, "entities": {"room": "sensor.ht"}})
    single.update({"room": "22.5"}, 100)
    assert single.house_temp() == 22.5 and len(single.status(100)["floors"]) == 1
    nothing = hm.HeatingModel({"enabled": True})
    nothing.update({}, 100)
    assert nothing.house_temp() is None


def test_floor_probes_never_raise_the_probe_fault_alarm():
    ents = {"boiler_out": "sensor.k", "floor0_temp": "sensor.p0"}
    model = hm.HeatingModel({"enabled": True, "floors": 2, "entities": ents})
    for t in range(0, 700, 10):
        model.update({"boiler_out": "30", "floor0_temp": "unavailable"}, t)
        model.evaluate_alarms(t)
    assert not model.active_alarms()          # a room sensor on battery is not boiler safety
