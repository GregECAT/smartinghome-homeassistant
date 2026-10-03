# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Wind Calendar Engine for Smarting HOME.

Estimates what a (planned or existing) small wind turbine would produce at
this location from the local weather station's anemometer, day by day:

1. Hourly mean wind (HA recorder statistics of the station's wind sensor) is
   moved to the turbine's hub height (wind shear, power law).
2. Within each hour the wind fluctuates (gusts) — the expected power is the
   turbine's power curve averaged over that spread, not the curve at the mean
   (power grows with v³: the curve at a daily or hourly mean underestimates,
   and a mean below cut-in would give 0 although gusty hours produce).
3. Energy of each hour is valued at the tariff price of that hour (the energy
   would replace an import), unless the user set a fixed price.

The calendar is rebuilt from the recorder (restarts don't lose data; history
goes back as far as the recorder's long-term statistics) and kept in its own
store, not in settings.json. Today's status uses the completed hours from the
recorder plus the live samples of the current hour.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

WIND_CALENDAR_KEY = "wind_calendar"            # legacy keys in settings.json (migrated away)
WIND_CALENDAR_META_KEY = "wind_calendar_meta"
WIND_CALENDAR_VERSION = 3
MAX_RETENTION_YEARS = 5
STORE_KEY = "smartinghome.wind_calendar"

DEFAULT_TURBINE: dict[str, float] = {
    "power_kw": 3,
    "rotor_diameter": 3.2,
    "cut_in": 3.0,        # m/s at hub height
    "rated_speed": 12,    # m/s
    "cut_out": 25,        # m/s — storm stop
    "investment": 25000,
    "price_kwh": 0,       # 0 = value energy at the tariff price of each hour
    "sensor_height": 8,   # m — anemometer above ground
    "hub_height": 8,      # m — turbine hub above ground
    "count": 1,           # number of identical turbines (e.g. on the house corners)
}
AIR_DENSITY = 1.225       # kg/m³
TURBINE_EFFICIENCY = 0.35  # Cp of a small turbine (Betz limit 0.593)
SHEAR_ALPHA = 0.25        # wind shear exponent — suburban terrain, trees, houses
GUST_SPREAD = 0.35        # within-hour std. deviation / mean (turbulence near the ground)
DEFAULT_PRICE = 0.87      # zł/kWh when no tariff function is available
DEFAULT_WIND_ENTITY = "sensor.ecowitt_wind_speed_9747"


# ── Physics ─────────────────────────────────────────────────────────────────

def sanitize_turbine(raw: dict[str, Any] | None) -> dict[str, float]:
    """Turbine parameters with defaults for missing / non-positive values."""
    out = dict(DEFAULT_TURBINE)
    for key, default in DEFAULT_TURBINE.items():
        try:
            val = float((raw or {}).get(key, default))
        except (TypeError, ValueError):
            continue
        if key == "price_kwh":
            out[key] = max(val, 0.0)
        elif key == "count":
            out[key] = float(max(1, min(int(val), 20))) if val > 0 else 1.0
        elif val > 0:
            out[key] = val
    out["rated_speed"] = max(out["rated_speed"], out["cut_in"] + 0.5)
    out["cut_out"] = max(out["cut_out"], out["rated_speed"] + 1)
    return out


def hub_speed(v_sensor_ms: float, t: dict[str, float]) -> float:
    """Wind at hub height from the anemometer reading (power-law wind profile)."""
    return v_sensor_ms * (t["hub_height"] / t["sensor_height"]) ** SHEAR_ALPHA


def power_curve_w(v_hub_ms: float, t: dict[str, float]) -> float:
    """Turbine output at a steady wind speed (W)."""
    if v_hub_ms < t["cut_in"] or v_hub_ms >= t["cut_out"]:
        return 0.0
    rated_w = t["power_kw"] * 1000
    if v_hub_ms >= t["rated_speed"]:
        return rated_w
    area = math.pi * (t["rotor_diameter"] / 2) ** 2
    return min(rated_w, 0.5 * AIR_DENSITY * area * v_hub_ms ** 3 * TURBINE_EFFICIENCY)


def calc_wind_power_watts(wind_kmh: float, rotor_diameter_m: float) -> float:
    """Legacy helper: P = 0.5 × ρ × A × v³ × Cp (no curve limits)."""
    v = max(wind_kmh, 0.0) / 3.6
    area = math.pi * (rotor_diameter_m / 2) ** 2
    return 0.5 * AIR_DENSITY * area * v ** 3 * TURBINE_EFFICIENCY


_GRID = [i / 10 - 3.0 for i in range(61)]                       # −3σ … +3σ
_WEIGHTS = [math.exp(-0.5 * z * z) for z in _GRID]
_WSUM = sum(_WEIGHTS)


def expected_power_w(mean_sensor_ms: float, t: dict[str, float], spread: float = GUST_SPREAD) -> float:
    """Mean turbine output over an hour with the given mean wind (anemometer)."""
    m = hub_speed(max(mean_sensor_ms, 0.0), t)
    if m <= 0:
        return 0.0
    sigma = spread * m
    total = 0.0
    for z, w in zip(_GRID, _WEIGHTS):
        v = m + z * sigma
        if v > 0:
            total += w * power_curve_w(v, t)
    return total / _WSUM


def rayleigh_annual_kwh(mean_hub_ms: float, t: dict[str, float]) -> float:
    """Annual energy for a long-term mean wind at hub height (Rayleigh distribution)."""
    if mean_hub_ms <= 0:
        return 0.0
    step = 0.25
    total = 0.0
    for i in range(1, int(35 / step)):
        v = i * step
        pdf = (math.pi * v / (2 * mean_hub_ms ** 2)) * math.exp(-math.pi * v * v / (4 * mean_hub_ms ** 2))
        total += pdf * power_curve_w(v, t) * step
    return total * 8760 / 1000 * t.get("count", 1)


def day_record(
    hourly_kmh: dict[int, float],
    t: dict[str, float],
    price_at: Callable[[int], float],
    gust_max_kmh: float = 0.0,
) -> dict[str, Any]:
    """One day from hourly mean winds (km/h at the anemometer), all turbines together."""
    n = t.get("count", 1)
    rated_w = t["power_kw"] * 1000 * n
    kwh = revenue = peak = 0.0
    productive = 0
    for hour, kmh in hourly_kmh.items():
        p = expected_power_w(kmh / 3.6, t) * n
        e = p / 1000
        kwh += e
        revenue += e * (t["price_kwh"] if t["price_kwh"] > 0 else price_at(hour))
        peak = max(peak, p)
        if p >= 0.02 * rated_w:
            productive += 1
    hours = len(hourly_kmh)
    return {
        "avg_wind_kmh": round(sum(hourly_kmh.values()) / hours, 1) if hours else 0.0,
        "max_gust_kmh": round(gust_max_kmh, 1),
        "samples": hours,
        "hourly": [round(hourly_kmh[h], 1) if h in hourly_kmh else None for h in range(24)],
        "kwh_produced": round(kwh, 3),
        "revenue_pln": round(revenue, 2),
        "capacity_factor": round(kwh / (rated_w / 1000 * hours), 4) if hours and rated_w else 0.0,
        "productive_hours": productive,
        "peak_power_w": round(peak),
    }


# ── Engine ──────────────────────────────────────────────────────────────────

class WindCalendar:
    """Daily wind-energy calendar rebuilt from the recorder."""

    def __init__(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store: Store = Store(hass, 1, STORE_KEY)
        self._calendar: dict[str, dict] = {}
        self._meta: dict[str, Any] = {}
        self._loaded = False
        self._turbine: dict[str, float] = dict(DEFAULT_TURBINE)
        self._turbine_loaded_at: float | None = None
        self._turbine_refreshing = False
        self._entity = DEFAULT_WIND_ENTITY
        self._gust_entity = ""
        self._price_fn: Callable[[datetime], float] | None = None
        # Today: completed hours from the recorder + live samples of this hour
        self._today_hours: dict[int, float] = {}
        self._today_gust = 0.0
        self._today_date = ""
        self._today_fetched = 0.0
        self._today_refreshing = False
        self._cur_hour = -1
        self._cur_samples: list[float] = []

    # ── configuration from the coordinator ──

    def set_sources(self, wind_entity: str | None, gust_entity: str | None) -> None:
        if wind_entity:
            self._entity = wind_entity
        if gust_entity:
            self._gust_entity = gust_entity

    def set_price_fn(self, fn: Callable[[datetime], float]) -> None:
        """Tariff buy price (zł/kWh) at a moment — values the turbine's energy."""
        self._price_fn = fn

    def _price_on(self, day: date) -> Callable[[int], float]:
        def price(hour: int) -> float:
            if self._price_fn is None:
                return DEFAULT_PRICE
            try:
                return float(self._price_fn(datetime(day.year, day.month, day.day, hour, 30)))
            except Exception:  # noqa: BLE001
                return DEFAULT_PRICE
        return price

    # ── persistence ──

    async def async_load(self) -> None:
        from .settings_io import read_async, write_async

        stored = await self._store.async_load() or {}
        self._calendar = stored.get("days", {})
        self._meta = stored.get("meta", {})
        settings = await read_async(self.hass)
        self._turbine = sanitize_turbine(settings.get("wind_turbine"))
        self._turbine_loaded_at = time.monotonic()
        if settings.get(WIND_CALENDAR_KEY):
            # v1/v2 kept the whole calendar in settings.json (sent to the panel on
            # every settings read) — it is rebuilt from the recorder into the store
            await write_async(self.hass, {WIND_CALENDAR_KEY: {}, WIND_CALENDAR_META_KEY: {}})
        self._loaded = True
        _LOGGER.info("Wind calendar loaded: %d days", len(self._calendar))

    async def _persist(self) -> None:
        cutoff = (datetime.now() - timedelta(days=MAX_RETENTION_YEARS * 365)).strftime("%Y-%m-%d")
        for key in [k for k in self._calendar if k < cutoff]:
            del self._calendar[key]
        dates = sorted(self._calendar)
        self._meta.update({
            "last_update": datetime.now().isoformat(timespec="seconds"),
            "total_days": len(dates),
            "oldest_date": dates[0] if dates else "",
            "newest_date": dates[-1] if dates else "",
            "version": WIND_CALENDAR_VERSION,
        })
        await self._store.async_save({"days": self._calendar, "meta": self._meta})

    # ── turbine parameters ──

    def _get_turbine_params(self) -> dict[str, float]:
        """Cached turbine parameters; refreshed in the background every 5 min."""
        if (self._turbine_loaded_at is None or time.monotonic() - self._turbine_loaded_at > 300) \
                and not self._turbine_refreshing:
            self._turbine_refreshing = True

            async def _refresh() -> None:
                try:
                    await self._async_get_turbine_params(force=True)
                finally:
                    self._turbine_refreshing = False

            self.hass.async_create_task(_refresh())
        return self._turbine

    async def _async_get_turbine_params(self, force: bool = False) -> dict[str, float]:
        if force or self._turbine_loaded_at is None or time.monotonic() - self._turbine_loaded_at > 300:
            from .settings_io import read_async

            try:
                settings = await read_async(self.hass)
            except Exception:  # noqa: BLE001
                settings = {}
            self._turbine = sanitize_turbine(settings.get("wind_turbine"))
            self._turbine_loaded_at = time.monotonic()
        return self._turbine

    # ── recorder ──

    async def _hourly_stats(self, start: datetime, end: datetime) -> tuple[dict[str, dict[int, float]], dict[str, float]]:
        """{day: {hour: mean km/h}}, {day: max gust km/h} from long-term statistics."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
        from homeassistant.util import dt as dt_util

        ids = {self._entity} | ({self._gust_entity} if self._gust_entity else set())
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, dt_util.as_utc(start), dt_util.as_utc(end),
            ids, "hour", None, {"mean", "max"},
        )
        factor = self._unit_factor()

        def when(row: dict) -> datetime | None:
            begin = row.get("start")
            if isinstance(begin, (int, float)):
                return dt_util.as_local(datetime.fromtimestamp(begin, tz=dt_util.UTC))
            if isinstance(begin, datetime):
                return dt_util.as_local(begin)
            try:
                return dt_util.as_local(datetime.fromisoformat(str(begin)))
            except (TypeError, ValueError):
                return None

        days: dict[str, dict[int, float]] = {}
        gusts: dict[str, float] = {}
        for row in stats.get(self._entity, []):
            local, mean = when(row), row.get("mean")
            if local is None or mean is None:
                continue
            days.setdefault(local.strftime("%Y-%m-%d"), {})[local.hour] = max(float(mean), 0.0) * factor
            if not self._gust_entity and row.get("max") is not None:
                key = local.strftime("%Y-%m-%d")
                gusts[key] = max(gusts.get(key, 0.0), float(row["max"]) * factor)
        for row in stats.get(self._gust_entity, []) if self._gust_entity else []:
            local = when(row)
            if local is None or row.get("max") is None:
                continue
            key = local.strftime("%Y-%m-%d")
            gusts[key] = max(gusts.get(key, 0.0), float(row["max"]) * factor)
        return days, gusts

    def _unit_factor(self) -> float:
        """Sensor unit → km/h."""
        state = self.hass.states.get(self._entity)
        unit = str(state.attributes.get("unit_of_measurement", "km/h")).lower() if state else "km/h"
        return {"m/s": 3.6, "mph": 1.609, "kn": 1.852, "knots": 1.852}.get(unit, 1.0)

    async def bootstrap_from_recorder(self, force: bool = False) -> int:
        """Rebuild the calendar from the recorder's hourly statistics.

        Full rebuild when the stored calendar is from an older version (daily
        averages only) or forced; otherwise only the last few days are refreshed.
        """
        if not self.hass:
            return 0
        full = force or self._meta.get("version") != WIND_CALENDAR_VERSION or not self._calendar
        now = datetime.now()
        start = (now - timedelta(days=MAX_RETENTION_YEARS * 365 if full else 3)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            days, gusts = await self._hourly_stats(start, midnight)
        except Exception as err:  # noqa: BLE001 — recorder missing / sensor without statistics
            _LOGGER.warning("Wind calendar: recorder statistics unavailable (%s)", err)
            return 0
        turbine = await self._async_get_turbine_params()
        built = 0
        for key, hours in days.items():
            if len(hours) < 12:
                continue  # sensor offline most of the day
            day = date.fromisoformat(key)
            rec = day_record(hours, turbine, self._price_on(day), gusts.get(key, 0.0))
            rec["source"] = "recorder"
            self._calendar[key] = rec
            built += 1
        self._meta["last_bootstrap"] = now.isoformat(timespec="seconds")
        self._meta["entity"] = self._entity
        await self._persist()
        _LOGGER.info("Wind calendar: %d days %s from the recorder", built, "rebuilt" if full else "refreshed")
        return built

    async def recalculate_all(self) -> int:
        """New turbine parameters → recompute every stored day from its hourly winds."""
        turbine = await self._async_get_turbine_params(force=True)
        count = 0
        for key, rec in self._calendar.items():
            hourly = rec.get("hourly")
            if not hourly:
                continue
            hours = {h: v for h, v in enumerate(hourly) if v is not None}
            new = day_record(hours, turbine, self._price_on(date.fromisoformat(key)), rec.get("max_gust_kmh", 0.0))
            new["source"] = rec.get("source", "recorder")
            self._calendar[key] = new
            count += 1
        await self._persist()
        self._today_fetched = 0.0  # today's status too
        _LOGGER.info("Wind calendar recalculated: %d days", count)
        return count

    # ── live (coordinator tick) ──

    def accumulate_sample(self, wind_kmh: float | None, gust_kmh: float | None) -> None:
        """Live sample (~30 s): current hour + day rollover; completed hours come from the recorder."""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        if today != self._today_date:
            if self._today_date:
                self.hass.async_create_task(self.bootstrap_from_recorder())  # close yesterday
            self._today_date, self._today_hours, self._today_gust = today, {}, 0.0
            self._today_fetched = 0.0
        if now.hour != self._cur_hour:
            self._cur_hour, self._cur_samples = now.hour, []
            self._today_fetched = 0.0  # a new completed hour is in the recorder
        if wind_kmh is not None:
            self._cur_samples.append(max(wind_kmh, 0.0) * self._unit_factor())
        if gust_kmh is not None:
            self._today_gust = max(self._today_gust, gust_kmh * self._unit_factor())
        if time.time() - self._today_fetched > 600 and not self._today_refreshing:
            self._today_refreshing = True
            self.hass.async_create_task(self._refresh_today())

    async def _refresh_today(self) -> None:
        try:
            now = datetime.now()
            midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
            days, gusts = await self._hourly_stats(midnight, now.replace(minute=0, second=0, microsecond=0))
            key = now.strftime("%Y-%m-%d")
            self._today_hours = {h: v for h, v in days.get(key, {}).items() if h < now.hour}
            self._today_gust = max(self._today_gust, gusts.get(key, 0.0))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Wind today refresh failed: %s", err)
        finally:
            self._today_fetched = time.time()
            self._today_refreshing = False

    def get_today_status(self) -> dict[str, Any]:
        now = datetime.now()
        hours = dict(self._today_hours)
        if self._cur_samples:
            hours[now.hour] = sum(self._cur_samples) / len(self._cur_samples)
        turbine = self._get_turbine_params()
        if not hours:
            return {"date": now.strftime("%Y-%m-%d"), "avg_wind_kmh": 0, "max_gust_kmh": 0, "samples": 0,
                    "hours": 0, "est_kwh": 0, "est_revenue": 0, "productive_pct": 0, "productive_hours": 0,
                    "elapsed_hours": round(now.hour + now.minute / 60, 1)}
        rec = day_record(hours, turbine, self._price_on(now.date()), self._today_gust)
        # the current hour counts with its elapsed share
        cur = hours.get(now.hour)
        if cur is not None:
            share = now.minute / 60
            p_cur = expected_power_w(cur / 3.6, turbine) * turbine.get("count", 1) / 1000
            rec["kwh_produced"] = round(rec["kwh_produced"] - p_cur * (1 - share), 3)
        return {
            "date": now.strftime("%Y-%m-%d"),
            "avg_wind_kmh": rec["avg_wind_kmh"],
            "max_gust_kmh": rec["max_gust_kmh"],
            "samples": len(self._cur_samples),
            "hours": len(hours),
            "est_kwh": max(rec["kwh_produced"], 0.0),
            "est_revenue": rec["revenue_pln"],
            "productive_hours": rec["productive_hours"],
            "productive_pct": round(rec["productive_hours"] / len(hours) * 100, 1) if hours else 0,
            "elapsed_hours": round(now.hour + now.minute / 60, 1),
            "hub_wind_ms": round(hub_speed((cur if cur is not None else 0) / 3.6, turbine), 1),
            "power_now_w": round(expected_power_w((cur if cur is not None else 0) / 3.6, turbine) * turbine.get("count", 1)),
        }

    async def close_day(self) -> dict[str, Any] | None:
        """Kept for the service API: yesterday is closed from the recorder."""
        await self.bootstrap_from_recorder()
        key = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return self._calendar.get(key)

    # ── queries ──

    def get_calendar_data(self, start_date: str | None = None, end_date: str | None = None) -> dict[str, Any]:
        cal = self._calendar
        if start_date and end_date:
            cal = {k: v for k, v in cal.items() if start_date <= k <= end_date}
        # the panel doesn't need the hourly lists
        days = {k: {f: v for f, v in rec.items() if f != "hourly"} for k, rec in cal.items()}
        return {
            "days": days,
            "summary": self._compute_summary(cal) if cal else self._empty_summary(),
            "meta": {**self._meta, "turbine": self._get_turbine_params(),
                     "model": {"shear_alpha": SHEAR_ALPHA, "gust_spread": GUST_SPREAD, "cp": TURBINE_EFFICIENCY}},
        }

    def _compute_summary(self, days: dict[str, dict]) -> dict[str, Any]:
        if not days:
            return self._empty_summary()
        turbine = self._get_turbine_params()
        total_kwh = sum(r.get("kwh_produced", 0) for r in days.values())
        total_revenue = sum(r.get("revenue_pln", 0) for r in days.values())
        total_days = len(days)
        productive = [k for k, r in days.items() if r.get("kwh_produced", 0) >= 0.1]
        best = max(days.items(), key=lambda kv: kv[1].get("kwh_produced", 0))
        worst_candidates = [(k, r) for k, r in days.items() if r.get("kwh_produced", 0) >= 0.1]
        worst = min(worst_candidates, key=lambda kv: kv[1]["kwh_produced"]) if worst_candidates else ("", {"kwh_produced": 0})
        dist = {"calm": 0, "light": 0, "moderate": 0, "strong": 0, "very_strong": 0}
        for rec in days.values():
            ms = rec.get("avg_wind_kmh", 0) / 3.6
            key = "calm" if ms < 2 else "light" if ms < 4 else "moderate" if ms < 6 else "strong" if ms < 8 else "very_strong"
            dist[key] += 1
        avg_wind = sum(r.get("avg_wind_kmh", 0) for r in days.values()) / total_days
        hours = sum(r.get("samples", 24) for r in days.values()) or 1
        annual_revenue = total_revenue / total_days * 365
        annual_kwh = total_kwh / total_days * 365
        investment = turbine["investment"] * turbine.get("count", 1)  # price per turbine × count
        payback = round(investment / annual_revenue, 1) if annual_revenue > 0 else None
        return {
            "total_days": total_days,
            "productive_days": len(productive),
            "productive_pct": round(len(productive) / total_days * 100, 1),
            "total_kwh": round(total_kwh, 2),
            "total_revenue": round(total_revenue, 2),
            "avg_daily_kwh": round(total_kwh / total_days, 2),
            "avg_daily_revenue": round(total_revenue / total_days, 2),
            "avg_wind_kmh": round(avg_wind, 1),
            "avg_wind_ms": round(avg_wind / 3.6, 1),
            "avg_hub_wind_ms": round(hub_speed(avg_wind / 3.6, turbine), 1),
            "avg_capacity_factor": round(total_kwh / (turbine["power_kw"] * turbine.get("count", 1) * hours), 4),
            "best_day": {"date": best[0], "kwh": best[1].get("kwh_produced", 0)},
            "worst_productive_day": {"date": worst[0], "kwh": worst[1].get("kwh_produced", 0)},
            "wind_distribution": dist,
            "annual_kwh_est": round(annual_kwh, 0),
            "annual_revenue_est": round(annual_revenue, 0),
            "payback_years": payback,
            "profit_20y": round(annual_revenue * 20 - investment, 0),
            "investment": investment,
        }

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        return {
            "total_days": 0, "productive_days": 0, "productive_pct": 0, "total_kwh": 0, "total_revenue": 0,
            "avg_daily_kwh": 0, "avg_daily_revenue": 0, "avg_wind_kmh": 0, "avg_wind_ms": 0, "avg_hub_wind_ms": 0,
            "avg_capacity_factor": 0,
            "best_day": {"date": "", "kwh": 0}, "worst_productive_day": {"date": "", "kwh": 0},
            "wind_distribution": {"calm": 0, "light": 0, "moderate": 0, "strong": 0, "very_strong": 0},
            "annual_kwh_est": 0, "annual_revenue_est": 0, "payback_years": None, "profit_20y": 0, "investment": 0,
        }
