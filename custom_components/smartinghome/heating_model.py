# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Solid-fuel (wood gasification) boiler with a heat buffer — measurement model.

Pure Python, no Home Assistant imports (the HA side is heating_ha.py).

Read only: this module never switches anything. Home Assistant is never the
boiler's safety layer — the thermal discharge valve on the cooling loop, the
boiler's own thermostats and the Laddomat keep the boiler safe without power,
Wi-Fi or HA. Here we measure, explain and warn:

- probe filter for DS18B20 probes (Shelly Plus Add-on): −127 = no reading,
  85.0 = power-on reset value (also a real boiler temperature, so it is accepted
  only when it fits the trend), single-sample jumps are dropped; the flue-gas
  sensor (thermocouple / PT100 with a transmitter) gets a wide range of its own,
- buffer and hot-water tank energy from two / one probes (layered linear profile),
  room left in the buffer and the wood load it can still take without overheating,
- boiler state from the outlet temperature and its trend (cold → ignition →
  burning → burn-out → idle, overheat), each fire is a session; with a flue-gas
  sensor the fire starts and ends by the flue (the water lags by an hour or more),
- how long the buffer lasts at the current draw,
- alarms with hold times and hysteresis (overheat, buffer full, heat not reaching
  the buffer, probe fault, CH / hot-water pump not drawing power, room cold with an
  empty buffer, heat flowing into the hot-water coil by gravity).

Settings "heating" (settings.json): see DEFAULTS / normalize().
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# ── constants ────────────────────────────────────────────────────────────────

WATER_KWH_PER_M3K = 1.14     # hot water (~60 °C): ρ·c ≈ 983 kg/m³ × 4.185 kJ/(kg·K)
WOOD_KWH_PER_KG = 4.0        # seasoned wood, ~20 % moisture (lower heating value)
HEAT_REF_C = 20.0            # heat content reference for measuring the buffer's draw
LAYERS = 20                  # buffer is integrated in horizontal layers

PROFILES: dict[str, dict[str, Any]] = {
    "atmos_dc": {
        "label": "Atmos DC…S / DC…GD (zgazowujący, wentylator wyciągowy)",
        "warn_c": 88.0, "alarm_c": 92.0, "tzb_c": 95.0, "min_return_c": 65.0, "efficiency": 0.80,
        "laddomat_on_c": 75.0,
    },
    "generic": {
        "label": "Inny kocioł zgazowujący drewno",
        "warn_c": 88.0, "alarm_c": 92.0, "tzb_c": 95.0, "min_return_c": 60.0, "efficiency": 0.78,
        "laddomat_on_c": 72.0,
    },
}

# role → (label, kind). Temperatures are DS18B20 / any °C sensor.
ROLES: dict[str, tuple[str, str]] = {
    "boiler_out": ("Kocioł — wyjście (przy Laddomacie)", "temperature"),
    # a thermocouple / PT100 + transmitter in the flue pipe (0–500 °C, not a DS18B20): fire on / off at once
    "flue_gas": ("Spaliny", "temperature"),
    "buffer_top": ("Bufor — góra", "temperature"),
    "buffer_bottom": ("Bufor — dół", "temperature"),
    "dhw": ("Zasobnik CWU", "temperature"),
    # the pipe feeding the tank's coil, right at the tank: warm with the pump stopped = gravity flow
    "dhw_coil_supply": ("Zasilanie wężownicy CWU (przy zasobniku)", "temperature"),
    "heating_supply": ("Zasilanie CO (za zaworem)", "temperature"),
    "heating_return": ("Powrót z grzejników", "temperature"),
    "room": ("Czujnik odniesienia — temperatura", "temperature"),
    "room_humidity": ("Czujnik odniesienia — wilgotność", "humidity"),
    # one sensor per storey; with storeys the reference is normally one of them (room_ref "floor"):
    # the house heats by the sensor of storey room_floor, entities.room follows it
    "floor0_temp": ("Kondygnacja 1 — temperatura", "temperature"),
    "floor0_humidity": ("Kondygnacja 1 — wilgotność", "humidity"),
    "floor1_temp": ("Kondygnacja 2 — temperatura", "temperature"),
    "floor1_humidity": ("Kondygnacja 2 — wilgotność", "humidity"),
    "floor2_temp": ("Kondygnacja 3 — temperatura", "temperature"),
    "floor2_humidity": ("Kondygnacja 3 — wilgotność", "humidity"),
    "outdoor": ("Temperatura zewnętrzna", "temperature"),
    "pump_switch": ("Pompa CO — przekaźnik", "switch"),
    "pump_power": ("Pompa CO — moc", "power"),
    "dhw_pump_switch": ("Pompa CWU — przekaźnik", "switch"),
    "dhw_pump_power": ("Pompa CWU — moc", "power"),
    # e.g. a unit heater in the garage hung on the CH circuit behind the pump: its fan runs with the pump
    "fan_switch": ("Nagrzewnica — wentylator (pracuje razem z pompą CO)", "switch"),
    "fan_power": ("Nagrzewnica — moc wentylatora", "power"),
    # the room with the unit heater (e.g. the garage) — shown on the drawing only; any °C sensor there will do,
    # bobrek: the PV battery standing in the garage (its BMS temperature)
    "fan_room_temp": ("Nagrzewnica — temperatura w pomieszczeniu (np. garaż)", "temperature"),
}
TEMP_ROLES = tuple(r for r, (_, kind) in ROLES.items() if kind == "temperature")
PROBE_ROLES = ("boiler_out", "buffer_top", "buffer_bottom", "dhw", "heating_supply", "heating_return",
               "flue_gas", "dhw_coil_supply")

# safety_dump: overheating heat goes into the house through the mixing valve — fine for radiators,
# never for a floor (screed, no overtemperature cut-out of its own)
EMITTERS = {
    "radiators": {"label": "Grzejniki", "buffer_min_c": 40.0, "supply_max_c": 75.0,
                  "curve_slope": 1.4, "safety_dump": True},
    "floor": {"label": "Ogrzewanie podłogowe", "buffer_min_c": 30.0, "supply_max_c": 45.0,
              "curve_slope": 0.5, "safety_dump": False},
    "mixed": {"label": "Grzejniki + podłogówka", "buffer_min_c": 40.0, "supply_max_c": 75.0,
              "curve_slope": 1.2, "safety_dump": False},
}
# panel emergency switches: element → never switched on while locked (automation and safety rules alike)
LOCK_ELEMENTS = ("pump", "dhw_pump", "valve")
# pumps also have the HAND position (AUTO / 0 / HAND, like the switch in the box): on whatever the mode
FORCE_ELEMENTS = ("pump", "dhw_pump")

MODES = {
    "off": "Wyłączone — tylko pomiary",
    "manual": "Ręczne",
    "auto": "Automatyczne",
    "summer": "Lato (bez grzania domu)",
}
VALVE_TYPES = {
    "none": "Brak / ręczny zawór",
    "cover": "Siłownik jako roleta (Shelly 2PM w trybie Cover)",
    "switches": "Dwa przekaźniki (otwórz / zamknij)",
}
MAX_FLOORS = 3
FLOOR_NAMES = ("Parter", "1. piętro", "Poddasze")
FAN_NAME = "Nagrzewnica"
# probe correction (K): a probe strapped to a pipe can read 10 K low until its mounting is fixed
OFFSET_MAX_K = 15.0

# where the hot-water tank's coil takes its heat from: the probe compared with the tank
DHW_SOURCES = {
    "buffer_top": "Z bufora",
    "boiler_out": "Prosto z kotła",
}

# how the hot-water pump decides: by the tank probe (differential), or by the clock (no tank probe needed)
DHW_MODES = {
    "auto": "Według sondy CWU",
    "schedule": "Według godzin",
}

DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "profile": "atmos_dc",
    "emitters": "radiators",
    "entities": {role: "" for role in ROLES},
    "offsets": {role: 0.0 for role in TEMP_ROLES},
    "buffer_l": 1000.0,
    "buffer_max_c": 90.0,       # buffer "full" (whole tank at this temperature)
    "buffer_min_c": None,       # None → emitters default (useful heat ends here)
    "buffer_full_c": 88.0,      # top of the buffer: "don't add wood" warning
    "probe_top_h": 0.85,        # probe heights as a fraction of the tank height
    "probe_bottom_h": 0.15,
    "dhw_l": 500.0,
    "dhw_cold_c": 10.0,
    "dhw_target_c": 55.0,
    "room_target_c": 21.0,
    # storeys: the house shown floor by floor, its mean temperature weighted by floor area
    "lockout": {"pump": False, "dhw_pump": False, "valve": False},
    "force_on": {"pump": False, "dhw_pump": False},
    "floors": 1,
    "floor_names": list(FLOOR_NAMES),
    "floor_areas": [None, None, None],   # m² (empty → equal weights)
    "floor_view_only": [False, False, False],   # shown, but left out of the house mean (e.g. a radiator head)
    "room_floor": 0,            # the storey of the reference room (index)
    "room_ref": "floor",        # with storeys, heat by: "floor" its sensor, "custom" entities.room, "none" the curve only
    "fan_name": FAN_NAME,       # the unit heater whose fan follows the CH pump (shown in the panel)
    "fan_off": False,           # the heater fan switched off from the panel: it stays off, heat dumps aside
    "fan_quiet": False,         # the heater fan stays off at night (fan_quiet_from–fan_quiet_to), heat dumps aside
    "fan_quiet_from": 23,
    "fan_quiet_to": 7,
    "warn_c": None,             # None → profile
    "alarm_c": None,
    "laddomat_on_c": None,      # None → profile; the boiler's own thermostat starts the Laddomat pump here
    "dump_top_c": None,         # heat dump by the buffer: None → by the boiler and buffer together (boiler ≥ alarm − 2, or
                                # ≥ warn with a full buffer, or top ≥ full + 4); a number → once the buffer's TOP is this hot,
                                # the boiler probe only at the alarm (a strap-on probe reads low while firing)
    "efficiency": None,
    "wood_kwh_per_kg": WOOD_KWH_PER_KG,
    "notify": True,
    # control (phase 2–3)
    "mode": "off",
    "valve_type": "none",
    "valve_cover": "",
    "valve_open_switch": "",
    "valve_close_switch": "",
    "valve_run_s": 120.0,       # ESBE ARA661: 120 s / 90°
    "valve_invert": False,
    "safety_dump": None,        # None → emitters default
    "curve_slope": None,        # None → emitters default
    "curve_shift": 0.0,
    "supply_min_c": 25.0,
    "supply_max_c": None,       # None → emitters default
    "heating_limit_c": 16.0,    # no space heating above this (damped outdoor temperature)
    "room_correction": True,
    "room_gain": 2.0,           # K of supply per K of room error
    "room_hyst_k": None,        # CH pump band: None → above the target (on below + 0.5 K, off at + 1 K, the valve holds
                                # the house in between); a number → centred on it (0.2 → on below 23.9, off at 24.1 at 24)
    "night_enabled": False,     # a lower house temperature at night
    "room_night_c": 19.0,       # the night (setback) target, °C
    "night_setback_k": 0.0,     # derived: day − night while enabled (the older settings stored only this)
    "night_from": 22,
    "night_to": 5,
    "setbacks": [],             # daytime setbacks (nobody at home): [{on, from, to, temp_c, days (Monday = 0)}]
    # hot-water charging pump (differential: source probe vs the tank)
    "dhw_source": "buffer_top",
    "dhw_on_diff_k": 5.0,       # start when the source is this much warmer than the tank …
    "dhw_off_diff_k": 2.0,      # … stop below this (else the tank heats the source back)
    "dhw_hyst_k": 5.0,          # recharge once the tank fell this far below dhw_target_c
    "dhw_dump": False,          # boiler too hot → tank up to dhw_max_c (needs a thermostatic mixer)
    "dhw_fire_only": False,     # charge the tank only while there is a fire in the boiler (the heat dump always)
    "dhw_max_c": 80.0,
    "dhw_mode": "auto",         # "schedule": charge in the hour windows below, also without a tank probe
    "dhw_win1_from": 5,
    "dhw_win1_to": 6,
    "dhw_win2_from": 17,
    "dhw_win2_to": 18,          # from == to → the window is off
    # advisor (phase 4)
    "weather_entity": "",
    "light_lead_h": 1.0,        # from lighting to the buffer taking heat
    "burn_hours": 4.0,
    "boiler_kw": 25.0,          # the boiler's heat output at full fire — caps how much wood a warm buffer takes
    "advisor_notify": True,
    "lit_notify": True,         # "the boiler is lit" (flue ≥ FLUE_FIRE_C) — switch it from lighting to automatic
    "notify_extra": [],         # more phones for the heating notifications only (notify.mobile_app_… services)
    "wood_price_kg": None,      # zł/kg, season cost (derived from wood_price / wood_price_unit when those are set)
    "wood_kind": "mixed",       # soft / mixed / hard — how many kg a cubic metre of seasoned wood weighs
    "wood_price": None,         # the price as bought …
    "wood_price_unit": "mp",    # … per "mp" (metr przestrzenny, stacked split logs), "m3" (solid) or "kg"
    "wood_moisture_pct": None,  # % water (wet basis): a number → wood_kwh_per_kg from it and the kind of wood
}

STATE_LABELS = {
    "unknown": "brak danych",
    "cold": "zimny",
    "ignition": "rozpalanie",
    "burning": "praca",
    "burnout": "wygasanie",
    "idle": "wygasły (ciepły)",
    "overheat": "przegrzanie",
}
FIRE_STATES = ("ignition", "burning", "burnout", "overheat")

# boiler state machine
WORK_C = 65.0            # outlet above this = the boiler is in operation
COLD_C = 40.0
RISING_K_MIN = 0.4       # K/min over the trend window
FALLING_K_MIN = -0.25
BURNOUT_DROP_K = 6.0     # below the session's max — not just the boiler thermostat pausing the fan
TREND_WINDOW_S = 600
BURNOUT_END_C = 60.0     # burn-out ends below this (an insulated boiler can sit at 50–60 °C for hours)
SESSION_END_C = 45.0
NO_FLOW_AFTER_S = 2400   # "heat not reaching the buffer" only after the fire has run this long
MAX_SESSIONS = 400       # a whole heating season
MAX_SETBACKS = 4         # daytime setback periods of the house target
PENDING_LOAD_S = 3 * 3600   # wood weighed before lighting waits this long for the fire the module recognises
FULL_WEIGH_S = 3600         # a fire counts as fully weighed when its first entry came within this of its start
LAST_FIRE_LOAD_S = 12 * 3600  # wood can still be booked to a fire this long after it ended

# probes
PROBE_FAULT_S = 120      # unavailable / invalid this long → probe fault
LADDOMAT_HYST_K = 3.0    # the boiler thermostat's switching difference, roughly
STARTUP_GRACE_S = 300    # after an HA restart integrations (Shelly …) come up unavailable for a while
JUMP_K = 10.0            # single-sample jump bigger than this is dropped …
JUMP_CONFIRM = 3         # … unless the next samples confirm it
JUMP_HOLD_S = 60.0       # … or it holds this long (a Shelly reports only changes)
DS_RESET_C = 85.0

# flue gas: a thermocouple or PT100 + 0–10 V transmitter (e.g. 0–400 °C), not a DS18B20 — its own range, no 85 °C
# reset value, and it moves tens of K a minute at ignition
FLUE_MIN_C = -20.0
FLUE_MAX_C = 500.0
FLUE_JUMP_K = 80.0
FLUE_FIRE_C = 100.0       # flue above this and rising = combustion: ignition / burning
FLUE_HOT_STEADY_C = 120.0  # … or steadily this hot (a fire already burning when the module first sees it)
FLUE_END_C = 80.0         # flue below this (not rising) ends the fire …
FLUE_END_HOLD_S = 900     # … once it stays there this long (the exhaust fan pausing at the boiler thermostat isn't the end)
FLUE_RISING_K_MIN = 2.0   # K/min over the trend window (the flue moves ~10× faster than the water)
FLUE_FALLING_K_MIN = -1.0

# gravity flow into the hot-water coil: the coil's supply pipe at the tank stays warm with the pump stopped
DHW_GRAVITY_OFF_S = 3600  # the pump stood this long — a pipe without flow has cooled down by then
DHW_GRAVITY_SRC_C = 50.0  # the source (buffer top / boiler) is hot enough to drive a gravity flow
DHW_GRAVITY_COIL_C = 35.0  # the pipe well above the room it runs through
DHW_GRAVITY_TANK_K = 3.0  # with a tank probe: the pipe warmer than the tank — that heat comes from the source
DHW_GRAVITY_NEAR_K = 15.0  # without one: the pipe within this of the source
DHW_GRAVITY_HOLD_S = 1200

# buffer discharge (runtime estimate)
RUNTIME_WINDOW_S = 3600
RUNTIME_MIN_KW = 0.05


def _f(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    v = _f(value)
    return min(max(default if v is None else v, lo), hi)


def _humidity(value: Any) -> float | None:
    h = _f(value)
    return h if h is not None and 0.0 <= h <= 100.0 else None


def switch_state(state: Any) -> bool | None:
    """HA switch state → on / off / unknown (unavailable, no entity)."""
    return True if state == "on" else False if state == "off" else None


# ── configuration ────────────────────────────────────────────────────────────

# seasoned firewood (~20 % moisture): kg per solid cubic metre; a stacked metre (metr przestrzenny, split logs
# laid in a pile) holds about 0.65 of it in wood — the rest is air. The third value: the lower heating value of
# the dry wood, MJ/kg (softwood a little higher per kg — resin)
WOOD_KINDS = {
    "hard": ("twarde (buk, dąb, grab, jesion)", 700.0, 18.3),
    "mixed": ("mieszane", 600.0, 18.6),
    "soft": ("miękkie (sosna, świerk, olcha, topola)", 480.0, 19.0),
}
WATER_LATENT_MJ_KG = 2.44   # the water in the wood has to be evaporated: lost per kg of water


def wood_kwh_per_kg_at(kind: str, moisture_pct: float) -> float:
    """The lower heating value of wood at a given moisture (wet basis), kWh/kg: dry wood's value for the dry part,
    minus evaporating the water. Hardwood: 10 % → 4.5, 12 % → 4.4, 20 % → 3.9, 30 % → 3.4 kWh/kg."""
    w = min(max(float(moisture_pct), 0.0), 100.0) / 100.0
    lhv = WOOD_KINDS.get(kind, WOOD_KINDS["mixed"])[2]
    return (lhv * (1.0 - w) - WATER_LATENT_MJ_KG * w) / 3.6
STACKED_SOLID = 0.65
WOOD_PRICE_UNITS = {"mp": "zł / mp (metr przestrzenny)", "m3": "zł / m³ (lity)", "kg": "zł / kg"}


def fully_weighed(fire: dict[str, Any] | None) -> bool:
    """All of a fire's wood was weighed: its first entry was weighed before lighting ("next"), booked after the
    fire to the whole fire ("last"), or came within FULL_WEIGH_S of the fire's start. A first entry later into a
    burning fire is a reload into a fire whose earlier loads nobody weighed (bobrek 25.11.2026: 5.5 kg at 13:47
    into a fire from 07:52) — its kg are real, but they are not the fire's wood: such a fire teaches nothing
    (the house's draw, the heat per kg), and its cost takes the larger of the weighed kg and the estimate."""
    loads = (fire or {}).get("loads") or []
    if not loads:                       # a total booked before the entries were kept counts as before
        return bool(_f((fire or {}).get("wood_kg")))
    first = loads[0]
    if first.get("to") in ("next", "last"):
        return True
    start, end = _f((fire or {}).get("start")), _f((fire or {}).get("end"))
    ts = _f(first.get("ts"))
    if start is None or ts is None:
        return False
    return ts - start <= FULL_WEIGH_S or (end is not None and ts >= end)


def wood_kg_per_unit(kind: str, unit: str) -> float:
    """kg of wood in one unit of the price: a stacked metre (mp), a solid cubic metre (m3) or a kg."""
    density = WOOD_KINDS.get(kind, WOOD_KINDS["mixed"])[1]
    return 1.0 if unit == "kg" else density if unit == "m3" else density * STACKED_SOLID


NOTIFY_EXTRA_MAX = 10
_NOTIFY_SERVICE = re.compile(r"^notify\.[a-z0-9_]+$")


def notify_targets(raw: Any) -> list[str]:
    """The extra recipients of the heating notifications: notify services ("notify.mobile_app_iphone"; a bare
    "mobile_app_iphone" gets the prefix), each once, at most NOTIFY_EXTRA_MAX; anything else is dropped."""
    out: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        name = str(item or "").strip().lower()
        if name and not name.startswith("notify."):
            name = f"notify.{name}"
        if _NOTIFY_SERVICE.match(name) and name not in out:
            out.append(name)
    return out[:NOTIFY_EXTRA_MAX]


ROOM_REFS = ("floor", "custom", "none")


def _room_ref(raw: dict[str, Any], out: dict[str, Any]) -> str:
    """What the house heats by when it has storeys: "floor" — the sensor of storey room_floor (entities.room
    follows it, with its offset), "custom" — a sensor picked by hand (entities.room), "none" — no reference,
    the heating curve only. One storey: the reference sensor is the house — entities.room as picked.
    Settings from before this choice (no room_ref) keep heating by the same sensor: a reference that is a
    storey's sensor makes that storey the reference, a reference storey without its own sensor gets the
    reference sensor, a reference apart from the storey sensors stays "custom", none with storey sensors "none"."""
    ents, offs = out["entities"], out["offsets"]
    ref = raw.get("room_ref")
    if out["floors"] < 2:
        return ref if ref in ROOM_REFS else "floor"
    temps = [ents[f"floor{i}_temp"] for i in range(out["floors"])]
    if ref not in ROOM_REFS:
        room = ents["room"]
        if not room:
            ref = "none" if any(temps) else "floor"
        elif room in temps:
            ref, out["room_floor"] = "floor", temps.index(room)
        else:
            ref = "custom" if temps[out["room_floor"]] else "floor"
    if ref == "floor":
        t, h = f"floor{out['room_floor']}_temp", f"floor{out['room_floor']}_humidity"
        if not ents[t] and ents["room"]:          # the reference moves onto its storey (older settings, 1 → 2 storeys)
            ents[t], offs[t] = ents["room"], offs["room"]
            ents[h] = ents[h] or ents["room_humidity"]
        ents["room"], ents["room_humidity"], offs["room"] = ents[t], ents[h], offs[t]
    elif ref == "none":
        ents["room"] = ents["room_humidity"] = ""
    return ref


def normalize(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Settings "heating" with defaults filled in and every number clamped to a sane range."""
    raw = cfg if isinstance(cfg, dict) else {}
    out = {**DEFAULTS}
    out["enabled"] = bool(raw.get("enabled", False))
    out["notify"] = raw.get("notify", True) is not False
    out["profile"] = raw.get("profile") if raw.get("profile") in PROFILES else DEFAULTS["profile"]
    out["emitters"] = raw.get("emitters") if raw.get("emitters") in EMITTERS else DEFAULTS["emitters"]
    profile = PROFILES[out["profile"]]
    emit = EMITTERS[out["emitters"]]

    ents = raw.get("entities") if isinstance(raw.get("entities"), dict) else {}
    out["entities"] = {role: str(ents.get(role) or "").strip() for role in ROLES}
    locks = raw.get("lockout") if isinstance(raw.get("lockout"), dict) else {}
    out["lockout"] = {el: locks.get(el) is True for el in LOCK_ELEMENTS}
    hand = raw.get("force_on") if isinstance(raw.get("force_on"), dict) else {}
    # one position at a time; a lock wins over HAND
    out["force_on"] = {el: hand.get(el) is True and not out["lockout"][el] for el in FORCE_ELEMENTS}
    offs = raw.get("offsets") if isinstance(raw.get("offsets"), dict) else {}
    out["offsets"] = {role: _num(offs.get(role), 0.0, -OFFSET_MAX_K, OFFSET_MAX_K) for role in TEMP_ROLES}

    out["buffer_l"] = _num(raw.get("buffer_l"), DEFAULTS["buffer_l"], 0.0, 20000.0)
    out["dhw_l"] = _num(raw.get("dhw_l"), DEFAULTS["dhw_l"], 0.0, 5000.0)
    out["buffer_max_c"] = _num(raw.get("buffer_max_c"), DEFAULTS["buffer_max_c"], 60.0, 95.0)
    out["buffer_min_c"] = _num(raw.get("buffer_min_c"), emit["buffer_min_c"], 20.0, out["buffer_max_c"] - 10.0)
    out["buffer_full_c"] = _num(raw.get("buffer_full_c"), DEFAULTS["buffer_full_c"], 60.0, 95.0)
    top = _num(raw.get("probe_top_h"), DEFAULTS["probe_top_h"], 0.5, 1.0)
    bottom = _num(raw.get("probe_bottom_h"), DEFAULTS["probe_bottom_h"], 0.0, 0.5)
    if top - bottom < 0.2:
        top, bottom = DEFAULTS["probe_top_h"], DEFAULTS["probe_bottom_h"]
    out["probe_top_h"], out["probe_bottom_h"] = top, bottom
    out["dhw_cold_c"] = _num(raw.get("dhw_cold_c"), DEFAULTS["dhw_cold_c"], 0.0, 25.0)
    out["dhw_target_c"] = _num(raw.get("dhw_target_c"), DEFAULTS["dhw_target_c"], out["dhw_cold_c"] + 10.0, 80.0)
    out["room_target_c"] = _num(raw.get("room_target_c"), DEFAULTS["room_target_c"], 10.0, 28.0)
    out["floors"] = int(_num(raw.get("floors"), 1, 1, MAX_FLOORS))
    names = raw.get("floor_names") if isinstance(raw.get("floor_names"), list) else []
    out["floor_names"] = [(str(names[i]).strip()[:24] if i < len(names) and str(names[i] or "").strip() else FLOOR_NAMES[i])
                          for i in range(MAX_FLOORS)]
    out["fan_name"] = str(raw.get("fan_name") or "").strip()[:24] or FAN_NAME
    out["fan_off"] = raw.get("fan_off") is True
    out["fan_quiet"] = raw.get("fan_quiet") is True
    for k in ("fan_quiet_from", "fan_quiet_to"):
        out[k] = int(_num(raw.get(k), DEFAULTS[k], 0, 23))
    areas = raw.get("floor_areas") if isinstance(raw.get("floor_areas"), list) else []
    out["floor_areas"] = [None] * MAX_FLOORS
    for i in range(MAX_FLOORS):
        a = _f(areas[i]) if i < len(areas) else None
        out["floor_areas"][i] = round(min(a, 5000.0), 1) if a is not None and a > 0 else None
    views = raw.get("floor_view_only") if isinstance(raw.get("floor_view_only"), list) else []
    out["floor_view_only"] = [i < len(views) and views[i] is True for i in range(MAX_FLOORS)]
    out["room_floor"] = int(_num(raw.get("room_floor"), 0, 0, out["floors"] - 1))
    out["room_ref"] = _room_ref(raw, out)
    out["warn_c"] = _num(raw.get("warn_c"), profile["warn_c"], 70.0, 94.0)
    out["alarm_c"] = _num(raw.get("alarm_c"), profile["alarm_c"], out["warn_c"] + 1.0, 98.0)
    out["laddomat_on_c"] = _num(raw.get("laddomat_on_c"), profile["laddomat_on_c"], 40.0, 90.0)
    top = _f(raw.get("dump_top_c"))
    out["dump_top_c"] = None if top is None or top <= 0 else round(min(max(top, 80.0), 98.0), 1)
    out["efficiency"] = _num(raw.get("efficiency"), profile["efficiency"], 0.4, 0.95)
    out["wood_kwh_per_kg"] = _num(raw.get("wood_kwh_per_kg"), WOOD_KWH_PER_KG, 2.5, 5.2)
    out["tzb_c"] = profile["tzb_c"]
    out["min_return_c"] = profile["min_return_c"]

    # control
    out["mode"] = raw.get("mode") if raw.get("mode") in MODES else "off"
    out["valve_type"] = raw.get("valve_type") if raw.get("valve_type") in VALVE_TYPES else "none"
    for key in ("valve_cover", "valve_open_switch", "valve_close_switch", "weather_entity"):
        out[key] = str(raw.get(key) or "").strip()
    out["valve_run_s"] = _num(raw.get("valve_run_s"), 120.0, 10.0, 600.0)
    out["valve_invert"] = bool(raw.get("valve_invert", False))
    dump = raw.get("safety_dump")
    # never into a floor: the screed has no overtemperature cut-out of its own
    out["safety_dump"] = False if out["emitters"] != "radiators" else (emit["safety_dump"] if dump is None else bool(dump))
    out["curve_slope"] = _num(raw.get("curve_slope"), emit["curve_slope"], 0.2, 3.0)
    out["curve_shift"] = _num(raw.get("curve_shift"), 0.0, -15.0, 15.0)
    out["supply_max_c"] = _num(raw.get("supply_max_c"), emit["supply_max_c"], 30.0, 85.0)
    out["supply_min_c"] = _num(raw.get("supply_min_c"), 25.0, 15.0, out["supply_max_c"] - 5.0)
    out["heating_limit_c"] = _num(raw.get("heating_limit_c"), 16.0, 5.0, 25.0)
    out["room_correction"] = raw.get("room_correction", True) is not False
    out["room_gain"] = _num(raw.get("room_gain"), 2.0, 0.0, 8.0)
    hyst = _f(raw.get("room_hyst_k"))
    out["room_hyst_k"] = None if hyst is None or hyst <= 0 else round(min(max(hyst, 0.2), 4.0), 1)
    # day and night targets of the house. Settings before v1.87 kept only the setback in K.
    day = out["room_target_c"]
    if _f(raw.get("room_night_c")) is None:
        legacy = _num(raw.get("night_setback_k"), 0.0, 0.0, 6.0)
        out["night_enabled"] = legacy > 0 if raw.get("night_enabled") is None else raw.get("night_enabled") is True
        out["room_night_c"] = _num(day - (legacy if legacy > 0 else 2.0), day - 2.0, 10.0, 28.0)
    else:
        out["night_enabled"] = raw.get("night_enabled") is True
        out["room_night_c"] = _num(raw.get("room_night_c"), day - 2.0, 10.0, 28.0)
    out["night_setback_k"] = round(max(day - out["room_night_c"], 0.0), 1) if out["night_enabled"] else 0.0
    out["night_from"] = int(_num(raw.get("night_from"), 22, 0, 23))
    out["night_to"] = int(_num(raw.get("night_to"), 5, 0, 23))
    out["setbacks"] = _setbacks(raw.get("setbacks"), day)
    # hot-water charging pump
    out["dhw_source"] = raw.get("dhw_source") if raw.get("dhw_source") in DHW_SOURCES else "buffer_top"
    out["dhw_on_diff_k"] = _num(raw.get("dhw_on_diff_k"), 5.0, 2.0, 15.0)
    out["dhw_off_diff_k"] = _num(raw.get("dhw_off_diff_k"), 2.0, 0.5, out["dhw_on_diff_k"] - 1.0)
    out["dhw_hyst_k"] = _num(raw.get("dhw_hyst_k"), 5.0, 1.0, 20.0)
    out["dhw_dump"] = bool(raw.get("dhw_dump", False))
    out["dhw_fire_only"] = bool(raw.get("dhw_fire_only", False))
    out["dhw_max_c"] = _num(raw.get("dhw_max_c"), max(80.0, out["dhw_target_c"]), out["dhw_target_c"], 90.0)
    out["dhw_mode"] = raw.get("dhw_mode") if raw.get("dhw_mode") in DHW_MODES else "auto"
    for k in ("dhw_win1_from", "dhw_win1_to", "dhw_win2_from", "dhw_win2_to"):
        out[k] = int(_num(raw.get(k), DEFAULTS[k], 0, 23))
    # advisor
    out["light_lead_h"] = _num(raw.get("light_lead_h"), 1.0, 0.0, 6.0)
    out["burn_hours"] = _num(raw.get("burn_hours"), 4.0, 1.0, 12.0)
    out["boiler_kw"] = _num(raw.get("boiler_kw"), 25.0, 5.0, 150.0)
    out["advisor_notify"] = raw.get("advisor_notify", True) is not False
    out["lit_notify"] = raw.get("lit_notify", True) is not False
    out["notify_extra"] = notify_targets(raw.get("notify_extra"))
    out["wood_kind"] = raw.get("wood_kind") if raw.get("wood_kind") in WOOD_KINDS else DEFAULTS["wood_kind"]
    out["wood_price_unit"] = (raw.get("wood_price_unit") if raw.get("wood_price_unit") in WOOD_PRICE_UNITS
                              else DEFAULTS["wood_price_unit"])
    bought = _f(raw.get("wood_price"))
    out["wood_price"] = None if bought is None or bought <= 0 else round(min(bought, 5000.0), 2)
    per_unit = wood_kg_per_unit(out["wood_kind"], out["wood_price_unit"])
    out["wood_kg_per_unit"] = round(per_unit, 1)          # shown in the panel next to the price
    moist = _f(raw.get("wood_moisture_pct"))
    out["wood_moisture_pct"] = None if moist is None or moist <= 0 else round(min(max(moist, 5.0), 60.0), 1)
    if out["wood_moisture_pct"] is not None:              # the heat per kg follows from the moisture and the kind
        out["wood_kwh_per_kg"] = round(min(max(wood_kwh_per_kg_at(out["wood_kind"], out["wood_moisture_pct"]),
                                               1.5), 5.4), 2)
    if out["wood_price"] is not None:
        price = out["wood_price"] / per_unit               # the price as bought → zł/kg
    else:
        price = _f(raw.get("wood_price_kg"))               # older settings: zł/kg typed directly
    out["wood_price_kg"] = None if price is None or price <= 0 else round(min(price, 10.0), 3)
    return out


OPTIONAL_DEFAULTS = ("buffer_min_c", "warn_c", "alarm_c", "efficiency", "curve_slope", "supply_max_c",
                     "laddomat_on_c")


def _setbacks(raw: Any, day: float) -> list[dict[str, Any]]:
    """Setback periods (nobody at home, e.g. 7–15 on workdays): hours, a lower house target and the weekdays
    (Monday = 0) on which the period starts. At most MAX_SETBACKS; a period without days never applies."""
    out: list[dict[str, Any]] = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        days = p.get("days")
        if isinstance(days, list):
            days = sorted({int(d) for d in days
                           if isinstance(d, (int, float)) and not isinstance(d, bool) and 0 <= d <= 6})
        else:
            days = list(range(7))
        out.append({"on": p.get("on", True) is not False,
                    "from": int(_num(p.get("from"), 7, 0, 23)), "to": int(_num(p.get("to"), 15, 0, 23)),
                    "temp_c": _num(p.get("temp_c"), day - 2.0, 10.0, 28.0), "days": days})
        if len(out) >= MAX_SETBACKS:
            break
    return out


def to_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    """What is stored in settings.json: normalized values; empty profile-dependent fields stay None
    (so a later change of the boiler profile / emitters still brings its defaults)."""
    src = raw if isinstance(raw, dict) else {}
    norm = normalize(src)
    out = {k: norm[k] for k in DEFAULTS}
    for k in OPTIONAL_DEFAULTS:
        if _f(src.get(k)) is None:
            out[k] = None
    if src.get("safety_dump") is None:
        out["safety_dump"] = None
    return out


# ── probes ───────────────────────────────────────────────────────────────────

@dataclass
class Probe:
    """One temperature reading with a DS18B20-aware filter.

    Faults are judged by availability and validity, not by the time since the last
    change: a Shelly reports only when the value changes, and a buffer at rest can
    keep the same temperature for hours.
    """

    value: float | None = None       # last accepted, offset applied
    status: str = "missing"          # ok / missing / unavailable / invalid
    raw: str | None = None
    bad_since: float | None = None   # first moment without a valid reading
    _pending: list[float] = field(default_factory=list)
    _pending_since: float | None = None
    # the sensor's valid range and filter (DS18B20 by default; see probe_for)
    lo_c: float = -55.0
    hi_c: float = 125.0
    ds18b20: bool = True             # 85.0 may be the power-on reset value
    jump_k: float = JUMP_K

    @property
    def fault(self) -> bool:
        return self.status in ("unavailable", "invalid")

    def bad_for(self, now: float) -> float:
        return 0.0 if self.bad_since is None else now - self.bad_since

    def update(self, raw: Any, now: float, offset: float = 0.0) -> None:
        if raw is None:
            self.status, self.raw, self.value, self.bad_since = "missing", None, None, None
            self._pending.clear()
            return
        self.raw = str(raw)
        v = _f(raw)
        if v is None:
            self._mark_bad("unavailable", now)
            return
        if v <= self.lo_c or v >= self.hi_c:  # −127 = DS18B20 not answering
            self._mark_bad("invalid", now)
            return
        if (self.ds18b20 and abs(v - DS_RESET_C) < 0.01
                and (self.value is None or abs(self.value - offset - DS_RESET_C) > 3.0)):
            # 85.0 right after a power glitch of the probe: only plausible next to a reading close to it
            self._mark_bad("invalid", now)
            return
        if self.value is not None and abs(v + offset - self.value) > self.jump_k:
            # the same HA state read again is not a new sample: count changed values, or the time it holds
            if not self._pending or self._pending[-1] != v:
                self._pending.append(v)
            if self._pending_since is None:
                self._pending_since = now
            if len(self._pending) < JUMP_CONFIRM and now - self._pending_since < JUMP_HOLD_S:
                return                          # keep the last value, wait for confirmation
            # the jump repeats / holds — it is real (probe moved, tank refilled …)
        self._pending.clear()
        self._pending_since = None
        self.value, self.status, self.bad_since = v + offset, "ok", None

    def _mark_bad(self, status: str, now: float) -> None:
        self.status = status
        if self.bad_since is None:
            self.bad_since = now
        self._pending.clear()
        self._pending_since = None


def probe_for(role: str) -> Probe:
    """The filter that fits the sensor behind a role: the flue sensor reads far beyond a DS18B20's range."""
    if role == "flue_gas":
        return Probe(lo_c=FLUE_MIN_C, hi_c=FLUE_MAX_C, ds18b20=False, jump_k=FLUE_JUMP_K)
    return Probe()


# ── tanks ────────────────────────────────────────────────────────────────────

def layer_temps(t_top: float, t_bottom: float, h_top: float, h_bottom: float, n: int = LAYERS) -> list[float]:
    """Linear profile between the probes, held flat beyond them (no extrapolation)."""
    lo, hi = min(t_top, t_bottom), max(t_top, t_bottom)
    out = []
    for i in range(n):
        h = (i + 0.5) / n
        t = t_bottom + (t_top - t_bottom) * (h - h_bottom) / (h_top - h_bottom)
        out.append(min(max(t, lo), hi))
    return out


def buffer_state(cfg: dict[str, Any], t_top: float | None, t_bottom: float | None) -> dict[str, Any] | None:
    """Energy above the useful minimum, room up to buffer_max_c, the wood that room takes."""
    vol_m3 = cfg["buffer_l"] / 1000.0
    if vol_m3 <= 0 or (t_top is None and t_bottom is None):
        return None
    if t_top is None or t_bottom is None:
        single = t_top if t_top is not None else t_bottom
        temps = [float(single)] * LAYERS          # one probe: assume a mixed tank
        estimated = True
    else:
        temps = layer_temps(t_top, t_bottom, cfg["probe_top_h"], cfg["probe_bottom_h"])
        estimated = False
    k = WATER_KWH_PER_M3K * vol_m3 / LAYERS
    t_min, t_max = cfg["buffer_min_c"], cfg["buffer_max_c"]
    energy = sum(max(t - t_min, 0.0) for t in temps) * k
    total = sum(max(t - HEAT_REF_C, 0.0) for t in temps) * k     # for measuring the draw (not clamped at t_min)
    capacity = (t_max - t_min) * WATER_KWH_PER_M3K * vol_m3
    space = sum(max(t_max - t, 0.0) for t in temps) * k
    per_kg = cfg["wood_kwh_per_kg"] * cfg["efficiency"]
    return {
        "mean_c": round(sum(temps) / len(temps), 1),
        "energy_kwh": round(energy, 1),
        "total_kwh": round(total, 2),
        "capacity_kwh": round(capacity, 1),
        "charge_pct": round(min(max(energy / capacity * 100.0, 0.0), 100.0), 0) if capacity > 0 else None,
        "space_kwh": round(space, 1),
        "wood_max_kg": round(space / per_kg, 1) if per_kg > 0 else None,
        "kwh_per_kg": round(per_kg, 2),
        "stratification_k": None if estimated else round(t_top - t_bottom, 1),
        "estimated": estimated,
    }


def dhw_state(cfg: dict[str, Any], t: float | None) -> dict[str, Any] | None:
    vol_m3 = cfg["dhw_l"] / 1000.0
    if vol_m3 <= 0 or t is None:
        return None
    cold, target = cfg["dhw_cold_c"], cfg["dhw_target_c"]
    return {
        "temp_c": round(t, 1),
        "energy_kwh": round(max(t - cold, 0.0) * WATER_KWH_PER_M3K * vol_m3, 1),
        "space_kwh": round(max(target - t, 0.0) * WATER_KWH_PER_M3K * vol_m3, 1),
        "charge_pct": round(min(max((t - cold) / (target - cold) * 100.0, 0.0), 100.0), 0),
    }


def floors_state(cfg: dict[str, Any], temps: list[float | None], hums: list[float | None],
                 t_room: float | None, h_room: float | None) -> tuple[list[dict[str, Any]], float | None]:
    """Storey by storey: its own sensor, else the reference room when it is on that storey.
    → (storeys, the house's mean temperature weighted by floor area — equal weights without areas).
    A storey marked "view only" shows its own sensor but stays out of the mean (and so out of the advisor):
    a radiator head measures next to a hot radiator, not the room."""
    out: list[dict[str, Any]] = []
    views = cfg.get("floor_view_only") or []
    for i in range(cfg["floors"]):
        ref = i == cfg["room_floor"] and cfg.get("room_ref") != "none"
        t = temps[i] if i < len(temps) else None
        h = hums[i] if i < len(hums) else None
        source = "probe" if t is not None else None
        if t is None and ref and t_room is not None:
            t, source = t_room, "room"
        if h is None and ref:
            h = h_room
        out.append({"name": cfg["floor_names"][i], "temp_c": None if t is None else round(t, 1),
                    "humidity": None if h is None else round(h, 0), "area_m2": cfg["floor_areas"][i],
                    "ref": ref, "source": source,
                    "view_only": source == "probe" and i < len(views) and bool(views[i])})
    known = [(f["temp_c"], f["area_m2"]) for f in out if f["temp_c"] is not None and not f["view_only"]]
    if not known:
        return out, None
    if all(a for _, a in known):
        mean = sum(t * a for t, a in known) / sum(a for _, a in known)
    else:
        mean = sum(t for t, _ in known) / len(known)
    return out, round(mean, 1)


def session_heat(s: dict[str, Any], energy_now: float | None) -> float | None:
    """The heat the wood gave in one fire: what the tanks (buffer + hot water) hold more at the end than at the
    start, plus what the house took meanwhile (house_kwh, from the house's heat loss) — the energy balance.
    The highest charge minus the start (stored_kwh) alone misses the house, and every reload after the buffer
    had been full once (bobrek 22.11.2026: a 24 h fire booked 52 kWh / 16 kg, really ~150 kWh / ~50 kg).
    Never below stored_kwh (the fire gave at least that); None without the house's share or a buffer reading."""
    start_e, house = s.get("stored_start_kwh"), s.get("house_kwh")
    if start_e is None or energy_now is None or house is None:
        return None
    heat = float(energy_now) - float(start_e) + float(house)
    stored = s.get("stored_kwh")
    if stored is None and s.get("stored_max_kwh") is not None:
        stored = float(s["stored_max_kwh"]) - float(start_e)
    return round(max(heat, float(stored or 0.0), 0.0), 1)


def monthly_summary(sessions: list[dict[str, Any]], utc_offset_s: float = 0.0,
                    price_kg: float | None = None) -> list[dict[str, Any]]:
    """Fires per calendar month (local time): count, hours, heat stored, wood (weighed where known)."""
    import time as _time

    months: dict[str, dict[str, Any]] = {}
    for s in sessions:
        start = _f(s.get("start"))
        if start is None:
            continue
        key = _time.strftime("%Y-%m", _time.gmtime(start + utc_offset_s))
        m = months.setdefault(key, {"month": key, "fires": 0, "hours": 0.0, "stored_kwh": 0.0, "heat_kwh": 0.0,
                                    "wood_kg": 0.0, "weighed": 0, "overheats": 0})
        m["fires"] += 1
        m["hours"] += float(s.get("duration_h") or 0.0)
        m["stored_kwh"] += float(s.get("stored_kwh") or 0.0)
        heat = _f(s.get("heat_kwh"))
        m["heat_kwh"] += heat if heat is not None else float(s.get("stored_kwh") or 0.0)
        kg = _f(s.get("wood_kg"))
        if kg is not None and fully_weighed(s):
            m["weighed"] += 1
        else:                       # not weighed, or only a reload of it: the larger of the weighed kg and the estimate
            est = _f(s.get("wood_est_kg"))
            kg = max(kg or 0.0, est or 0.0) if (kg is not None or est is not None) else None
        m["wood_kg"] += kg or 0.0
        m["overheats"] += 1 if s.get("overheat") else 0
    out = []
    for key in sorted(months):
        m = months[key]
        m["hours"] = round(m["hours"], 1)
        m["stored_kwh"] = round(m["stored_kwh"], 0)
        m["heat_kwh"] = round(m["heat_kwh"], 0)
        m["wood_kg"] = round(m["wood_kg"], 0)
        m["cost"] = round(m["wood_kg"] * price_kg, 0) if price_kg else None
        out.append(m)
    return out


# ── trend helper ─────────────────────────────────────────────────────────────

class Trend:
    """Least-squares slope of (t, value) samples over a time window."""

    def __init__(self, window_s: float) -> None:
        self.window_s = window_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, now: float, value: float) -> None:
        self.samples.append((now, value))
        while self.samples and now - self.samples[0][0] > self.window_s:
            self.samples.popleft()

    def clear(self) -> None:
        self.samples.clear()

    def span_s(self) -> float:
        return self.samples[-1][0] - self.samples[0][0] if len(self.samples) > 1 else 0.0

    def slope_per_s(self) -> float | None:
        if len(self.samples) < 3 or self.span_s() < self.window_s * 0.3:
            return None
        n = len(self.samples)
        mt = sum(s[0] for s in self.samples) / n
        mv = sum(s[1] for s in self.samples) / n
        den = sum((s[0] - mt) ** 2 for s in self.samples)
        if den <= 0:
            return None
        return sum((s[0] - mt) * (s[1] - mv) for s in self.samples) / den

    def rise(self) -> float | None:
        """Last minus first value in the window."""
        return self.samples[-1][1] - self.samples[0][1] if len(self.samples) > 1 else None


# ── alarms ───────────────────────────────────────────────────────────────────

@dataclass
class AlarmSpec:
    id: str
    level: str          # critical / warning / info
    title: str
    message: str
    action: str = ""
    hold_s: float = 0.0


@dataclass
class _AlarmTrack:
    since: float
    active: bool = False
    spec: AlarmSpec | None = None


# ── the model ────────────────────────────────────────────────────────────────

class HeatingModel:
    """Feed it the raw states every tick; read status() / active_alarms()."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        self.cfg = normalize(cfg)
        self.probes: dict[str, Probe] = {role: probe_for(role) for role in TEMP_ROLES}
        self.humidity: float | None = None
        self.floor_humidity: list[float | None] = [None] * MAX_FLOORS
        self.floors: list[dict[str, Any]] = []
        self.house_c: float | None = None
        self.pump_on: bool | None = None
        self.pump_w: float | None = None
        self.dhw_pump_on: bool | None = None
        self.dhw_pump_w: float | None = None
        self.fan_on: bool | None = None
        self.fan_w: float | None = None
        self.laddomat_on: bool | None = None
        self.room_target: float | None = None   # the target in force now (day / night), set by the controller loop
        self.state = "unknown"
        self.state_since: float | None = None
        self.session: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []
        self.pending_loads: list[dict[str, float]] = []   # weighed before lighting: [{ts, kg}] for the next fire
        self.notes: list[str] = []                         # log lines for the integration to pick up
        self.buffer: dict[str, Any] | None = None
        self.dhw: dict[str, Any] | None = None
        self.runtime_h: float | None = None
        self.discharge_kw: float | None = None
        self._boiler_trend = Trend(TREND_WINDOW_S)
        self._flue_trend = Trend(TREND_WINDOW_S)
        self._flue_low_since: float | None = None   # flue below FLUE_END_C (not rising) since
        self._top_trend = Trend(1200)
        self._buffer_trend = Trend(RUNTIME_WINDOW_S)
        self._pump_low_since: float | None = None
        self._dhw_pump_low_since: float | None = None
        self._dhw_pump_off_since: float | None = None
        self.dump_active = False                    # a heat dump in progress (set by the controller loop)
        self._alarms: dict[str, _AlarmTrack] = {}
        self.updated: float | None = None
        self.first_update: float | None = None
        self.external: list[AlarmSpec] = []      # conditions from the controller (heating_control.py)
        self.grid_outage = False

    # configuration / persistence
    def configure(self, cfg: dict[str, Any] | None) -> None:
        self.cfg = normalize(cfg)

    def dump(self) -> dict[str, Any]:
        return {"state": self.state, "state_since": self.state_since,
                "session": self.session, "sessions": self.sessions[-MAX_SESSIONS:],
                "pending_loads": self.pending_loads}

    def restore(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        self.sessions = [s for s in (data.get("sessions") or []) if isinstance(s, dict)][-MAX_SESSIONS:]
        if isinstance(data.get("session"), dict):
            self.session = dict(data["session"])
        self.pending_loads = [{"ts": float(p["ts"]), "kg": float(p["kg"]), "to": "next"}
                              for p in (data.get("pending_loads") or [])
                              if isinstance(p, dict) and _f(p.get("ts")) is not None and _f(p.get("kg"))]
        if data.get("state") in STATE_LABELS:
            self.state = str(data["state"])
            self.state_since = _f(data.get("state_since"))

    # input
    def update(self, raw: dict[str, Any], now: float) -> None:
        """raw: role → HA state string (None when no entity is configured / it doesn't exist)."""
        cfg = self.cfg
        if self.first_update is None:
            self.first_update = now
        for role in TEMP_ROLES:
            self.probes[role].update(raw.get(role), now, cfg["offsets"].get(role, 0.0))
        self.humidity = _humidity(raw.get("room_humidity"))
        self.floor_humidity = [_humidity(raw.get(f"floor{i}_humidity")) for i in range(MAX_FLOORS)]
        self.floors, self.house_c = floors_state(
            cfg, [self.t(f"floor{i}_temp") for i in range(MAX_FLOORS)], self.floor_humidity,
            self.t("room"), self.humidity)
        self.pump_on = switch_state(raw.get("pump_switch"))
        self.pump_w = _f(raw.get("pump_power"))
        self.dhw_pump_on = switch_state(raw.get("dhw_pump_switch"))
        self.dhw_pump_w = _f(raw.get("dhw_pump_power"))
        # how long the hot-water pump has stood: a relay briefly unavailable (Wi-Fi) keeps the clock running
        if self.dhw_pump_on is True or raw.get("dhw_pump_switch") is None:
            self._dhw_pump_off_since = None
        elif self.dhw_pump_on is False and self._dhw_pump_off_since is None:
            self._dhw_pump_off_since = now
        self.fan_on = switch_state(raw.get("fan_switch"))
        self.fan_w = _f(raw.get("fan_power"))
        self.grid_outage = raw.get("grid_outage") is True

        self.buffer = buffer_state(cfg, self.t("buffer_top"), self.t("buffer_bottom"))
        self.dhw = dhw_state(cfg, self.t("dhw"))
        if self.t("buffer_top") is not None:
            self._top_trend.add(now, self.t("buffer_top"))
        self._update_boiler(now)
        self._update_laddomat()
        self._update_runtime(now)
        self.updated = now

    def _update_laddomat(self) -> None:
        """The Laddomat pump is started by the boiler's own thermostat (Atmos ≈ 75 °C); nothing reports
        it, so it is inferred from the boiler outlet, with a small hysteresis. None without a reading."""
        tb = self.t("boiler_out")
        if tb is None:
            self.laddomat_on = None
            return
        on_c = self.cfg["laddomat_on_c"]
        self.laddomat_on = tb >= (on_c - LADDOMAT_HYST_K if self.laddomat_on else on_c)

    def t(self, role: str) -> float | None:
        p = self.probes.get(role)
        return p.value if p is not None and p.status == "ok" else None

    def house_temp(self) -> float | None:
        """The whole house (area-weighted storeys) — what the building's heat loss depends on."""
        return self.house_c if self.house_c is not None else self.t("room")

    # boiler
    def _set_state(self, state: str, now: float) -> None:
        if state != self.state:
            self.state, self.state_since = state, now

    def _flue_trend_k_min(self) -> float | None:
        sps = self._flue_trend.slope_per_s()
        return None if sps is None else sps * 60.0

    def _update_boiler(self, now: float) -> None:
        f = self.t("flue_gas")
        f_kmin = None
        if f is not None:
            self._flue_trend.add(now, f)
            f_kmin = self._flue_trend_k_min()
        if f is not None and f < FLUE_END_C and not (f_kmin is not None and f_kmin >= FLUE_RISING_K_MIN):
            if self._flue_low_since is None:
                self._flue_low_since = now
        else:
            self._flue_low_since = None
        t = self.t("boiler_out")
        if t is None:
            if self.probes["boiler_out"].status == "missing" or self.probes["boiler_out"].bad_for(now) > PROBE_FAULT_S:
                self._set_state("unknown", now)
            return
        trend = self._boiler_trend
        trend.add(now, t)
        sps = trend.slope_per_s()
        k_min = None if sps is None else sps * 60.0
        prev = self.state
        # an open session is a fire even when a probe outage turned the state into "unknown"
        fire_before = prev in FIRE_STATES or self.session is not None
        state = self._water_state(t, k_min, prev, fire_before)
        if f is not None and state != "overheat":
            state = self._flue_state(t, k_min, f, f_kmin, prev, fire_before, state, now)
        self._set_state(state, now)
        self._track_session(t, now, f)

    def _water_state(self, t: float, k_min: float | None, prev: str, fire_before: bool) -> str:
        """The boiler's state from its outlet water and the trend — all there is without a flue sensor."""
        rising = k_min is not None and k_min >= RISING_K_MIN
        falling = k_min is not None and k_min <= FALLING_K_MIN
        peak = max(float((self.session or {}).get("max_c") or t), t)
        if t >= self.cfg["alarm_c"] or (prev == "overheat" and t >= self.cfg["alarm_c"] - 2.0):
            return "overheat"
        if t >= WORK_C:
            if not fire_before:
                # a warm boiler that only holds its heat is not a fire: rising, or clearly hot
                hot_steady = k_min is not None and not falling and t >= WORK_C + 10.0
                return "burning" if rising or hot_steady else "idle"
            if falling and t <= peak - BURNOUT_DROP_K:
                return "burnout"
            if prev == "burnout" and not rising:
                return "burnout"
            return "burning"
        if rising:
            return "ignition"
        if prev == "ignition" and not falling and t > SESSION_END_C:
            return "ignition"                    # slow start — keep waiting
        if (prev in ("burning", "burnout", "overheat") or (fire_before and prev == "unknown")) and t >= BURNOUT_END_C:
            return "burnout"
        return "idle" if t >= COLD_C else "cold"

    def _flue_state(self, t: float, k_min: float | None, f: float, f_kmin: float | None, prev: str,
                    fire_before: bool, water: str, now: float) -> str:
        """With a flue sensor the fire starts and ends by the flue: it is hot within minutes of lighting and cold
        soon after the last embers, while the water in an insulated boiler lags by an hour or more both ways.
        In between: burn-out once the flue falls below FLUE_FIRE_C, otherwise burning ↔ burn-out by the water;
        the end needs the flue cold for FLUE_END_HOLD_S (the exhaust fan pausing at the boiler thermostat drops
        the flue for a while too). A fire the flue has not shown (a session from before the sensor, a sensor
        reading low by mistake) is judged by the water alone."""
        f_rising = f_kmin is not None and f_kmin >= FLUE_RISING_K_MIN
        f_falling = f_kmin is not None and f_kmin <= FLUE_FALLING_K_MIN
        flue_fire = f >= FLUE_FIRE_C and (f_rising or (f_kmin is not None and not f_falling and f >= FLUE_HOT_STEADY_C))
        warm = "burning" if t >= WORK_C else "ignition"
        rest = "idle" if t >= COLD_C else "cold"
        if not fire_before:
            if flue_fire:
                return warm
            # the water alone starts a fire only when it clearly shows one (a flue sensor stuck low); a warm
            # boiler that holds its heat, or water warming by itself, is not a fire while the flue stays cold
            water_rising = k_min is not None and k_min >= RISING_K_MIN
            return "burning" if t >= WORK_C and water_rising else rest
        seen = f >= FLUE_FIRE_C or float((self.session or {}).get("flue_max_c") or 0.0) >= FLUE_FIRE_C
        if not seen:
            return water
        if self._flue_low_since is not None and now - self._flue_low_since >= FLUE_END_HOLD_S:
            return rest                          # the flue went cold: the fire is over, however warm the water
        if flue_fire:
            return warm                          # combustion — also picking up again after a reload
        if f < FLUE_FIRE_C and (f_falling or (prev == "burnout" and not f_rising)):
            return "burnout"                     # the flame dies down (an insulated boiler's water barely shows it)
        if water in FIRE_STATES:
            return water
        return "burnout"                         # the water would end the fire, the flue hasn't gone cold yet

    def _track_session(self, t: float, now: float, flue: float | None = None) -> None:
        fire = self.state in FIRE_STATES
        energy = self._stored_kwh()
        if fire and self.session is None:
            self.session = {
                "start": now, "end": None, "start_c": round(t, 1), "max_c": round(t, 1),
                "stored_start_kwh": energy, "stored_max_kwh": energy, "overheat": False,
            }
            pending = [p for p in self.pending_loads if now - p["ts"] <= PENDING_LOAD_S]
            if pending:                 # weighed before lighting: it belongs to this fire
                self.session["loads"] = pending
                self.session["wood_kg"] = round(sum(p["kg"] for p in pending), 1)
                self.notes.append(f"Zważone przed rozpaleniem: {self.session['wood_kg']:.1f} kg — "
                                  f"przypisane do palenia, które właśnie się zaczęło")
            self.pending_loads = []
        if self.session is None:
            stale = [p for p in self.pending_loads if now - p["ts"] > PENDING_LOAD_S]
            if stale:
                self.pending_loads = [p for p in self.pending_loads if now - p["ts"] <= PENDING_LOAD_S]
                self.notes.append(f"Zważone drewno ({sum(p['kg'] for p in stale):.1f} kg) nie trafiło do żadnego "
                                  f"palenia — w ciągu {PENDING_LOAD_S // 3600} h nie było rozpalenia; wpisz je ponownie")
            return
        s = self.session
        s["max_c"] = round(max(float(s.get("max_c") or t), t), 1)
        if flue is not None:
            s["flue_max_c"] = round(max(float(s.get("flue_max_c") or flue), flue), 0)
        if self.state == "overheat":
            s["overheat"] = True
        if energy is not None:
            s["stored_max_kwh"] = energy if s.get("stored_max_kwh") is None else max(s["stored_max_kwh"], energy)
            if s.get("stored_start_kwh") is None:
                s["stored_start_kwh"] = energy
        if not fire:
            s["end"] = now
            s["duration_h"] = round((now - float(s["start"])) / 3600.0, 2)
            start_e, max_e = s.get("stored_start_kwh"), s.get("stored_max_kwh")
            s["stored_kwh"] = round(max_e - start_e, 1) if start_e is not None and max_e is not None else None
            s["stored_end_kwh"] = energy
            s["heat_kwh"] = session_heat(s, energy)
            per_kg = self.cfg["wood_kwh_per_kg"] * self.cfg["efficiency"]
            heat = s["heat_kwh"] if s["heat_kwh"] is not None else s.get("stored_kwh")
            s["wood_est_kg"] = round(heat / per_kg, 1) if heat and per_kg else None
            self.sessions.append(s)
            self.sessions = self.sessions[-MAX_SESSIONS:]
            self.session = None

    def last_fire_open(self, now: float) -> dict[str, Any] | None:
        """The last ended fire while wood can still be booked to it (LAST_FIRE_LOAD_S)."""
        if self.sessions and now - float(self.sessions[-1].get("end") or 0) <= LAST_FIRE_LOAD_S:
            return self.sessions[-1]
        return None

    @staticmethod
    def _add_load(fire: dict[str, Any], kg: float, now: float, to: str = "current") -> None:
        loads = fire.setdefault("loads", [])
        if not loads and fire.get("wood_kg"):           # a total booked before the entries were kept
            loads.append({"ts": float(fire.get("start") or now), "kg": float(fire["wood_kg"])})
        loads.append({"ts": round(now, 1), "kg": round(kg, 1), "to": to})
        fire["wood_kg"] = round(sum(x["kg"] for x in loads), 1)

    def log_load(self, kg: float, now: float, target: str = "auto") -> tuple[str, dict[str, Any] | None] | None:
        """Weighed wood. target: "current" — the fire burning now (a reload adds up); "last" — the fire that ended up
        to 12 h ago; "next" — weighed before lighting, it waits for the fire the module recognises within 3 h (the
        water shows a new fire only after 10–30 min, and until then a new entry would land on the previous fire);
        "auto" — the current fire, else the last one. Returns (where, fire) or None when there is nothing to book to."""
        if target in ("auto", "current") and self.session is not None:
            self._add_load(self.session, kg, now, "current")
            return "current", self.session
        if target == "next":
            self.pending_loads.append({"ts": round(now, 1), "kg": round(kg, 1), "to": "next"})
            return "next", None
        if target in ("auto", "last"):
            last = self.last_fire_open(now)
            if last is not None:
                self._add_load(last, kg, now, "last")
                return "last", last
        return None

    def undo_load(self, now: float) -> tuple[float, str] | None:
        """Remove the latest weighed entry (waiting for a fire, the current fire or the last one)."""
        spots: list[tuple[float, str, list[dict[str, Any]], dict[str, Any] | None]] = []
        if self.pending_loads:
            spots.append((self.pending_loads[-1]["ts"], "next", self.pending_loads, None))
        for where, fire in (("current", self.session), ("last", self.last_fire_open(now))):
            if fire is not None:
                if not fire.get("loads") and fire.get("wood_kg"):
                    fire["loads"] = [{"ts": float(fire.get("start") or now), "kg": float(fire["wood_kg"])}]
                if fire.get("loads"):
                    spots.append((float(fire["loads"][-1]["ts"]), where, fire["loads"], fire))
        if not spots:
            return None
        _, where, loads, fire = max(spots, key=lambda x: x[0])
        kg = float(loads.pop()["kg"])
        if fire is not None:
            fire["wood_kg"] = round(sum(x["kg"] for x in loads), 1) if loads else None
        return kg, where

    def _stored_kwh(self) -> float | None:
        """Heat in the buffer + hot water tank (what a fire leaves behind, without the house's draw)."""
        parts = [x["energy_kwh"] for x in (self.buffer, self.dhw) if x]
        return round(sum(parts), 1) if parts else None

    # buffer runtime
    def _update_runtime(self, now: float) -> None:
        self.runtime_h = self.discharge_kw = None
        if not self.buffer:
            self._buffer_trend.clear()
            return
        if self.state in FIRE_STATES:
            self._buffer_trend.clear()        # charging — the draw is unknown
            return
        self._buffer_trend.add(now, self.buffer["total_kwh"])
        if self._buffer_trend.span_s() < RUNTIME_WINDOW_S * 0.5:
            return
        sps = self._buffer_trend.slope_per_s()
        if sps is None:
            return
        kw = -sps * 3600.0
        self.discharge_kw = round(kw, 2)
        if kw >= RUNTIME_MIN_KW:
            self.runtime_h = round(self.buffer["energy_kwh"] / kw, 1)

    # gravity flow into the hot-water coil
    def dhw_pump_off_s(self, now: float) -> float | None:
        """How long the hot-water pump has stood (None: running, or no relay assigned)."""
        if self.dhw_pump_on is True or self._dhw_pump_off_since is None:
            return None
        return now - self._dhw_pump_off_since

    def _dhw_gravity(self, now: float, was: bool) -> tuple[float, float, str, float | None, float] | None:
        """The coil's supply pipe at the tank stays warm although the pump has stood for an hour and no heat dump
        runs: hot water rises from the buffer (or the boiler) into the coil on its own. A pipe without flow cools
        to the room it runs through; conduction from the tank itself can't make it warmer than the tank, so with
        a tank probe the pipe must be warmer than the tank, without one within DHW_GRAVITY_NEAR_K of the source.
        → (coil, source, source name, tank, pump off s)."""
        coil = self.t("dhw_coil_supply")
        off_s = self.dhw_pump_off_s(now)
        if coil is None or off_s is None or off_s < DHW_GRAVITY_OFF_S or self.dump_active:
            return None
        if self.cfg["dhw_source"] == "boiler_out":
            src, name = self.t("boiler_out"), "kocioł"
        else:
            src, name = self.t("buffer_top"), "góra bufora"
            if src is None and self.t("buffer_bottom") is not None:
                src, name = self.t("buffer_bottom"), "dół bufora"   # never warmer than the top: only later
        if src is None:
            return None
        tank = self.t("dhw")
        ref = tank + DHW_GRAVITY_TANK_K if tank is not None else src - DHW_GRAVITY_NEAR_K
        h = 2.0 if was else 0.0
        if src >= DHW_GRAVITY_SRC_C - h and coil >= DHW_GRAVITY_COIL_C - h and coil >= ref - h:
            return coil, src, name, tank, off_s
        return None

    # alarms
    def _conditions(self, now: float) -> list[AlarmSpec]:
        cfg = self.cfg
        out: list[AlarmSpec] = []
        active = {k for k, tr in self._alarms.items() if tr.active}
        t_boiler, t_top = self.t("boiler_out"), self.t("buffer_top")

        if t_boiler is not None:
            hyst = 2.0
            # independent: "hot" stays active under "overheat" (shown hidden), so a short overheat
            # doesn't send "resolved" for the warning and then a new warning
            if t_boiler >= cfg["alarm_c"] or ("heating_overheat" in active and t_boiler >= cfg["alarm_c"] - hyst):
                out.append(AlarmSpec(
                    "heating_overheat", "critical", f"Przegrzanie kotła: {t_boiler:.0f} °C",
                    f"Wyjście z kotła {t_boiler:.0f} °C — zawór termiczny otwiera się przy {cfg['tzb_c']:.0f} °C.",
                    "Nie dokładaj drewna i nie otwieraj drzwiczek zasypowych. Sprawdź, czy pracuje pompa "
                    "Laddomatu; odbierz ciepło (pompa CO, otwarty zawór mieszający, grzejniki).",
                    hold_s=30))
            if t_boiler >= cfg["warn_c"] or ("heating_hot" in active and t_boiler >= cfg["warn_c"] - hyst):
                out.append(AlarmSpec(
                    "heating_hot", "warning", f"Kocioł gorący: {t_boiler:.0f} °C",
                    f"Wyjście z kotła {t_boiler:.0f} °C, próg alarmu {cfg['alarm_c']:.0f} °C.",
                    "Nie dokładaj drewna; sprawdź, czy bufor jeszcze przyjmuje ciepło.", hold_s=60))

        if t_top is not None and (t_top >= cfg["buffer_full_c"]
                                  or ("heating_buffer_full" in active and t_top >= cfg["buffer_full_c"] - 3.0)):
            out.append(AlarmSpec(
                "heating_buffer_full", "warning", f"Bufor pełny: góra {t_top:.0f} °C",
                "Bufor jest naładowany — kolejny załadunek nie ma gdzie oddać ciepła.",
                "Nie dokładaj drewna, dopóki bufor się nie rozładuje.", hold_s=120))

        if (t_boiler is not None and t_top is not None and self.state in ("burning", "overheat")
                and self.session and now - float(self.session["start"]) >= NO_FLOW_AFTER_S
                and t_boiler >= 85.0 and t_top <= t_boiler - 15.0
                and self._top_trend.span_s() >= 1100 and (self._top_trend.rise() or 0.0) < 1.0):
            out.append(AlarmSpec(
                "heating_no_flow", "warning", "Ciepło nie płynie do bufora",
                f"Kocioł {t_boiler:.0f} °C, a góra bufora {t_top:.0f} °C i od 20 min nie rośnie.",
                "Sprawdź pompę i wkład termostatyczny Laddomatu oraz zawory na obiegu kotła."))

        grace = self.first_update is None or now - self.first_update < STARTUP_GRACE_S
        faulty = [] if grace else [role for role in PROBE_ROLES + ("room", "outdoor")
                                   if self.probes[role].fault and self.probes[role].bad_for(now) >= PROBE_FAULT_S]
        if faulty:
            # a lost boiler probe turns the state into "unknown" — an open session still means a fire
            fire = self.state in FIRE_STATES or self.session is not None
            hot = any(role in ("boiler_out", "buffer_top") for role in faulty) and fire
            names = ", ".join(ROLES[r][0] for r in faulty)
            action = ("Sprawdź Shelly w kotłowni (zasilanie, Wi-Fi) i przewody sond; wartości −127 °C lub "
                      "stałe 85 °C oznaczają przerwany przewód albo zanik zasilania sondy.")
            if any(role in ("boiler_out", "buffer_top") for role in faulty):
                action += (" Dopóki kocioł lub góra bufora nie mają odczytu, w trybach Auto, Ręczne i Lato pompa CO "
                           "pracuje awaryjnie. Przy montażu albo bez wody w instalacji przełącz tryb na „Wyłączone”.")
            if "flue_gas" in faulty:
                action += (" Czujnik spalin: sprawdź zasilanie przetwornika i przewód czujnika; do jego powrotu "
                           "palenie rozpoznaje temperatura wody w kotle.")
            out.append(AlarmSpec(
                "heating_probe_fault", "critical" if hot else "warning", "Brak odczytu czujnika temperatury",
                f"Bez poprawnego odczytu: {names}.", action))

        # a relay on with ~0 W behind it: the pump has no power (fuse, wire, the AUTO–0–HAND switch at 0 / HAND)
        low = self.pump_on and self.pump_w is not None and self.pump_w < 3.0
        self._pump_low_since = (self._pump_low_since or now) if low else None
        if low and now - self._pump_low_since >= 120:
            out.append(AlarmSpec(
                "heating_pump_fault", "warning", "Pompa CO nie pobiera prądu",
                f"Przekaźnik pompy jest włączony, a moc wynosi {self.pump_w:.0f} W.",
                "Sprawdź zasilanie i bezpiecznik pompy, przewód za przekaźnikiem i położenie "
                "przełącznika AUTO–0–RĘKA (jeśli jest)."))
        low = self.dhw_pump_on and self.dhw_pump_w is not None and self.dhw_pump_w < 3.0
        self._dhw_pump_low_since = (self._dhw_pump_low_since or now) if low else None
        if low and now - self._dhw_pump_low_since >= 120:
            out.append(AlarmSpec(
                "heating_dhw_pump_fault", "warning", "Pompa CWU nie pobiera prądu",
                f"Przekaźnik pompy CWU jest włączony, a moc wynosi {self.dhw_pump_w:.0f} W.",
                "Sprawdź zasilanie i bezpiecznik pompy, przewód za przekaźnikiem i położenie "
                "przełącznika AUTO–0–RĘKA (jeśli jest)."))

        gravity = self._dhw_gravity(now, "heating_dhw_gravity" in active)
        if gravity:
            coil, src, src_name, tank, off_s = gravity
            out.append(AlarmSpec(
                "heating_dhw_gravity", "warning", "Ciepło płynie do CWU grawitacyjnie",
                f"Pompa CWU stoi od {off_s / 60:.0f} min, a zasilanie wężownicy przy zasobniku ma {coil:.0f} °C "
                f"({src_name} {src:.0f} °C" + (f", zasobnik {tank:.0f} °C)." if tank is not None else ").")
                + " Bez przepływu ta rura stygnie do temperatury otoczenia — gorąca woda krąży sama, bez pompy.",
                "Zamontuj zawór zwrotny (grawitacyjny, z klapką) na zasilaniu wężownicy CWU albo zrób syfon termiczny "
                "(pętla rury w dół przy buforze). Bez tego bufor stale oddaje ciepło do zasobnika, a sterowanie CWU "
                "(godziny, „tylko przy paleniu”) nie działa. Jeśli przełącznik pompy CWU w skrzynce stoi na RĘKA, "
                "pompa pracuje mimo wyłączonego przekaźnika — to nie grawitacja.",
                hold_s=DHW_GRAVITY_HOLD_S))

        room = self.t("room")
        target = self.room_target if self.room_target is not None else cfg["room_target_c"]
        if (room is not None and self.buffer and self.buffer.get("charge_pct") is not None
                and self.state not in FIRE_STATES
                and room <= target - 1.5 and self.buffer["charge_pct"] < 15):
            out.append(AlarmSpec(
                "heating_light_fire", "info", "Czas rozpalić w kotle",
                f"W domu {room:.1f} °C (zadana {target:.1f} °C), bufor naładowany "
                f"w {self.buffer['charge_pct']:.0f}%.",
                f"Bufor przyjmie ok. {self.buffer['wood_max_kg']:.0f} kg drewna. Zważ je i wpisz w panelu Ogrzewanie.",
                hold_s=1800))

        fire_now = self.state in FIRE_STATES or self.session is not None
        if self.grid_outage and fire_now:
            out.append(AlarmSpec(
                "heating_grid_outage", "critical", "Zanik prądu w trakcie palenia",
                "Sieć nie ma napięcia, a w kotle się pali. Jeśli kotłownia nie jest zasilana z falownika "
                "(wyjście awaryjne), pompy i wentylator kotła stoją.",
                "Nie dokładaj drewna. Sprawdź pompę Laddomatu i temperaturę kotła; zawór termiczny 95 °C "
                "schłodzi kocioł tylko wtedy, gdy w kranie jest woda pod ciśnieniem.", hold_s=20))

        out.extend(self.external)

        if self.humidity is not None and (self.humidity > 65.0 or self.humidity < 30.0):
            high = self.humidity > 65.0
            out.append(AlarmSpec(
                "heating_humidity", "info", f"Wilgotność w domu {self.humidity:.0f}%",
                "Za wysoka wilgotność sprzyja pleśni." if high else "Bardzo suche powietrze.",
                "Przewietrz / sprawdź wentylację." if high else "Rozważ nawilżanie.", hold_s=6 * 3600))
        return out

    def evaluate_alarms(self, now: float) -> tuple[list[AlarmSpec], list[AlarmSpec]]:
        """→ (raised now, cleared now). active_alarms() lists everything active."""
        found = {a.id: a for a in self._conditions(now)}
        raised: list[AlarmSpec] = []
        cleared: list[AlarmSpec] = []
        for aid, spec in found.items():
            tr = self._alarms.get(aid)
            if tr is None:
                tr = self._alarms[aid] = _AlarmTrack(since=now)
            prev_level = tr.spec.level if tr.spec else None
            tr.spec = spec
            if not tr.active and now - tr.since >= spec.hold_s:
                tr.active = True
                raised.append(spec)
            elif tr.active and prev_level == "warning" and spec.level == "critical":
                raised.append(spec)       # escalation
        for aid in list(self._alarms):
            if aid not in found:
                tr = self._alarms.pop(aid)
                if tr.active and tr.spec:
                    cleared.append(tr.spec)
        return raised, cleared

    def active_alarms(self) -> list[AlarmSpec]:
        order = {"critical": 0, "warning": 1, "info": 2}
        specs = [tr.spec for tr in self._alarms.values() if tr.active and tr.spec]
        if any(a.id == "heating_overheat" for a in specs):
            specs = [a for a in specs if a.id != "heating_hot"]   # the critical one says it all
        return sorted(specs, key=lambda a: order.get(a.level, 3))

    # output
    def status(self, now: float) -> dict[str, Any]:
        probes = {}
        for role in TEMP_ROLES:
            p = self.probes[role]
            probes[role] = {"value": None if p.value is None else round(p.value, 1), "status": p.status,
                            "raw": p.raw, "fault_s": round(p.bad_for(now))}
        session = None
        if self.session:
            s = self.session
            session = {**s, "duration_h": round((now - float(s["start"])) / 3600.0, 2),
                       "stored_kwh": (round(s["stored_max_kwh"] - s["stored_start_kwh"], 1)
                                      if s.get("stored_max_kwh") is not None and s.get("stored_start_kwh") is not None
                                      else None)}
            session["heat_kwh"] = session_heat(session, self._stored_kwh())
        sps = self._boiler_trend.slope_per_s()
        f_kmin = self._flue_trend_k_min() if probes["flue_gas"]["value"] is not None else None
        off_s = self.dhw_pump_off_s(now)
        gravity = self._alarms.get("heating_dhw_gravity")
        return {
            "updated": self.updated,
            "probes": probes,
            "humidity": self.humidity,
            "floors": self.floors,
            "house_c": self.house_c,
            "pump": {"on": self.pump_on, "power_w": self.pump_w},
            "dhw_pump": {"on": self.dhw_pump_on, "power_w": self.dhw_pump_w,
                         "off_min": None if off_s is None else round(off_s / 60.0)},
            # gravity: the alarm active; suspected: its condition holds, still within the hold time
            "dhw_coil": {"temp_c": probes["dhw_coil_supply"]["value"],
                         "gravity": bool(gravity and gravity.active), "suspected": gravity is not None},
            "fan": {"on": self.fan_on, "power_w": self.fan_w},
            "laddomat": {"on": self.laddomat_on, "on_c": self.cfg["laddomat_on_c"]},
            "boiler": {
                "state": self.state, "label": STATE_LABELS.get(self.state, self.state),
                "since": self.state_since, "temp_c": probes["boiler_out"]["value"],
                "trend_k_min": None if sps is None else round(sps * 60.0, 2),
                "flue_c": probes["flue_gas"]["value"],
                "flue_trend_k_min": None if f_kmin is None else round(f_kmin, 1),
                "firing": self.state in FIRE_STATES,
            },
            "buffer": self.buffer,
            "dhw": self.dhw,
            "runtime_h": self.runtime_h,
            "discharge_kw": self.discharge_kw,
            "session": session,
            "last_session": self.sessions[-1] if self.sessions else None,
            # weighing: entries waiting for the next fire, and whether the last fire still takes wood
            "wood": {"pending": [dict(p) for p in self.pending_loads],
                     # the fire burning now has all its wood weighed (None: nothing weighed in it)
                     "current_full": (fully_weighed(self.session) if self.session and self.session.get("loads")
                                      else None),
                     "pending_kg": round(sum(p["kg"] for p in self.pending_loads), 1) if self.pending_loads else None,
                     "last_open": self.last_fire_open(now) is not None,
                     "pending_hold_h": PENDING_LOAD_S // 3600},
            "alarms": [{"id": a.id, "level": a.level, "title": a.title, "message": a.message, "action": a.action}
                       for a in self.active_alarms()],
        }
