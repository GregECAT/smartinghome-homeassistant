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


def test_view_only_storey_is_shown_but_left_out_of_the_mean():
    cfg = hm.normalize({"floors": 3, "room_floor": 1, "floor_areas": [105, 105, 40],
                        "floor_view_only": [False, False, True]})
    assert cfg["floor_view_only"] == [False, False, True]
    floors, mean = hm.floors_state(cfg, [None, None, 31.2], [None] * 3, 24.0, None)
    assert floors[2]["temp_c"] == 31.2 and floors[2]["view_only"] is True
    assert mean == 24.0                      # the radiator head does not pull the house up
    model = hm.HeatingModel({"enabled": True, "floors": 3, "room_floor": 1, "floor_view_only": [False, False, True],
                             "entities": {"room": "sensor.ht", "floor2_temp": "sensor.trv"}})
    model.update({"room": "24.0", "floor2_temp": "31.2"}, 100)
    assert model.house_temp() == 24.0


def test_view_only_needs_the_storeys_own_sensor():
    cfg = hm.normalize({"floors": 2, "room_floor": 0, "floor_view_only": [True, False]})
    floors, mean = hm.floors_state(cfg, [None, 22.0], [None] * 2, 21.0, None)
    assert floors[0]["source"] == "room" and floors[0]["view_only"] is False
    assert mean == 21.5
    assert hm.normalize({"floor_view_only": "yes"})["floor_view_only"] == [False, False, False]


def test_reference_is_picked_from_the_storeys():
    # heat by a storey: the reference follows that storey's sensor and its offset
    c = hm.normalize({"floors": 2, "room_ref": "floor", "room_floor": 1, "offsets": {"floor1_temp": 0.5},
                      "entities": {"room": "sensor.old", "floor0_temp": "sensor.p0", "floor1_temp": "sensor.p1",
                                   "floor1_humidity": "sensor.h1"}})
    assert c["room_ref"] == "floor"
    assert c["entities"]["room"] == "sensor.p1" and c["entities"]["room_humidity"] == "sensor.h1"
    assert c["offsets"]["room"] == 0.5
    # the storey without a sensor: no reference (the panel lists it as „brak czujnika”)
    c = hm.normalize({"floors": 2, "room_ref": "floor", "room_floor": 1, "entities": {"floor0_temp": "sensor.p0"}})
    assert c["entities"]["room"] == ""
    # a sensor picked by hand stays as it is; "none" heats by the curve only
    c = hm.normalize({"floors": 2, "room_ref": "custom", "entities": {"room": "sensor.salon", "floor0_temp": "sensor.p0"}})
    assert c["room_ref"] == "custom" and c["entities"]["room"] == "sensor.salon"
    c = hm.normalize({"floors": 2, "room_ref": "none", "entities": {"room": "sensor.salon", "room_humidity": "sensor.h"}})
    assert c["entities"]["room"] == "" and c["entities"]["room_humidity"] == ""
    floors, _ = hm.floors_state(c, [20.0, 21.0], [None] * 2, None, None)
    assert not any(f["ref"] for f in floors)
    assert hm.to_settings({"floors": 2, "room_ref": "custom"})["room_ref"] == "custom"


def test_one_storey_keeps_the_reference_as_picked():
    c = hm.normalize({"entities": {"room": "sensor.ht"}})
    assert c["room_ref"] == "floor" and c["entities"]["room"] == "sensor.ht"
    # 1 → 2 storeys: the reference moves onto its storey instead of getting lost
    c = hm.normalize({"floors": 2, "room_ref": "floor", "entities": {"room": "sensor.ht", "room_humidity": "sensor.hh"},
                      "offsets": {"room": -0.3}})
    assert c["entities"]["floor0_temp"] == "sensor.ht" and c["entities"]["floor0_humidity"] == "sensor.hh"
    assert c["entities"]["room"] == "sensor.ht" and c["offsets"]["floor0_temp"] == -0.3


def test_older_settings_keep_heating_by_the_same_sensor():
    # bobrek 24.11.2026: the parter sensor as the reference, the parter storey left empty
    c = hm.normalize({"floors": 3, "room_floor": 0, "entities": {
        "room": "sensor.parter", "room_humidity": "sensor.parter_h", "floor1_temp": "sensor.p1", "floor2_temp": "sensor.trv"}})
    assert c["room_ref"] == "floor" and c["room_floor"] == 0
    assert c["entities"]["floor0_temp"] == "sensor.parter" and c["entities"]["floor0_humidity"] == "sensor.parter_h"
    assert c["entities"]["room"] == "sensor.parter" and c["entities"]["floor1_temp"] == "sensor.p1"
    # the reference is another storey's sensor → that storey becomes the reference
    c = hm.normalize({"floors": 2, "room_floor": 0, "entities": {"room": "sensor.p1", "floor0_temp": "sensor.p0",
                                                                 "floor1_temp": "sensor.p1"}})
    assert c["room_ref"] == "floor" and c["room_floor"] == 1 and c["entities"]["room"] == "sensor.p1"
    # a reference apart from the storey sensors stays picked by hand
    c = hm.normalize({"floors": 2, "entities": {"room": "sensor.salon", "floor0_temp": "sensor.p0"}})
    assert c["room_ref"] == "custom" and c["entities"]["room"] == "sensor.salon"
    # storey sensors and no reference: no reference still (no room control switched on behind the user's back)
    c = hm.normalize({"floors": 2, "entities": {"floor0_temp": "sensor.p0"}})
    assert c["room_ref"] == "none" and c["entities"]["room"] == ""
