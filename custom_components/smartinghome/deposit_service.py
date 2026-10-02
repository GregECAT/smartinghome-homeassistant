"""Prosumer deposit tracker — Home Assistant side.

Hourly energy from the recorder (Tauron eLicznik "balanced" statistics when the
tauron_importer integration is installed, otherwise the inverter's grid meter),
RCE prices from the PSE public API (cached per month in an HA Store), energy
prices from the "Energia i koszty" tariff. The arithmetic is in
prosumer_deposit.py.

Settings "prosumer_deposit":
  {start: "YYYY-MM-DD" (hourly net-billing since), opening: [{month, amount}],
   invoices: {"YYYY-MM": zł}, use_in_autopilot: bool}
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import meter_tariffs as mt
from . import prosumer_deposit as pd
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PSE_URL = "https://api.raporty.pse.pl/api/rce-pln"
DEFAULTS: dict[str, Any] = {
    "start": "2026-03-19",
    "opening": [],
    "invoices": {},
    "use_in_autopilot": True,
}
REFRESH_S = 3 * 3600
FALLBACK_EXPORT = "sensor.meter_total_energy_export"
FALLBACK_IMPORT = "sensor.meter_total_energy_import"


_DTIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2})[ab]?:(\d{2})")


def _parse_dtime(text: str) -> datetime:
    """PSE local time; the repeated hour on the October DST change is "02a" / "02b"."""
    m = _DTIME.match(str(text))
    if not m:
        raise ValueError(f"PSE time {text!r}")
    return datetime(*(int(x) for x in m.groups()))


class DepositTracker:
    """Computes the deposit status on demand; cached for a few hours."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, 1, f"{DOMAIN}.rce_history")
        self._rce: dict[str, dict[str, Any]] | None = None  # month → {"ts", "complete", "q": [[iso_end, price]]}
        self._status: dict[str, Any] | None = None
        self._status_ts = 0.0
        self._busy = False

    @property
    def export_value_factor(self) -> float:
        st = self._status or {}
        if not st.get("settings", {}).get("use_in_autopilot", True):
            return 1.0
        return float(st.get("export_value_factor", 1.0))

    # ── RCE history (PSE API) ──
    async def _rce_month(self, month: str, now: datetime) -> list[tuple[datetime, float]]:
        if self._rce is None:
            self._rce = (await self._store.async_load() or {}).get("months", {})
        cached = self._rce.get(month)
        current = month == pd.month_key(now)
        if cached and (cached.get("complete") or time.time() - cached.get("ts", 0) < 6 * 3600):
            return [(_parse_dtime(t), p) for t, p in cached["q"]]
        first = datetime.fromisoformat(f"{month}-01")
        last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        if current:
            last = now.replace(tzinfo=None)
        params = {
            "$select": "dtime,rce_pln",
            "$filter": f"business_date ge '{first.date()}' and business_date le '{last.date()}'",
            "$first": "5000",
        }
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(PSE_URL, params=params, timeout=30) as resp:
                resp.raise_for_status()
                rows = (await resp.json()).get("value", [])
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("RCE history %s: PSE API error (%s)", month, type(err).__name__)
            return [(_parse_dtime(t), p) for t, p in (cached or {}).get("q", [])]
        q = []
        for r in rows:
            try:
                _parse_dtime(r["dtime"])  # skip rows we couldn't read later
                q.append((str(r["dtime"]).replace(" ", "T"), float(r["rce_pln"])))
            except (KeyError, TypeError, ValueError):
                continue
        days = (last - first).days + 1
        self._rce[month] = {"ts": time.time(), "complete": not current and len(q) >= days * 24 * 4 - 8,
                            "q": q}
        await self._store.async_save({"months": self._rce})
        return [(_parse_dtime(t), p) for t, p in q]

    # ── energy statistics ──
    async def _sources(self, meter_cfg: dict[str, Any]) -> tuple[str | None, str | None, str]:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import list_statistic_ids

        ids = await get_instance(self.hass).async_add_executor_job(list_statistic_ids, self.hass)
        known = {i["statistic_id"] for i in ids}
        stat = str(meter_cfg.get("energy_stat") or "")
        if stat.startswith("tauron_importer:") and stat.endswith("_balanced_consumption"):
            gen = stat[: -len("consumption")] + "generation"
            if gen in known:
                return gen, stat, "tauron"
        tauron = sorted(i for i in known if i.startswith("tauron_importer:") and i.endswith("_balanced_generation"))
        if tauron:
            return tauron[0], tauron[0][: -len("generation")] + "consumption", "tauron"
        if FALLBACK_EXPORT in known and FALLBACK_IMPORT in known:
            return FALLBACK_EXPORT, FALLBACK_IMPORT, "inverter_meter"
        return None, None, "none"

    async def _hours(self, stat_id: str, start: datetime) -> list[tuple[datetime, float]]:
        from homeassistant.components.recorder import get_instance
        from homeassistant.components.recorder.statistics import statistics_during_period

        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period, self.hass, dt_util.as_utc(start), None,
            {stat_id}, "hour", None, {"change"},
        )
        out = []
        for row in stats.get(stat_id, []):
            change, ts = row.get("change"), row.get("start")
            if change is None or ts is None:
                continue
            kwh = float(change)
            if not 0 <= kwh < 100:  # meter resets / imports of old history
                continue
            t = dt_util.utc_from_timestamp(ts) if isinstance(ts, (int, float)) else ts
            out.append((dt_util.as_local(t).replace(tzinfo=None), kwh))
        return out

    # ── status ──
    async def async_status(self, force: bool = False) -> dict[str, Any]:
        if self._status and not force and time.time() - self._status_ts < REFRESH_S:
            return self._status
        if self._busy and self._status:
            return self._status
        self._busy = True
        try:
            self._status = await self._compute()
            self._status_ts = time.time()
        finally:
            self._busy = False
        return self._status

    async def _compute(self) -> dict[str, Any]:
        from .meter_ws import meter_settings
        from .settings_io import read_async

        settings = await read_async(self.hass)
        cfg = {**DEFAULTS, **(settings.get("prosumer_deposit") or {})}
        meter = meter_settings(settings)
        tariff = mt.resolve(str(meter.get("tariff") or "G13"), meter.get("prices"))
        vat = mt.VAT if tariff.get("kind") == "home" else 1.0

        def energy_price_gross(t: datetime) -> float:
            return tariff["energy"].get(mt.zone_at(tariff["id"], t), 0.0) * vat

        now = dt_util.now()
        current = pd.month_key(now)
        try:
            start_day = datetime.fromisoformat(str(cfg["start"])[:10])
        except ValueError:
            start_day = datetime(2026, 3, 19)
        first_month = pd.month_key(start_day)
        export_id, import_id, source = await self._sources(meter)
        base = {"settings": cfg, "source": source, "tariff": tariff["id"], "first_month": first_month,
                "current_month": current, "generated": now.isoformat(timespec="minutes")}
        if export_id is None:
            return {**base, "error": "no_source", "export_value_factor": 1.0}

        # A year before the start too: last year's months are the projection of the coming ones
        hist_start = min(start_day, now.replace(tzinfo=None) - timedelta(days=400)).replace(day=1)
        exports = await self._hours(export_id, hist_start)
        imports = await self._hours(import_id, hist_start)
        months = sorted({pd.month_key(t) for t, k in exports if k > 0})
        quarters: list[tuple[datetime, float]] = []
        for m in months:
            quarters += await self._rce_month(m, now)
        rce = pd.hourly_rce(quarters)
        flows = pd.monthly_flows(exports, imports, rce, energy_price_gross)
        # The first month counts only from the start day
        if first_month in flows and start_day.day > 1:
            part = pd.monthly_flows(
                [(t, k) for t, k in exports if t >= start_day],
                [(t, k) for t, k in imports if t >= start_day], rce, energy_price_gross,
            ).get(first_month)
            if part:
                flows[first_month] = part
        projection = pd.project(flows, current)
        res = pd.settle(flows, first_month=first_month, current_month=current,
                        opening=cfg.get("opening"), projection=projection)
        res["reconcile"] = pd.reconcile(res["months"], cfg.get("invoices"))
        data_until = max((t for t, _ in imports), default=None)
        return {**base, **res,
                "data_until": data_until.isoformat(timespec="minutes") if data_until else None,
                "history": {k: v for k, v in flows.items() if k < first_month}}


def get_tracker(hass: HomeAssistant) -> DepositTracker:
    """One tracker per HA run (shared by the panel and the autopilot)."""
    data = hass.data.setdefault(DOMAIN, {})
    tracker = data.get("_deposit_tracker")
    if tracker is None:
        tracker = data["_deposit_tracker"] = DepositTracker(hass)
    return tracker
