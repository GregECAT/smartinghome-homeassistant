# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Grid voltage report — evidence for a complaint to the distribution operator.

Pure Python. Input: recorder statistics of the three phase voltages (hourly
mean/max for the long history, 5-minute means for the recent days) and the
hourly mean grid power (+ export). Output: per-day figures, EN 50160 weekly
check (95 % of 10-minute means within 230 V ± 10 %), and hours with a high
voltage while the house was not exporting (the grid itself is too high).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

UN = 230.0
LOW = UN * 0.9     # 207 V
HIGH = UN * 1.1    # 253 V


def ten_minute_means(rows: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Aligned 10-minute means (hh:00, hh:10 …) from 5-minute means."""
    by_slot: dict[datetime, list[float]] = {}
    for start, mean in rows:
        slot = start.replace(minute=start.minute // 10 * 10, second=0, microsecond=0)
        by_slot.setdefault(slot, []).append(mean)
    return [(t, sum(v) / len(v)) for t, v in sorted(by_slot.items()) if len(v) == 2]


def build_report(
    hourly: dict[str, list[tuple[datetime, float | None, float | None]]],
    five_min: dict[str, list[tuple[datetime, float]]],
    grid_hourly: dict[datetime, float],
    limit_v: float = HIGH,
) -> dict[str, Any]:
    """hourly/five_min keyed by phase ("L1".."L3"), local naive timestamps (period start)."""
    days: dict[str, dict[str, Any]] = {}

    def day(t: datetime) -> dict[str, Any]:
        return days.setdefault(t.date().isoformat(), {
            "date": t.date().isoformat(), "max_v": 0.0, "max_hour_mean": 0.0,
            "hours_max_over": 0, "hours_mean_over": 0, "hours_high_no_export": 0,
            "ten_min_over": None, "ten_min_total": None,
        })

    hour_over: dict[datetime, set[str]] = {}
    hour_mean_over: dict[datetime, set[str]] = {}
    no_export_high: list[dict[str, Any]] = []
    for phase, rows in hourly.items():
        for start, mean, mx in rows:
            d = day(start)
            if mx is not None:
                d["max_v"] = max(d["max_v"], round(mx, 1))
                if mx > limit_v:
                    hour_over.setdefault(start, set()).add(phase)
            if mean is not None:
                d["max_hour_mean"] = max(d["max_hour_mean"], round(mean, 1))
                if mean > limit_v:
                    hour_mean_over.setdefault(start, set()).add(phase)
                grid = grid_hourly.get(start)
                # High voltage while the house takes energy (or exports almost nothing)
                if mean >= limit_v - 3 and grid is not None and grid <= 100:
                    no_export_high.append({"hour": start.isoformat(timespec="minutes"), "phase": phase,
                                           "mean_v": round(mean, 1), "grid_w": round(grid)})
    for start in hour_over:
        day(start)["hours_max_over"] += 1
    for start in hour_mean_over:
        day(start)["hours_mean_over"] += 1
    for item in no_export_high:
        day(datetime.fromisoformat(item["hour"]))["hours_high_no_export"] += 1

    # 10-minute means (recent days): count intervals above the limit / outside ±10 %
    weeks: dict[str, dict[str, int]] = {}
    worst_10: list[dict[str, Any]] = []
    for phase, rows in five_min.items():
        for t, m in ten_minute_means(rows):
            d = day(t)
            d["ten_min_total"] = (d["ten_min_total"] or 0) + 1
            d["ten_min_over"] = (d["ten_min_over"] or 0) + (1 if m > limit_v else 0)
            iso = t.isocalendar()
            w = weeks.setdefault(f"{iso[0]}-W{iso[1]:02d}", {"total": 0, "outside": 0})
            w["total"] += 1
            if not LOW <= m <= HIGH:
                w["outside"] += 1
            if m > limit_v:
                worst_10.append({"start": t.isoformat(timespec="minutes"), "phase": phase,
                                 "mean_v": round(m, 1), "grid_w": round(grid_hourly.get(
                                     t.replace(minute=0), 0))})
    week_rows = [
        {"week": k, "intervals": v["total"], "outside": v["outside"],
         "within_pct": round(100 * (v["total"] - v["outside"]) / v["total"], 2) if v["total"] else None,
         "ok": v["total"] > 0 and (v["total"] - v["outside"]) / v["total"] >= 0.95}
        for k, v in sorted(weeks.items())
    ]
    day_rows = sorted(days.values(), key=lambda d: d["date"])
    worst_10.sort(key=lambda x: -x["mean_v"])
    return {
        "limit_v": limit_v,
        "phases": phase_balance(hourly, five_min, grid_hourly),
        "days": day_rows,
        "weeks": week_rows,
        "worst_10min": worst_10[:50],
        "high_without_export": sorted(no_export_high, key=lambda x: -x["mean_v"])[:50],
        "summary": {
            "days": len(day_rows),
            "days_over": sum(1 for d in day_rows if d["hours_max_over"] or (d["ten_min_over"] or 0)),
            "max_v": max((d["max_v"] for d in day_rows), default=None),
            "hours_max_over": sum(d["hours_max_over"] for d in day_rows),
            "hours_mean_over": sum(d["hours_mean_over"] for d in day_rows),
            "ten_min_over": sum(d["ten_min_over"] or 0 for d in day_rows),
            "hours_high_no_export": len(no_export_high),
            "first_day": day_rows[0]["date"] if day_rows else None,
        },
    }


UNBALANCE_PCT = 2.0   # PN-EN 50160: negative-sequence unbalance ≤ 2 % for 95 % of 10-min means / week
SPREAD_V = 5.0        # hours with the phases this far apart are listed


def _lvur(vals: list[float]) -> float:
    """Voltage unbalance from the three magnitudes (NEMA: max deviation from the mean, %).

    The inverter gives no phase angles, so the EN 50160 negative-sequence factor can't be
    computed exactly; for a low-voltage grid this is the usual close approximation.
    """
    avg = sum(vals) / len(vals)
    return max(abs(v - avg) for v in vals) / avg * 100 if avg else 0.0


def phase_balance(
    hourly: dict[str, list[tuple[datetime, float | None, float | None]]],
    five_min: dict[str, list[tuple[datetime, float]]],
    grid_hourly: dict[datetime, float],
) -> dict[str, Any]:
    """Phase-to-phase differences: per day, which phase is high, with / without export."""
    phases = sorted(hourly)
    by_hour: dict[datetime, dict[str, float]] = {}
    for ph in phases:
        for start, mean, _mx in hourly[ph]:
            if mean is not None:
                by_hour.setdefault(start, {})[ph] = mean
    hours = [(t, v) for t, v in sorted(by_hour.items()) if len(v) == len(phases) == 3]
    days: dict[str, dict[str, Any]] = {}
    highest = {ph: 0 for ph in phases}
    spread_export: list[float] = []
    spread_idle: list[float] = []
    top: list[dict[str, Any]] = []
    for t, v in hours:
        vals = [v[ph] for ph in phases]
        spread = max(vals) - min(vals)
        hi = max(phases, key=lambda ph: v[ph])
        lo = min(phases, key=lambda ph: v[ph])
        highest[hi] += 1
        grid = grid_hourly.get(t)
        exporting = grid is not None and grid > 300
        (spread_export if exporting else spread_idle).append(spread)
        d = days.setdefault(t.date().isoformat(), {
            "date": t.date().isoformat(), "hours": 0, "max_spread": 0.0, "sum_spread": 0.0,
            "hours_spread_over": 0, "max_unbalance_pct": 0.0, "high": {ph: 0 for ph in phases},
        })
        d["hours"] += 1
        d["sum_spread"] += spread
        d["max_spread"] = max(d["max_spread"], round(spread, 1))
        d["max_unbalance_pct"] = max(d["max_unbalance_pct"], round(_lvur(vals), 2))
        d["high"][hi] += 1
        if spread >= SPREAD_V:
            d["hours_spread_over"] += 1
            top.append({"hour": t.isoformat(timespec="minutes"), "spread_v": round(spread, 1),
                        "high": hi, "low": lo, **{ph: round(v[ph], 1) for ph in phases},
                        "grid_w": round(grid) if grid is not None else None})
    day_rows = []
    for d in sorted(days.values(), key=lambda x: x["date"]):
        d["avg_spread"] = round(d.pop("sum_spread") / d["hours"], 1)
        d["mostly_high"] = max(d["high"], key=d["high"].get)
        day_rows.append(d)

    # 10-minute means (recent days): EN 50160-style weekly check of the unbalance
    tens: dict[datetime, dict[str, float]] = {}
    for ph, rows in five_min.items():
        for t, m in ten_minute_means(rows):
            tens.setdefault(t, {})[ph] = m
    weeks: dict[str, dict[str, int]] = {}
    for t, v in tens.items():
        if len(v) != 3:
            continue
        iso = t.isocalendar()
        w = weeks.setdefault(f"{iso[0]}-W{iso[1]:02d}", {"total": 0, "over": 0})
        w["total"] += 1
        if _lvur(list(v.values())) > UNBALANCE_PCT:
            w["over"] += 1
    week_rows = [
        {"week": k, "intervals": w["total"], "over": w["over"],
         "within_pct": round(100 * (w["total"] - w["over"]) / w["total"], 2),
         "ok": (w["total"] - w["over"]) / w["total"] >= 0.95}
        for k, w in sorted(weeks.items()) if w["total"]
    ]

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 1) if xs else None

    n = len(hours)
    top.sort(key=lambda x: -x["spread_v"])
    return {
        "days": day_rows,
        "weeks": week_rows,
        "top_hours": top[:50],
        "summary": {
            "hours": n,
            "avg_spread": avg([s for s in spread_export + spread_idle]),
            "avg_spread_export": avg(spread_export),
            "avg_spread_no_export": avg(spread_idle),
            "max_spread": max((d["max_spread"] for d in day_rows), default=None),
            "max_unbalance_pct": max((d["max_unbalance_pct"] for d in day_rows), default=None),
            "hours_spread_over": sum(d["hours_spread_over"] for d in day_rows),
            "highest_share": {ph: round(100 * c / n, 1) for ph, c in highest.items()} if n else {},
            "spread_limit_v": SPREAD_V,
            "unbalance_limit_pct": UNBALANCE_PCT,
        },
    }


def window_start(now: datetime, days: int) -> datetime:
    return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
