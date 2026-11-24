# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Deye through ha-solarman (HACS, profile deye_p3) — read only.

Finds the ha-solarman entities by unique_id, which ha-solarman builds as
slugify(f"{config_entry_id}_{name}_{platform}") — independent of the device name
the user picked (default "Inverter"). Nothing here writes to the inverter.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from . import deye_tou as tou

SOLARMAN_PLATFORM = "solarman"
_CACHE_S = 300.0

# Smarting HOME sensor_map key → ha-solarman deye_p3 entity key. Only keys whose meaning
# and sign match Smarting HOME's convention. Grid and battery power (Deye: + import,
# + discharge) and phase powers/currents are left to the user until the sign is checked
# on a real installation.
DEYE_SENSOR_KEYS: dict[str, str] = {
    "pv_power": "pv_power_sensor",
    "load_power": "load_power_sensor",
    "battery_soc": "battery_sensor",
    "pv1_power": "pv1_power_sensor",
    "pv1_voltage": "pv1_voltage_sensor",
    "pv1_current": "pv1_current_sensor",
    "pv2_power": "pv2_power_sensor",
    "pv2_voltage": "pv2_voltage_sensor",
    "pv2_current": "pv2_current_sensor",
    "pv3_power": "pv3_power_sensor",
    "pv4_power": "pv4_power_sensor",
    "battery_voltage": "battery_voltage_sensor",
    "battery_temp": "battery_temperature_sensor",
    "voltage_l1": "grid_l1_voltage_sensor",
    "voltage_l2": "grid_l2_voltage_sensor",
    "voltage_l3": "grid_l3_voltage_sensor",
    "grid_frequency": "grid_frequency_sensor",
    "pv_today": "today_production_sensor",
    "grid_import_today": "today_energy_import_sensor",
    "grid_export_today": "today_energy_export_sensor",
    "battery_charge_today": "today_battery_charge_sensor",
    "battery_discharge_today": "today_battery_discharge_sensor",
    "inverter_temp": "dc_temperature_sensor",
}

_TOU_MARKER = "program_1_time_time"
_cache: dict[int, tuple[float, int, dict[str, str]]] = {}


def solarman_entities(hass: HomeAssistant) -> tuple[dict[str, str], int]:
    """(ha-solarman key → entity_id, number of Deye inverters with a Time of Use table).

    The map belongs to the one inverter with a TOU table (or to the only ha-solarman
    device); it is empty when there is none or more than one.
    """
    now = time.monotonic()
    cached = _cache.get(id(hass))
    if cached and now - cached[0] < _CACHE_S:
        return cached[2], cached[1]
    by_entry: dict[str, dict[str, str]] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.platform != SOLARMAN_PLATFORM or not entry.config_entry_id or entry.disabled_by:
            continue
        prefix = slugify(entry.config_entry_id) + "_"
        unique_id = str(entry.unique_id or "")
        if unique_id.startswith(prefix):
            by_entry.setdefault(entry.config_entry_id, {})[unique_id[len(prefix):]] = entry.entity_id
    with_tou = [m for m in by_entry.values() if _TOU_MARKER in m]
    if len(with_tou) == 1:
        result = with_tou[0]
    elif not with_tou and len(by_entry) == 1:
        result = next(iter(by_entry.values()))
    else:
        result = {}
    _cache[id(hass)] = (now, len(with_tou), result)
    return result, len(with_tou)


def resolve_sensor(hass: HomeAssistant, key: str, entity_id: str, default_id: str) -> str:
    """The ha-solarman entity for a sensor_map key, when the map still holds a Deye
    default that does not exist (the defaults predate ha-solarman's naming)."""
    solarman_key = DEYE_SENSOR_KEYS.get(key)
    if not solarman_key or entity_id != default_id:
        return entity_id  # the user picked this entity — keep it
    if entity_id and (
        hass.states.get(entity_id) is not None or er.async_get(hass).async_get(entity_id) is not None
    ):
        return entity_id
    found = solarman_entities(hass)[0].get(solarman_key)
    return found or entity_id


def _state(hass: HomeAssistant, entities: dict[str, str], key: str) -> Any:
    entity_id = entities.get(key)
    state = hass.states.get(entity_id) if entity_id else None
    return state.state if state else None


def _clock_drift_s(hass: HomeAssistant, entities: dict[str, str]) -> float | None:
    """Inverter clock − the time it was read (ha-solarman reads it every 60 s)."""
    entity_id = entities.get("date_time_datetime") or next(
        (e for e in entities.values() if e.startswith("datetime.")), None
    )
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    value = dt_util.parse_datetime(state.state)
    if value is None:
        return None
    return (value - state.last_updated).total_seconds()


def read_table(hass: HomeAssistant, entities: dict[str, str]) -> tou.TouTable:
    slots = tuple(
        tou.Slot(
            start=tou.parse_time(_state(hass, entities, f"program_{n}_time_time")),
            soc=tou.parse_int(_state(hass, entities, f"program_{n}_soc_number")),
            charging=tou.parse_option(_state(hass, entities, f"program_{n}_charging_select")),
        )
        for n in range(1, tou.SLOT_COUNT + 1)
    )
    return tou.TouTable(
        slots=slots,
        enabled=tou.parse_tou_enabled(_state(hass, entities, "time_of_use_select")),
        grid_charging=tou.parse_switch(_state(hass, entities, "battery_grid_charging_switch")),
        low_soc=tou.parse_int(_state(hass, entities, "battery_low_soc_number")),
        clock_drift_s=_clock_drift_s(hass, entities),
    )


class DeyePreview:
    """The Deye controller in shadow mode: what it would write for the current intent."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.shadow = tou.TouShadow()
        self.last_change: dict[str, Any] | None = None
        self._last_problems: tuple[str, ...] = ()

    def update(self, intent: str, now: datetime | None = None) -> list[str]:
        """Recompute; returns new lines for the Autopilot decision log."""
        now = now or dt_util.now()
        entities, inverters = solarman_entities(self.hass)
        notes: list[str] = []
        if not entities or _TOU_MARKER not in entities:
            problem = (
                "Znaleziono kilka falowników Deye w ha-solarman — podgląd obsługuje jeden."
                if inverters > 1
                else "Nie znaleziono harmonogramu Time of Use z integracji ha-solarman (profil deye_p3)."
            )
            self.shadow.last = tou.ShadowResult(available=False, intent=intent, problems=[problem])
        else:
            soc_now = tou.parse_float(_state(self.hass, entities, "battery_sensor"))
            result = self.shadow.update(read_table(self.hass, entities), intent=intent, soc_now=soc_now, now=now)
            if result.writes:
                self.last_change = {
                    "at": now.strftime("%H:%M"),
                    "intent_label": tou.INTENT_LABELS.get(intent, intent),
                    "writes": [w.describe() for w in result.writes],
                }
                notes.append(tou.describe_writes(result))
        problems = tuple(self.shadow.last.problems)
        if problems != self._last_problems:
            self._last_problems = problems
            notes.extend(f"Deye — podgląd: {p}" for p in problems)
        return notes

    def status(self) -> dict[str, Any]:
        data = self.shadow.last.as_dict()
        data["last_change"] = self.last_change
        return data
