# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""PV forecast confidence learned from this installation's own history.

Pure Python. Each past day gives (morning forecast kWh, actual PV kWh). The
forecast's error depends on the weather: clear days come close, cloudy days
miss by a lot — so days are grouped by how sunny the forecast was (relative to
the installation's own clear-sky days in the window, which also covers the
season and the size of the array). The confidence the planner relies on is a
low quantile of actual/forecast in the day's group: the sun delivers at least
that on ~80 % of such days. Works for any installation — no constants about
the array, only its history.
"""
from __future__ import annotations

from datetime import date
from typing import Any

CLASSES = ("cloudy", "mixed", "sunny")
MIN_DAYS = 5          # fewer days in a group → fall back to all days
MIN_TOTAL = 7         # fewer days in total → the configured confidence
QUANTILE = 0.2        # conservative: the sun delivers at least this on ~80 % of days
CLAMP = (0.3, 1.1)


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def sky_class(forecast_kwh: float, clear_kwh: float) -> str:
    """How sunny the forecast is, relative to the installation's clear days."""
    if clear_kwh <= 0:
        return "mixed"
    share = forecast_kwh / clear_kwh
    if share >= 0.65:
        return "sunny"
    if share >= 0.3:
        return "mixed"
    return "cloudy"


def learn(days: list[tuple[date, float, float]]) -> dict[str, Any]:
    """Per-class confidence from (day, forecast_kwh, actual_kwh) rows."""
    # Sanity: a forecast of a few hundred Wh says nothing; a ratio past 0.05–2.5 is a
    # broken sensor or a forecast from another source; the same actual two days running
    # is a frozen counter (seen: 178.7 kWh/day for a week)
    rows = []
    prev_a = None
    for d, f, a in sorted(days):
        frozen = prev_a is not None and abs(a - prev_a) < 0.05
        prev_a = a
        if f >= 0.5 and not frozen and 0.05 <= a / f <= 2.5:
            rows.append((d, f, a))
    if len(rows) < MIN_TOTAL:
        return {"samples": len(rows), "clear_kwh": 0.0, "classes": {}}
    clear = _quantile([f for _, f, _ in rows], 0.9)
    groups: dict[str, list[float]] = {c: [] for c in CLASSES}
    every = []
    for _, f, a in rows:
        r = a / f
        every.append(r)
        groups[sky_class(f, clear)].append(r)
    out: dict[str, Any] = {}
    for c in CLASSES:
        vals = groups[c] if len(groups[c]) >= MIN_DAYS else every
        out[c] = {
            "confidence": round(min(max(_quantile(vals, QUANTILE), CLAMP[0]), CLAMP[1]), 2),
            "median": round(_quantile(vals, 0.5), 2),
            "days": len(groups[c]),
            "own": len(groups[c]) >= MIN_DAYS,
        }
    return {"samples": len(rows), "clear_kwh": round(clear, 1), "classes": out}


def confidence(model: dict[str, Any] | None, forecast_kwh: float, fallback: float) -> tuple[float, str]:
    """Confidence for a day with this forecast (and the group it fell into)."""
    classes = (model or {}).get("classes") or {}
    if not classes:
        return fallback, "default"
    c = sky_class(forecast_kwh, float(model.get("clear_kwh") or 0.0))
    return float(classes[c]["confidence"]), c


def daily_rows(
    forecast_hours: list[tuple[date, int, float]],
    pv_hours: list[tuple[date, int, float]],
    today: date,
    morning_hour: int = 7,
) -> list[tuple[date, float, float]]:
    """Join the morning forecast (state in the hour `morning_hour`) with the day's PV.

    forecast_hours — (day, hour, forecast-today kWh); pv_hours — (day, hour, mean W).
    Days with < 20 hours of PV data or without a morning forecast are skipped.
    """
    fc: dict[date, float] = {}
    for d, h, v in forecast_hours:
        if h >= morning_hour and d not in fc and v is not None:
            fc[d] = float(v)
    pv: dict[date, list[float]] = {}
    for d, _h, w in pv_hours:
        if w is not None:
            pv.setdefault(d, []).append(max(float(w), 0.0))
    out = []
    for d in sorted(fc):
        if d >= today or len(pv.get(d, [])) < 20:
            continue
        out.append((d, fc[d], sum(pv[d]) / 1000.0))
    return out
