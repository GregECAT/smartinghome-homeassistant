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
from statistics import median
from typing import Any

MAX_SAMPLES = 24 * 30          # hourly, 30 days
MIN_SAMPLES = 24
MIN_DT_SPREAD_K = 4.0
UA_MIN_KW_K, UA_MAX_KW_K = 0.02, 2.0
HORIZON_H = 96
MARGIN_K = 3.0                 # don't plan the buffer to its very maximum
WATER_KWH_PER_M3K = 1.14


class BuildingModel:
    def __init__(self) -> None:
        self.samples: list[dict[str, float]] = []
        self.ua: float | None = None        # kW/K
        self.base: float = 0.0              # kW
        self.r2: float | None = None
        self._acc: dict[str, Any] | None = None

    def dump(self) -> dict[str, Any]:
        return {"samples": self.samples[-MAX_SAMPLES:]}

    def restore(self, data: dict[str, Any] | None) -> None:
        if isinstance(data, dict):
            self.samples = [s for s in (data.get("samples") or []) if isinstance(s, dict)][-MAX_SAMPLES:]
            self.fit()

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
        if self.ua is None:
            return None
        return max(self.ua * (room_target - t_out) + self.base, 0.0)

    def status(self) -> dict[str, Any]:
        return {"ua_w_k": None if self.ua is None else round(self.ua * 1000.0),
                "base_kw": round(self.base, 2), "r2": self.r2, "samples": len(self.samples)}


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


def depletion(energy_kwh: float, now: float, building: BuildingModel, room_target: float,
              forecast: list[tuple[float, float]], t_out_now: float | None) -> tuple[float | None, float | None]:
    """(timestamp when the useful heat runs out or None beyond the horizon, demand of the next 24 h in kWh)."""
    if building.ua is None:
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
        kw = building.demand_kw(room_target, t_out) or 0.0
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
    """Median heat per kg of the last weighed loads (stored + the house's draw during the fire)."""
    vals = []
    for s in reversed(sessions):
        kg = s.get("wood_kg")
        if not kg or kg < 3:
            continue
        heat = (s.get("stored_kwh") or 0.0) + (s.get("house_kwh") or 0.0)
        if heat > 0:
            vals.append(heat / kg)
        if len(vals) >= 5:
            break
    if len(vals) < 2:
        return None
    return round(_clamp(median(vals), 1.5, 4.8), 2)


def advise(cfg: dict[str, Any], status: dict[str, Any], building: BuildingModel, now: float,
           room_target: float, forecast: list[tuple[float, float]], t_out_now: float | None,
           per_kg: float) -> dict[str, Any]:
    """Advice for the panel / sensors. status = HeatingModel.status()."""
    buf = status.get("buffer") or {}
    dhw = status.get("dhw") or {}
    firing = bool((status.get("boiler") or {}).get("firing"))
    out: dict[str, Any] = {"basis": None, "empty_at": None, "light_at": None, "demand_24h_kwh": None,
                           "demand_kw": None, "wood_kg": None, "fill_kwh": None, "per_kg": round(per_kg, 2),
                           "building": building.status(), "firing": firing}
    if not buf:
        return out
    energy = float(buf.get("energy_kwh") or 0.0)
    empty_at, demand_24 = depletion(energy, now, building, room_target, forecast, t_out_now)
    if demand_24 is not None:
        out["basis"] = "model"
        out["demand_24h_kwh"] = demand_24
        t_now = temp_at(forecast, now, t_out_now)
        out["demand_kw"] = None if t_now is None else round(building.demand_kw(room_target, t_now) or 0.0, 2)
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
    if out["basis"] == "model" and forecast:
        hours = int(cfg["burn_hours"])
        temps = [temp_at(forecast, now + h * 3600 + 1800, t_out_now) for h in range(hours)]
        burn = sum(building.demand_kw(room_target, t) or 0.0 for t in temps if t is not None)
    else:
        burn = burn_kw * cfg["burn_hours"]
    out["fill_kwh"] = round(room + burn, 1)
    out["wood_kg"] = round(max(room + burn, 0.0) / per_kg) if per_kg > 0 else None
    return out
