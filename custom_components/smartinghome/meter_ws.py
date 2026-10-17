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

An independent meter (Shelly EM / 3EM / Pro 3EM, Eastron SDM over Modbus, any
HA meter — meter_device) adds export and balance, phases, tg φ. Read only:
nothing here writes to the meter or to an inverter.
"""
from __future__ import annotations

import statistics as pystats
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import meter_device as md
from . import meter_tariffs as mt

SETTINGS_KEY = "meter"
DEFAULT_METER_SETTINGS: dict[str, Any] = {
    **md.METER_DEFAULTS,
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
    websocket_api.async_register_command(hass, ws_meter_detect)


def meter_settings(settings: dict[str, Any]) -> dict[str, Any]:
    stored = settings.get(SETTINGS_KEY)
    out = {**DEFAULT_METER_SETTINGS, **(stored if isinstance(stored, dict) else {})}
    for key in md.LIST_ROLES:
        value = out.get(key)
        out[key] = [str(v) for v in value if v] if isinstance(value, list) else []
    return out


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


async def _statistic_ids(hass: HomeAssistant) -> list[dict[str, Any]]:
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import list_statistic_ids

    return await get_instance(hass).async_add_executor_job(list_statistic_ids, hass)


def _unit(row: dict[str, Any]) -> str:
    return row.get("statistics_unit_of_measurement") or row.get("unit_of_measurement") or ""


def _sum_statistics(rows: list[dict[str, Any]], units: tuple[str, ...]) -> list[dict[str, str]]:
    """Cumulative statistics (has_sum) in the given units: id, name, unit."""
    return [
        {"id": r["statistic_id"], "name": r.get("name") or r["statistic_id"], "unit": _unit(r)}
        for r in rows if r.get("has_sum") and _unit(r) in units
    ]


_ENERGY_UNITS = ("kWh", "Wh", "MWh")
_REACTIVE_ENERGY_UNITS = ("kvarh", "varh", "kVArh", "VArh")


def _consumption_candidates(all_energy: list[dict[str, str]]) -> list[dict[str, str]]:
    """Cumulative kWh consumption statistics usable as a meter, utility meters first."""
    out = [item for item in all_energy if _is_consumption_candidate(item["id"], item["name"])]

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


def _sources_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    energy_all = _sum_statistics(rows, _ENERGY_UNITS)
    return {
        "energy": _consumption_candidates(energy_all),
        "energy_all": sorted(energy_all, key=lambda x: x["id"]),
        "reactive": sorted(_sum_statistics(rows, _REACTIVE_ENERGY_UNITS), key=lambda x: x["id"]),
    }


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/meter/sources"})
@websocket_api.async_response
async def ws_meter_sources(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], _sources_payload(await _statistic_ids(hass)))


# Integrations whose entities the "Wykryj licznik" button looks at
DETECT_PLATFORMS = ("shelly", "modbus")


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/meter/detect"})
@callback
def ws_meter_detect(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Meter proposals from the entity registry — shown for approval, never applied here."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    ents, devs = er.async_get(hass), dr.async_get(hass)
    rows = []
    for e in ents.entities.values():
        if e.domain != "sensor" or e.platform not in DETECT_PLATFORMS:
            continue
        dev = devs.async_get(e.device_id) if e.device_id else None
        st = hass.states.get(e.entity_id)
        attrs = st.attributes if st else {}
        rows.append({
            "entity_id": e.entity_id,
            "platform": e.platform,
            "device_id": e.device_id or "",
            "device_name": (dev.name_by_user or dev.name) if dev else "",
            "unique_id": e.unique_id or "",
            "original_name": e.original_name or e.name or attrs.get("friendly_name") or "",
            "translation_key": e.translation_key or "",
            "unit": e.unit_of_measurement or attrs.get("unit_of_measurement") or "",
            "device_class": e.device_class or e.original_device_class or attrs.get("device_class") or "",
            "state_class": (e.capabilities or {}).get("state_class") or attrs.get("state_class") or "",
            "disabled": e.disabled_by is not None,
        })
    connection.send_result(msg["id"], {
        "proposals": md.propose_mapping(rows),
        "scanned": len(rows),
        "platforms": list(DETECT_PLATFORMS),
    })


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
    meta = await _statistic_ids(hass)
    payload = _sources_payload(meta)
    sources = payload["energy"]
    lists = {"sources": sources, "energy_all": payload["energy_all"], "reactive": payload["reactive"]}
    known = {x["id"]: x for x in payload["energy_all"]}
    units = {r["statistic_id"]: _unit(r) for r in meta}
    label = cfg.get("meter_device") or "Licznik"

    import_ids = cfg["import_stats"] or ([cfg["energy_stat"]] if cfg.get("energy_stat") else [])
    stat = _stat_source(import_ids, known, label)
    if stat is None and not import_ids:
        # Automatic: only an unambiguous consumption meter, never a guess
        auto = next((s for s in sources if _auto_pick(s)), None)
        stat = {**auto, "ids": [auto["id"]]} if auto else None
    if stat is None:
        connection.send_result(msg["id"], {"settings": cfg, "source": None, **lists})
        return
    export_ids = cfg["export_stats"] or ([cfg["export_stat"]] if cfg.get("export_stat") else [])
    export_src = _stat_source(export_ids, known, label)
    react_id = cfg.get("reactive_energy_stat") or ""
    react_unit = next((x["unit"] for x in payload["reactive"] if x["id"] == react_id), None)

    now = dt_util.now()
    start = (now.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).replace(day=1)
    start = min(start, now - timedelta(days=31))
    instance = get_instance(hass)
    change_ids = set(stat["ids"]) | set(export_src["ids"] if export_src else [])
    if react_unit:
        change_ids.add(react_id)
    stats = await instance.async_add_executor_job(
        statistics_during_period, hass, dt_util.as_utc(start), None,
        change_ids, "hour", None, {"change"},
    )

    def series(ids: list[str]) -> list[tuple[datetime, float]]:
        return md.sum_hours([_hours_from_rows(stats.get(i, []), known.get(i, {}).get("unit", "kWh")) for i in ids])

    imp = series(stat["ids"])
    exp = series(export_src["ids"]) if export_src else []
    balanced = bool(exp) and cfg.get("balance_hourly", True) is not False
    cost_hours = md.balance_hours(imp, exp)[0] if balanced else imp
    now_naive = now.replace(tzinfo=None)
    result = summarize(cost_hours, cfg, now_naive)
    result["cost_basis"] = "balanced" if balanced else "import"
    if exp:
        by_day: dict[str, float] = {}
        for t, k in exp:
            key = t.date().isoformat()
            by_day[key] = by_day.get(key, 0.0) + k
        for row in result["daily"]:
            row["export_kwh"] = round(by_day.get(row["date"], 0.0), 3)

    # tg φ: kvarh counter, else hourly means of P and Q
    react_kvarh: list[tuple[datetime, float]] = []
    if react_unit:
        react_kvarh = _scaled_hours(stats.get(react_id, []), react_unit, "reactive_energy", "change")
    p_mean: list[tuple[datetime, float]] = []
    q_mean: list[tuple[datetime, float]] = []
    p_id, q_id = cfg.get("power_entity") or "", cfg.get("reactive_power_entity") or ""
    if not react_kvarh and p_id and q_id:
        means = await instance.async_add_executor_job(
            statistics_during_period, hass, dt_util.as_utc(start), None,
            {p_id, q_id}, "hour", None, {"mean"},
        )
        p_mean = _scaled_hours(means.get(p_id, []), units.get(p_id), "power", "mean")
        q_mean = _scaled_hours(means.get(q_id, []), units.get(q_id), "reactive_power", "mean")
    react_rows, react_src = _reactive_hours(cfg, imp, react_kvarh, p_mean, q_mean)
    result.update(meter_extras(imp, exp, react_rows, now_naive))
    if "reactive" in result:
        result["reactive"]["source"] = react_src

    # Phases: today's min / max (5-minute statistics); live values come from HA states
    phase_roles = [r for r in ("power_entity", *(f"{k}_{p}" for k in ("power", "voltage", "current")
                                                  for p in md.PHASES)) if cfg.get(r)]
    if phase_roles:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        short = await instance.async_add_executor_job(
            statistics_during_period, hass, dt_util.as_utc(midnight), None,
            {cfg[r] for r in phase_roles}, "5minute", None, {"min", "max"},
        )
        rows = {}
        for role in phase_roles:
            eid = cfg[role]
            kind = "power" if role.startswith("power") else ""
            rows[role] = [(md.to_base(md.num(r.get("min")), units.get(eid), kind),
                           md.to_base(md.num(r.get("max")), units.get(eid), kind))
                          for r in short.get(eid, [])]
        result["phases_today"] = md.day_extremes(rows)

    connection.send_result(msg["id"], {
        **result, "settings": cfg, "source": stat, "export_source": export_src, **lists,
    })


def meter_extras(
    imp: list[tuple[datetime, float]],
    exp: list[tuple[datetime, float]],
    reactive: list[tuple[datetime, float, float]],
    now: datetime,
) -> dict[str, Any]:
    """Export / balance and tg φ per month (current, previous) from hourly series."""
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (month_start - timedelta(days=1)).replace(day=1)
    out: dict[str, Any] = {}
    if exp:
        out["exchange"] = {
            "current_month": md.exchange_month(imp, exp, month_start, now + timedelta(days=1)),
            "previous_month": md.exchange_month(imp, exp, prev_start, month_start),
        }
    if reactive:
        out["reactive"] = {
            "current_month": md.tan_phi_period([r for r in reactive if r[0] >= month_start]),
            "previous_month": md.tan_phi_period([r for r in reactive if prev_start <= r[0] < month_start]),
            "limit": md.TAN_PHI_LIMIT,
        }
    return out


def _reactive_hours(
    cfg: dict[str, Any],
    imp: list[tuple[datetime, float]],
    react_kvarh: list[tuple[datetime, float]],
    p_mean: list[tuple[datetime, float]],
    q_mean: list[tuple[datetime, float]],
) -> tuple[list[tuple[datetime, float, float]], str | None]:
    """(hour, P kWh, Q kvarh): from the kvarh counter + import, else from mean P and Q.

    The kvarh counter only grows, so it cannot tell capacitive from inductive energy;
    signed mean reactive power can (Q < 0 = capacitive, as the meter reports it).
    """
    if react_kvarh and imp:
        q = dict(react_kvarh)
        return [(t, k, q[t]) for t, k in imp if t in q], "energy"
    if p_mean and q_mean:
        q = dict(q_mean)
        rows = []
        for t, p_w in p_mean:
            if t not in q:
                continue
            drawn = -md.grid_power_canonical(p_w, cfg.get("power_sign"))  # + import
            rows.append((t, drawn / 1000, q[t] / 1000))
        return rows, "power"
    return [], None


def _stat_source(ids: list[str], known: dict[str, dict[str, str]], label: str) -> dict[str, Any] | None:
    stats = [known[i] for i in ids if i in known]
    if not stats:
        return None
    if len(stats) == 1:
        return {**stats[0], "ids": [stats[0]["id"]]}
    return {"id": " + ".join(x["id"] for x in stats), "name": f"{label} (suma {len(stats)} liczników)",
            "unit": stats[0]["unit"], "ids": [x["id"] for x in stats]}


def _scaled_hours(rows: list[dict[str, Any]], unit: str | None, kind: str, field: str
                  ) -> list[tuple[datetime, float]]:
    """(local naive hour start, value in the base unit) from statistic rows."""
    from homeassistant.util import dt as dt_util

    out = []
    for row in rows:
        value, start = md.num(row.get(field)), row.get("start")
        if value is None or start is None:
            continue
        value = md.to_base(value, unit, kind)
        if field == "change" and not 0 <= value < MAX_HOURLY_KWH:
            continue  # counter reset / gap
        ts = dt_util.utc_from_timestamp(start) if isinstance(start, (int, float)) else start
        out.append((dt_util.as_local(ts).replace(tzinfo=None), value))
    return out
