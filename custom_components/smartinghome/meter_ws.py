# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""WebSocket API of the "Energia i koszty" panel.

Reads hourly statistics of a utility meter (TAURON eLicznik via Tauron
AMIplus, or any cumulative kWh sensor with long-term statistics), splits
them into tariff zones and prices them with meter_tariffs.
"""
from __future__ import annotations

import statistics as pystats
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import meter_tariffs as mt

SETTINGS_KEY = "meter"
DEFAULT_METER_SETTINGS: dict[str, Any] = {
    "site_kind": "business",
    "tariff": "C13",
    "contract_kw": None,
    "prices": {},
    "energy_stat": "",
    "power_entity": "",
    "pf_entity": "",
    "baseload_alert_w": 150,
}
MAX_HOURLY_KWH = 500.0  # guards counter resets / gaps


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_meter_tariffs)
    websocket_api.async_register_command(hass, ws_meter_sources)
    websocket_api.async_register_command(hass, ws_meter_summary)


def meter_settings(settings: dict[str, Any]) -> dict[str, Any]:
    stored = settings.get(SETTINGS_KEY)
    return {**DEFAULT_METER_SETTINGS, **(stored if isinstance(stored, dict) else {})}


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/meter/tariffs"})
@callback
def ws_meter_tariffs(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], {
        "tariffs": mt.catalog(),
        "zone_labels": mt.ZONE_LABELS,
        "fixed_labels": mt.FIXED_LABELS,
        "vat": mt.VAT,
        "per_kwh_fees": mt.PER_KWH_FEES_NETTO,
    })


_NOT_CONSUMPTION = ("export", "generation", "production", "feed_in", "feedin", "return",
                    "_pv", "pv_", "solar", "battery", "discharge", "charge")
_CONSUMPTION_HINTS = ("consumption", "import", "pobor", "pobór", "zuzycie", "zużycie")


def _is_consumption_candidate(sid: str, name: str) -> bool:
    if sid.startswith("sensor.smarting_home"):
        return False  # our own counters come from the inverter sensor map
    text = f"{sid} {name}".lower()
    return not any(word in text for word in _NOT_CONSUMPTION)


_AGGREGATES = ("daily", "monthly", "yearly", "annual", "12_months", "configurable",
               "today", "this_month", "this_year")


def _auto_pick(item: dict[str, str]) -> bool:
    """Safe enough to pick without asking: a utility meter or an import counter.

    Period aggregates (e.g. Tauron AMIplus "daily energy consumption") are
    excluded: eLicznik fills them once a day, so a whole day would land in a
    single hour and the zone split would be wrong. Its hourly external
    statistics (tauron_amiplus:…_consumption) are the right source.
    """
    text = f"{item['id']} {item['name']}".lower().replace(" ", "_")
    if any(word in text for word in _AGGREGATES):
        return False
    return any(hint in text for hint in _CONSUMPTION_HINTS)


async def _energy_statistics(hass: HomeAssistant) -> list[dict[str, str]]:
    """Cumulative kWh consumption statistics usable as a meter, utility meters first."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import list_statistic_ids

    rows = await get_instance(hass).async_add_executor_job(list_statistic_ids, hass)
    out = []
    for row in rows:
        unit = (row.get("statistics_unit_of_measurement") or row.get("unit_of_measurement") or "")
        if not row.get("has_sum") or unit not in ("kWh", "Wh", "MWh"):
            continue
        sid = row["statistic_id"]
        name = row.get("name") or sid
        if not _is_consumption_candidate(sid, name):
            continue
        out.append({"id": sid, "name": name, "unit": unit})

    def rank(item: dict[str, str]) -> tuple[int, str]:
        # Hourly-balanced consumption is what the invoice bills (net-billing
        # prosumers: raw meter import is much higher). Without PV both are equal.
        sid = item["id"]
        if ":" in sid and sid.endswith("_balanced_consumption"):
            return (0, sid)
        if ":" in sid and sid.endswith("_consumption"):
            return (1, sid)
        if _auto_pick(item):
            return (2, sid)
        return (3, sid)

    return sorted(out, key=rank)


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/meter/sources"})
@websocket_api.async_response
async def ws_meter_sources(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], {"energy": await _energy_statistics(hass)})


def _hours_from_rows(rows: list[dict[str, Any]], unit: str) -> list[tuple[datetime, float]]:
    from homeassistant.util import dt as dt_util

    scale = {"Wh": 0.001, "MWh": 1000.0}.get(unit, 1.0)
    out = []
    for row in rows:
        change, start = row.get("change"), row.get("start")
        if change is None or start is None:
            continue
        kwh = float(change) * scale
        if not 0 <= kwh < MAX_HOURLY_KWH:
            continue
        ts = dt_util.utc_from_timestamp(start) if isinstance(start, (int, float)) else start
        out.append((dt_util.as_local(ts).replace(tzinfo=None), kwh))
    return out


def summarize(
    hours: list[tuple[datetime, float]], cfg: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Everything the panel shows, from (local naive hour start, kWh) pairs."""
    tariff = mt.resolve(cfg.get("tariff", "C13"), cfg.get("prices"))
    contract_kw = mt._num(cfg.get("contract_kw"))
    fixed = mt.monthly_fixed(tariff, contract_kw)
    fixed_total = round(sum(fixed.values()), 2)

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    days_in_month = monthrange(now.year, now.month)[1]

    cur = [(t, k) for t, k in hours if t >= month_start]
    prev = [(t, k) for t, k in hours if prev_start <= t < month_start]
    data_until = max((t for t, _ in hours), default=None)

    def month_block(rows: list[tuple[datetime, float]], full_days: int, fixed: float) -> dict[str, Any]:
        c = mt.cost_hours(tariff, rows)
        days = len({t.date() for t, _ in rows})
        return {**c, "days_with_data": days, "days": full_days,
                "fixed": round(fixed, 2), "total": round(c["variable"] + fixed, 2)}

    # Month to date carries the fixed fees of the days elapsed, not the whole month
    current = month_block(cur, days_in_month, fixed_total * now.day / days_in_month)
    current["fixed_month"] = fixed_total
    # Forecast: average hour so far (keeps the zone mix) × hours in month.
    # Counted in hours — eLicznik data ends mid-day, a partial day is not a day.
    # Needs 3 days of data; one night alone would say nothing about the month.
    if len(cur) >= 72:
        f = days_in_month * 24 / len(cur)
        current["forecast_kwh"] = round(current["kwh"] * f, 1)
        current["forecast_variable"] = round(current["variable"] * f, 2)
        current["forecast_total"] = round(current["variable"] * f + fixed_total, 2)
    prev_days = monthrange(prev_start.year, prev_start.month)[1]
    prev_with_data = len({t.date() for t, _ in prev})
    # A month the meter covers only partly (new eLicznik account) carries the fixed
    # fees of the days with data — a full month of fees on one day of kWh misleads
    prev_complete = prev_with_data >= prev_days - 1
    previous = month_block(prev, prev_days,
                           fixed_total if prev_complete else fixed_total * prev_with_data / prev_days)
    previous["complete"] = prev_complete
    previous["fixed_month"] = fixed_total

    # Daily series (last 31 days with zone split)
    by_day: dict[str, list[tuple[datetime, float]]] = {}
    for t, k in hours:
        if t >= now - timedelta(days=31):
            by_day.setdefault(t.date().isoformat(), []).append((t, k))
    daily = []
    for day in sorted(by_day):
        c = mt.cost_hours(tariff, by_day[day])
        d = datetime.fromisoformat(day).date()
        # hours < 23: the day eLicznik has not finished yet (23/25 h on DST days)
        daily.append({"date": day, "free": mt.is_free_day(d), "kwh": c["kwh"],
                      "hours": len(by_day[day]),
                      "variable": c["variable"],
                      "zones": {z: v["kwh"] for z, v in c["zones"].items()}})

    # Baseload: median of night hours 1–4 over the last 14 days with data
    recent = [(t, k) for t, k in hours if data_until and t >= data_until - timedelta(days=14)]
    night = [k for t, k in recent if 1 <= t.hour < 5]
    baseload_w = round(pystats.median(night) * 1000) if night else None
    free_avg = [k for t, k in recent if mt.is_free_day(t.date())]
    work_hours = [k for t, k in recent if not mt.is_free_day(t.date()) and 8 <= t.hour < 16]

    # Average profile per hour of day (last 28 days): working vs free days
    prof_rows = [(t, k) for t, k in hours if data_until and t >= data_until - timedelta(days=28)]
    profile: dict[str, list[float | None]] = {}
    for label, pick in (("work", False), ("free", True)):
        buckets: list[list[float]] = [[] for _ in range(24)]
        for t, k in prof_rows:
            if mt.is_free_day(t.date()) == pick:
                buckets[t.hour].append(k)
        profile[label] = [round(sum(b) / len(b), 3) if b else None for b in buckets]

    def peak_of(rows: list[tuple[datetime, float]]) -> dict[str, Any] | None:
        if not rows:
            return None
        t, k = max(rows, key=lambda r: r[1])
        return {"kw": round(k, 2), "at": t.isoformat(timespec="minutes")}

    # Zone of each hour on a working day this month (chart background)
    workday = next(
        (month_start + timedelta(days=i) for i in range(days_in_month)
         if not mt.is_free_day((month_start + timedelta(days=i)).date())),
        month_start,
    )
    zone_by_hour = [mt.zone_at(tariff["id"], workday.replace(hour=h)) for h in range(24)]

    baseload_year_kwh = round(baseload_w * 8.76, 0) if baseload_w is not None else None
    off_price = sum(mt.variable_price(tariff, "off_peak" if "off_peak" in tariff["zones"] else "flat"))
    return {
        "tariff": tariff,
        "contract_kw": contract_kw,
        "fixed_breakdown": fixed,
        "fixed_total": fixed_total,
        "current_month": current,
        "previous_month": previous,
        "daily": daily,
        "profile": profile,
        "zone_by_hour": zone_by_hour,
        "baseload": {
            "w": baseload_w,
            "year_kwh": baseload_year_kwh,
            "year_cost": round(baseload_year_kwh * off_price, 0) if baseload_year_kwh else None,
            "free_day_avg_w": round(sum(free_avg) / len(free_avg) * 1000) if free_avg else None,
            "work_hours_avg_w": round(sum(work_hours) / len(work_hours) * 1000) if work_hours else None,
        },
        "peak": {"current_month": peak_of(cur), "previous_month": peak_of(prev)},
        "data_until": data_until.isoformat(timespec="minutes") if data_until else None,
        # Hours with any consumption — a meter that has only reported zeros so
        # far (new eLicznik account) counts as "no data yet"
        "hours_count": sum(1 for _, k in hours if k > 0),
    }


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/meter/summary"})
@websocket_api.async_response
async def ws_meter_summary(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period
    from homeassistant.util import dt as dt_util

    from .settings_io import read_async

    cfg = meter_settings(await read_async(hass))
    sources = await _energy_statistics(hass)
    stat = next((s for s in sources if s["id"] == cfg.get("energy_stat")), None)
    if stat is None and not cfg.get("energy_stat"):
        # Automatic: only an unambiguous consumption meter, never a guess
        stat = next((s for s in sources if _auto_pick(s)), None)
    if stat is None:
        connection.send_result(msg["id"], {"settings": cfg, "source": None, "sources": sources})
        return

    now = dt_util.now()
    start = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).replace(day=1)
    start = min(start, now - timedelta(days=31))
    instance = get_instance(hass)
    stats = await instance.async_add_executor_job(
        statistics_during_period, hass, dt_util.as_utc(start), None,
        {stat["id"]}, "hour", None, {"change"},
    )
    hours = _hours_from_rows(stats.get(stat["id"], []), stat["unit"])
    result = summarize(hours, cfg, now.replace(tzinfo=None))
    connection.send_result(msg["id"], {**result, "settings": cfg, "source": stat, "sources": sources})
