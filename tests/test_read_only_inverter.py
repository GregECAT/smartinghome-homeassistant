"""Deye / Growatt are read only: no EnergyManager path writes to an inverter; Deye gets a
Time of Use write preview from the ha-solarman entities.

Run: python3 -m pytest tests/  (no Home Assistant needed — the HA names are stubbed)
"""
from __future__ import annotations

import asyncio
import importlib
import re
import sys
import time
import types
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"


def _ensure(name: str, **attrs) -> types.ModuleType:
    """The module if present (real or another test's stub), else a stub; adds missing names."""
    mod = sys.modules.get(name)
    if mod is None:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            mod = sys.modules[name] = types.ModuleType(name)
    for key, value in attrs.items():
        if not hasattr(mod, key):
            setattr(mod, key, value)
    return mod


def _slugify(text: str, separator: str = "_") -> str:
    return re.sub(r"[^a-z0-9]+", separator, str(text).lower()).strip(separator)


class _HomeAssistantError(Exception):
    pass


_ensure("homeassistant")
_ensure("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_ensure("homeassistant.exceptions", HomeAssistantError=_HomeAssistantError)
_er = _ensure("homeassistant.helpers.entity_registry", async_get=lambda hass: hass.registry)
_ensure("homeassistant.helpers", entity_registry=_er)
_dt = _ensure("homeassistant.util.dt", now=datetime.now, parse_datetime=datetime.fromisoformat)
_ensure("homeassistant.util", dt=_dt, slugify=_slugify)

_pkg = types.ModuleType("smartinghome_ro")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_ro", _pkg)
em_mod = importlib.import_module("smartinghome_ro.energy_manager")
deye_ha = importlib.import_module("smartinghome_ro.deye_ha")
const = importlib.import_module("smartinghome_ro.const")


# ── fake Home Assistant ──


class _State:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}
        self.last_updated = None


class _States:
    def __init__(self, values):
        self._values = dict(values)

    def get(self, entity_id):
        value = self._values.get(entity_id)
        return value if value is None or isinstance(value, _State) else _State(value)


class _Services:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data=None, **_kw):
        self.calls.append((domain, service, data or {}))


class _Registry:
    def __init__(self, entries=()):
        self.entities = {e.entity_id: e for e in entries}

    def async_get(self, entity_id):
        return self.entities.get(entity_id)


def _entry(entity_id, platform, unique_id="", config_entry_id="", options=None):
    return types.SimpleNamespace(
        entity_id=entity_id, platform=platform, unique_id=unique_id,
        config_entry_id=config_entry_id, disabled_by=None, domain=entity_id.split(".")[0],
        device_id="dev1", capabilities={"options": options} if options else None,
    )


class _Hass:
    def __init__(self, states=None, entries=()):
        self.states = _States(states or {})
        self.services = _Services()
        self.registry = _Registry(entries)

    def async_create_task(self, coro):
        coro.close()


# GoodWe entities present, so without the read-only gate a Deye install would write to them
_GOODWE_STATES = {
    "number.goodwe_eco_mode_soc": "100",
    "number.goodwe_eco_mode_power": "0",
    "number.goodwe_depth_of_discharge_on_grid": "90",
    "number.goodwe_grid_export_limit": "10000",
    "select.goodwe_tryb_pracy_falownika": "general",
}
_INVERTER_DOMAINS = {"goodwe", "modbus", "number", "select"}


def _manager(brand, hass=None):
    hass = hass or _Hass(_GOODWE_STATES)
    em_mod.er.async_get = lambda h: h.registry
    return em_mod.EnergyManager(hass, "dev1", inverter_brand=brand), hass


async def _every_write_path(em):
    for mode in const.HEMSMode:
        await em.set_mode(mode)
    await em.charge_pv_only()
    await em.charge_from_grid(power_w=3000)
    await em.force_charge()
    await em.force_discharge(power_w=3000)
    await em.stop_force_charge()
    await em.stop_force_discharge()
    await em.battery_to_home()
    await em.pv_export()
    await em.battery_hold()
    await em.set_general_mode(charging=False)
    await em.emergency_stop()
    await em.force_custom(work_mode="eco_charge", modbus_47511=1, charge_current="18",
                          export_limit=0, eco_mode_power=100, eco_mode_soc=100)
    await em.set_export_limit(3000)
    await em.set_export_cap(1000)
    await em.set_export_cap(None)
    await em.check_voltage_protection(256.0, 250.0, 250.0, 50.0)
    await em.check_soc_safety(5.0, 3, 0.0)
    await em.check_night_arbitrage(20.0, 2, 1.0)
    await em._set_dod(70)  # the strategy controller calls these directly
    await em._enable_charging()
    await em._block_charging()


def test_read_only_brands_write_nothing_to_the_inverter():
    for brand in (const.INVERTER_BRAND_DEYE, const.INVERTER_BRAND_GROWATT):
        em, hass = _manager(brand)
        asyncio.run(_every_write_path(em))
        writes = [c for c in hass.services.calls if c[0] in _INVERTER_DOMAINS]
        assert writes == [], f"{brand}: {writes[:3]}"
        assert em.read_only and "tylko odczytuje" in em.read_only_message


def test_goodwe_still_writes():
    em, hass = _manager(const.INVERTER_BRAND_GOODWE)
    asyncio.run(em.charge_from_grid())
    domains = {c[0] for c in hass.services.calls}
    assert {"goodwe", "number", "select"} <= domains
    assert not em.read_only


def test_intent_still_tracks_the_plan_on_read_only_brands():
    em, _ = _manager(const.INVERTER_BRAND_DEYE)
    asyncio.run(em.charge_from_grid())
    assert em.intent == "charge_grid"
    asyncio.run(em.battery_hold())
    assert em.intent == "hold"
    status = em.control_status()
    assert status["read_only"] and status["intent"] == "hold"


# ── Deye preview from ha-solarman entities ──

_CE = "01JTESTDEYE"


def _solarman(entity_id, key):
    return _entry(entity_id, "solarman", unique_id=f"{_CE.lower()}_{key}", config_entry_id=_CE)


def _deye_hass(soc="62"):
    times = ["00:00:00", "06:00:00", "13:00:00", "15:00:00", "22:00:00", "23:00:00"]
    socs = ["80", "20", "30", "20", "80", "80"]
    charging = ["Grid", "Disabled", "Disabled", "Disabled", "Grid", "Grid"]
    states, entries = {}, []
    for n in range(1, 7):
        for key, domain, value in (
            (f"program_{n}_time_time", "time", times[n - 1]),
            (f"program_{n}_soc_number", "number", socs[n - 1]),
            (f"program_{n}_charging_select", "select", charging[n - 1]),
        ):
            eid = f"{domain}.inverter_{key.rsplit('_', 1)[0]}"
            states[eid] = value
            entries.append(_solarman(eid, key))
    for eid, key, value in (
        ("select.inverter_time_of_use", "time_of_use_select", "Week"),
        ("switch.inverter_battery_grid_charging", "battery_grid_charging_switch", "on"),
        ("number.inverter_battery_low_soc", "battery_low_soc_number", "10"),
        ("sensor.inverter_battery", "battery_sensor", soc),
        ("sensor.inverter_pv_power", "pv_power_sensor", "4200"),
    ):
        states[eid] = value
        entries.append(_solarman(eid, key))
    return _Hass(states, entries)


def _fixed_now(monkeypatch, hour, minute=0):
    fake_dt = types.SimpleNamespace(
        now=lambda: datetime(2026, 11, 24, hour, minute),
        parse_datetime=datetime.fromisoformat,
    )
    monkeypatch.setattr(deye_ha, "dt_util", fake_dt)
    monkeypatch.setattr(deye_ha.er, "async_get", lambda h: h.registry)
    deye_ha._cache.clear()


def test_deye_preview_shows_the_running_slot_and_writes_nothing(monkeypatch):
    _fixed_now(monkeypatch, 14)
    hass = _deye_hass()
    em, _ = _manager(const.INVERTER_BRAND_DEYE, hass)
    em._started = time.monotonic() - 1000  # past the startup grace
    asyncio.run(em.charge_from_grid())
    assert asyncio.run(em.async_watchdog("")) is None
    preview = em.control_status()["preview"]
    assert preview["available"] and preview["slot"] == 3 and preview["window"] == "13:00–15:00"
    assert preview["target_soc"] == 100 and preview["target_charging"] == "Grid"
    assert preview["writes_today"] == 2 and preview["last_change"]["writes"]
    notes = em.pop_inverter_notes()
    assert notes and "nic nie zapisano" in notes[0]
    assert em.pop_inverter_notes() == []
    assert [c for c in hass.services.calls if c[0] in _INVERTER_DOMAINS | {"solarman"}] == []


def test_deye_preview_hold_uses_soc_at_start(monkeypatch):
    _fixed_now(monkeypatch, 9, 30)
    em, _ = _manager(const.INVERTER_BRAND_DEYE, _deye_hass(soc="62.4"))
    em._started = time.monotonic() - 1000
    asyncio.run(em.battery_hold())
    asyncio.run(em.async_watchdog(""))
    preview = em.control_status()["preview"]
    assert preview["slot"] == 2 and preview["target_soc"] == 63 and preview["target_charging"] == "Disabled"


def test_deye_preview_without_ha_solarman_reports_a_problem(monkeypatch):
    _fixed_now(monkeypatch, 14)
    em, _ = _manager(const.INVERTER_BRAND_DEYE, _Hass())
    em._started = time.monotonic() - 1000
    asyncio.run(em.async_watchdog(""))
    preview = em.control_status()["preview"]
    assert not preview["available"] and "ha-solarman" in preview["problems"][0]
    assert any("ha-solarman" in n for n in em.pop_inverter_notes())
    asyncio.run(em.async_watchdog(""))
    assert em.pop_inverter_notes() == []  # the same problem is logged once


def test_deye_sensor_defaults_resolve_to_ha_solarman(monkeypatch):
    _fixed_now(monkeypatch, 14)
    hass = _deye_hass()
    default = const.DEFAULT_SENSOR_MAP_DEYE["battery_soc"]
    assert deye_ha.resolve_sensor(hass, "battery_soc", default, default) == "sensor.inverter_battery"
    assert deye_ha.resolve_sensor(hass, "pv_power", "sensor.deye_pv_power", "sensor.deye_pv_power") == (
        "sensor.inverter_pv_power"
    )
    # The user's own pick stays, and power signs are not guessed
    assert deye_ha.resolve_sensor(hass, "battery_soc", "sensor.my_soc", default) == "sensor.my_soc"
    grid = const.DEFAULT_SENSOR_MAP_DEYE["grid_power"]
    assert deye_ha.resolve_sensor(hass, "grid_power", grid, grid) == grid
