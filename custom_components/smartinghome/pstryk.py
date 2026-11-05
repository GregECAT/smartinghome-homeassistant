# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Pstryk (dynamic-price electricity seller) — prices from the "Pstryk.pl" HA integration.

Source: the HACS integration github.com/Bankilo/pstryk-home-assistant (domain `pstryk`).
Its sensors (unique_id `pstryk_buy_price` / `pstryk_sell_price`, entity id derived from
the device name, e.g. sensor.pstryk_energy_prices_buy_price) carry the current hour's
gross price in PLN/kWh as the state and the hourly series in the attributes
`prices_today`, `prices_tomorrow`, `prices`: [{"time": ISO with offset, "price": float}].
The integration reads the Pstryk API unified-metrics `pricing.price_gross` (buy) and
`pricing.price_prosumer_gross` (sell), resolution=hour.

What the price contains is NOT documented by Pstryk. Live data from a business meter
(integration 0.3.4, 2026-11-05) shows buy prices of 1.07–2.29 zł/kWh gross while TGE
day-ahead was ~0.3–0.7 zł/kWh — far above energy + VAT, so the buy price is taken as
ALL-IN, distribution of the meter's OSD tariff included (default of the meter setting
`pstryk_includes_distribution` = True). The API's `cost` metric itemises energy and
distribution separately, so the setting can be switched off once an invoice shows that
distribution is billed on top. Unverified until the first Pstryk invoice.

Pure Python (no Home Assistant imports) so it can be unit-tested.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable, Iterable, Mapping

PROVIDER = "pstryk"
PSTRYK_DOMAIN = "pstryk"
HACS_URL = "https://github.com/Bankilo/pstryk-home-assistant"
UNIQUE_IDS = {"buy": "pstryk_buy_price", "sell": "pstryk_sell_price"}
# Entity ids seen for these unique ids: has_entity_name + device "Pstryk Energy Prices"
# (current HA naming) and the names in the integration's README. Fallback only — the
# entity registry (platform + unique_id) decides.
FALLBACK_ENTITY_IDS = {
    "buy": ("sensor.pstryk_energy_prices_buy_price", "sensor.pstryk_buy_price", "sensor.pstryk_pl_buy_price"),
    "sell": ("sensor.pstryk_energy_prices_sell_price", "sensor.pstryk_sell_price", "sensor.pstryk_pl_sell_price"),
}
SERIES_ATTRS = ("prices_today", "prices_tomorrow", "prices")
VAT = 1.23
# Without a meter tariff saved in "Energia i koszty" the planner adds G11 distribution
DEFAULT_DIST_TARIFF = "G11"
# Recorder backfill: a state is carried forward at most this long (a sensor that
# keeps its price for hours writes no new state; a stopped HA writes none either)
MAX_CARRY = timedelta(hours=12)


def is_pstryk(provider: Any) -> bool:
    return str(provider or "").strip().lower() == PROVIDER


def pstryk_active(provider: Any, tariff: Any, settings: Mapping[str, Any] | None) -> bool:
    """Pstryk prices drive planning / tariff data only on an explicit choice.

    Provider Pstryk, or the dynamic tariff with settings `dynamic_price_source` =
    "pstryk". Merely having the Pstryk sensors (e.g. a home HA showing another meter's
    contract) never changes anything — such prices are informational only.
    """
    if is_pstryk(provider):
        return True
    return (str(tariff or "").lower() == "dynamic"
            and str((settings or {}).get("dynamic_price_source") or "").lower() == PROVIDER)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(str(value).replace(",", ".")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def hour_utc(when: datetime) -> datetime:
    return when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def parse_time(value: Any) -> datetime | None:
    """Aware datetime from an ISO string (or datetime); naive values are rejected."""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo is not None and dt.utcoffset() is not None else None


def parse_prices(attrs: Mapping[str, Any] | None) -> dict[datetime, float]:
    """Hourly prices {UTC hour start: PLN/kWh} from the sensor's attributes.

    Reads prices_today, prices_tomorrow and prices (overlapping — the same hour counts
    once). Entries without an offset, with a bad time or a non-numeric price are
    skipped. Several entries in one hour (should the integration move to the 15-minute
    market products) are averaged. Offsets (+01:00 / +02:00) make DST days 23 / 25 h.
    """
    buckets: dict[datetime, dict[datetime, float]] = {}
    for key in SERIES_ATTRS:
        rows = (attrs or {}).get(key)
        if not isinstance(rows, (list, tuple)):
            continue
        for entry in rows:
            if not isinstance(entry, Mapping):
                continue
            start = parse_time(entry.get("time", entry.get("timestamp")))
            price = _finite(entry.get("price"))
            if start is None or price is None:
                continue
            exact = start.astimezone(timezone.utc)
            buckets.setdefault(hour_utc(exact), {})[exact] = price
    return {h: sum(v.values()) / len(v) for h, v in sorted(buckets.items())}


def pick_entity(rows: Iterable[Mapping[str, Any]], kind: str, existing: Iterable[str] = ()) -> str | None:
    """Entity id of the Pstryk buy / sell sensor.

    rows — entity registry entries {entity_id, platform, unique_id, disabled}: the
    enabled one with platform `pstryk` and the integration's unique_id wins; else the
    first known entity id that exists in the state machine.
    """
    uid = UNIQUE_IDS.get(kind)
    for row in rows:
        if (row.get("platform") == PSTRYK_DOMAIN and row.get("unique_id") == uid
                and not row.get("disabled") and str(row.get("entity_id", "")).startswith("sensor.")):
            return str(row["entity_id"])
    have = set(existing)
    return next((eid for eid in FALLBACK_ENTITY_IDS.get(kind, ()) if eid in have), None)


def local_day_counts(series: Mapping[datetime, float], tz: tzinfo, today: date) -> tuple[int, int]:
    """(hours today, hours tomorrow) in local time."""
    tomorrow = today + timedelta(days=1)
    days = [h.astimezone(tz).date() for h in series]
    return days.count(today), days.count(tomorrow)


# ── distribution on top of the Pstryk energy price ────────────────────────────

def includes_distribution(meter_cfg: Mapping[str, Any] | None) -> bool:
    """Meter setting `pstryk_includes_distribution` (default True — see the docstring)."""
    value = (meter_cfg or {}).get("pstryk_includes_distribution", True)
    return value is not False and str(value).lower() not in ("false", "0", "no", "nie")


def distribution_fn(meter_cfg: Mapping[str, Any] | None, has_meter: bool) -> Callable[[datetime], float]:
    """Gross per-kWh variable distribution (+ quality/OZE/cogeneration fees) at a local hour.

    The distribution tariff and its invoice prices come from the "Energia i koszty"
    settings (meter_tariffs, netto); without saved meter settings G11 (TAURON
    Dystrybucja 2026). Returns 0 everywhere while the meter setting says the Pstryk
    price already includes distribution (the default — see the module docstring).
    """
    from . import meter_tariffs as mt

    cfg = meter_cfg or {}
    if includes_distribution(cfg):
        return lambda _when: 0.0
    tariff_id = str(cfg.get("tariff") or DEFAULT_DIST_TARIFF) if has_meter else DEFAULT_DIST_TARIFF
    t = mt.resolve(tariff_id, cfg.get("prices") if has_meter else None)

    def adder(when: datetime) -> float:
        zone = mt.zone_at(t["id"], when.replace(tzinfo=None) if when.tzinfo else when)
        return (t["dist"].get(zone, 0.0) + t["per_kwh_fees"]) * VAT

    return adder


def with_adder(series: Mapping[datetime, float], tz: tzinfo, adder: Callable[[datetime], float]) -> dict[datetime, float]:
    """Series + the per-hour adder (evaluated at the local hour)."""
    return {h: p + adder(h.astimezone(tz)) for h, p in series.items()}


def choose_dynamic(
    pstryk: Mapping[str, Any] | None, entsoe: tuple[dict[datetime, float], float | None],
) -> tuple[dict[datetime, float], float | None, dict[datetime, float], str]:
    """(buy series, current buy price, sell series, source name) for the planner.

    Pstryk (see pstryk_ha.planner_prices) wins whenever it has a price; else the
    ENTSO-E series and all-in price as before (source "" keeps the old plan notes).
    """
    if pstryk and (pstryk.get("buy") or pstryk.get("buy_now") is not None):
        return dict(pstryk.get("buy") or {}), pstryk.get("buy_now"), dict(pstryk.get("sell") or {}), "Pstryk"
    series, now = entsoe
    return dict(series or {}), now, {}, ""


# ── storage of the price history (meter panel) ────────────────────────────────

def to_store(series: Mapping[datetime, float]) -> dict[str, float]:
    """{epoch hour as str: price} — compact JSON for the HA Store."""
    return {str(int(h.timestamp()) // 3600): round(p, 5) for h, p in series.items()}


def from_store(raw: Mapping[str, Any] | None) -> dict[datetime, float]:
    out: dict[datetime, float] = {}
    for key, value in (raw or {}).items():
        price = _finite(value)
        try:
            hour = datetime.fromtimestamp(int(key) * 3600, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if price is not None:
            out[hour] = price
    return out


def merge(stored: Mapping[datetime, float], fresh: Mapping[datetime, float], now: datetime,
          keep_days: int = 400) -> tuple[dict[datetime, float], bool]:
    """Stored history + fresh prices (fresh win — a republished price corrects the old one)."""
    cutoff = now - timedelta(days=keep_days)
    out = {h: p for h, p in stored.items() if h >= cutoff}
    changed = len(out) != len(stored)
    for h, p in fresh.items():
        if h < cutoff:
            continue
        if out.get(h) is None or abs(out[h] - p) > 1e-6:
            out[h] = p
            changed = True
    return out, changed


def hours_from_state_changes(
    changes: Iterable[tuple[datetime, Any]], start: datetime, end: datetime,
    slack: timedelta = timedelta(minutes=10), max_carry: timedelta = MAX_CARRY,
) -> dict[datetime, float]:
    """Hourly prices from the recorder's state history of the buy sensor.

    The sensor has no state_class, so HA keeps no long-term statistics of it — only
    the state history (purge_keep_days, 10 days by default). The integration refreshes
    at the top of the hour; a state written up to `slack` after an hour starts prices
    that hour. A price is carried over the following hours (no new state when the price
    repeats) for at most `max_carry`; unknown / unavailable states end it.
    """
    rows = sorted(((t.astimezone(timezone.utc), _finite(v)) for t, v in changes if t.tzinfo), key=lambda r: r[0])
    out: dict[datetime, float] = {}
    hour = hour_utc(start)
    i, current, since = 0, None, None
    while hour < end:
        cutoff = hour + slack
        while i < len(rows) and rows[i][0] <= cutoff:
            current, since = rows[i][1], rows[i][0]
            i += 1
        if current is not None and since is not None and hour - since <= max_carry:
            out[hour] = current
        hour += timedelta(hours=1)
    return out


def local_naive(series: Mapping[datetime, float], tz: tzinfo, scale: float = 1.0) -> dict[datetime, float]:
    """{local naive hour start: price × scale} — the meter panel's hour keys.

    The repeated hour of the October DST change keeps the later price (the meter's
    hourly series uses the same naive keys and merges that hour too).
    """
    return {h.astimezone(tz).replace(tzinfo=None): p * scale for h, p in sorted(series.items())}


# ── AI context ────────────────────────────────────────────────────────────────

def day_summary(series: Mapping[datetime, float], tz: tzinfo, day: date, top: int = 3) -> dict[str, Any] | None:
    hours = sorted((h.astimezone(tz), p) for h, p in series.items() if h.astimezone(tz).date() == day)
    if not hours:
        return None
    prices = [p for _, p in hours]
    by_price = sorted(hours, key=lambda r: (r[1], r[0]))
    return {
        "hours": len(hours),
        "avg": sum(prices) / len(prices),
        "min": by_price[0][1], "min_at": by_price[0][0].strftime("%H:%M"),
        "max": by_price[-1][1], "max_at": by_price[-1][0].strftime("%H:%M"),
        "cheapest": [(t.strftime("%H:%M"), p) for t, p in by_price[:top]],
        "dearest": [(t.strftime("%H:%M"), p) for t, p in reversed(by_price[-top:])],
    }


def _fmt(rows: list[tuple[str, float]]) -> str:
    return ", ".join(f"{t} {p:.2f}" for t, p in rows)


def prompt_lines(
    buy: Mapping[datetime, float], sell: Mapping[datetime, float] | None, now: datetime,
    includes_distribution: bool = True,
) -> list[str]:
    """Short Pstryk price context for AI prompts (instead of G1x zones)."""
    tz = now.tzinfo or timezone.utc
    today = now.date()
    lines = [
        "## Taryfa: Pstryk — cena dynamiczna (godzinowa)",
        "- Cena zakupu zmienia się CO GODZINĘ (ceny z integracji Pstryk.pl, brutto PLN/kWh). Brak stref G11/G12/G13.",
        ("- Cena Pstryk to koszt całkowity 1 kWh (energia + dystrybucja + VAT)." if includes_distribution else
         "- Cena Pstryk to energia BEZ dystrybucji — operator sieci dolicza dystrybucję za każdą kWh."),
    ]
    now_price = buy.get(hour_utc(now)) if now.tzinfo else None
    if now_price is not None:
        lines.append(f"- Teraz ({now.strftime('%H:00')}): {now_price:.2f} PLN/kWh")
    for label, day in (("Dziś", today), ("Jutro", today + timedelta(days=1))):
        s = day_summary(buy, tz, day)
        if s is None:
            lines.append(f"- {label}: ceny jeszcze nieopublikowane (zwykle po ~13:00 dnia poprzedniego)."
                         if label == "Jutro" else f"- {label}: brak cen z integracji Pstryk.")
            continue
        lines.append(
            f"- {label} ({s['hours']} h): średnio {s['avg']:.2f}, min {s['min']:.2f} o {s['min_at']}, "
            f"max {s['max']:.2f} o {s['max_at']} PLN/kWh"
        )
        lines.append(f"  - najtańsze godziny: {_fmt(s['cheapest'])}")
        lines.append(f"  - najdroższe godziny: {_fmt(s['dearest'])}")
    if sell:
        s = day_summary(sell, tz, today)
        if s is not None:
            lines.append(
                f"- Sprzedaż (prosument, Pstryk) dziś: średnio {s['avg']:.2f}, max {s['max']:.2f} o {s['max_at']} PLN/kWh"
            )
    lines.append("- Zasada: ładuj z sieci w najtańszych godzinach, korzystaj z baterii w najdroższych; "
                 "nie zakładaj stałych stref szczytu.")
    return lines


def serialize(series: Mapping[datetime, float]) -> dict[str, float]:
    """{ISO UTC hour: price} — JSON-safe form for coordinator data / AI snapshots."""
    return {h.isoformat(): round(p, 4) for h, p in sorted(series.items())}


def deserialize(raw: Any) -> dict[datetime, float]:
    out: dict[datetime, float] = {}
    if isinstance(raw, Mapping):
        for key, value in raw.items():
            start, price = parse_time(key), _finite(value)
            if start is not None and price is not None:
                out[hour_utc(start)] = price
    return out
