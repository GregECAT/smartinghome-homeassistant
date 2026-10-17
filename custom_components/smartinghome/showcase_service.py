# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Installation showcase — Home Assistant side (timer, data sources, HTTP POST).

Off by default. Settings "showcase": {enabled, url, title, description, location};
the HMAC secret lives under "showcase_secret" (settings_io.SECRET_KEYS: never sent
to the panel, never accepted through settings/update — only via
smartinghome/showcase/save). Payload and signature: showcase.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from . import showcase as sc
from .const import (
    DOMAIN,
    SENSOR_BATTERY_POWER,
    SENSOR_BATTERY_SOC,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_LOAD_TODAY,
    SENSOR_LOAD_TOTAL,
    SENSOR_PV_GENERATION_TODAY,
    SENSOR_PV_POWER,
    SENSOR_RCE_PRICE,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

SETTINGS_KEY = "showcase"
SECRET_KEY = "showcase_secret"
DEFAULTS: dict[str, Any] = {
    "enabled": False, "url": sc.DEFAULT_URL, "title": "", "description": "", "location": "",
}
SAMPLE_S = 30  # voltage samples for the 10-minute means
MIN_SECRET_LEN = 32

_LOAD_CANDIDATES = ("sensor.total_load", "sensor.goodwe_total_load")
_PV_CANDIDATES = ("sensor.total_pv_generation", "sensor.goodwe_total_pv_generation")
_IMPORT_FALLBACK = "sensor.meter_total_energy_import"
_EXPORT_FALLBACK = "sensor.meter_total_energy_export"


def valid_url(url: str) -> bool:
    """https only (plain http just for a local test receiver)."""
    try:
        p = urlparse(str(url or "").strip())
    except ValueError:
        return False
    if p.scheme == "https" and p.hostname:
        return True
    return p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1", "::1")


class ShowcasePublisher:
    """One per Home Assistant: samples voltages, sends the showcase every 5 minutes."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._unsub = None
        self._volts = sc.RollingMean()
        self._last_send = -1e9
        self._daily_ts = -1e9
        self._daily_cache: list[dict[str, Any]] | None = None
        self._busy = False
        self.status: dict[str, Any] = {}

    # ── lifecycle ──
    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_interval

        self._unsub = async_track_time_interval(self.hass, self._tick, timedelta(seconds=SAMPLE_S))

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    # ── settings ──
    async def async_config(self) -> tuple[dict[str, Any], str]:
        from .settings_io import read_async

        settings = await read_async(self.hass)
        cfg = {**DEFAULTS, **(settings.get(SETTINGS_KEY) or {})}
        return cfg, str(settings.get(SECRET_KEY) or "")

    async def async_save(self, updates: dict[str, Any], secret: str | None, clear_secret: bool) -> dict[str, Any]:
        from .settings_io import write_async

        cfg, _ = await self.async_config()
        for key in ("enabled", "url", "title", "description", "location"):
            if key in updates:
                cfg[key] = updates[key]
        cfg["enabled"] = bool(cfg.get("enabled"))
        cfg["url"] = str(cfg.get("url") or sc.DEFAULT_URL).strip()
        cfg["title"] = str(cfg.get("title") or "")[: sc.TITLE_MAX]
        cfg["description"] = str(cfg.get("description") or "")[: sc.DESCRIPTION_MAX]
        cfg["location"] = str(cfg.get("location") or "")[: sc.LOCATION_MAX]
        out: dict[str, Any] = {SETTINGS_KEY: {k: cfg[k] for k in DEFAULTS}}
        if clear_secret:
            out[SECRET_KEY] = ""
        elif secret:
            out[SECRET_KEY] = secret
        await write_async(self.hass, out)
        self._last_send = -1e9  # send on the next tick (≤ 30 s) when enabled
        self._daily_ts = -1e9
        return cfg

    # ── data ──
    def _coordinator(self):
        for entry_data in self.hass.data.get(DOMAIN, {}).values():
            if isinstance(entry_data, dict) and entry_data.get("coordinator") is not None:
                return entry_data["coordinator"], entry_data.get("strategy_controller")
        return None, None

    def _sample(self) -> None:
        coordinator, _ = self._coordinator()
        data = getattr(coordinator, "data", None) or {}
        self._volts.add(time.monotonic(), (
            data.get(SENSOR_GRID_VOLTAGE_L1), data.get(SENSOR_GRID_VOLTAGE_L2), data.get(SENSOR_GRID_VOLTAGE_L3),
        ))

    @staticmethod
    def _live_numbers(data: dict[str, Any]) -> dict[str, Any]:
        return {
            "pv_w": data.get(SENSOR_PV_POWER),
            "load_w": data.get(SENSOR_LOAD_TOTAL),
            "grid_meter_w": data.get(SENSOR_GRID_POWER_TOTAL),
            "battery_inv_w": data.get(SENSOR_BATTERY_POWER),
            "soc": data.get(SENSOR_BATTERY_SOC),
            "pv_today": data.get(SENSOR_PV_GENERATION_TODAY),
            "load_today": data.get(SENSOR_LOAD_TODAY),
            "import_today": data.get("grid_import_daily"),
            "export_today": data.get("grid_export_daily"),
        }

    @staticmethod
    def _autopilot(data: dict[str, Any], ctrl) -> dict[str, Any] | None:
        if ctrl is None:
            return None
        res = data.get("autopilot_status") if isinstance(data.get("autopilot_status"), dict) else {}
        strategy = getattr(getattr(ctrl, "active_strategy", None), "value", "") or res.get("strategy", "")
        arb = res.get("arbitrage") if isinstance(res.get("arbitrage"), dict) else None
        if arb is None and strategy == "max_profit":
            arb = getattr(ctrl, "_arb_status", None)
        hours = (arb or {}).get("hours") or []
        action = ""
        if arb is not None and bool(getattr(ctrl, "enabled", False)):
            cmd = getattr(ctrl, "_arb_cmd", None)
            action = cmd[0] if cmd else (hours[0].get("action", "") if hours else "")
        return {
            "enabled": bool(getattr(ctrl, "enabled", False)),
            "strategy": strategy,
            "strategy_label": res.get("strategy_label", ""),
            "action": action,
            "actions": res.get("actions") or [],
            "note": (arb or {}).get("note", ""),
            "plan_hours": hours,
        }

    async def _price(self, data: dict[str, Any], now: datetime) -> dict[str, Any]:
        from .arbitrage import buy_price
        from .settings_io import read_async

        settings = await read_async(self.hass)
        tariff = str(data.get("tariff_type") or "g13").lower()
        provider = str(settings.get("energy_provider") or data.get("energy_provider") or "tauron").lower()
        if tariff == "dynamic":
            zone, buy = "dynamic", data.get("dynamic_buy_price")
        else:
            zone, buy = buy_price(now, tariff, provider)
        return {
            "rce_mwh": data.get(SENSOR_RCE_PRICE), "rce_sell_kwh": data.get("rce_sell_price"),
            "tariff": tariff, "provider": provider, "zone": zone, "buy_kwh": buy,
        }

    async def _deposit(self) -> dict[str, Any] | None:
        try:
            from .deposit_service import get_tracker

            status = await asyncio.wait_for(get_tracker(self.hass).async_status(), timeout=60)
            return sc.deposit_block(status)
        except Exception as err:  # noqa: BLE001 — the deposit is optional
            _LOGGER.debug("Showcase: deposit unavailable: %s", err)
            return None

    async def _daily(self, coordinator) -> list[dict[str, Any]] | None:
        """Last 30 complete days per day from the recorder (long-term statistics)."""
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import (
            list_statistic_ids,
            statistics_during_period,
        )

        smap = getattr(coordinator, "_sensor_map", {}) or {}
        instance = get_instance(self.hass)
        stat_ids = await instance.async_add_executor_job(list_statistic_ids, self.hass)
        known = {s["statistic_id"] for s in stat_ids if s.get("has_sum")}

        def pick(*cands: str) -> str:
            return next((c for c in cands if c and c in known), "")

        ext = sorted(k for k in known if ":" in k)
        ids = {
            "pv": pick(smap.get("total_production", ""), *_PV_CANDIDATES),
            "load": pick(smap.get("total_load_consumption", ""), *_LOAD_CANDIDATES),
            # Inverter grid meter first: up to date (the utility meter lags a day or two)
            "import": pick(smap.get("total_energy_import", ""), _IMPORT_FALLBACK)
            or next((k for k in ext if k.endswith("_balanced_consumption")), ""),
            "export": pick(smap.get("total_energy_export", ""), _EXPORT_FALLBACK)
            or next((k for k in ext if k.endswith("_balanced_generation")), ""),
        }
        wanted = {v for v in ids.values() if v}
        if not wanted:
            return None
        today = dt_util.now().date()
        start = dt_util.start_of_local_day(today - timedelta(days=sc.DAILY_DAYS))
        end = dt_util.start_of_local_day(today)
        stats = await instance.async_add_executor_job(
            statistics_during_period, self.hass, dt_util.as_utc(start), dt_util.as_utc(end),
            wanted, "day", None, {"change"},
        )
        series: dict[str, list[tuple[str, float]]] = {}
        for key, sid in ids.items():
            rows = []
            for row in stats.get(sid, []) if sid else []:
                change, st = row.get("change"), row.get("start")
                if change is None or st is None:
                    continue
                ts = dt_util.utc_from_timestamp(st) if isinstance(st, (int, float)) else st
                rows.append((dt_util.as_local(ts).date().isoformat(), change))
            series[key] = rows
        return sc.daily_rows(series, today)

    async def async_payload(self, cfg: dict[str, Any], with_daily: bool) -> dict[str, Any]:
        coordinator, ctrl = self._coordinator()
        data = dict(getattr(coordinator, "data", None) or {})
        now = dt_util.now()
        daily = None
        if with_daily and coordinator is not None:
            try:
                daily = await self._daily(coordinator)
                self._daily_cache = daily
            except Exception as err:  # noqa: BLE001 — recorder optional
                _LOGGER.debug("Showcase: daily statistics unavailable: %s", err)
        return sc.build_payload(
            config=cfg, data=self._live_numbers(data), now=now, version=VERSION,
            voltages=self._volts.means(time.monotonic()),
            autopilot=self._autopilot(data, ctrl),
            price=await self._price(data, now) if data else None,
            deposit=await self._deposit(),
            daily=daily,
        )

    # ── sending ──
    async def _tick(self, _now=None) -> None:
        self._sample()
        if self._busy or time.monotonic() - self._last_send < sc.SEND_INTERVAL_S - 5:
            return
        cfg, secret = await self.async_config()
        if not cfg.get("enabled"):
            return
        await self.async_send(cfg, secret)

    async def async_send(self, cfg: dict[str, Any] | None = None, secret: str | None = None) -> dict[str, Any]:
        if cfg is None or secret is None:
            cfg, secret = await self.async_config()
        self._last_send = time.monotonic()
        attempt = dt_util.now().isoformat(timespec="seconds")
        if not secret or len(secret) < MIN_SECRET_LEN:
            self.status = {**self.status, "last_attempt": attempt, "ok": False, "error": "Brak sekretu (min. 32 znaki)"}
            return self.status
        url = str(cfg.get("url") or sc.DEFAULT_URL).strip()
        if not valid_url(url):
            self.status = {**self.status, "last_attempt": attempt, "ok": False, "error": "Adres musi zaczynać się od https://"}
            return self.status
        self._busy = True
        try:
            with_daily = time.monotonic() - self._daily_ts >= sc.DAILY_INTERVAL_S
            payload = await self.async_payload(cfg, with_daily)
            body, headers = sc.signed_request(secret, payload, int(time.time()))
            if len(body) > sc.MAX_BODY_BYTES and "daily" in payload:
                payload.pop("daily")
                body, headers = sc.signed_request(secret, payload, int(time.time()))
            session = async_get_clientsession(self.hass)
            import aiohttp

            async with session.post(url, data=body, headers=headers,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                text = (await resp.text())[:200]
                ok = 200 <= resp.status < 300
            self.status = {
                "last_attempt": attempt, "ok": ok, "http_status": resp.status, "bytes": len(body),
                "error": "" if ok else text,
                "last_ok": attempt if ok else self.status.get("last_ok"),
                "daily_sent": attempt if ok and "daily" in payload else self.status.get("daily_sent"),
            }
            if ok and "daily" in payload:
                self._daily_ts = time.monotonic()
            if not ok:
                _LOGGER.warning("Showcase POST %s → HTTP %s: %s", urlparse(url).hostname, resp.status, text)
        except Exception as err:  # noqa: BLE001 — never affects the installation
            self.status = {**self.status, "last_attempt": attempt, "ok": False, "error": str(err)[:200] or type(err).__name__}
            _LOGGER.debug("Showcase send failed: %s", err)
        finally:
            self._busy = False
        return self.status


def get_publisher(hass: HomeAssistant) -> ShowcasePublisher | None:
    return (hass.data.get(DOMAIN) or {}).get("_showcase")
