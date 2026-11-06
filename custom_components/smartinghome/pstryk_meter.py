# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Pstryk meter in Home Assistant: readings and Pstryk's cost over the Pstryk API.

Opt-in per site (meter setting `pstryk_meter`, panel "Energia i koszty" → Ustawienia):
only the HA of the site whose meter Pstryk installed should ask for it — another HA
with the same key (e.g. a home showing the company's prices) would read the same meter.

- API key: taken from the "Pstryk.pl" integration's config entry (never logged, never
  sent anywhere but api.pstryk.pl);
- every 20 min: hourly meter_values + cost (backfill from the start of last month,
  then the last hours), kept 400 days (HA Store) and written as external statistics
  `smartinghome:pstryk_consumption` (and `_export` once there is export) — usable in
  the Energy dashboard and as a meter source;
- every 5 min: minute meter_values of the last 15 min → live power;
- no reading for 3 h → notification "Miernik Pstryk nie przesyła danych" (the contract
  switches to the fixed price after 30 days without meter data in a year), resolved
  when data is back;
- 429 → wait Retry-After; 401/403 → stop until the key in Pstryk.pl changes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant

from . import pstryk_api as pa
from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)

_KEY = "_pstryk_meter"
HOURLY_EVERY = timedelta(minutes=20)
LIVE_EVERY = timedelta(minutes=5)
OFFLINE_AFTER = timedelta(hours=3)
STAT_IMPORT = f"{DOMAIN}:pstryk_consumption"
STAT_EXPORT = f"{DOMAIN}:pstryk_export"
STAT_COST = f"{DOMAIN}:pstryk_cost"
STAT_TARIFF_COST = f"{DOMAIN}:pstryk_tariff_cost"
STATS_VERSION = 3  # 2: + pstryk_cost, 3: + tariff cost of the same kWh


class AuthFailed(Exception):
    pass


class RateLimited(Exception):
    def __init__(self, seconds: int) -> None:
        super().__init__(f"rate limited for {seconds} s")
        self.seconds = seconds


class PstrykMeter:
    def __init__(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store = Store(hass, 1, f"{DOMAIN}.pstryk_meter")
        self.hours: dict[int, dict[str, Any]] = {}
        self.backfilled = False
        self.power_w: float | None = None
        self.power_at: datetime | None = None
        self.last_data: datetime | None = None
        self.last_ok: datetime | None = None
        self.error: str | None = None
        self.enabled = False
        self.offline_notified = False
        self._blocked_until: datetime | None = None
        self._bad_token: str | None = None
        self._next_hourly: datetime | None = None
        self._stats_version = 0
        self._unsub = None
        self._lock = asyncio.Lock()

    # ── lifecycle ──────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_interval

        raw = await self._store.async_load() or {}
        self.hours = {int(k): v for k, v in (raw.get("hours") or {}).items()}
        self.backfilled = bool(raw.get("backfilled"))
        self.offline_notified = bool(raw.get("offline_notified"))
        self.last_data = pa._time(raw.get("last_data"))
        self._stats_version = int(raw.get("stats_version") or 0)
        self._unsub = async_track_time_interval(self.hass, self._tick, LIVE_EVERY)
        self.hass.async_create_task(self._tick())

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _save(self) -> None:
        await self._store.async_save({
            "hours": {str(k): v for k, v in self.hours.items()},
            "backfilled": self.backfilled,
            "offline_notified": self.offline_notified,
            "last_data": self.last_data.isoformat() if self.last_data else None,
            "stats_version": self._stats_version,
        })

    # ── polling ────────────────────────────────────────────────────────────
    def _token(self) -> str | None:
        for entry in self.hass.config_entries.async_entries("pstryk"):
            token = (entry.data or {}).get("api_token")
            if token:
                return str(token)
        return None

    async def _enabled(self) -> bool:
        from .meter_ws import meter_settings
        from .settings_io import read_async

        return bool(meter_settings(await read_async(self.hass)).get("pstryk_meter"))

    async def _tick(self, _now: Any = None) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._run()
            except Exception as err:  # noqa: BLE001 — never break HA on a poll
                self.error = str(err)
                _LOGGER.debug("Pstryk meter poll failed: %s", err)

    async def _run(self) -> None:
        self.enabled = await self._enabled()
        if not self.enabled:
            return
        token = self._token()
        if not token:
            self.error = "no_integration"
            return
        digest = hashlib.sha256(token.encode()).hexdigest()
        if self._bad_token == digest:
            return  # rejected key — wait for a new one in Pstryk.pl
        now = datetime.now(timezone.utc)
        if self._blocked_until and now < self._blocked_until:
            return
        try:
            if self._next_hourly is None or now >= self._next_hourly:
                await self._poll_hours(token, now)
                self._next_hourly = now + HOURLY_EVERY
            await self._poll_live(token, now)
            self.error = None
            self.last_ok = now
        except AuthFailed:
            self._bad_token = digest
            self.error = "auth"
            _LOGGER.warning("Pstryk meter: the API key was rejected — enter the current key in "
                            "the Pstryk.pl integration (Pstryk keeps one active key per account)")
        except RateLimited as err:
            self._blocked_until = now + timedelta(seconds=err.seconds)
            self.error = f"rate_limited:{err.seconds}"
            _LOGGER.info("Pstryk meter: rate limited, next try in %d s", err.seconds)
        await self._check_offline(now)

    async def _get(self, token: str, url: str) -> Any:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(self.hass)
        headers = {"Authorization": token, "Accept": "application/json",
                   "User-Agent": f"SmartingHome/{VERSION} (Home Assistant)"}
        async with session.get(url, headers=headers, timeout=30) as resp:
            if resp.status in (401, 403):
                raise AuthFailed()
            if resp.status == 429:
                raise RateLimited(pa.retry_after(resp.headers.get("Retry-After")))
            if resp.status == 404:
                raise RuntimeError("meter not found or no active contract (404)")
            if resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            return await resp.json(content_type=None)

    async def _poll_hours(self, token: str, now: datetime) -> None:
        from homeassistant.util import dt as dt_util

        end = pa.hour_floor(now) + timedelta(hours=1)
        if not self.backfilled or not self.hours:
            local = dt_util.as_local(now)
            first = (local.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).replace(day=1)
            start = first.astimezone(timezone.utc)
        else:
            start = pa.hour_dt(max(self.hours)) - timedelta(hours=3)
        frames = pa.parse_frames(await self._get(token, pa.url("meter_values,cost", "hour", start, end)))
        changed_from, changed = pa.merge_hours(self.hours, frames, now)
        newest = pa.last_data(frames)
        if newest and (self.last_data is None or newest > self.last_data):
            self.last_data = newest
        self.backfilled = True
        if self._stats_version < STATS_VERSION and self.hours:
            changed_from, changed = None, True  # new statistic (cost) → write all hours once
            self._stats_version = STATS_VERSION
        if changed:
            await self._save()
            await self._import_statistics(changed_from)

    async def _poll_live(self, token: str, now: datetime) -> None:
        frames = pa.parse_frames(await self._get(
            token, pa.url("meter_values", "minute", now - timedelta(minutes=15), now)))
        power, at = pa.live_power(frames)
        if power is not None:
            self.power_w, self.power_at = power, at
        newest = pa.last_data(frames)
        if newest and (self.last_data is None or newest > self.last_data):
            self.last_data = newest

    async def _import_statistics(self, from_key: int | None) -> None:
        try:
            from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
            from homeassistant.components.recorder.statistics import async_add_external_statistics
        except ImportError:
            return
        prices = self._buy_prices()

        def cost(key: int, row: dict[str, Any]) -> float:
            # Pstryk's own cost; an hour without it (no contract yet, API gap): kWh × the
            # Pstryk.pl buy price (gross, all-in — matched Pstryk's cost on 2026-11-05)
            gross = pa.hour_gross(row)
            if gross is None:
                price = prices.get(pa.hour_dt(key))
                gross = (row.get("i") or 0.0) * price if price is not None else 0.0
            return gross

        tariff_cost = await self._tariff_cost_fn()

        for stat_id, field, name, unit in ((STAT_IMPORT, "i", "Pstryk — energia pobrana", "kWh"),
                                           (STAT_EXPORT, "e", "Pstryk — energia oddana", "kWh"),
                                           (STAT_COST, cost, "Pstryk — koszt energii (brutto)", "PLN"),
                                           (STAT_TARIFF_COST, tariff_cost,
                                            "Taryfa z faktury — koszt tych samych kWh (brutto)", "PLN")):
            if field is None:
                continue
            if field == "e" and sum(r.get("e") or 0 for r in self.hours.values()) < 0.01:
                continue
            rows = pa.statistic_rows(self.hours, field, from_key)
            if not rows:
                continue
            meta: dict[str, Any] = {"has_mean": False, "has_sum": True, "name": name, "source": DOMAIN,
                                    "statistic_id": stat_id, "unit_of_measurement": unit}
            try:  # HA 2025.x: mean type / unit class
                from homeassistant.components.recorder.models import StatisticMeanType

                meta["mean_type"] = StatisticMeanType.NONE
                meta.pop("has_mean", None)  # replaced by mean_type (HA 2025.4+)
            except ImportError:
                pass
            try:
                from homeassistant.util.unit_conversion import EnergyConverter

                meta["unit_class"] = EnergyConverter.UNIT_CLASS if unit == "kWh" else None
            except (ImportError, AttributeError):
                pass
            data = [StatisticData(start=t, state=kwh, sum=total) for t, kwh, total in rows]
            try:
                async_add_external_statistics(self.hass, StatisticMetaData(**meta), data)
            except Exception as err:  # noqa: BLE001 — older HA without some meta keys
                for key in ("unit_class", "mean_type"):
                    meta.pop(key, None)
                meta["has_mean"] = False
                _LOGGER.debug("Pstryk statistics retry without new meta keys: %s", err)
                async_add_external_statistics(self.hass, StatisticMetaData(**meta), data)

    async def _tariff_cost_fn(self):
        """kWh × the invoice tariff's zone price (energy + variable distribution + fees) × VAT.

        Same hours and kWh as Pstryk's cost — both are variable costs (fixed fees apart).
        None when the meter tariff has no prices (C11 without the invoice).
        """
        from homeassistant.util import dt as dt_util

        from . import meter_tariffs as mt
        from .meter_ws import meter_settings
        from .settings_io import read_async

        cfg = meter_settings(await read_async(self.hass))
        tariff = mt.resolve(cfg.get("tariff", "C13"), cfg.get("prices"))
        if tariff.get("needs_input"):
            return None
        tz = dt_util.now().tzinfo

        def fn(key: int, row: dict[str, Any]) -> float:
            when = pa.hour_dt(key).astimezone(tz).replace(tzinfo=None)
            e, d = mt.variable_price(tariff, mt.zone_at(tariff["id"], when))
            return (row.get("i") or 0.0) * (e + d) * mt.VAT

        return fn

    def _buy_prices(self) -> dict[datetime, float]:
        """Stored Pstryk.pl buy prices {UTC hour: gross zł/kWh} (pstryk_ha history)."""
        try:
            from .pstryk_ha import get_history

            hist = get_history(self.hass)
            return dict(hist.buy) if hist else {}
        except Exception:  # noqa: BLE001
            return {}

    # ── offline alert ──────────────────────────────────────────────────────
    async def _check_offline(self, now: datetime) -> None:
        if self.last_data is None or self.error in ("auth", "no_integration"):
            return
        offline = now - self.last_data > OFFLINE_AFTER
        if offline == self.offline_notified:
            return
        self.offline_notified = offline
        await self._save()
        try:
            from homeassistant.util import dt as dt_util

            from .notifier import async_deliver
            from .settings_io import read_async

            cfg = (await read_async(self.hass)).get("notification_config") or {}
            since = dt_util.as_local(self.last_data).strftime("%d.%m %H:%M")
            if offline:
                await async_deliver(
                    self.hass, cfg, key="pstryk_meter_offline", level="warning",
                    title="Miernik Pstryk nie przesyła danych",
                    message=(f"Ostatni odczyt z Miernika Pstryk: {since}. Sprawdź zasilanie i Wi-Fi miernika. "
                             "Według umowy po 30 dobach bez danych w roku Pstryk rozlicza po cenie stałej."),
                    source="Pstryk")
            else:
                await async_deliver(
                    self.hass, cfg, key="pstryk_meter_offline", level="resolved",
                    title="Miernik Pstryk znowu przesyła dane", message=f"Ostatni odczyt: {since}.",
                    source="Pstryk")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Pstryk meter notification failed: %s", err)

    # ── panel ──────────────────────────────────────────────────────────────
    def status(self) -> dict[str, Any]:
        from homeassistant.util import dt as dt_util

        now = datetime.now(timezone.utc)
        local = dt_util.as_local(now)
        month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_start = (month_start - timedelta(days=1)).replace(day=1)
        day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        iso = lambda t: t.isoformat(timespec="minutes") if t else None  # noqa: E731
        return {
            "enabled": self.enabled,
            "integration": bool(self._token()),
            "error": self.error,
            "power_w": self.power_w,
            "power_at": iso(self.power_at),
            "last_data": iso(self.last_data),
            "last_ok": iso(self.last_ok),
            "offline": bool(self.last_data and now - self.last_data > OFFLINE_AFTER),
            "stored_hours": len(self.hours),
            "first_hour": iso(pa.hour_dt(min(self.hours))) if self.hours else None,
            "today": pa.period(self.hours, day_start, now + timedelta(hours=1)),
            "current_month": pa.period(self.hours, month_start, now + timedelta(hours=1)),
            "previous_month": pa.period(self.hours, prev_start, month_start),
            "statistic_id": STAT_IMPORT if self.hours else None,
            "cost_statistic_id": STAT_COST if self.hours else None,
            "tariff_cost_statistic_id": STAT_TARIFF_COST if self.hours else None,
            "cost_labels": pa.COST_LABELS,
        }

    def breakeven(self, cfg: dict[str, Any]) -> dict[str, Any] | None:
        """This month's break-even exchange price vs the meter tariff (pstryk_api.breakeven)."""
        from homeassistant.util import dt as dt_util

        from . import meter_tariffs as mt

        tariff = mt.resolve(cfg.get("tariff", "C13"), cfg.get("prices"))
        if tariff.get("needs_input") or not self.hours:
            return None
        tz = dt_util.now().tzinfo

        def tariff_net(key: int, row: dict[str, Any]) -> float:
            when = pa.hour_dt(key).astimezone(tz).replace(tzinfo=None)
            e, d = mt.variable_price(tariff, mt.zone_at(tariff["id"], when))
            return (row.get("i") or 0.0) * (e + d)

        now = datetime.now(timezone.utc)
        month_start = dt_util.as_local(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return pa.breakeven(self.hours, month_start, now + timedelta(hours=1), tariff_net, tz)

    def local_hours(self) -> list[tuple[datetime, float]]:
        from homeassistant.util import dt as dt_util

        return pa.local_hours(self.hours, dt_util.now().tzinfo)

    async def async_refresh(self) -> None:
        """Poll now (settings saved / panel button) — still respects 429 and a rejected key."""
        self._next_hourly = None
        await self._tick()


def get_meter(hass: HomeAssistant) -> PstrykMeter | None:
    return hass.data.get(DOMAIN, {}).get(_KEY)


async def async_start(hass: HomeAssistant) -> None:
    if get_meter(hass) is not None:
        return
    meter = PstrykMeter(hass)
    hass.data.setdefault(DOMAIN, {})[_KEY] = meter
    await meter.async_start()


def stop(hass: HomeAssistant) -> None:
    meter = hass.data.get(DOMAIN, {}).pop(_KEY, None)
    if meter is not None:
        meter.stop()
