# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Max Zysk planner on the dynamic tariff: purchases priced per hour from the ENTSO-E
day-ahead series (gross, with the integration's per-kWh adder), the current price past
the published hours, G1x tariffs untouched, hourly net-billing still respected.

Run: uvx --python 3.14 --with pytest pytest -q tests  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
arb = importlib.import_module("smartinghome_pure.arbitrage")

WAW = ZoneInfo("Europe/Warsaw")
ADDER = 0.3137  # (margin 0.05 + excise 0.005 + distribution 0.20) × 1.23 — the ENTSO-E docs example


def _entries(day: datetime, values: dict[int, float]) -> list[dict]:
    """ENTSO-E `prices_today` / `prices_tomorrow` entries (hourly, start in UTC)."""
    out = []
    for h, v in sorted(values.items()):
        start = datetime(day.year, day.month, day.day, h, tzinfo=WAW).astimezone(timezone.utc)
        out.append({"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(),
                    "start_local": f"{h:02d}:00", "value": v, "raw_eur_mwh": 0.0})
    return out


def _attrs(today: dict[int, float], tomorrow: dict[int, float] | None = None,
           day: datetime = datetime(2026, 10, 17)) -> dict:  # noqa: DTZ001 — calendar day only
    return {"prices_today": _entries(day, today),
            "prices_tomorrow": _entries(day + timedelta(days=1), tomorrow or {}),
            "tomorrow_available": bool(tomorrow)}


def _inputs(now: datetime, tariff: str = "dynamic", horizon_h: int = 30, slot_minutes: int = 60, **kw):
    return arb.build_inputs(
        now, tariff=tariff, provider="tauron", rce={}, load_profile_kw=[0.8] * 24,
        pv_today_remaining_kwh=0.0, pv_tomorrow_kwh=0.0, sunrise_h=7.0, sunset_h=17.5,
        horizon_h=horizon_h, slot_minutes=slot_minutes, **kw,
    )


# ── composition of the gross price ──────────────────────────────────────────────

def test_series_gets_the_all_in_adder_of_the_integration():
    today = {h: 0.30 + 0.01 * h for h in range(24)}
    series, current = arb.dynamic_prices(0.40, 0.40 + ADDER, _attrs(today, {0: 0.25}), "PLN/kWh")
    assert abs(current - (0.40 + ADDER)) < 1e-9
    assert len(series) == 25
    key = datetime(2026, 10, 17, 18, tzinfo=WAW).astimezone(timezone.utc)
    assert abs(series[key] - (0.48 + ADDER)) < 1e-9
    key = datetime(2026, 10, 18, 0, tzinfo=WAW).astimezone(timezone.utc)
    assert abs(series[key] - (0.25 + ADDER)) < 1e-9


def test_series_per_mwh_is_scaled_and_foreign_currency_is_ignored():
    series, current = arb.dynamic_prices(400.0, 713.7, _attrs({10: 500.0}), "PLN/MWh")
    assert abs(current - 0.7137) < 1e-9
    assert abs(next(iter(series.values())) - (0.5 + 0.3137)) < 1e-9
    assert arb.dynamic_prices(0.1, 0.2, _attrs({10: 0.1}), "EUR/kWh") == ({}, None)


def test_without_wholesale_or_with_inconsistent_sensors_only_the_current_price():
    attrs = _attrs({h: 0.3 for h in range(24)})
    assert arb.dynamic_prices("unavailable", 0.71, attrs) == ({}, 0.71)
    assert arb.dynamic_prices(0.90, 0.71, attrs) == ({}, 0.71)  # all-in below wholesale
    assert arb.dynamic_prices(0.40, "unknown", attrs) == ({}, None)


# ── slots priced from the series ────────────────────────────────────────────────

def test_dynamic_slots_take_their_hour_and_fall_back_to_the_current_price():
    today = {h: 0.20 + 0.02 * h for h in range(24)}
    series, current = arb.dynamic_prices(today[10], today[10] + ADDER, _attrs(today))
    now = datetime(2026, 10, 17, 10, 20, tzinfo=WAW)
    ins = _inputs(now, slot_minutes=30, dynamic_buy=series, dynamic_now=current)
    for h in ins:
        if h.start.date() == now.date():
            assert h.zone == arb.ZONE_DYNAMIC
            assert abs(h.buy - (today[h.start.hour] + ADDER)) < 1e-9, h
        else:  # tomorrow not published yet → the current hour's price
            assert h.zone == arb.ZONE_DYNAMIC_NOW
            assert abs(h.buy - (today[10] + ADDER)) < 1e-9, h
    assert not any(h.no_import for h in ins)  # no tariff peaks on market prices
    assert arb.dynamic_note(ins) == (
        "brak prognozy cen dynamicznych od 18.10 00:00 — zakup wyceniony ceną bieżącą")


def test_current_price_of_the_series_beats_a_stale_all_in_sensor():
    # 11:03, the ENTSO-E sensors still hold the 10:00 all-in price: the series has 11:00
    today = {10: 0.50, 11: 0.30}
    series, _ = arb.dynamic_prices(0.50, 0.50 + ADDER, _attrs(today))
    ins = _inputs(datetime(2026, 10, 17, 11, 3, tzinfo=WAW), horizon_h=3,
                  dynamic_buy=series, dynamic_now=0.50 + ADDER)
    assert abs(ins[0].buy - (0.30 + ADDER)) < 1e-9
    assert all(abs(h.buy - (0.30 + ADDER)) < 1e-9 for h in ins[1:])  # 12:00+ unknown


def test_full_series_has_no_note():
    series, current = arb.dynamic_prices(0.3, 0.3 + ADDER, _attrs(
        {h: 0.3 for h in range(24)}, {h: 0.4 for h in range(24)}))
    ins = _inputs(datetime(2026, 10, 17, 14, 0, tzinfo=WAW), dynamic_buy=series, dynamic_now=current)
    assert all(h.zone == arb.ZONE_DYNAMIC for h in ins)
    assert arb.dynamic_note(ins) == ""


def test_no_series_prices_everything_at_the_current_price():
    ins = _inputs(datetime(2026, 10, 17, 9, 0, tzinfo=WAW), dynamic_buy={}, dynamic_now=0.77)
    assert all(h.zone == arb.ZONE_DYNAMIC_NOW and h.buy == 0.77 for h in ins)
    assert arb.dynamic_note(ins) == "brak prognozy cen dynamicznych — zakup wyceniony ceną bieżącą"


def test_no_dynamic_price_at_all_keeps_the_flat_price_and_says_so():
    ins = _inputs(datetime(2026, 10, 17, 9, 0, tzinfo=WAW))
    assert all(h.zone == "flat" for h in ins)
    assert "stałą ceną" in arb.dynamic_note(ins)


def test_dst_end_every_slot_gets_the_price_of_its_utc_hour():
    # 2026-10-25: 25 hours, 02:00–03:00 local twice — the series is keyed by UTC hour
    start = datetime(2026, 10, 25, 0, 0, tzinfo=WAW).astimezone(timezone.utc)
    series = {start + timedelta(hours=k): 0.1 * k for k in range(25)}
    ins = _inputs(datetime(2026, 10, 25, 0, 0, tzinfo=WAW), horizon_h=25, dynamic_buy=series, dynamic_now=0.0)
    assert [round(h.buy, 3) for h in ins] == [round(0.1 * k, 3) for k in range(25)]


# ── the plan buys when the market is cheap ───────────────────────────────────────

def _night(tariff: str, series=None, current=None, slot_minutes: int = 60):
    now = datetime(2026, 10, 17, 22, 0, tzinfo=WAW)
    ins = _inputs(now, tariff=tariff, horizon_h=12, slot_minutes=slot_minutes,
                  dynamic_buy=series, dynamic_now=current)
    for h in ins:
        h.sell = 0.10
    arb.mark_daylight(ins, 7.0, 17.5)
    return ins


NIGHT_TODAY = {22: 0.55, 23: 0.45}
NIGHT_TOMORROW = {0: 0.25, 1: 0.20, 2: 0.18, 3: 0.20, 4: 0.30, 5: 0.45, 6: 0.85, 7: 0.95,
                  8: 0.90, 9: 0.70}


def test_dynamic_plan_charges_in_the_cheapest_hours_for_the_expensive_morning():
    series, current = arb.dynamic_prices(0.55, 0.55 + ADDER, _attrs(NIGHT_TODAY, NIGHT_TOMORROW))
    ins = _night("dynamic", series, current)
    plan = arb.optimize(20, ins, arb.ArbitrageParams(slot_minutes=60, wear_cost=0.10, min_profit=0.05))
    charge_hours = [hp.start[11:13] for hp in plan.hours if hp.action == arb.ACT_CHARGE_GRID]
    assert charge_hours and set(charge_hours) <= {"00", "01", "02", "03"}, charge_hours
    morning = [hp for hp in plan.hours if hp.start[11:13] in ("06", "07", "08")]
    assert all(hp.grid_import < 0.05 for hp in morning)  # the battery covers the expensive hours
    assert plan.total_cost < plan.baseline_cost - 0.5
    assert all(hp.zone == arb.ZONE_DYNAMIC for hp in plan.hours)


def test_flat_fallback_sees_nothing_to_gain():
    # the old behaviour (G11-like flat price): no reason to buy ahead at night
    plan = arb.optimize(20, _night("dynamic"), arb.ArbitrageParams(slot_minutes=60, wear_cost=0.10, min_profit=0.05))
    assert not any(hp.action == arb.ACT_CHARGE_GRID for hp in plan.hours)


# ── hourly net-billing with dynamic prices ───────────────────────────────────────

def test_dynamic_no_grid_charge_and_battery_sale_in_the_same_clock_hour():
    # cheap 19:00 hour, RCE spikes in its second half: charging 19:00–19:30 and selling
    # 19:30–20:00 only cycles the battery — the meter nets both within the hour
    series, current = arb.dynamic_prices(0.30, 0.30 + ADDER, _attrs(
        {17: 0.60, 18: 0.60, 19: 0.05, 20: 0.60, 21: 0.60, 22: 0.60, 23: 0.60}))
    now = datetime(2026, 10, 17, 17, 0, tzinfo=WAW)
    ins = _inputs(now, horizon_h=7, slot_minutes=30, dynamic_buy=series, dynamic_now=current)
    for h in ins:
        h.sell = 1.60 if (h.start.hour, h.start.minute) in ((19, 30), (20, 0)) else 0.40
        h.load_kwh = 0.2
    plan = arb.optimize(50, ins, arb.ArbitrageParams(slot_minutes=30, wear_cost=0.10, min_profit=0.05))
    assert any(hp.grid_export > 0.1 for hp in plan.hours)  # the spike is still sold
    by_hour: dict[str, list] = {}
    for hp in plan.hours:
        by_hour.setdefault(hp.start[:13], []).append(hp)
    for hour, slots in by_hour.items():
        grid_charge = any(s.battery_kwh > 0.01 and s.grid_import > 0.2 + 1e-6 for s in slots)
        battery_sale = any(s.battery_kwh < -0.01 and s.grid_export > 1e-6 for s in slots)
        assert not (grid_charge and battery_sale), (hour, [(s.start, s.action, s.battery_kwh) for s in slots])


# ── G11 / G12 / G13 unchanged ────────────────────────────────────────────────────

def test_fixed_tariffs_ignore_dynamic_prices():
    series = {datetime(2026, 10, 17, 8, tzinfo=timezone.utc) + timedelta(hours=k): 0.01 for k in range(40)}
    now = datetime(2026, 10, 17, 9, 40, tzinfo=WAW)
    for tariff in ("g11", "g12", "g12w", "g13"):
        plain = _inputs(now, tariff=tariff, slot_minutes=30)
        fed = _inputs(now, tariff=tariff, slot_minutes=30, dynamic_buy=series, dynamic_now=0.01)
        assert [(h.zone, h.buy, h.no_import) for h in fed] == [(h.zone, h.buy, h.no_import) for h in plain]
        for h in plain:
            assert (h.zone, h.buy) == arb.buy_price(h.start, tariff, "tauron")
    g13 = _inputs(datetime(2026, 10, 19, 9, 40, tzinfo=WAW), tariff="g13")  # Monday
    assert any(h.no_import for h in g13)  # G13 peaks still marked
