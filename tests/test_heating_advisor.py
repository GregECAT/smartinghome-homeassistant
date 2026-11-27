"""Firing advisor: building model fit, buffer depletion from a forecast, when to light, wood.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import math
import sys
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hm = importlib.import_module("smartinghome_pure.heating_model")
ha = importlib.import_module("smartinghome_pure.heating_advisor")

UA, BASE = 0.25, 0.3     # synthetic house: 250 W/K + 0.3 kW of hot water / losses


def _train(model, days=3, room=21.0):
    """A cooling buffer feeding the synthetic house, outdoor swinging 10 K over the day."""
    e = 120.0
    t = 0.0
    while t < days * 86400:
        t_out = -2.0 + 5.0 * math.sin(t / 86400 * 2 * math.pi)
        kw = UA * (room - t_out) + BASE
        e -= kw * 60 / 3600
        if e < 20:
            e = 120.0          # refilled by a fire …
            model.add(t, e, room, t_out, firing=True, pump_on=True)   # … which invalidates that hour
        else:
            model.add(t, e, room, t_out, firing=False, pump_on=True)
        t += 60
    return t


def test_building_model_learns_ua_and_base():
    m = ha.BuildingModel()
    _train(m)
    assert m.ua == pytest.approx(UA, rel=0.05)
    assert m.base == pytest.approx(BASE, abs=0.1)
    assert m.r2 is not None and m.r2 > 0.9
    restored = ha.BuildingModel()
    restored.restore(m.dump())
    assert restored.ua == pytest.approx(m.ua)


def test_building_model_needs_data_and_skips_fire_and_pump_off_hours():
    m = ha.BuildingModel()
    for h in range(10):
        for k in range(60):
            m.add(h * 3600 + k * 60, 100 - h - k / 60, 21, 0, firing=False, pump_on=True)
    assert m.ua is None and len(m.samples) == 9
    m2 = ha.BuildingModel()
    for k in range(180):
        m2.add(k * 60, 100 - k / 60, 21, 0, firing=False, pump_on=False)
    assert m2.samples == []


def test_depletion_and_demand_from_forecast():
    m = ha.BuildingModel()
    m.ua, m.base = 0.25, 0.0
    flat = [(h * 3600.0, 1.0) for h in range(0, 100)]
    empty_at, demand = ha.depletion(25.0, 0.0, m, 21.0, flat, None)
    assert demand == pytest.approx(0.25 * 20 * 24, rel=0.01)        # 5 kW × 24 h
    assert empty_at == pytest.approx(5 * 3600, rel=0.01)            # 25 kWh / 5 kW
    colder = [(h * 3600.0, -9.0) for h in range(0, 100)]
    empty2, _ = ha.depletion(25.0, 0.0, m, 21.0, colder, None)
    assert empty2 < empty_at
    assert ha.depletion(25.0, 0.0, ha.BuildingModel(), 21.0, flat, None) == (None, None)


def test_advise_model_basis_light_at_and_wood():
    cfg = hm.normalize({"buffer_l": 1000, "light_lead_h": 1.0, "burn_hours": 4})
    m = ha.BuildingModel()
    m.ua, m.base = 0.25, 0.0
    status = {"buffer": hm.buffer_state(cfg, 60.0, 45.0), "dhw": hm.dhw_state(cfg, 45.0),
              "boiler": {"firing": False}}
    fc = [(h * 3600.0, 1.0) for h in range(0, 100)]
    adv = ha.advise(cfg, status, m, 0.0, 21.0, fc, None, per_kg=3.2)
    assert adv["basis"] == "model"
    assert adv["light_at"] == pytest.approx(adv["empty_at"] - 3600)
    # the load for the lighting: the room the buffer will have by then (what the house takes until the light
    # time frees it) + the house during the burn — no more than the tanks soak up of the 20 kW a 25 kW boiler
    # gives above the house's 5 kW
    assert adv["wood_at"] == adv["light_at"]
    room = status["buffer"]["space_kwh"] - 3 * 1.14 + 5.0 * adv["light_at"] / 3600 + status["dhw"]["space_kwh"]
    expected = min(room + 5.0 * 4, room / (1 - 5.0 / 25.0)) / 3.2
    assert adv["wood_kg"] == round(expected)
    unlimited = ha.advise({**cfg, "boiler_kw": 0}, status, m, 0.0, 21.0, fc, None, per_kg=3.2)
    assert unlimited["wood_kg"] == round((room + 5.0 * 4) / 3.2)
    firing = ha.advise(cfg, {**status, "boiler": {"firing": True}}, m, 0.0, 21.0, fc, None, per_kg=3.2)
    assert firing["light_at"] is None


def _bobrek(charge_kwh, firing=True, fire_left=None, boiler_kw=24):
    """bobrek 27.11.2026 19:54: 1000 L buffer (59.3 kWh between 40 and 92 °C), hot water full, the house
    ~587 W/K at 24.5 °C inside and −0.5 °C outside (≈ 14.7 kW), 3.54 kWh/kg."""
    cfg = hm.normalize({"buffer_l": 1000, "burn_hours": 4, "boiler_kw": boiler_kw})
    m = ha.BuildingModel()
    m.ua, m.base = 0.587, 0.0
    status = {"buffer": {"energy_kwh": charge_kwh, "space_kwh": 59.3 - charge_kwh, "capacity_kwh": 59.3},
              "dhw": {"energy_kwh": 25.9, "space_kwh": 0.0}, "boiler": {"firing": firing}}
    fc = [(h * 3600.0, -0.5) for h in range(0, 100)]
    return ha.advise(cfg, status, m, 0.0, 24.5, fc, None, per_kg=3.54, fire_left_kwh=fire_left)


def test_a_warm_buffer_takes_only_what_the_boiler_gives_above_the_house():
    # "load 19 kg" at 87 %: 4.2 kWh of room + 59 kWh the house takes in 4 h — but a 24 kW boiler gives
    # ~9.3 kW more than the house takes, which fills the 4.2 kWh in under half an hour
    adv = _bobrek(51.7)
    h = 0.587 * 25.0
    share = 1 - h / 24.0
    room = 7.6 - 3 * 1.14
    assert adv["wood_kg"] == round(room / share / 3.54) == 3
    # a full load (the house's heat for 4 h, ~17 kg) fits once the buffer has dropped to about half
    assert adv["full_kg"] == round(h * 4 / 3.54) == 17
    assert not adv["full_now"]
    need = share * h * 4 - room                       # kWh the house must take out of the buffer first
    assert adv["full_at"] / 3600 == pytest.approx(need / h, abs=0.25)
    assert 45 <= adv["full_pct"] <= 55
    assert adv["boiler_kw"] == 24


def test_an_emptier_buffer_is_ready_for_a_full_load_now():
    adv = _bobrek(15.0)
    assert adv["full_now"] and adv["full_at"] == 0.0
    # room 40.9 kWh + the house's 58.7 kWh over 4 h: the burn time caps it, the boiler doesn't
    assert adv["wood_kg"] == round((59.3 - 15.0 - 3 * 1.14 + 0.587 * 25 * 4) / 3.54)


def test_the_weighed_wood_still_burning_takes_the_same_room():
    no_wood = _bobrek(30.0)
    with_wood = _bobrek(30.0, fire_left=10.0)
    assert with_wood["wood_kg"] < no_wood["wood_kg"]
    assert with_wood["full_at"] > no_wood["full_at"]


def test_a_house_taking_the_boilers_whole_power_caps_nothing():
    assert ha.surplus_share(30.0, 24.0) == 0.0 and ha.surplus_share(6.0, 24.0) == pytest.approx(0.75)
    assert ha.surplus_share(6.0, 0.0) == 1.0
    assert ha.load_kwh(4.0, 120.0, 4.0, 24.0) == 124.0          # 30 kW house, 24 kW boiler
    assert ha.load_kwh(4.0, 24.0, 4.0, 24.0) == pytest.approx(16.0 / 3)   # 6 kW house: 4 / 0.75
    assert ha.load_kwh(4.0, 24.0, 4.0, 0.0) == 28.0             # no power known: room + house, as before


def test_a_cold_house_without_a_fire_loads_for_the_lighting():
    adv = _bobrek(51.7, firing=False)
    assert adv["full_kg"] is None and adv["light_at"] is not None
    # lit an hour before the buffer runs out: ~41 kWh of room + the house's 59 kWh over 4 h (the boiler's cap,
    # 41 / 0.39 = 106 kWh, doesn't bind) → 28 kg
    h = 0.587 * 25.0
    room = 59.3 - 3 * 1.14 - h * 1.0
    assert adv["wood_at"] == adv["light_at"]
    assert adv["wood_kg"] == round((room + h * 4) / 3.54) == 28


def test_boiler_power_setting():
    assert hm.normalize({})["boiler_kw"] == 25.0
    assert hm.normalize({"boiler_kw": 2})["boiler_kw"] == 5.0
    assert hm.normalize({"boiler_kw": "24"})["boiler_kw"] == 24.0


def test_advise_trend_fallback_without_model():
    cfg = hm.normalize({})
    status = {"buffer": hm.buffer_state(cfg, 70.0, 60.0), "dhw": None, "boiler": {"firing": False},
              "runtime_h": 10.0, "discharge_kw": 2.0}
    adv = ha.advise(cfg, status, ha.BuildingModel(), 1000.0, 21.0, [], 0.0, per_kg=3.2)
    assert adv["basis"] == "trend" and adv["empty_at"] == 1000.0 + 36000.0
    assert adv["wood_kg"] > 0


def test_learned_kwh_per_kg_needs_two_weighed_loads():
    s = [{"wood_kg": 15, "stored_kwh": 40, "house_kwh": 8}]
    assert ha.learned_kwh_per_kg(s) is None
    s.append({"wood_kg": 20, "stored_kwh": 50, "house_kwh": 14})
    s.append({"stored_kwh": 30})               # not weighed — ignored
    assert ha.learned_kwh_per_kg(s) == pytest.approx((48 / 15 + 64 / 20) / 2, abs=0.01)


def test_temp_at_interpolates():
    fc = [(0.0, 0.0), (3600.0, 10.0)]
    assert ha.temp_at(fc, 1800.0, None) == 5.0
    assert ha.temp_at(fc, 7200.0, None) == 10.0
    assert ha.temp_at([], 5.0, 3.0) == 3.0


def test_monthly_summary_and_log_load():
    model = hm.HeatingModel({"enabled": True})
    model.sessions = [
        {"start": 1_700_000_000, "duration_h": 4, "stored_kwh": 40, "wood_est_kg": 12},
        {"start": 1_700_090_000, "duration_h": 3.5, "stored_kwh": 35, "wood_est_kg": 11, "end": 1_700_100_000},
    ]
    where, fire = model.log_load(14.0, 1_700_100_000 + 3600)
    assert where == "last" and fire["wood_kg"] == 14.0
    months = hm.monthly_summary(model.sessions, 3600, price_kg=0.6)
    assert len(months) == 1 and months[0]["fires"] == 2
    assert months[0]["wood_kg"] == 26 and months[0]["weighed"] == 1 and months[0]["cost"] == pytest.approx(16)
    assert model.log_load(5.0, 1_700_100_000 + 13 * 3600) is None     # too long after the fire
