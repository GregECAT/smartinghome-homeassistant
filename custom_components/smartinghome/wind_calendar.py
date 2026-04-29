"""Wind Calendar Engine for Smarting HOME.

Calendar-based wind energy profitability analysis that tracks daily wind
averages and calculates per-day energy production/revenue.

Instead of using annual average wind (which underestimates profitability),
this engine:
1. Accumulates wind speed samples every coordinator tick (~30s)
2. At midnight, closes the day with computed average, production, and revenue
3. Stores up to 5 years of daily records in settings.json
4. Can bootstrap historical data from HA Recorder API

The frontend then queries this calendar to show profitability by
day/week/month/year — answering "how many days per year was the turbine
profitable?" instead of "what is the average wind speed?".
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

WIND_CALENDAR_KEY = "wind_calendar"
WIND_CALENDAR_META_KEY = "wind_calendar_meta"
WIND_CALENDAR_VERSION = 2
MAX_RETENTION_YEARS = 5

# Default turbine params (overridden by user settings)
DEFAULT_TURBINE = {
    "power_kw": 3,
    "rotor_diameter": 3.2,
    "cut_in": 3.0,       # m/s
    "rated_speed": 12,    # m/s
    "investment": 25000,
    "price_kwh": 0.87,
}

# Air density at sea level (kg/m³)
AIR_DENSITY = 1.225
# Small turbine efficiency (Betz limit ~0.593, real ~0.35)
TURBINE_EFFICIENCY = 0.35


def calc_wind_power_watts(wind_kmh: float, rotor_diameter_m: float) -> float:
    """Calculate theoretical wind power output in watts.

    P = 0.5 × ρ × A × v³ × Cp
    """
    v = wind_kmh / 3.6  # m/s
    if v <= 0:
        return 0.0
    a = math.pi * (rotor_diameter_m / 2) ** 2  # swept area m²
    return 0.5 * AIR_DENSITY * a * (v ** 3) * TURBINE_EFFICIENCY


def calc_day_production(
    avg_wind_kmh: float,
    turbine: dict[str, Any],
    hours: float = 24.0,
) -> dict[str, Any]:
    """Calculate energy production for one day given average wind speed.

    Uses the daily average wind speed to compute power output, capped at
    the turbine's nominal power. If wind is below cut-in, production is 0.

    Returns dict with kwh_produced, revenue_pln, capacity_factor, etc.
    """
    power_kw = turbine.get("power_kw", 3)
    diameter = turbine.get("rotor_diameter", 3.2)
    cut_in = turbine.get("cut_in", 3.0)
    rated_speed = turbine.get("rated_speed", 12)
    price_kwh = turbine.get("price_kwh", 0.87)
    nominal_w = power_kw * 1000

    wind_ms = avg_wind_kmh / 3.6

    if wind_ms < cut_in:
        return {
            "kwh_produced": 0.0,
            "revenue_pln": 0.0,
            "capacity_factor": 0.0,
            "avg_power_w": 0.0,
            "status": "below_cutin",
        }

    # Calculate average power from average wind
    avg_power = calc_wind_power_watts(avg_wind_kmh, diameter)

    # Cap at rated power (when wind >= rated_speed)
    if wind_ms >= rated_speed:
        avg_power = nominal_w
    elif avg_power > nominal_w:
        avg_power = nominal_w

    kwh = (avg_power * hours) / 1000
    revenue = kwh * price_kwh
    capacity_factor = avg_power / nominal_w if nominal_w > 0 else 0.0

    return {
        "kwh_produced": round(kwh, 3),
        "revenue_pln": round(revenue, 2),
        "capacity_factor": round(capacity_factor, 4),
        "avg_power_w": round(avg_power, 1),
        "status": "full_power" if wind_ms >= rated_speed else "partial",
    }


class WindCalendar:
    """Calendar-based wind energy tracking engine."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the wind calendar."""
        self.hass = hass

        # Intra-day accumulator
        self._samples: list[float] = []       # wind speed km/h samples
        self._gust_max: float = 0.0           # max gust today
        self._current_date: str = ""          # YYYY-MM-DD
        self._productive_count: int = 0       # samples above cut-in

        # Calendar data (loaded from settings.json)
        self._calendar: dict[str, dict] = {}
        self._meta: dict[str, Any] = {}
        self._loaded = False

    # ── Public API ──────────────────────────────────────────────

    async def async_load(self) -> None:
        """Load calendar data from settings.json."""
        from .settings_io import read_sync
        settings = await self.hass.async_add_executor_job(read_sync, self.hass)
        self._calendar = settings.get(WIND_CALENDAR_KEY, {})
        self._meta = settings.get(WIND_CALENDAR_META_KEY, {})
        self._loaded = True
        _LOGGER.info(
            "Wind calendar loaded: %d days, oldest=%s",
            len(self._calendar),
            self._meta.get("oldest_date", "N/A"),
        )

    def accumulate_sample(
        self, wind_kmh: float | None, gust_kmh: float | None
    ) -> None:
        """Add a wind speed sample from the coordinator tick.

        Called every ~30 seconds with current Ecowitt data.
        """
        if wind_kmh is None:
            return

        today = datetime.now().strftime("%Y-%m-%d")

        # Midnight rollover check
        if self._current_date and self._current_date != today:
            # Schedule day close (async) — don't block coordinator
            self.hass.async_create_task(self._close_day_async())

        if not self._current_date:
            self._current_date = today

        self._samples.append(wind_kmh)
        if gust_kmh is not None and gust_kmh > self._gust_max:
            self._gust_max = gust_kmh

        # Track productive samples (above default cut-in)
        turbine = self._get_turbine_params()
        cut_in_kmh = turbine.get("cut_in", 3.0) * 3.6
        if wind_kmh >= cut_in_kmh:
            self._productive_count += 1

    async def close_day(self) -> dict[str, Any] | None:
        """Close the current day, compute production, and persist.

        Returns the day record or None if no data.
        """
        if not self._samples:
            return None

        date_key = self._current_date or datetime.now().strftime("%Y-%m-%d")
        avg_wind = sum(self._samples) / len(self._samples)
        turbine = self._get_turbine_params()

        # Calculate production
        production = calc_day_production(avg_wind, turbine)

        # Estimate productive hours from sample ratio
        total_samples = len(self._samples)
        productive_ratio = (
            self._productive_count / total_samples
            if total_samples > 0 else 0
        )
        productive_hours = round(productive_ratio * 24, 1)

        # Build record
        record = {
            "avg_wind_kmh": round(avg_wind, 1),
            "max_gust_kmh": round(self._gust_max, 1),
            "samples": total_samples,
            "kwh_produced": production["kwh_produced"],
            "revenue_pln": production["revenue_pln"],
            "capacity_factor": production["capacity_factor"],
            "productive_hours": productive_hours,
            "peak_power_w": round(
                calc_wind_power_watts(self._gust_max, turbine.get("rotor_diameter", 3.2)),
                0,
            ),
        }

        # Store in calendar
        self._calendar[date_key] = record

        # Prune old data
        pruned = self._prune_old_data()

        # Update metadata
        dates = sorted(self._calendar.keys())
        self._meta = {
            "last_update": datetime.now().isoformat(),
            "total_days": len(self._calendar),
            "oldest_date": dates[0] if dates else "",
            "newest_date": dates[-1] if dates else "",
            "version": WIND_CALENDAR_VERSION,
        }

        # Persist
        await self._persist()

        _LOGGER.info(
            "Wind calendar day closed: %s — avg=%.1f km/h, "
            "kWh=%.2f, revenue=%.2f PLN, productive_h=%.1f, pruned=%d",
            date_key, avg_wind, production["kwh_produced"],
            production["revenue_pln"], productive_hours, pruned,
        )

        # Reset accumulator for new day
        self._samples = []
        self._gust_max = 0.0
        self._productive_count = 0
        self._current_date = datetime.now().strftime("%Y-%m-%d")

        return record

    async def bootstrap_from_recorder(self) -> int:
        """Bootstrap historical data from HA Recorder API.

        Fetches up to 5 years of hourly wind speed statistics and
        computes daily averages for each day found.

        Returns the number of days bootstrapped.
        """
        if not self.hass:
            return 0

        # Check if already bootstrapped recently (within 24h)
        last_bootstrap = self._meta.get("last_bootstrap", "")
        if last_bootstrap:
            try:
                last_dt = datetime.fromisoformat(last_bootstrap)
                if (datetime.now() - last_dt).total_seconds() < 86400:
                    _LOGGER.info(
                        "Wind calendar bootstrap skipped — last run %s",
                        last_bootstrap,
                    )
                    return 0
            except (ValueError, TypeError):
                pass

        _LOGGER.info("Wind calendar: starting bootstrap from Recorder...")


        # Use the WebSocket-style call
        try:
            now = datetime.now()
            start = now - timedelta(days=MAX_RETENTION_YEARS * 365)
            start = start.replace(hour=0, minute=0, second=0, microsecond=0)

            # Try direct Recorder WS call (same as frontend uses)
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
            from homeassistant.components.recorder import get_instance

            recorder_instance = get_instance(self.hass)

            stats_result = await recorder_instance.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                now,
                {"sensor.ecowitt_wind_speed_9747"},
                "hour",
                None,
                {"mean"},
            )

            hourly_data = stats_result.get(
                "sensor.ecowitt_wind_speed_9747", []
            )

            if not hourly_data:
                _LOGGER.warning(
                    "Wind calendar bootstrap: no Recorder data found"
                )
                self._meta["last_bootstrap"] = now.isoformat()
                await self._persist()
                return 0

            # Group hourly means by date
            daily_buckets: dict[str, list[float]] = {}
            daily_gusts: dict[str, float] = {}

            for entry in hourly_data:
                mean_val = entry.get("mean")
                if mean_val is None:
                    continue
                try:
                    dt = datetime.fromisoformat(str(entry.get("start", "")))
                except (ValueError, TypeError):
                    continue
                date_key = dt.strftime("%Y-%m-%d")
                if date_key not in daily_buckets:
                    daily_buckets[date_key] = []
                    daily_gusts[date_key] = 0.0
                daily_buckets[date_key].append(float(mean_val))
                # Max hourly mean as rough gust estimate
                if float(mean_val) > daily_gusts[date_key]:
                    daily_gusts[date_key] = float(mean_val)

            # Compute daily records
            turbine = self._get_turbine_params()
            bootstrapped = 0

            for date_key, values in daily_buckets.items():
                # Skip if we already have data for this day
                if date_key in self._calendar:
                    continue

                avg_wind = sum(values) / len(values)
                production = calc_day_production(avg_wind, turbine)

                # Estimate productive hours
                cut_in_kmh = turbine.get("cut_in", 3.0) * 3.6
                productive = sum(1 for v in values if v >= cut_in_kmh)
                productive_hours = round(
                    (productive / len(values)) * 24, 1
                ) if values else 0

                self._calendar[date_key] = {
                    "avg_wind_kmh": round(avg_wind, 1),
                    "max_gust_kmh": round(daily_gusts.get(date_key, 0), 1),
                    "samples": len(values),
                    "kwh_produced": production["kwh_produced"],
                    "revenue_pln": production["revenue_pln"],
                    "capacity_factor": production["capacity_factor"],
                    "productive_hours": productive_hours,
                    "peak_power_w": round(
                        calc_wind_power_watts(
                            daily_gusts.get(date_key, 0),
                            turbine.get("rotor_diameter", 3.2),
                        ),
                        0,
                    ),
                    "source": "recorder",
                }
                bootstrapped += 1

            # Prune and update meta
            self._prune_old_data()
            dates = sorted(self._calendar.keys())
            self._meta = {
                "last_bootstrap": now.isoformat(),
                "last_update": now.isoformat(),
                "total_days": len(self._calendar),
                "oldest_date": dates[0] if dates else "",
                "newest_date": dates[-1] if dates else "",
                "version": WIND_CALENDAR_VERSION,
                "bootstrap_count": bootstrapped,
            }

            await self._persist()

            _LOGGER.info(
                "Wind calendar bootstrapped: %d new days from Recorder "
                "(total %d days, range %s → %s)",
                bootstrapped,
                len(self._calendar),
                dates[0] if dates else "?",
                dates[-1] if dates else "?",
            )

            return bootstrapped

        except ImportError:
            _LOGGER.warning(
                "Wind calendar bootstrap: Recorder API not available"
            )
            self._meta["last_bootstrap"] = datetime.now().isoformat()
            await self._persist()
            return 0
        except Exception as err:
            _LOGGER.error("Wind calendar bootstrap failed: %s", err)
            self._meta["last_bootstrap"] = datetime.now().isoformat()
            await self._persist()
            return 0

    async def recalculate_all(self) -> int:
        """Recalculate production/revenue for all historical days.

        Called when user changes turbine parameters. Wind data stays the same,
        but kWh/revenue/capacity_factor are recomputed.

        Returns number of days recalculated.
        """
        turbine = self._get_turbine_params()
        count = 0

        for date_key, record in self._calendar.items():
            avg_wind = record.get("avg_wind_kmh", 0)
            if avg_wind <= 0:
                continue

            production = calc_day_production(avg_wind, turbine)
            record["kwh_produced"] = production["kwh_produced"]
            record["revenue_pln"] = production["revenue_pln"]
            record["capacity_factor"] = production["capacity_factor"]
            record["avg_power_w"] = production.get("avg_power_w", 0)

            # Recalc peak power from gust
            gust = record.get("max_gust_kmh", 0)
            if gust > 0:
                record["peak_power_w"] = round(
                    calc_wind_power_watts(
                        gust, turbine.get("rotor_diameter", 3.2)
                    ),
                    0,
                )

            # Recalc productive hours
            cut_in_kmh = turbine.get("cut_in", 3.0) * 3.6
            if avg_wind >= cut_in_kmh:
                # Keep existing productive_hours or estimate from avg
                pass
            else:
                record["productive_hours"] = 0

            count += 1

        if count > 0:
            self._meta["last_update"] = datetime.now().isoformat()
            await self._persist()

        _LOGGER.info(
            "Wind calendar recalculated: %d days with new turbine params", count
        )
        return count

    def get_calendar_data(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Get calendar data for a date range.

        If no dates provided, returns all data.
        Returns dict with 'days' (filtered records) and 'summary'.
        """
        cal = self._calendar

        if start_date and end_date:
            cal = {
                k: v for k, v in cal.items()
                if start_date <= k <= end_date
            }

        if not cal:
            return {
                "days": {},
                "summary": self._empty_summary(),
                "meta": self._meta,
            }

        return {
            "days": cal,
            "summary": self._compute_summary(cal),
            "meta": self._meta,
        }

    def get_today_status(self) -> dict[str, Any]:
        """Get the current day's accumulation status (live)."""
        if not self._samples:
            return {
                "date": self._current_date or datetime.now().strftime("%Y-%m-%d"),
                "avg_wind_kmh": 0,
                "max_gust_kmh": 0,
                "samples": 0,
                "est_kwh": 0,
                "est_revenue": 0,
                "productive_pct": 0,
            }

        avg = sum(self._samples) / len(self._samples)
        turbine = self._get_turbine_params()

        # Estimate production for elapsed hours
        now = datetime.now()
        elapsed_hours = now.hour + now.minute / 60

        production = calc_day_production(
            avg, turbine, hours=elapsed_hours
        )

        total = len(self._samples)
        productive_pct = round(
            (self._productive_count / total) * 100, 1
        ) if total > 0 else 0

        return {
            "date": self._current_date,
            "avg_wind_kmh": round(avg, 1),
            "max_gust_kmh": round(self._gust_max, 1),
            "samples": total,
            "est_kwh": production["kwh_produced"],
            "est_revenue": production["revenue_pln"],
            "productive_pct": productive_pct,
            "elapsed_hours": round(elapsed_hours, 1),
        }

    # ── Private helpers ─────────────────────────────────────────

    def _get_turbine_params(self) -> dict[str, Any]:
        """Read turbine parameters from settings (sync, cached)."""
        try:
            from .settings_io import read_sync
            settings = read_sync(self.hass)
            wt = settings.get("wind_turbine")
            if wt and isinstance(wt, dict):
                return wt
        except Exception:
            pass
        return DEFAULT_TURBINE

    async def _close_day_async(self) -> None:
        """Async wrapper for close_day (called from midnight rollover)."""
        try:
            await self.close_day()
        except Exception as err:
            _LOGGER.error("Wind calendar close_day failed: %s", err)

    def _prune_old_data(self) -> int:
        """Remove records older than MAX_RETENTION_YEARS. Returns count removed."""
        cutoff = (
            datetime.now() - timedelta(days=MAX_RETENTION_YEARS * 365)
        ).strftime("%Y-%m-%d")

        old_keys = [k for k in self._calendar if k < cutoff]
        for k in old_keys:
            del self._calendar[k]
        return len(old_keys)

    async def _persist(self) -> None:
        """Save calendar and meta to settings.json."""
        from .settings_io import write_async
        await write_async(self.hass, {
            WIND_CALENDAR_KEY: self._calendar,
            WIND_CALENDAR_META_KEY: self._meta,
        })

    def _compute_summary(self, days: dict[str, dict]) -> dict[str, Any]:
        """Compute aggregate summary for a set of day records."""
        if not days:
            return self._empty_summary()

        total_kwh = 0.0
        total_revenue = 0.0
        total_wind_sum = 0.0
        productive_days = 0
        best_day = {"date": "", "kwh": 0}
        worst_productive_day = {"date": "", "kwh": float("inf")}
        wind_distribution = {
            "calm": 0,          # < 2 m/s (< 7.2 km/h)
            "light": 0,         # 2-4 m/s
            "moderate": 0,      # 4-6 m/s
            "strong": 0,        # 6-8 m/s
            "very_strong": 0,   # > 8 m/s
        }

        for date_key, rec in days.items():
            kwh = rec.get("kwh_produced", 0)
            total_kwh += kwh
            total_revenue += rec.get("revenue_pln", 0)
            avg_wind = rec.get("avg_wind_kmh", 0)
            total_wind_sum += avg_wind

            if kwh > 0:
                productive_days += 1

            if kwh > best_day["kwh"]:
                best_day = {"date": date_key, "kwh": kwh}

            if kwh > 0 and kwh < worst_productive_day["kwh"]:
                worst_productive_day = {"date": date_key, "kwh": kwh}

            # Wind class distribution
            wind_ms = avg_wind / 3.6
            if wind_ms < 2:
                wind_distribution["calm"] += 1
            elif wind_ms < 4:
                wind_distribution["light"] += 1
            elif wind_ms < 6:
                wind_distribution["moderate"] += 1
            elif wind_ms < 8:
                wind_distribution["strong"] += 1
            else:
                wind_distribution["very_strong"] += 1

        total_days = len(days)
        avg_wind = total_wind_sum / total_days if total_days > 0 else 0

        # Average capacity factor
        cf_sum = sum(rec.get("capacity_factor", 0) for rec in days.values())
        avg_capacity_factor = cf_sum / total_days if total_days > 0 else 0

        # Fix worst if never set
        if worst_productive_day["kwh"] == float("inf"):
            worst_productive_day = {"date": "", "kwh": 0}

        # Investment ROI calculation
        turbine = self._get_turbine_params()
        investment = turbine.get("investment", 25000)
        # Annualize revenue if we have partial year data
        if total_days > 0 and total_days < 365:
            annual_revenue_est = (total_revenue / total_days) * 365
        else:
            annual_revenue_est = total_revenue

        payback_years = (
            round(investment / annual_revenue_est, 1)
            if annual_revenue_est > 0 else None
        )
        profit_20y = round(annual_revenue_est * 20 - investment, 0)

        return {
            "total_days": total_days,
            "productive_days": productive_days,
            "productive_pct": round(
                (productive_days / total_days) * 100, 1
            ) if total_days > 0 else 0,
            "total_kwh": round(total_kwh, 2),
            "total_revenue": round(total_revenue, 2),
            "avg_daily_kwh": round(
                total_kwh / total_days, 2
            ) if total_days > 0 else 0,
            "avg_daily_revenue": round(
                total_revenue / total_days, 2
            ) if total_days > 0 else 0,
            "avg_wind_kmh": round(avg_wind, 1),
            "avg_wind_ms": round(avg_wind / 3.6, 1),
            "avg_capacity_factor": round(avg_capacity_factor, 4),
            "best_day": best_day,
            "worst_productive_day": worst_productive_day,
            "wind_distribution": wind_distribution,
            "annual_revenue_est": round(annual_revenue_est, 0),
            "payback_years": payback_years,
            "profit_20y": profit_20y,
            "investment": investment,
        }

    @staticmethod
    def _empty_summary() -> dict[str, Any]:
        """Return an empty summary structure."""
        return {
            "total_days": 0,
            "productive_days": 0,
            "productive_pct": 0,
            "total_kwh": 0,
            "total_revenue": 0,
            "avg_daily_kwh": 0,
            "avg_daily_revenue": 0,
            "avg_wind_kmh": 0,
            "avg_wind_ms": 0,
            "best_day": {"date": "", "kwh": 0},
            "worst_productive_day": {"date": "", "kwh": 0},
            "wind_distribution": {
                "calm": 0, "light": 0, "moderate": 0,
                "strong": 0, "very_strong": 0,
            },
            "annual_revenue_est": 0,
            "payback_years": None,
            "profit_20y": 0,
            "investment": 0,
        }
