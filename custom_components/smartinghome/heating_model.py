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
  only when it fits the trend), single-sample jumps are dropped,
- buffer and hot-water tank energy from two / one probes (layered linear profile),
  room left in the buffer and the wood load it can still take without overheating,
- boiler state from the outlet temperature and its trend (cold → ignition →
  burning → burn-out → idle, overheat), each fire is a session,
- how long the buffer lasts at the current draw,
- alarms with hold times and hysteresis (overheat, buffer full, heat not reaching
  the buffer, probe fault, CH pump not drawing power, room cold with an empty buffer).

Settings "heating" (settings.json): see DEFAULTS / normalize().
"""
from __future__ import annotations

import math
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
    },
    "generic": {
        "label": "Inny kocioł zgazowujący drewno",
        "warn_c": 88.0, "alarm_c": 92.0, "tzb_c": 95.0, "min_return_c": 60.0, "efficiency": 0.78,
    },
}

# role → (label, kind). Temperatures are DS18B20 / any °C sensor.
ROLES: dict[str, tuple[str, str]] = {
    "boiler_out": ("Kocioł — wyjście (przy Laddomacie)", "temperature"),
    "buffer_top": ("Bufor — góra", "temperature"),
    "buffer_bottom": ("Bufor — dół", "temperature"),
    "dhw": ("Zasobnik CWU", "temperature"),
    "heating_supply": ("Zasilanie CO (za zaworem)", "temperature"),
    "heating_return": ("Powrót z grzejników", "temperature"),
    "room": ("Pokój referencyjny — temperatura", "temperature"),
    "room_humidity": ("Pokój referencyjny — wilgotność", "humidity"),
    "outdoor": ("Temperatura zewnętrzna", "temperature"),
    "pump_switch": ("Pompa CO — przekaźnik", "switch"),
    "pump_power": ("Pompa CO — moc", "power"),
}
TEMP_ROLES = tuple(r for r, (_, kind) in ROLES.items() if kind == "temperature")
PROBE_ROLES = ("boiler_out", "buffer_top", "buffer_bottom", "dhw", "heating_supply", "heating_return")

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
    "warn_c": None,             # None → profile
    "alarm_c": None,
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
    "night_setback_k": 0.0,
    "night_from": 22,
    "night_to": 5,
    # advisor (phase 4)
    "weather_entity": "",
    "light_lead_h": 1.0,        # from lighting to the buffer taking heat
    "burn_hours": 4.0,
    "advisor_notify": True,
    "wood_price_kg": None,      # zł/kg, season cost
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

# probes
PROBE_FAULT_S = 120      # unavailable / invalid this long → probe fault
STARTUP_GRACE_S = 300    # after an HA restart integrations (Shelly …) come up unavailable for a while
JUMP_K = 10.0            # single-sample jump bigger than this is dropped …
JUMP_CONFIRM = 3         # … unless the next samples confirm it
JUMP_HOLD_S = 60.0       # … or it holds this long (a Shelly reports only changes)
DS_RESET_C = 85.0

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


# ── configuration ────────────────────────────────────────────────────────────

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
    offs = raw.get("offsets") if isinstance(raw.get("offsets"), dict) else {}
    out["offsets"] = {role: _num(offs.get(role), 0.0, -5.0, 5.0) for role in TEMP_ROLES}

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
    out["warn_c"] = _num(raw.get("warn_c"), profile["warn_c"], 70.0, 94.0)
    out["alarm_c"] = _num(raw.get("alarm_c"), profile["alarm_c"], out["warn_c"] + 1.0, 98.0)
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
    out["night_setback_k"] = _num(raw.get("night_setback_k"), 0.0, 0.0, 6.0)
    out["night_from"] = int(_num(raw.get("night_from"), 22, 0, 23))
    out["night_to"] = int(_num(raw.get("night_to"), 5, 0, 23))
    # advisor
    out["light_lead_h"] = _num(raw.get("light_lead_h"), 1.0, 0.0, 6.0)
    out["burn_hours"] = _num(raw.get("burn_hours"), 4.0, 1.0, 12.0)
    out["advisor_notify"] = raw.get("advisor_notify", True) is not False
    price = _f(raw.get("wood_price_kg"))
    out["wood_price_kg"] = None if price is None or price <= 0 else min(price, 10.0)
    return out


OPTIONAL_DEFAULTS = ("buffer_min_c", "warn_c", "alarm_c", "efficiency", "curve_slope", "supply_max_c")


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
        if v <= -55.0 or v >= 125.0:          # −127 = DS18B20 not answering
            self._mark_bad("invalid", now)
            return
        if abs(v - DS_RESET_C) < 0.01 and (self.value is None or abs(self.value - offset - DS_RESET_C) > 3.0):
            # 85.0 right after a power glitch of the probe: only plausible next to a reading close to it
            self._mark_bad("invalid", now)
            return
        if self.value is not None and abs(v + offset - self.value) > JUMP_K:
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
        m = months.setdefault(key, {"month": key, "fires": 0, "hours": 0.0, "stored_kwh": 0.0,
                                    "wood_kg": 0.0, "weighed": 0, "overheats": 0})
        m["fires"] += 1
        m["hours"] += float(s.get("duration_h") or 0.0)
        m["stored_kwh"] += float(s.get("stored_kwh") or 0.0)
        kg = _f(s.get("wood_kg"))
        if kg is not None:
            m["weighed"] += 1
        else:
            kg = _f(s.get("wood_est_kg"))
        m["wood_kg"] += kg or 0.0
        m["overheats"] += 1 if s.get("overheat") else 0
    out = []
    for key in sorted(months):
        m = months[key]
        m["hours"] = round(m["hours"], 1)
        m["stored_kwh"] = round(m["stored_kwh"], 0)
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
        self.probes: dict[str, Probe] = {role: Probe() for role in TEMP_ROLES}
        self.humidity: float | None = None
        self.pump_on: bool | None = None
        self.pump_w: float | None = None
        self.state = "unknown"
        self.state_since: float | None = None
        self.session: dict[str, Any] | None = None
        self.sessions: list[dict[str, Any]] = []
        self.buffer: dict[str, Any] | None = None
        self.dhw: dict[str, Any] | None = None
        self.runtime_h: float | None = None
        self.discharge_kw: float | None = None
        self._boiler_trend = Trend(TREND_WINDOW_S)
        self._top_trend = Trend(1200)
        self._buffer_trend = Trend(RUNTIME_WINDOW_S)
        self._pump_low_since: float | None = None
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
                "session": self.session, "sessions": self.sessions[-MAX_SESSIONS:]}

    def restore(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        self.sessions = [s for s in (data.get("sessions") or []) if isinstance(s, dict)][-MAX_SESSIONS:]
        if isinstance(data.get("session"), dict):
            self.session = dict(data["session"])
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
        hum = _f(raw.get("room_humidity"))
        self.humidity = hum if hum is not None and 0.0 <= hum <= 100.0 else None
        sw = raw.get("pump_switch")
        self.pump_on = None if sw is None else (sw == "on" if sw in ("on", "off") else None)
        self.pump_w = _f(raw.get("pump_power"))
        self.grid_outage = raw.get("grid_outage") is True

        self.buffer = buffer_state(cfg, self.t("buffer_top"), self.t("buffer_bottom"))
        self.dhw = dhw_state(cfg, self.t("dhw"))
        if self.t("buffer_top") is not None:
            self._top_trend.add(now, self.t("buffer_top"))
        self._update_boiler(now)
        self._update_runtime(now)
        self.updated = now

    def t(self, role: str) -> float | None:
        p = self.probes.get(role)
        return p.value if p is not None and p.status == "ok" else None

    # boiler
    def _set_state(self, state: str, now: float) -> None:
        if state != self.state:
            self.state, self.state_since = state, now

    def _update_boiler(self, now: float) -> None:
        t = self.t("boiler_out")
        if t is None:
            if self.probes["boiler_out"].status == "missing" or self.probes["boiler_out"].bad_for(now) > PROBE_FAULT_S:
                self._set_state("unknown", now)
            return
        trend = self._boiler_trend
        trend.add(now, t)
        sps = trend.slope_per_s()
        k_min = None if sps is None else sps * 60.0
        rising = k_min is not None and k_min >= RISING_K_MIN
        falling = k_min is not None and k_min <= FALLING_K_MIN
        prev = self.state
        # an open session is a fire even when a probe outage turned the state into "unknown"
        fire_before = prev in FIRE_STATES or self.session is not None
        peak = max(float((self.session or {}).get("max_c") or t), t)

        if t >= self.cfg["alarm_c"] or (prev == "overheat" and t >= self.cfg["alarm_c"] - 2.0):
            state = "overheat"
        elif t >= WORK_C:
            if not fire_before:
                # a warm boiler that only holds its heat is not a fire: rising, or clearly hot
                hot_steady = k_min is not None and not falling and t >= WORK_C + 10.0
                state = "burning" if rising or hot_steady else "idle"
            elif falling and t <= peak - BURNOUT_DROP_K:
                state = "burnout"
            elif prev == "burnout" and not rising:
                state = "burnout"
            else:
                state = "burning"
        elif rising:
            state = "ignition"
        elif prev == "ignition" and not falling and t > SESSION_END_C:
            state = "ignition"                    # slow start — keep waiting
        elif (prev in ("burning", "burnout", "overheat") or (fire_before and prev == "unknown")) and t >= BURNOUT_END_C:
            state = "burnout"
        elif t >= COLD_C:
            state = "idle"
        else:
            state = "cold"
        self._set_state(state, now)
        self._track_session(t, now)

    def _track_session(self, t: float, now: float) -> None:
        fire = self.state in FIRE_STATES
        energy = self._stored_kwh()
        if fire and self.session is None:
            self.session = {
                "start": now, "end": None, "start_c": round(t, 1), "max_c": round(t, 1),
                "stored_start_kwh": energy, "stored_max_kwh": energy, "overheat": False,
            }
        if self.session is None:
            return
        s = self.session
        s["max_c"] = round(max(float(s.get("max_c") or t), t), 1)
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
            per_kg = self.cfg["wood_kwh_per_kg"] * self.cfg["efficiency"]
            s["wood_est_kg"] = round(s["stored_kwh"] / per_kg, 1) if s.get("stored_kwh") and per_kg else None
            self.sessions.append(s)
            self.sessions = self.sessions[-MAX_SESSIONS:]
            self.session = None

    def log_load(self, kg: float, now: float) -> dict[str, Any] | None:
        """Weighed wood of the current fire (or of the last one, up to 12 h after it ended)."""
        target = self.session
        if target is None and self.sessions and now - float(self.sessions[-1].get("end") or 0) <= 12 * 3600:
            target = self.sessions[-1]
        if target is None:
            return None
        target["wood_kg"] = round(float(target.get("wood_kg") or 0.0) + kg, 1)
        return target

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
            out.append(AlarmSpec(
                "heating_probe_fault", "critical" if hot else "warning", "Brak odczytu czujnika temperatury",
                f"Bez poprawnego odczytu: {names}.",
                "Sprawdź Shelly w kotłowni (zasilanie, Wi-Fi) i przewody sond; wartości −127 °C lub "
                "stałe 85 °C oznaczają przerwany przewód albo zanik zasilania sondy."))

        if self.pump_on and self.pump_w is not None and self.pump_w < 3.0:
            self._pump_low_since = self._pump_low_since or now
            if now - self._pump_low_since >= 120:
                out.append(AlarmSpec(
                    "heating_pump_fault", "warning", "Pompa CO nie pobiera prądu",
                    f"Przekaźnik pompy jest włączony, a moc wynosi {self.pump_w:.0f} W.",
                    "Sprawdź zasilanie i bezpiecznik pompy oraz przewód za przekaźnikiem."))
        else:
            self._pump_low_since = None

        room = self.t("room")
        if (room is not None and self.buffer and self.buffer.get("charge_pct") is not None
                and self.state not in FIRE_STATES
                and room <= cfg["room_target_c"] - 1.5 and self.buffer["charge_pct"] < 15):
            out.append(AlarmSpec(
                "heating_light_fire", "info", "Czas rozpalić w kotle",
                f"W pokoju {room:.1f} °C (zadana {cfg['room_target_c']:.1f} °C), bufor naładowany "
                f"w {self.buffer['charge_pct']:.0f}%.",
                f"Bufor przyjmie ok. {self.buffer['wood_max_kg']:.0f} kg drewna.", hold_s=1800))

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
        sps = self._boiler_trend.slope_per_s()
        return {
            "updated": self.updated,
            "probes": probes,
            "humidity": self.humidity,
            "pump": {"on": self.pump_on, "power_w": self.pump_w},
            "boiler": {
                "state": self.state, "label": STATE_LABELS.get(self.state, self.state),
                "since": self.state_since, "temp_c": probes["boiler_out"]["value"],
                "trend_k_min": None if sps is None else round(sps * 60.0, 2),
                "firing": self.state in FIRE_STATES,
            },
            "buffer": self.buffer,
            "dhw": self.dhw,
            "runtime_h": self.runtime_h,
            "discharge_kw": self.discharge_kw,
            "session": session,
            "last_session": self.sessions[-1] if self.sessions else None,
            "alarms": [{"id": a.id, "level": a.level, "title": a.title, "message": a.message, "action": a.action}
                       for a in self.active_alarms()],
        }
