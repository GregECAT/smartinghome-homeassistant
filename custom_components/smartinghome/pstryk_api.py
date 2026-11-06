# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Pstryk meter over the Pstryk integration API (unified-metrics) — pure logic.

Endpoint GET https://api.pstryk.pl/integrations/meter-data/unified-metrics/ with the
API key from the Pstryk app in the Authorization header (the same key the "Pstryk.pl"
HA integration uses; Pstryk keeps ONE active key per account — generating a new one
revokes the old). Metrics used:
- meter_values (frames: energy_active_import_register / _export_register /
  energy_balance, kWh per frame) — resolution hour (history) and minute (last 24 h
  only, for the live power);
- cost — Pstryk's own hourly cost: energy_cost_net, service_cost_net,
  var_dist_cost_net, fix_dist_cost_net, power_fee_cost_net, vat, energy_import_cost
  (gross), energy_sold_value … (names from the API description; any other numeric
  field is kept as is).
Times are UTC; `resolution=hour` does not allow `for_tz`. A frame with `is_live` is
the one still running. 429 comes with Retry-After.

Pure Python (no Home Assistant imports) so it can be unit-tested.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode

API_URL = "https://api.pstryk.pl/integrations/meter-data/unified-metrics/"
METER_KEYS = ("meter_values", "meterValues")
IMPORT = "energy_active_import_register"
EXPORT = "energy_active_export_register"
# Pstryk's net cost parts of an imported kWh (what the invoice adds up)
NET_PARTS = ("energy_cost_net", "service_cost_net", "var_dist_cost_net", "fix_dist_cost_net",
             "power_fee_cost_net")
COST_LABELS = {
    "energy_cost_net": "Energia (TGE)",
    "service_cost_net": "Opłata za obsługę",
    "var_dist_cost_net": "Dystrybucja zmienna",
    "fix_dist_cost_net": "Dystrybucja stała",
    "power_fee_cost_net": "Opłata mocowa",
    "excise": "Akcyza",
    "excise_cost": "Akcyza",
    "vat": "VAT",
}
EXCISE_KEYS = ("excise", "excise_cost")  # outside the VAT base
VAT_RATE = 0.23
# Live check 2026-11-05 (business meter, before the sales contract starts): cost frames
# carry energy_cost_net, service_cost_net, var_dist_cost_net, power_fee_cost_net, excise,
# energy_sold_value(_net) — no vat / fix_dist_cost_net / energy_import_cost. Their sum
# matched kWh × the integration's buy price ÷ 1.23 to the grosz.
DEFAULT_RETRY_S = 900
MAX_HOURLY_KWH = 500.0


def iso_z(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def url(metrics: str, resolution: str, start: datetime, end: datetime) -> str:
    return API_URL + "?" + urlencode({
        "metrics": metrics, "resolution": resolution,
        "window_start": iso_z(start), "window_end": iso_z(end),
    })


def hour_floor(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def parse_frames(data: Any) -> list[dict[str, Any]]:
    """[{start, end, live, imp, exp, cost: {field: value}}] from a unified-metrics response."""
    frames = data.get("frames") if isinstance(data, Mapping) else None
    if not isinstance(frames, list):
        return []
    out = []
    for fr in frames:
        if not isinstance(fr, Mapping):
            continue
        start, end = _time(fr.get("start")), _time(fr.get("end"))
        if start is None:
            continue
        metrics = fr.get("metrics") if isinstance(fr.get("metrics"), Mapping) else {}
        mv = next((metrics[k] for k in METER_KEYS if isinstance(metrics.get(k), Mapping)), {})
        cost = metrics.get("cost") if isinstance(metrics.get("cost"), Mapping) else {}
        out.append({
            "start": start.astimezone(timezone.utc),
            "end": end.astimezone(timezone.utc) if end else None,
            "live": bool(fr.get("is_live")),
            "imp": _num(mv.get(IMPORT)),
            "exp": _num(mv.get(EXPORT)),
            "cost": {k: v for k, v in ((k, _num(v)) for k, v in cost.items()) if v is not None},
        })
    return out


def merge_hours(stored: dict[int, dict[str, Any]], frames: Iterable[Mapping[str, Any]],
                now: datetime, keep_days: int = 400) -> tuple[int | None, bool]:
    """Merge hourly frames into {epoch hour: {"i", "e", "c", "live"}}.

    Returns (earliest changed epoch hour, changed). A frame without meter data (no
    reading yet) is skipped; the running hour is kept with live=True and replaced later.
    """
    cutoff = int((now - timedelta(days=keep_days)).timestamp()) // 3600
    changed_from: int | None = None
    for old in [h for h in stored if h < cutoff]:
        del stored[old]
    for fr in frames:
        imp = fr.get("imp")
        if imp is None or not 0 <= imp < MAX_HOURLY_KWH:
            continue
        key = int(fr["start"].timestamp()) // 3600
        if key < cutoff:
            continue
        row = {"i": round(imp, 4), "e": round(fr.get("exp") or 0.0, 4), "c": dict(fr.get("cost") or {}),
               "live": bool(fr.get("live"))}
        if stored.get(key) != row:
            stored[key] = row
            changed_from = key if changed_from is None else min(changed_from, key)
    return changed_from, changed_from is not None


def hour_dt(key: int) -> datetime:
    return datetime.fromtimestamp(key * 3600, tz=timezone.utc)


def live_power(frames: Iterable[Mapping[str, Any]]) -> tuple[float | None, datetime | None]:
    """(W, minute start) from the last complete minute frame with data (import − export)."""
    done = [f for f in frames if not f.get("live") and f.get("imp") is not None]
    if not done:
        return None, None
    last = max(done, key=lambda f: f["start"])
    minutes = 1.0
    if last.get("end"):
        minutes = max((last["end"] - last["start"]).total_seconds() / 60, 1 / 60)
    kwh = (last["imp"] or 0.0) - (last.get("exp") or 0.0)
    return round(kwh / minutes * 60 * 1000, 1), last["start"]


def last_data(frames: Iterable[Mapping[str, Any]]) -> datetime | None:
    """Start of the newest frame that carries a reading (live or not)."""
    starts = [f["start"] for f in frames if f.get("imp") is not None]
    return max(starts) if starts else None


def hour_gross(row: Mapping[str, Any]) -> float | None:
    """Pstryk's gross cost of one stored hour (None when the API sent no cost)."""
    c = row.get("c") or {}
    if not c:
        return None
    if "energy_import_cost" in c:
        return float(c["energy_import_cost"])
    parts = sum(c.get(k, 0.0) for k in NET_PARTS)
    vat = c["vat"] if "vat" in c else parts * VAT_RATE
    return parts + vat + sum(c.get(k, 0.0) for k in EXCISE_KEYS)


def statistic_rows(stored: Mapping[int, Mapping[str, Any]], field: Any, from_key: int | None
                   ) -> list[tuple[datetime, float, float]]:
    """(hour start UTC, value, cumulative sum) for complete hours from from_key on.

    field — a stored key ("i", "e") or a function (epoch hour, row) → value.
    The running hour is left out — statistics are written once an hour is done.
    """
    total = 0.0
    out = []
    for key in sorted(stored):
        row = stored[key]
        if row.get("live"):
            continue
        kwh = float((field(key, row) if callable(field) else row.get(field)) or 0.0)
        total += kwh
        if from_key is None or key >= from_key:
            out.append((hour_dt(key), round(kwh, 4), round(total, 4)))
    return out


def period(stored: Mapping[int, Mapping[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    """kWh and Pstryk's cost (net parts, VAT, gross) for hours in [start, end)."""
    a, b = int(start.timestamp()) // 3600, int(end.timestamp()) // 3600
    rows = [stored[k] for k in sorted(stored) if a <= k < b]
    cost: dict[str, float] = {}
    for r in rows:
        for k, v in (r.get("c") or {}).items():
            cost[k] = cost.get(k, 0.0) + v
    parts = sum(cost.get(k, 0.0) for k in NET_PARTS)
    excise = sum(cost.get(k, 0.0) for k in EXCISE_KEYS)
    net = parts + excise
    priced = sum(1 for r in rows if r.get("c"))
    gross = cost.get("energy_import_cost")
    if gross is None and priced:
        vat = cost["vat"] if "vat" in cost else parts * VAT_RATE
        gross = parts + vat + excise
    return {
        "hours": len(rows),
        "priced_hours": priced,
        "kwh": round(sum(r.get("i", 0.0) for r in rows), 3),
        "export_kwh": round(sum(r.get("e", 0.0) for r in rows), 3),
        "cost": {k: round(v, 2) for k, v in sorted(cost.items())},
        "net": round(net, 2) if priced else None,
        "gross": round(gross, 2) if gross is not None else None,
    }


def breakeven(stored: Mapping[int, Mapping[str, Any]], start: datetime, end: datetime,
              tariff_net: Any, tz: Any = timezone.utc) -> dict[str, Any] | None:
    """Market price at which Pstryk costs the same as a fixed tariff — same hours, netto.

    Uses hours in [start, end) with a reading and Pstryk's cost (energy_cost_net = the
    exchange-price part). tariff_net(key, row) → the tariff's netto cost of that hour
    (energy + variable distribution + per-kWh fees). Pstryk's netto = NET_PARTS + excise.
    market_avg — consumption-weighted exchange price paid; threshold — the weighted
    average exchange price at which both would cost the same with this hourly shape:
    market_avg + (tariff − Pstryk) / kWh. Below the threshold Pstryk is cheaper.
    Fixed fees are not included (meter_ws adds the monthly fee difference).
    """
    a, b = int(start.timestamp()) // 3600, int(end.timestamp()) // 3600
    days: dict[str, list[float]] = {}
    tot = [0.0, 0.0, 0.0, 0.0]  # kWh, market, pstryk, tariff
    hours = 0
    for key in sorted(stored):
        row = stored[key]
        c = row.get("c") or {}
        kwh = float(row.get("i") or 0.0)
        if not a <= key < b or row.get("live") or kwh <= 0 or "energy_cost_net" not in c:
            continue
        pst = sum(c.get(k, 0.0) for k in NET_PARTS) + sum(c.get(k, 0.0) for k in EXCISE_KEYS)
        vals = (kwh, float(c["energy_cost_net"]), pst, float(tariff_net(key, row)))
        day = hour_dt(key).astimezone(tz).date().isoformat()
        d = days.setdefault(day, [0.0, 0.0, 0.0, 0.0, 0])
        for i, v in enumerate(vals):
            tot[i] += v
            d[i] += v
        d[4] += 1
        hours += 1
    if not hours or tot[0] <= 0:
        return None

    def summary(k: float, m: float, p: float, t: float) -> dict[str, float]:
        return {"kwh": round(k, 3), "market_avg": round(m / k, 4), "pstryk_avg": round(p / k, 4),
                "tariff_avg": round(t / k, 4), "threshold": round((m + t - p) / k, 4),
                "margin": round((t - p) / k, 4)}

    return {"hours": hours, **summary(*tot),
            "daily": [{"date": day, "hours": int(v[4]), **summary(*v[:4])} for day, v in sorted(days.items())]}


def local_hours(stored: Mapping[int, Mapping[str, Any]], tz: Any) -> list[tuple[datetime, float]]:
    """(local naive hour start, import kWh) — the meter panel's hour keys."""
    return [(hour_dt(k).astimezone(tz).replace(tzinfo=None), float(stored[k].get("i") or 0.0))
            for k in sorted(stored)]


def retry_after(header: Any, default: int = DEFAULT_RETRY_S) -> int:
    """Seconds to wait after a 429 (Retry-After in seconds; at least a minute)."""
    value = _num(header)
    return max(60, int(value)) if value is not None else default
