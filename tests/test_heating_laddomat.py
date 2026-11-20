"""The Laddomat pump as the diagram shows it: started by the boiler's thermostat, inferred from the outlet.

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


def test_threshold_from_profile_or_setting():
    assert hm.normalize({})["laddomat_on_c"] == 75.0                       # Atmos profile
    assert hm.normalize({"profile": "generic"})["laddomat_on_c"] == 72.0
    assert hm.normalize({"laddomat_on_c": 70})["laddomat_on_c"] == 70.0
    assert hm.normalize({"laddomat_on_c": 150})["laddomat_on_c"] == 90.0    # clamped
    assert hm.to_settings({})["laddomat_on_c"] is None                      # follows a later profile change


def test_laddomat_follows_the_outlet_with_hysteresis():
    model = hm.HeatingModel({"enabled": True, "entities": {"boiler_out": "sensor.k"}})
    states = []
    for i, t in enumerate([60, 65, 69.9, 74.9, 75.0, 73.0, 72.1, 71.9, 76.0]):
        model.update({"boiler_out": str(t)}, 100.0 + 10 * i)
        states.append(model.laddomat_on)
    assert states == [False, False, False, False, True, True, True, False, True]
    assert model.status(200.0)["laddomat"] == {"on": True, "on_c": 75.0}


def test_no_reading_no_guess():
    model = hm.HeatingModel({"enabled": True, "entities": {"boiler_out": "sensor.k"}})
    model.update({"boiler_out": "unavailable"}, 100.0)
    assert model.laddomat_on is None
