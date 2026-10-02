"""PV forecast per panel plane (Open-Meteo), calibrated with the measured MPPT energy.

The panel's PV string config (settings "pv_string_config") describes each
MPPT as one or more planes: direction, tilt, panel count × power. For every
plane Open-Meteo gives the hourly global tilted irradiance (GTI); energy is
GTI × kWp × performance ratio with a cell-temperature derate. Each MPPT then
gets its own calibration factor = measured energy / modelled energy over the
last complete days (energy ledger), so shading, soiling and clipping that the
model doesn't know are learned from the installation itself.

Also fetches the hourly outdoor temperature (past + forecast) used by the
load forecast. Pure computation is kept in module functions for unit tests.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# Commercial installations use the customer endpoint with an API key (settings "open_meteo_api_key")
OPEN_METEO_CUSTOMER_URL = "https://customer-api.open-meteo.com/v1/forecast"
PERFORMANCE_RATIO = 0.86      # inverter, wiring, mismatch, soiling (before calibration)
TEMP_COEFF = 0.004            # power loss per °C of cell temperature above 25 °C
REFRESH_S = 1800              # forecast refresh period
PAST_DAYS = 14                # history for calibration and the load-temperature model
CAL_MIN_DAYS = 2
CAL_RANGE = (0.4, 1.6)

# Open-Meteo azimuth: 0 = south, −90 = east, 90 = west
_AZIMUTH = {"S": 0, "SW": 45, "W": 90, "NW": 135, "N": 180, "NE": -135, "E": -90, "SE": -45}


@dataclass
class Plane:
    mppt: int        # 1-based MPPT / PV input
    kwp: float
    tilt: float
    azimuth: float
    label: str = ""


def planes_from_config(cfg: dict[str, Any] | None) -> list[Plane]:
    """settings.pv_string_config → planes (one per substring)."""
    planes: list[Plane] = []
    for i in range(1, 5):
        sc = (cfg or {}).get(f"pv{i}") or {}
        for sub in sc.get("substrings") or []:
            try:
                kwp = float(sub.get("panel_count", 0)) * float(sub.get("panel_power", 0)) / 1000
                tilt = float(sub.get("tilt", 35))
            except (TypeError, ValueError):
                continue
            direction = str(sub.get("direction", "S")).upper()
            if kwp <= 0 or direction not in _AZIMUTH:
                continue
            planes.append(Plane(i, kwp, tilt, _AZIMUTH[direction], f"pv{i} {direction} {tilt:.0f}°"))
    return planes


def plane_energy(gti_wm2: float, temp_c: float | None, kwp: float) -> float:
    """kWh in one hour for a plane from its mean GTI (W/m²) in that hour."""
    if gti_wm2 <= 0:
        return 0.0
    cell = (temp_c if temp_c is not None else 15.0) + 0.03 * gti_wm2
    derate = 1.0 - TEMP_COEFF * max(cell - 25.0, 0.0)
    return gti_wm2 / 1000 * kwp * PERFORMANCE_RATIO * derate


def calibration_factor(measured: list[float], modelled: list[float]) -> float:
    """Energy-weighted measured/modelled ratio over days (clamped)."""
    pairs = [(m, p) for m, p in zip(measured, modelled) if p > 0.5]
    if len(pairs) < CAL_MIN_DAYS:
        return 1.0
    ratio = sum(m for m, _ in pairs) / sum(p for _, p in pairs)
    return min(max(ratio, CAL_RANGE[0]), CAL_RANGE[1])


class PVForecaster:
    """Hourly PV forecast (kWh per hour start, local time) per MPPT."""

    def __init__(self, hass: Any, latitude: float, longitude: float) -> None:
        self.hass = hass
        self.lat = latitude
        self.lon = longitude
        self.planes: list[Plane] = []
        # raw model: {local hour start: {mppt: kWh}}
        self._model: dict[datetime, dict[int, float]] = {}
        self.temps: dict[datetime, float] = {}      # hourly outdoor temperature (°C)
        self.calibration: dict[int, float] = {}
        self._fetched = 0.0
        self._config_key = ""
        self.error = ""

    # ── fetch ─────────────────────────────────────────────────────

    async def async_refresh(
        self, pv_string_config: dict | None, ledger: Any = None, force: bool = False, api_key: str = "",
    ) -> bool:
        planes = planes_from_config(pv_string_config)
        key = repr([(p.mppt, p.kwp, p.tilt, p.azimuth) for p in planes])
        if not planes:
            self.planes, self._model = [], {}
            return False
        if not force and key == self._config_key and time.time() - self._fetched < REFRESH_S:
            return True
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.hass)
        model: dict[datetime, dict[int, float]] = {}
        temps: dict[datetime, float] = {}
        try:
            for plane in planes:
                params = {
                    "latitude": f"{self.lat:.4f}",
                    "longitude": f"{self.lon:.4f}",
                    "hourly": "global_tilted_irradiance,temperature_2m",
                    "tilt": f"{plane.tilt:.0f}",
                    "azimuth": f"{plane.azimuth:.0f}",
                    "timezone": "auto",
                    "past_days": str(PAST_DAYS),
                    "forecast_days": "3",
                }
                url = OPEN_METEO_URL
                if api_key:
                    url, params["apikey"] = OPEN_METEO_CUSTOMER_URL, api_key
                async with session.get(url, params=params, timeout=20) as resp:
                    resp.raise_for_status()
                    payload = await resp.json()
                hourly = payload.get("hourly") or {}
                for ts, gti, temp in zip(
                    hourly.get("time") or [],
                    hourly.get("global_tilted_irradiance") or [],
                    hourly.get("temperature_2m") or [],
                ):
                    # Open-Meteo radiation is the mean of the preceding hour
                    start = datetime.fromisoformat(ts) - timedelta(hours=1)
                    if temp is not None:
                        temps[start] = float(temp)
                    if gti is None:
                        continue
                    slot = model.setdefault(start, {})
                    slot[plane.mppt] = slot.get(plane.mppt, 0.0) + plane_energy(float(gti), temp, plane.kwp)
        except Exception as err:  # noqa: BLE001 — keep the last forecast on network errors
            self.error = str(err)[:200]
            _LOGGER.warning("Open-Meteo PV forecast failed: %s", err)
            return bool(self._model)
        self.planes, self._model, self.temps = planes, model, temps
        self._fetched, self._config_key, self.error = time.time(), key, ""
        if ledger is not None:
            self._calibrate(ledger)
        return True

    def _calibrate(self, ledger: Any) -> None:
        from homeassistant.util import dt as dt_util

        today = dt_util.now().date()
        mppts = sorted({p.mppt for p in self.planes})
        measured: dict[int, list[float]] = {m: [] for m in mppts}
        modelled: dict[int, list[float]] = {m: [] for m in mppts}
        for back in range(1, PAST_DAYS):
            day = today - timedelta(days=back)
            rec = ledger.day(day)
            if rec.get("covered_min", 0) < 20 * 60 or rec.get("gap_pv", 0) > 1.0:
                continue  # incomplete day (outage / no data) or per-MPPT energy missing
            model_day = self.day_by_mppt(day, calibrated=False)
            if not model_day:
                continue
            for m in mppts:
                measured[m].append(float(rec.get(f"mppt{m}", 0.0)))
                modelled[m].append(model_day.get(m, 0.0))
        self.calibration = {m: calibration_factor(measured[m], modelled[m]) for m in mppts}

    # ── results ───────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return bool(self._model)

    def hour_kwh(self, start: datetime, calibrated: bool = True) -> float:
        slot = self._model.get(start.replace(minute=0, second=0, microsecond=0, tzinfo=None))
        if not slot:
            return 0.0
        return sum(v * (self.calibration.get(m, 1.0) if calibrated else 1.0) for m, v in slot.items())

    def day_by_mppt(self, day: date, calibrated: bool = True) -> dict[int, float]:
        out: dict[int, float] = {}
        for start, slot in self._model.items():
            if start.date() != day:
                continue
            for m, v in slot.items():
                out[m] = out.get(m, 0.0) + v * (self.calibration.get(m, 1.0) if calibrated else 1.0)
        return out

    def day_total(self, day: date) -> float:
        return sum(self.day_by_mppt(day).values())

    def remaining_today(self, now: datetime) -> float:
        now = now.replace(tzinfo=None)
        total = 0.0
        for start in self._model:
            if start.date() != now.date():
                continue
            end = start + timedelta(hours=1)
            if end <= now:
                continue
            share = 1.0 if start >= now else (end - now).total_seconds() / 3600
            total += self.hour_kwh(start) * share
        return total

    def so_far_today(self, now: datetime) -> float:
        return self.day_total(now.date()) - self.remaining_today(now)

    def power_now_w(self, now: datetime) -> float:
        return self.hour_kwh(now.replace(tzinfo=None)) * 1000

    def hourly(self) -> dict[tuple[date, int], float]:
        """{(date, hour): kWh} calibrated — for the arbitrage planner."""
        return {(s.date(), s.hour): self.hour_kwh(s) for s in self._model}

    def daily_temps(self) -> dict[date, float]:
        days: dict[date, list[float]] = {}
        for start, t in self.temps.items():
            days.setdefault(start.date(), []).append(t)
        return {d: sum(v) / len(v) for d, v in days.items() if len(v) >= 18}

    def status(self, now: datetime) -> dict[str, Any]:
        return {
            "source": "open_meteo",
            "planes": [p.label for p in self.planes],
            "calibration": {f"pv{m}": round(f, 2) for m, f in self.calibration.items()},
            "today_kwh": round(self.day_total(now.date()), 2),
            "tomorrow_kwh": round(self.day_total(now.date() + timedelta(days=1)), 2),
            "updated": datetime.fromtimestamp(self._fetched).strftime("%H:%M") if self._fetched else "",
            "error": self.error,
        }
