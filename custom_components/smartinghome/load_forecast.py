"""House load forecast from Home Assistant history.

Hourly means of the house load (recorder statistics) over the last weeks give
one 24-hour profile for working days and one for days off (weekends + Polish
public holidays), with recent days weighted more. A heating model fitted on
the same days (daily kWh vs heating degrees from the outdoor temperature)
normalises the history and scales the forecast to the expected temperature —
a cold evening is planned with more load than the average one.

The pure fitting/forecast functions take plain dicts so they can be unit-tested.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .pl_holidays import is_day_off

_LOGGER = logging.getLogger(__name__)

HISTORY_DAYS = 42
HALF_LIFE_DAYS = 14.0
BASE_TEMP = 15.5          # °C — no heating above this daily mean
MIN_HOURS_PER_DAY = 20
REFRESH_S = 3600


def _daytype(day: date) -> str:
    return "off" if is_day_off(day) else "work"


@dataclass
class LoadModel:
    profiles: dict[str, list[float | None]] = field(
        default_factory=lambda: {"work": [None] * 24, "off": [None] * 24}
    )
    base_kwh: float = 0.0     # daily kWh at/above BASE_TEMP
    per_degree: float = 0.0   # extra kWh per heating degree
    days: int = 0

    def temp_factor(self, temp: float | None) -> float:
        if temp is None or self.base_kwh <= 0 or self.per_degree <= 0:
            return 1.0
        pred = self.base_kwh + self.per_degree * max(BASE_TEMP - temp, 0.0)
        return min(max(pred / self.base_kwh, 0.7), 2.0)


def fit(
    hourly: dict[date, dict[int, float]],
    daily_temps: dict[date, float],
    today: date,
) -> LoadModel:
    """hourly: {date: {hour: kW}} → profiles + heating model."""
    days = {d: h for d, h in hourly.items() if len(h) >= MIN_HOURS_PER_DAY and d < today}
    model = LoadModel(days=len(days))
    if not days:
        return model

    def weight(d: date) -> float:
        return 0.5 ** ((today - d).days / HALF_LIFE_DAYS)

    # Heating model: weighted least squares of daily kWh on heating degrees
    pts = [
        (max(BASE_TEMP - daily_temps[d], 0.0), sum(h.values()) * 24 / len(h), weight(d))
        for d, h in days.items() if d in daily_temps
    ]
    if len(pts) >= 7:
        sw = sum(w for _, _, w in pts)
        mx = sum(x * w for x, _, w in pts) / sw
        my = sum(y * w for _, y, w in pts) / sw
        vxx = sum(w * (x - mx) ** 2 for x, _, w in pts)
        if vxx / sw >= 4.0:  # enough temperature spread to tell heating from noise
            slope = sum(w * (x - mx) * (y - my) for x, y, w in pts) / vxx
            model.per_degree = max(slope, 0.0)
            model.base_kwh = max(my - model.per_degree * mx, 1.0)

    # Profiles, normalised to BASE_TEMP when the heating model is known
    sums: dict[str, list[float]] = {"work": [0.0] * 24, "off": [0.0] * 24}
    wsum: dict[str, list[float]] = {"work": [0.0] * 24, "off": [0.0] * 24}
    for d, hours in days.items():
        norm = model.temp_factor(daily_temps.get(d))
        kind = _daytype(d)
        w = weight(d)
        for hour, kw in hours.items():
            sums[kind][hour] += w * kw / norm
            wsum[kind][hour] += w
    for kind in ("work", "off"):
        model.profiles[kind] = [
            sums[kind][h] / wsum[kind][h] if wsum[kind][h] > 0 else None for h in range(24)
        ]
    return model


def forecast_kw(model: LoadModel, when: datetime, temp: float | None) -> float | None:
    """Expected mean house load (kW) in the hour starting at `when`."""
    kind = _daytype(when.date())
    value = model.profiles[kind][when.hour]
    if value is None:  # no day of this type yet — use the other one
        value = model.profiles["off" if kind == "work" else "work"][when.hour]
    if value is None:
        return None
    return value * model.temp_factor(temp)


class LoadForecaster:
    """Keeps the fitted model; refreshed hourly from recorder statistics."""

    def __init__(self, hass: Any, entity_id: str) -> None:
        self.hass = hass
        self.entity_id = entity_id
        self.model = LoadModel()
        self._fetched = 0.0
        self.daily_temps: dict[date, float] = {}

    async def async_refresh(self, daily_temps: dict[date, float], force: bool = False) -> None:
        if daily_temps:
            self.daily_temps = daily_temps
        if not force and time.time() - self._fetched < REFRESH_S:
            return
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period
        from homeassistant.util import dt as dt_util

        now = dt_util.now()
        start = dt_util.as_utc(now - timedelta(days=HISTORY_DAYS))
        try:
            stats = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period, self.hass, start, None,
                {self.entity_id}, "hour", None, {"mean"},
            )
        except Exception as err:  # noqa: BLE001 — recorder missing / no statistics
            _LOGGER.debug("Load history unavailable: %s", err)
            return
        hourly: dict[date, dict[int, float]] = {}
        for row in stats.get(self.entity_id, []):
            mean, begin = row.get("mean"), row.get("start")
            if mean is None or begin is None:
                continue
            if isinstance(begin, (int, float)):
                begin = datetime.fromtimestamp(begin, tz=dt_util.UTC)
            local = dt_util.as_local(begin)
            hourly.setdefault(local.date(), {})[local.hour] = max(float(mean), 0.0) / 1000
        self.model = fit(hourly, self.daily_temps, now.date())
        self._fetched = time.time()

    def kw(self, when: datetime, temps: dict[date, float] | None = None) -> float | None:
        temp = (temps or self.daily_temps).get(when.date())
        return forecast_kw(self.model, when, temp)

    def status(self) -> dict[str, Any]:
        m = self.model
        return {
            "days": m.days,
            "base_kwh": round(m.base_kwh, 1),
            "kwh_per_degree": round(m.per_degree, 2),
            "work": [round(v, 2) if v is not None else None for v in m.profiles["work"]],
            "off": [round(v, 2) if v is not None else None for v in m.profiles["off"]],
        }
