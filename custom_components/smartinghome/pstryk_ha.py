# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Home Assistant side of the Pstryk prices (pure logic: pstryk.py).

- finds the Pstryk.pl integration's sensors in the entity registry,
- gives the planner / coordinator / AI the hourly buy and sell series,
- keeps an hourly price history (HA Store) for the meter panel's cost — the sensors
  have no state_class, so the recorder keeps no long-term statistics of them, and
  their price lists are excluded from the recorder (`_unrecorded_attributes`),
- WebSocket smartinghome/pstryk/status for the panel's diagnostic.
Read only: nothing here talks to the Pstryk API or changes the Pstryk integration.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from . import pstryk as pk
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_CACHE_KEY = "_pstryk_entities"
_HISTORY_KEY = "_pstryk_history"
_RESOLVE_TTL_S = 600


def resolve_entities(hass: HomeAssistant, force: bool = False) -> dict[str, str | None]:
    """{"buy": entity_id | None, "sell": …} — registry (platform + unique_id) first."""
    cache = hass.data.setdefault(DOMAIN, {}).get(_CACHE_KEY)
    if cache and not force and time.monotonic() - cache[0] < _RESOLVE_TTL_S and all(cache[1].values()):
        return dict(cache[1])
    rows: list[dict[str, Any]] = []
    try:
        from homeassistant.helpers import entity_registry as er

        rows = [
            {"entity_id": e.entity_id, "platform": e.platform, "unique_id": e.unique_id,
             "disabled": e.disabled_by is not None}
            for e in er.async_get(hass).entities.values() if e.platform == pk.PSTRYK_DOMAIN
        ]
    except Exception as err:  # noqa: BLE001 — registry not ready: fall back to known ids
        _LOGGER.debug("Pstryk: entity registry unavailable: %s", err)
    existing = [eid for eid in hass.states.async_entity_ids("sensor") if eid.startswith("sensor.pstryk")]
    found = {kind: pk.pick_entity(rows, kind, existing) for kind in pk.UNIQUE_IDS}
    hass.data[DOMAIN][_CACHE_KEY] = (time.monotonic(), found)
    return dict(found)


def read_series(hass: HomeAssistant) -> dict[str, Any]:
    """Current Pstryk prices: series, current-hour states and entity ids."""
    ents = resolve_entities(hass)
    out: dict[str, Any] = {"entities": ents}
    for kind, eid in ents.items():
        st = hass.states.get(eid) if eid else None
        attrs = dict(st.attributes) if st else {}
        unit = str(attrs.get("unit_of_measurement") or "PLN/kWh")
        scale = 0.001 if unit.lower().endswith("/mwh") else 1.0
        if unit and not unit.upper().startswith("PLN"):
            out[kind], out[f"{kind}_now"] = {}, None
            continue
        series = pk.parse_prices(attrs)
        out[kind] = {h: p * scale for h, p in series.items()} if scale != 1.0 else series
        now_val = pk._finite(st.state) if st else None
        out[f"{kind}_now"] = now_val * scale if now_val is not None else None
    return out


def _meter_cfg(settings: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    from .meter_ws import meter_settings

    return meter_settings(settings), isinstance(settings.get("meter"), dict)


def planner_prices(
    hass: HomeAssistant, settings: dict[str, Any], tariff: str, provider: str, now: datetime,
) -> dict[str, Any] | None:
    """Pstryk series for the arbitrage planner, or None (→ ENTSO-E / the tariff as before).

    Only on an explicit choice (pstryk.pstryk_active): provider Pstryk, or the dynamic
    tariff with `dynamic_price_source` = "pstryk". Buy: Pstryk buy series (+ the OSD
    distribution when the meter setting says the price excludes it). Sell: the Pstryk
    prosumer series — only when the provider is Pstryk (others keep RCE × coefficient).
    """
    if not pk.pstryk_active(provider, tariff, settings):
        return None
    data = read_series(hass)
    buy = data.get("buy") or {}
    if not buy and data.get("buy_now") is None:
        return None
    cfg, has_meter = _meter_cfg(settings)
    adder = pk.distribution_fn(cfg, has_meter)
    tz = now.tzinfo
    buy_series = pk.with_adder(buy, tz, adder) if tz else dict(buy)
    buy_now = data.get("buy_now")
    if buy_now is not None:
        buy_now += adder(now)
    sell = (data.get("sell") or {}) if pk.is_pstryk(provider) else {}
    return {"buy": buy_series, "buy_now": buy_now, "sell": sell, "entities": data["entities"],
            "includes_distribution": pk.includes_distribution(cfg)}


def dynamic_tariff_data(
    hass: HomeAssistant, settings: dict[str, Any], now: datetime, provider: str = "", tariff: str = "dynamic",
) -> dict[str, Any] | None:
    """Coordinator keys of the dynamic tariff (dynamic_buy_price …) priced by Pstryk.

    None unless Pstryk is explicitly chosen (pstryk.pstryk_active)."""
    if not pk.pstryk_active(provider or settings.get("energy_provider"), tariff, settings):
        return None
    data = read_series(hass)
    buy = data.get("buy") or {}
    hour = pk.hour_utc(now)
    if not buy or hour not in buy and data.get("buy_now") is None:
        return None
    cfg, has_meter = _meter_cfg(settings)
    adder = pk.distribution_fn(cfg, has_meter)
    tz = now.tzinfo
    full = pk.with_adder(buy, tz, adder)
    today = sorted(p for h, p in full.items() if h.astimezone(tz).date() == now.date())
    price_now = full.get(hour)
    if price_now is None:
        price_now = data["buy_now"] + adder(now)
    nxt = full.get(hour + timedelta(hours=1), price_now)
    today = today or [price_now]
    cheaper = sum(1 for p in today if p < price_now)
    return {
        "allin_now": price_now,
        "allin_next": nxt,
        "min": today[0],
        "max": today[-1],
        "avg": sum(today) / len(today),
        "rank": cheaper + 1,
        "percentile": round(cheaper / len(today) * 100, 1),
        "hourly": pk.serialize(buy),
    }


# ── price history for the meter panel ────────────────────────────────────────

class PstrykPriceHistory:
    """Hourly Pstryk prices kept for the meter panel (400 days, HA Store)."""

    TICK = timedelta(minutes=20)

    def __init__(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store = Store(hass, 1, f"{DOMAIN}.pstryk_prices")
        self.buy: dict[datetime, float] = {}
        self.sell: dict[datetime, float] = {}
        self._unsub = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        raw = await self._store.async_load() or {}
        self.buy = pk.from_store(raw.get("buy"))
        self.sell = pk.from_store(raw.get("sell"))
        self._loaded = True

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_interval

        await self.async_load()
        self._unsub = async_track_time_interval(self.hass, self._tick, self.TICK)
        await self._tick()

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _save(self) -> None:
        await self._store.async_save({"buy": pk.to_store(self.buy), "sell": pk.to_store(self.sell)})

    async def _tick(self, _now=None) -> None:
        from homeassistant.util import dt as dt_util

        try:
            data = read_series(self.hass)
            now = dt_util.utcnow()
            self.buy, b = pk.merge(self.buy, data.get("buy") or {}, now)
            self.sell, s = pk.merge(self.sell, data.get("sell") or {}, now)
            if b or s:
                await self._save()
        except Exception as err:  # noqa: BLE001 — never break HA on a price snapshot
            _LOGGER.debug("Pstryk price snapshot failed: %s", err)

    async def async_buy_history(self, start: datetime, end: datetime) -> dict[datetime, float]:
        """Buy prices (UTC hour → gross PLN/kWh) since start: Store + recorder backfill."""
        await self.async_load()
        await self._tick()
        missing = any(
            (start + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0) not in self.buy
            for i in range(int((end - start).total_seconds() // 3600))
        )
        if missing:
            # the recorder keeps states ~10 days (purge_keep_days) — older hours can't come back
            backfill = await self._recorder_hours(max(start, end - timedelta(days=11)), end)
            fresh = {h: p for h, p in backfill.items() if h not in self.buy}
            if fresh:
                self.buy, changed = pk.merge(self.buy, fresh, end)
                if changed:
                    await self._save()
        return {h: p for h, p in self.buy.items() if start <= h < end}

    async def _recorder_hours(self, start: datetime, end: datetime) -> dict[datetime, float]:
        eid = resolve_entities(self.hass).get("buy")
        if not eid:
            return {}
        try:
            from homeassistant.components.recorder import get_instance, history

            states = await get_instance(self.hass).async_add_executor_job(
                history.get_significant_states,
                self.hass,
                start,
                end,
                [eid],
                None,  # filters
                True,  # include_start_time_state
                False,  # significant_changes_only — every hourly price change
                False,  # minimal_response
                True,  # no_attributes
            )
        except Exception as err:  # noqa: BLE001 — recorder off / purged
            _LOGGER.debug("Pstryk recorder backfill failed: %s", err)
            return {}
        changes = [(st.last_changed, st.state) for st in states.get(eid, [])]
        return pk.hours_from_state_changes(changes, start, end)

    def counts(self) -> dict[str, Any]:
        first = min(self.buy) if self.buy else None
        return {"stored_hours": len(self.buy), "stored_from": first.isoformat() if first else None}


def get_history(hass: HomeAssistant) -> PstrykPriceHistory | None:
    return hass.data.get(DOMAIN, {}).get(_HISTORY_KEY)


async def async_start_history(hass: HomeAssistant) -> None:
    """Once per HA run (like the auto updater); stopped with the last config entry."""
    if get_history(hass) is not None:
        return
    hist = PstrykPriceHistory(hass)
    hass.data.setdefault(DOMAIN, {})[_HISTORY_KEY] = hist
    await hist.async_start()


def stop_history(hass: HomeAssistant) -> None:
    hist = hass.data.get(DOMAIN, {}).pop(_HISTORY_KEY, None)
    if hist is not None:
        hist.stop()


# ── WebSocket: diagnostic for the panel ──────────────────────────────────────

@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_pstryk_status)


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/pstryk/status"})
@websocket_api.async_response
async def ws_pstryk_status(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from homeassistant.util import dt as dt_util

    from .settings_io import read_async

    settings = await read_async(hass)
    now = dt_util.now()
    ents = resolve_entities(hass, force=True)
    data = read_series(hass)
    buy = data.get("buy") or {}
    today_h, tomorrow_h = pk.local_day_counts(buy, now.tzinfo, now.date())
    summary = pk.day_summary(buy, now.tzinfo, now.date())
    cfg, has_meter = _meter_cfg(settings)
    hist = get_history(hass)
    connection.send_result(msg["id"], {
        "installed": bool(ents.get("buy")),
        "configured": bool(hass.config_entries.async_entries(pk.PSTRYK_DOMAIN)),
        "entities": ents,
        "buy_now": data.get("buy_now"),
        "sell_now": data.get("sell_now"),
        "today_hours": today_h,
        "tomorrow_hours": tomorrow_h,
        "sell_hours": len(data.get("sell") or {}),
        "avg_today": round(summary["avg"], 4) if summary else None,
        "min_today": round(summary["min"], 4) if summary else None,
        "max_today": round(summary["max"], 4) if summary else None,
        "includes_distribution": pk.includes_distribution(cfg),
        "dist_tariff": str(cfg.get("tariff") or pk.DEFAULT_DIST_TARIFF) if has_meter else pk.DEFAULT_DIST_TARIFF,
        "meter_energy_price": cfg.get("energy_price", "tariff"),
        "provider": str(settings.get("energy_provider") or ""),
        "dynamic_price_source": str(settings.get("dynamic_price_source") or "entsoe"),
        "hacs_url": pk.HACS_URL,
        **(hist.counts() if hist else {"stored_hours": 0, "stored_from": None}),
    })
