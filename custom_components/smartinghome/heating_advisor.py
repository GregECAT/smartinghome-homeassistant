# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Firing advisor — how long the buffer lasts, when to light, how much wood to load.

Pure Python, no Home Assistant imports.

Building model: in hours without a fire (and with the CH pump running, when there is
one) the buffer only gives heat to the house, so its hourly drop in heat content is
the house's draw. Fitted against (room − outdoor):  draw_kW = UA · ΔT + base
(base: hot water, tank losses, gains). Needs about a day of varied weather; until
then the advice falls back to the last hour's trend (heating_model.runtime_h).

Forecast: the hourly outdoor forecast (HA weather entity) turned into hourly demand,
taken out of the buffer's useful heat until it is empty → "bufor wystarczy do",
minus the time a fresh fire needs to reach the buffer → "rozpal do".

Wood: room in the buffer up to its maximum (minus a 3 K margin), room in the hot
water tank, plus what the house takes during the burn, divided by the heat one kg of
wood really gives — learned from weighed loads when there are any.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from statistics import median
from typing import Any

from .heating_model import fully_weighed

MAX_SAMPLES = 24 * 30          # hourly, 30 days
MIN_SAMPLES = 24
MIN_DT_SPREAD_K = 4.0
UA_MIN_KW_K, UA_MAX_KW_K = 0.02, 2.0
HORIZON_H = 96
MARGIN_K = 3.0                 # don't plan the buffer to its very maximum
WATER_KWH_PER_M3K = 1.14
# the house's draw before the hourly model is fitted: the buffer's discharge without a fire (pump cycling and
# all) per K of room − outdoor, averaged over about half a day of such hours
DRAW_TAU_S = 12 * 3600
DRAW_MIN_OBS_S = 2 * 3600
DRAW_MIN_DT_K = 5.0
# the house's draw from WEIGHED fires: one cycle = from a fire's start to the next fire's start; the wood gave
# kg × kWh/kg (settings), the tanks kept the difference, the house took the rest — over whole cycles, so it needs
# no hours without a fire (bobrek fires ~20 h a day). Takes precedence over the discharge ratio above.
CYCLE_MIN_H, CYCLE_MAX_H = 3.0, 72.0
CYCLE_MIN_KG = 3.0
CYCLE_KEEP, CYCLE_MEDIAN_N = 20, 5


class BuildingModel:
    def __init__(self) -> None:
        self.samples: list[dict[str, float]] = []
        self.ua: float | None = None        # kW/K
        self.base: float = 0.0              # kW
        self.r2: float | None = None
        self._acc: dict[str, Any] | None = None
        self.draw_ua: float | None = None   # kW/K, from the buffer's discharge (observe_draw)
        self.draw_obs_s: float = 0.0
        self.cycles: list[dict[str, float]] = []      # weighed fire cycles: {ts, kw, dt, h}
        self._cyc: dict[str, float] | None = None     # the cycle being tracked: {start, s, k} (Σ ΔT·s)

    def dump(self) -> dict[str, Any]:
        return {"samples": self.samples[-MAX_SAMPLES:],
                "draw": {"ua": self.draw_ua, "obs_s": round(self.draw_obs_s)},
                "cycles": self.cycles[-CYCLE_KEEP:], "cycle": self._cyc}

    def restore(self, data: dict[str, Any] | None) -> None:
        if isinstance(data, dict):
            self.samples = [s for s in (data.get("samples") or []) if isinstance(s, dict)][-MAX_SAMPLES:]
            self.fit()
            draw = data.get("draw") if isinstance(data.get("draw"), dict) else {}
            self.cycles = [c for c in (data.get("cycles") or [])
                           if isinstance(c, dict) and isinstance(c.get("kw"), (int, float))
                           and isinstance(c.get("dt"), (int, float)) and c["dt"] > 0][-CYCLE_KEEP:]
            cyc = data.get("cycle")
            if isinstance(cyc, dict) and all(isinstance(cyc.get(k), (int, float)) for k in ("start", "s", "k")):
                self._cyc = {"start": float(cyc["start"]), "s": float(cyc["s"]), "k": float(cyc["k"])}
            ua = draw.get("ua")
            if isinstance(ua, (int, float)) and not isinstance(ua, bool) and 0 <= ua <= UA_MAX_KW_K:
                self.draw_ua = float(ua)
                obs = draw.get("obs_s")
                self.draw_obs_s = float(obs) if isinstance(obs, (int, float)) and obs > 0 else 0.0

    def observe_draw(self, kw: float | None, dt_k: float | None, dt_s: float) -> None:
        """The buffer's measured discharge without a fire (heating_model.discharge_kw: the last hour's trend, the
        CH pump cycling or not) against room − outdoor. Kept as a running ratio — enough to book the house's
        share of a fire long before the hourly model below has its 24 clean hours (bobrek fires ~20 h a day)."""
        if kw is None or kw < -0.2 or dt_k is None or dt_k < DRAW_MIN_DT_K or dt_s <= 0:
            return                  # (a buffer gaining heat without a fire — a PV heater — is no draw)
        r = _clamp(max(kw, 0.0) / dt_k, 0.0, UA_MAX_KW_K)
        w = min(dt_s / DRAW_TAU_S, 1.0)
        self.draw_ua = r if self.draw_ua is None else self.draw_ua + (r - self.draw_ua) * w
        self.draw_obs_s += dt_s

    # weighed fire cycles
    @property
    def cycle_start(self) -> float | None:
        return None if self._cyc is None else self._cyc["start"]

    def open_cycle(self, fire_start: float) -> None:
        self._cyc = {"start": float(fire_start), "s": 0.0, "k": 0.0}

    def cycle_add(self, dt_k: float | None, dt_s: float) -> None:
        if self._cyc is not None and dt_k is not None and dt_s > 0:
            self._cyc["s"] += dt_s
            self._cyc["k"] += dt_k * dt_s

    def close_cycle(self, prev: dict[str, Any] | None, new: dict[str, Any], per_kg: float) -> str | None:
        """A new fire started: book the cycle of the previous one when all its wood was weighed (entries in
        "loads"). Returns a log line, or None when the cycle can't be used."""
        cyc = self._cyc
        if cyc is None or prev is None or per_kg <= 0 or not fully_weighed(prev):
            return None                          # nothing weighed, or only a reload into the fire
        if abs(float(prev.get("start") or 0.0) - cyc["start"]) > 1.0:
            return None                          # not the fire this cycle was tracked from
        kg = float(prev.get("wood_kg") or 0.0)
        e0, e1 = prev.get("stored_start_kwh"), new.get("stored_start_kwh")
        hours = (float(new["start"]) - cyc["start"]) / 3600.0
        if kg < CYCLE_MIN_KG or e0 is None or e1 is None or not CYCLE_MIN_H <= hours <= CYCLE_MAX_H:
            return None
        if cyc["s"] < hours * 3600.0 * 0.5:
            return None                          # too little of the cycle seen (HA down most of it)
        dt_k = cyc["k"] / cyc["s"]
        kwh = kg * per_kg - (float(e1) - float(e0))
        if dt_k < DRAW_MIN_DT_K or kwh <= 0:
            return None
        kw = kwh / hours
        self.cycles.append({"ts": float(new["start"]), "kw": round(kw, 3), "dt": round(dt_k, 2), "h": round(hours, 2),
                            "kg": round(kg, 1)})
        self.cycles = self.cycles[-CYCLE_KEEP:]
        return (f"Zważone palenie: {kg:.1f} kg wystarczyło na {hours:.1f} h — dom brał średnio {kw:.2f} kW "
                f"przy {dt_k:.1f} K różnicy dom − zewnątrz ({kw / dt_k * 1000:.0f} W/K)")

    def cycle_ua(self) -> float | None:
        vals = [c["kw"] / c["dt"] for c in self.cycles[-CYCLE_MEDIAN_N:] if c.get("dt")]
        return _clamp(median(vals), UA_MIN_KW_K, UA_MAX_KW_K) if vals else None

    def demand_basis(self) -> str | None:
        """What the house's demand comes from: "model" (fitted hours without a fire), "history" (weighed fires)."""
        return "model" if self.ua is not None else "history" if self.cycles else None

    def house_kw(self, t_room: float | None, t_out: float | None) -> float | None:
        """What the house takes now: the fitted model, the weighed fires, else the discharge ratio once it has
        seen two hours."""
        if t_room is None or t_out is None:
            return None
        if self.ua is not None:
            return max(self.ua * (t_room - t_out) + self.base, 0.0)
        cua = self.cycle_ua()
        if cua is not None:
            return max(cua * (t_room - t_out), 0.0)
        if self.draw_ua is not None and self.draw_obs_s >= DRAW_MIN_OBS_S:
            return max(self.draw_ua * (t_room - t_out), 0.0)
        return None

    def add(self, now: float, total_kwh: float | None, t_room: float | None, t_out: float | None,
            firing: bool, pump_on: bool | None) -> None:
        key = int(now // 3600)
        acc = self._acc
        if acc is None or acc["key"] != key:
            if acc is not None:
                self._close(acc, now)
            acc = self._acc = {"key": key, "valid": True, "n": 0, "dt": 0.0,
                               "e_first": None, "e_last": None, "t_first": None, "t_last": None}
        if firing or pump_on is False or total_kwh is None or t_room is None or t_out is None:
            acc["valid"] = False
            return
        if acc["e_first"] is None:
            acc["e_first"], acc["t_first"] = total_kwh, now
        acc["e_last"], acc["t_last"] = total_kwh, now
        acc["dt"] += t_room - t_out
        acc["n"] += 1

    def _close(self, acc: dict[str, Any], now: float) -> None:
        if not acc["valid"] or acc["n"] < 6 or acc["t_first"] is None or now - acc["t_last"] > 600:
            return
        span_h = (acc["t_last"] - acc["t_first"]) / 3600.0
        if span_h < 0.75:
            return
        kw = (acc["e_first"] - acc["e_last"]) / span_h
        if kw < -0.2:            # the buffer gained heat without a fire (PV heater, …): not a draw
            return
        self.samples.append({"ts": acc["key"] * 3600, "dt": round(acc["dt"] / acc["n"], 2), "kw": round(max(kw, 0.0), 3)})
        self.samples = self.samples[-MAX_SAMPLES:]
        self.fit()

    def fit(self) -> None:
        pts = [(s["dt"], s["kw"]) for s in self.samples if s.get("dt") is not None and s.get("kw") is not None]
        self.ua, self.base, self.r2 = None, 0.0, None
        if len(pts) < MIN_SAMPLES:
            return
        xs = [p[0] for p in pts]
        if max(xs) - min(xs) < MIN_DT_SPREAD_K:
            mean_dt = sum(xs) / len(xs)
            if mean_dt > 3.0:                       # one kind of weather: ratio estimate
                self.ua = _clamp(sum(p[1] for p in pts) / len(pts) / mean_dt, UA_MIN_KW_K, UA_MAX_KW_K)
            return
        ua, base = _linfit(pts)
        resid = [y - (ua * x + base) for x, y in pts]
        sd = math.sqrt(sum(r * r for r in resid) / len(resid)) or 0.0
        if sd > 0:
            kept = [p for p, r in zip(pts, resid) if abs(r) <= 2.5 * sd]
            if len(kept) >= MIN_SAMPLES:
                pts = kept
                ua, base = _linfit(pts)
        if ua <= 0:
            mean_dt = sum(p[0] for p in pts) / len(pts)
            ua, base = (sum(p[1] for p in pts) / len(pts) / mean_dt if mean_dt > 0 else UA_MIN_KW_K), 0.0
        self.ua = _clamp(ua, UA_MIN_KW_K, UA_MAX_KW_K)
        self.base = _clamp(base, -2.0, 2.0)
        mean_y = sum(p[1] for p in pts) / len(pts)
        ss_tot = sum((p[1] - mean_y) ** 2 for p in pts)
        ss_res = sum((p[1] - (self.ua * p[0] + self.base)) ** 2 for p in pts)
        self.r2 = round(1 - ss_res / ss_tot, 2) if ss_tot > 0 else None

    def demand_kw(self, room_target: float, t_out: float) -> float | None:
        if self.ua is not None:
            return max(self.ua * (room_target - t_out) + self.base, 0.0)
        cua = self.cycle_ua()
        return None if cua is None else max(cua * (room_target - t_out), 0.0)

    def status(self) -> dict[str, Any]:
        return {"ua_w_k": None if self.ua is None else round(self.ua * 1000.0),
                "base_kw": round(self.base, 2), "r2": self.r2, "samples": len(self.samples),
                "draw_w_k": None if self.draw_ua is None else round(self.draw_ua * 1000.0),
                "draw_h": round(self.draw_obs_s / 3600.0, 1),
                "cycle_w_k": None if self.cycle_ua() is None else round(self.cycle_ua() * 1000.0),
                "cycles": len(self.cycles), "basis": self.demand_basis()}


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def _linfit(pts: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den <= 0:
        return 0.0, my
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
    return slope, my - slope * mx


def temp_at(forecast: list[tuple[float, float]], ts: float, fallback: float | None) -> float | None:
    """Outdoor temperature at ts from an hourly forecast (linear between points)."""
    if not forecast:
        return fallback
    if ts <= forecast[0][0]:
        return forecast[0][1] if fallback is None else fallback
    for (t0, v0), (t1, v1) in zip(forecast, forecast[1:]):
        if t0 <= ts <= t1:
            return v0 + (v1 - v0) * (ts - t0) / (t1 - t0) if t1 > t0 else v0
    return forecast[-1][1]


Target = float | Callable[[float], float]


def _target(room_target: Target, ts: float) -> float:
    """The house target at a moment: a fixed value, or the day / night schedule."""
    return float(room_target(ts)) if callable(room_target) else float(room_target)


def depletion(energy_kwh: float, now: float, building: BuildingModel, room_target: Target,
              forecast: list[tuple[float, float]], t_out_now: float | None) -> tuple[float | None, float | None]:
    """(timestamp when the useful heat runs out or None beyond the horizon, demand of the next 24 h in kWh)."""
    if building.demand_basis() is None:
        return None, None
    e = energy_kwh
    empty_at = None
    demand_24 = 0.0
    step = 900.0
    t = now
    for _ in range(int(HORIZON_H * 3600 / step)):
        t_out = temp_at(forecast, t + step / 2, t_out_now)
        if t_out is None:
            return None, None
        kw = building.demand_kw(_target(room_target, t + step / 2), t_out) or 0.0
        kwh = kw * step / 3600.0
        if t - now < 24 * 3600:
            demand_24 += kwh
        if empty_at is None:
            if kw > 0 and e - kwh <= 0:
                empty_at = t + (e / kw * 3600.0 if kw > 0 else 0.0)
            e -= kwh
        t += step
        if empty_at is not None and t - now >= 24 * 3600:
            break
    return empty_at, round(demand_24, 1)


def learned_kwh_per_kg(sessions: list[dict[str, Any]]) -> float | None:
    """Median heat per kg of the last weighed loads (the fire's energy balance: tanks + the house's draw)."""
    vals = []
    for s in reversed(sessions):
        kg = s.get("wood_kg")
        if not kg or kg < 3 or not fully_weighed(s):
            continue
        heat = s.get("heat_kwh")
        if heat is None:            # fires booked before v1.100: the highest charge + the house, as then
            heat = (s.get("stored_kwh") or 0.0) + (s.get("house_kwh") or 0.0)
        if heat > 0:
            vals.append(heat / kg)
        if len(vals) >= 5:
            break
    if len(vals) < 2:
        return None
    return round(_clamp(median(vals), 1.5, 4.8), 2)


def advise(cfg: dict[str, Any], status: dict[str, Any], building: BuildingModel, now: float,
           room_target: Target, forecast: list[tuple[float, float]], t_out_now: float | None,
           per_kg: float, fire_left_kwh: float | None = None) -> dict[str, Any]:
    """Advice for the panel / sensors. status = HeatingModel.status(). fire_left_kwh: what the weighed wood of the
    fire burning now will still give (kg × kWh/kg − what this fire has given so far) — added to the buffer."""
    buf = status.get("buffer") or {}
    dhw = status.get("dhw") or {}
    firing = bool((status.get("boiler") or {}).get("firing"))
    out: dict[str, Any] = {"basis": None, "empty_at": None, "light_at": None, "demand_24h_kwh": None,
                           "demand_kw": None, "wood_kg": None, "fill_kwh": None, "per_kg": round(per_kg, 2),
                           "building": building.status(), "firing": firing}
    if not buf:
        return out
    energy = float(buf.get("energy_kwh") or 0.0)
    out["buffer_kwh"] = round(energy, 1)
    out["fire_left_kwh"] = None if fire_left_kwh is None else round(max(fire_left_kwh, 0.0), 1)
    energy += max(fire_left_kwh or 0.0, 0.0)
    out["heat_left_kwh"] = round(energy, 1)
    empty_at, demand_24 = depletion(energy, now, building, room_target, forecast, t_out_now)
    if demand_24 is not None:
        out["basis"] = building.demand_basis()
        out["demand_24h_kwh"] = demand_24
        t_now = temp_at(forecast, now, t_out_now)
        out["demand_kw"] = None if t_now is None else round(building.demand_kw(_target(room_target, now), t_now) or 0.0, 2)
        out["empty_at"] = empty_at
    elif status.get("runtime_h") is not None:
        out["basis"] = "trend"
        out["empty_at"] = now + float(status["runtime_h"]) * 3600.0
        out["demand_kw"] = status.get("discharge_kw")
    if out["empty_at"] is not None and not firing:
        out["light_at"] = max(out["empty_at"] - cfg["light_lead_h"] * 3600.0, now)

    # wood for a fire started now: room in the tanks + the house's draw during the burn
    margin = MARGIN_K * WATER_KWH_PER_M3K * cfg["buffer_l"] / 1000.0
    room = max(float(buf.get("space_kwh") or 0.0) - margin, 0.0) + float(dhw.get("space_kwh") or 0.0)
    burn_kw = out["demand_kw"] if out["demand_kw"] is not None else 0.0
    if out["basis"] in ("model", "history") and forecast:
        hours = int(cfg["burn_hours"])
        moments = [now + h * 3600 + 1800 for h in range(hours)]
        burn = sum(building.demand_kw(_target(room_target, ts), t) or 0.0
                   for ts in moments if (t := temp_at(forecast, ts, t_out_now)) is not None)
    else:
        burn = burn_kw * cfg["burn_hours"]
    out["fill_kwh"] = round(room + burn, 1)
    out["wood_kg"] = round(max(room + burn, 0.0) / per_kg) if per_kg > 0 else None
    return out
