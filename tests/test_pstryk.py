# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Pstryk: prices from the Pstryk.pl integration (Bankilo, domain `pstryk`) in the
planner, the meter panel and the AI context.

Attribute shape as seen live on a business HA (integration 0.3.4, 2026-11-05):
sensor.pstryk_energy_prices_buy_price, prices_today / prices_tomorrow / prices,
[{"time": "2026-11-05T00:00:00+01:00", "price": 1.541}, …], PLN/kWh.

Run: uvx --python 3.14 --with pytest pytest -q tests  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"


def _stub(name: str, **attrs) -> types.ModuleType:
    mod = sys.modules.get(name) or types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# pstryk_ha / meter_ws import Home Assistant only for WebSocket handlers and the registry
_passthrough = lambda *a, **k: (lambda f: f)  # noqa: E731
_stub("voluptuous", Required=lambda *a, **k: a[0], Optional=lambda *a, **k: a[0])
_stub("homeassistant")
_stub("homeassistant.components")
_stub("homeassistant.components.websocket_api", websocket_command=_passthrough,
      async_response=lambda f: f, async_register_command=lambda *a, **k: None)
sys.modules["homeassistant.components"].websocket_api = sys.modules["homeassistant.components.websocket_api"]
_stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
_REGISTRY: list = []
_er = _stub("homeassistant.helpers.entity_registry",
            async_get=lambda hass: types.SimpleNamespace(entities={i: e for i, e in enumerate(_REGISTRY)}))
_stub("homeassistant.helpers", entity_registry=_er)

_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
pk = importlib.import_module("smartinghome_pure.pstryk")
ph = importlib.import_module("smartinghome_pure.pstryk_ha")
arb = importlib.import_module("smartinghome_pure.arbitrage")
mt = importlib.import_module("smartinghome_pure.meter_tariffs")
mw = importlib.import_module("smartinghome_pure.meter_ws")
tp = importlib.import_module("smartinghome_pure.tariff_prompt")

WAW = ZoneInfo("Europe/Warsaw")
# Live buy prices, company HA, 2026-11-05 00:00–14:00 (gross zł/kWh)
LIVE = [1.541, 1.507, 1.478, 1.467, 1.482, 1.559, 2.099, 2.291, 2.073, 1.591, 1.206, 1.074, 1.072, 1.26, 1.821]


def _items(day: date, prices: dict[int, float], tz=WAW) -> list[dict]:
    """Integration attribute items: local hour with its offset."""
    return [{"time": datetime(day.year, day.month, day.day, h, tzinfo=tz).isoformat(), "price": p}
            for h, p in sorted(prices.items())]


def _attrs(today: dict[int, float], tomorrow: dict[int, float] | None = None,
           day: date = date(2026, 11, 5)) -> dict:
    t = _items(day, today)
    m = _items(day + timedelta(days=1), tomorrow or {})
    return {"prices_today": t, "prices_tomorrow": m, "prices": t + m,
            "unit_of_measurement": "PLN/kWh", "is_cheap": False, "is_expensive": False}


def _utc(y, mo, d, h, tz=WAW) -> datetime:
    return datetime(y, mo, d, h, tzinfo=tz).astimezone(timezone.utc)


# ── parsing the attributes ─────────────────────────────────────────────────────

def test_live_attributes_parse_to_utc_hours():
    series = pk.parse_prices(_attrs(dict(enumerate(LIVE))))
    assert len(series) == 15  # prices_today and prices overlap — each hour once
    assert series[_utc(2026, 11, 5, 0)] == 1.541
    assert series[datetime(2026, 11, 4, 23, tzinfo=timezone.utc)] == 1.541  # +01:00 in November
    assert series[_utc(2026, 11, 5, 7)] == 2.291


def test_summer_offset_and_tomorrow_missing():
    attrs = _attrs({h: 0.5 + h / 100 for h in range(24)}, None, day=date(2026, 7, 1))
    series = pk.parse_prices(attrs)
    assert len(series) == 24
    assert series[datetime(2026, 6, 30, 22, tzinfo=timezone.utc)] == 0.5  # 00:00+02:00
    assert pk.local_day_counts(series, WAW, date(2026, 7, 1)) == (24, 0)


def test_dst_days_have_23_and_25_hours():
    # 2026-10-25: 02:00+02:00 → 02:00+01:00 (25 h); 2026-03-29: no 02:00 (23 h)
    start = datetime(2026, 10, 24, 22, tzinfo=timezone.utc)  # local midnight 25.10
    autumn = [{"time": (start + timedelta(hours=i)).astimezone(WAW).isoformat(), "price": float(i)} for i in range(25)]
    spring_start = datetime(2026, 3, 28, 23, tzinfo=timezone.utc)
    spring = [{"time": (spring_start + timedelta(hours=i)).astimezone(WAW).isoformat(), "price": float(i)} for i in range(23)]
    a = pk.parse_prices({"prices_today": autumn})
    s = pk.parse_prices({"prices_today": spring})
    assert len(a) == 25 and len(s) == 23
    assert pk.local_day_counts(a, WAW, date(2026, 10, 25)) == (25, 0)
    assert pk.local_day_counts(s, WAW, date(2026, 3, 29)) == (23, 0)
    # the repeated 02:00 keeps both prices as separate UTC hours
    assert a[datetime(2026, 10, 25, 0, tzinfo=timezone.utc)] == 2.0
    assert a[datetime(2026, 10, 25, 1, tzinfo=timezone.utc)] == 3.0


def test_malformed_entries_are_skipped():
    attrs = {
        "prices_today": [
            {"time": "2026-11-05T00:00:00+01:00", "price": 1.0},
            {"time": "2026-11-05T01:00:00", "price": 1.1},           # no offset
            {"time": "jutro", "price": 1.2},                          # bad time
            {"time": "2026-11-05T03:00:00+01:00", "price": "abc"},   # bad price
            {"time": "2026-11-05T04:00:00+01:00", "price": None},
            {"time": "2026-11-05T05:00:00+01:00", "price": float("nan")},
            {"time": "2026-11-05T06:00:00+01:00", "price": True},
            {"time": "2026-11-05T07:00:00+01:00", "price": "1,25"},  # decimal comma
            {"time": "2026-11-05T08:00:00+01:00", "price": -0.125},  # negative is a price
            "garbage", None,
        ],
        "prices_tomorrow": None,
        "prices": "not a list",
    }
    series = pk.parse_prices(attrs)
    assert sorted(series.values()) == [-0.125, 1.0, 1.25]
    assert pk.parse_prices(None) == {} and pk.parse_prices({}) == {}


def test_quarter_hours_are_averaged_per_hour():
    rows = [{"time": f"2026-11-05T10:{m:02d}:00+01:00", "price": p} for m, p in ((0, 1.0), (15, 2.0), (30, 3.0), (45, 2.0))]
    assert pk.parse_prices({"prices_today": rows}) == {_utc(2026, 11, 5, 10): 2.0}


def test_entity_resolved_by_registry_unique_id_then_known_ids():
    rows = [
        {"entity_id": "sensor.pstryk_energy_prices_buy_price_next_hour", "platform": "pstryk",
         "unique_id": "pstryk_buy_price_next_hour"},
        {"entity_id": "sensor.renamed_buy", "platform": "pstryk", "unique_id": "pstryk_buy_price", "disabled": True},
        {"entity_id": "sensor.my_buy", "platform": "pstryk", "unique_id": "pstryk_buy_price"},
        {"entity_id": "sensor.other", "platform": "entsoe", "unique_id": "pstryk_sell_price"},
    ]
    assert pk.pick_entity(rows, "buy") == "sensor.my_buy"
    assert pk.pick_entity(rows, "sell") is None
    assert pk.pick_entity([], "sell", ["sensor.pstryk_energy_prices_sell_price"]) == "sensor.pstryk_energy_prices_sell_price"


# ── planner: Pstryk beats ENTSO-E, prosumer sell series ──────────────────────

class _State:
    def __init__(self, state, attrs):
        self.state, self.attributes = state, attrs


class _States:
    def __init__(self, states):
        self._s = states

    def get(self, eid):
        return self._s.get(eid)

    def async_entity_ids(self, domain):
        return [e for e in self._s if e.startswith(domain + ".")]


def _hass(states: dict) -> types.SimpleNamespace:
    return types.SimpleNamespace(states=_States(states), data={})


def _pstryk_hass(today=None, tomorrow=None, sell=None):
    _REGISTRY[:] = [
        types.SimpleNamespace(entity_id="sensor.pstryk_energy_prices_buy_price", platform="pstryk",
                              unique_id="pstryk_buy_price", disabled_by=None),
        types.SimpleNamespace(entity_id="sensor.pstryk_energy_prices_sell_price", platform="pstryk",
                              unique_id="pstryk_sell_price", disabled_by=None),
    ]
    today = today if today is not None else dict(enumerate(LIVE))
    return _hass({
        "sensor.pstryk_energy_prices_buy_price": _State(str(today.get(12, "unknown")), _attrs(today, tomorrow)),
        "sensor.pstryk_energy_prices_sell_price": _State("0.7", _attrs(sell or {h: 0.56 + h * 0.02 for h in range(24)})),
    })


NOW = datetime(2026, 11, 5, 12, 20, tzinfo=WAW)


def test_planner_uses_pstryk_all_in_without_adder_by_default():
    hass = _pstryk_hass()
    out = ph.planner_prices(hass, {}, "dynamic", "pstryk", NOW)
    assert out["entities"]["buy"] == "sensor.pstryk_energy_prices_buy_price"
    assert out["includes_distribution"] is True
    assert out["buy"][_utc(2026, 11, 5, 7)] == 2.291  # all-in: nothing added
    assert out["buy_now"] == 1.072
    assert len(out["sell"]) == 24


def test_planner_adds_osd_distribution_when_the_meter_setting_says_so():
    hass = _pstryk_hass()
    settings = {"meter": {"tariff": "C13", "pstryk_includes_distribution": False}}
    out = ph.planner_prices(hass, settings, "dynamic", "pstryk", NOW)
    morning = (0.2155 + mt.PER_KWH_FEES_NETTO) * 1.23   # C13 Thursday 07:00 = morning peak
    assert abs(out["buy"][_utc(2026, 11, 5, 7)] - (2.291 + morning)) < 1e-9
    night = (0.1527 + mt.PER_KWH_FEES_NETTO) * 1.23
    assert abs(out["buy"][_utc(2026, 11, 5, 2)] - (1.478 + night)) < 1e-9
    # without saved meter settings: G11 distribution
    g11 = (0.2464 + mt.PER_KWH_FEES_NETTO) * 1.23
    adder = pk.distribution_fn({"pstryk_includes_distribution": False}, has_meter=False)
    assert abs(adder(datetime(2026, 11, 5, 7)) - g11) < 1e-9


def test_sensors_alone_change_nothing_at_home():
    """Home HA (Tauron) with the integration showing another meter's contract."""
    hass = _pstryk_hass()
    assert ph.planner_prices(hass, {"energy_provider": "tauron"}, "g13", "tauron", NOW) is None
    assert ph.planner_prices(hass, {}, "dynamic", "tauron", NOW) is None  # ENTSO-E stays
    assert ph.dynamic_tariff_data(hass, {"energy_provider": "tauron"}, NOW, "tauron") is None
    assert not pk.pstryk_active("tauron", "g13", {"dynamic_price_source": "pstryk"})


def test_dynamic_tariff_with_pstryk_chosen_as_source_buys_but_sells_at_rce():
    hass = _pstryk_hass()
    out = ph.planner_prices(hass, {"dynamic_price_source": "pstryk"}, "dynamic", "tauron", NOW)
    assert out["buy"] and out["sell"] == {}  # prosumer price only for a Pstryk customer
    assert ph.dynamic_tariff_data(hass, {"dynamic_price_source": "pstryk"}, NOW, "tauron") is not None


def test_without_pstryk_sensors_entso_e_stays():
    _REGISTRY[:] = []
    assert ph.planner_prices(_hass({}), {}, "dynamic", "pstryk", NOW) is None
    entsoe = ({_utc(2026, 11, 5, 13): 0.9}, 0.8)
    assert pk.choose_dynamic(None, entsoe) == ({_utc(2026, 11, 5, 13): 0.9}, 0.8, {}, "")


def test_pstryk_series_preferred_over_entso_e():
    pst = {"buy": {_utc(2026, 11, 5, 13): 1.26}, "buy_now": 1.072, "sell": {_utc(2026, 11, 5, 13): 0.6}}
    entsoe = ({_utc(2026, 11, 5, 13): 0.9}, 0.8)
    series, now_price, sell, source = pk.choose_dynamic(pst, entsoe)
    assert series == {_utc(2026, 11, 5, 13): 1.26} and now_price == 1.072 and source == "Pstryk"
    assert sell == {_utc(2026, 11, 5, 13): 0.6}
    # an empty Pstryk answer falls back to ENTSO-E
    assert pk.choose_dynamic({"buy": {}, "buy_now": None, "sell": {}}, entsoe)[3] == ""


def _inputs(now, **kw):
    return arb.build_inputs(
        now, tariff="dynamic", provider="pstryk", rce={}, load_profile_kw=[0.8] * 24,
        pv_today_remaining_kwh=0.0, pv_tomorrow_kwh=0.0, sunrise_h=7.0, sunset_h=16.0,
        horizon_h=30, slot_minutes=30, **kw,
    )


def test_build_inputs_prices_buy_and_sell_from_pstryk():
    buy = pk.parse_prices(_attrs({h: 1.0 + h / 100 for h in range(24)}))
    sell = pk.parse_prices(_attrs({h: (-0.1 if h == 13 else 0.5 + h / 100) for h in range(24)}))
    inputs = _inputs(NOW, dynamic_buy=buy, dynamic_now=1.12, dynamic_sell=sell)
    first = inputs[0]
    assert first.zone == arb.ZONE_DYNAMIC and first.buy == 1.12 and abs(first.sell - 0.62) < 1e-9
    at13 = next(h for h in inputs if h.start == datetime(2026, 11, 5, 13, 0))
    assert at13.sell == 0.0  # negative prosumer price: export worth nothing, never a cost
    # tomorrow is not published: buy estimated from today's hour, sell back to RCE × 1.23
    t9 = next(h for h in inputs if h.start == datetime(2026, 11, 6, 9, 0))
    assert t9.zone == arb.ZONE_DYNAMIC_EST and abs(t9.buy - 1.09) < 1e-9
    assert abs(t9.sell - 0.4 * arb.RCE_PROSUMER_COEFFICIENT) < 1e-9  # no RCE → 400 zł/MWh default
    assert not any(h.no_import for h in inputs)  # dynamic: no tariff peaks


def test_plan_note_names_pstryk():
    buy = pk.parse_prices(_attrs({h: 1.0 for h in range(24)}, {h: 1.0 for h in range(24)}))
    inputs = _inputs(datetime(2026, 11, 5, 14, 0, tzinfo=WAW), dynamic_buy=buy)
    assert arb.dynamic_note(inputs, "Pstryk") == "ceny Pstryk"
    est = _inputs(NOW, dynamic_buy=pk.parse_prices(_attrs({h: 1.0 for h in range(24)})))
    note = arb.dynamic_note(est, "Pstryk")
    assert note.startswith("ceny Pstryk; ceny od 06.11 00:00 szacowane")
    assert arb.dynamic_note(est).startswith("ceny od 06.11")  # ENTSO-E notes unchanged
    assert arb.dynamic_note([], "Pstryk").startswith("brak cen dynamicznych (Pstryk)")


def test_coordinator_dynamic_keys_from_pstryk():
    hass = _pstryk_hass()
    d = ph.dynamic_tariff_data(hass, {"energy_provider": "pstryk"}, NOW)
    assert d["allin_now"] == 1.072 and d["allin_next"] == 1.26
    assert d["min"] == 1.072 and d["max"] == 2.291
    assert d["rank"] == 1 and d["percentile"] == 0.0
    assert pk.deserialize(d["hourly"])[_utc(2026, 11, 5, 7)] == 2.291


# ── price history (meter panel) ───────────────────────────────────────────────

def test_store_roundtrip_and_merge():
    series = pk.parse_prices(_attrs(dict(enumerate(LIVE))))
    assert pk.from_store(pk.to_store(series)) == series
    now = datetime(2026, 11, 5, 13, tzinfo=timezone.utc)
    old = {now - timedelta(days=500): 1.0}
    merged, changed = pk.merge(old, series, now)
    assert changed and len(merged) == 15  # 500-day-old hour pruned
    again, changed = pk.merge(merged, series, now)
    assert not changed and again == merged
    fixed, changed = pk.merge(merged, {_utc(2026, 11, 5, 0): 1.6}, now)
    assert changed and fixed[_utc(2026, 11, 5, 0)] == 1.6


def test_recorder_state_history_to_hours():
    t0 = datetime(2026, 11, 5, 0, 0, 2, tzinfo=timezone.utc)
    changes = [
        (t0, "1.5"),
        (t0 + timedelta(hours=1), "1.4"),
        # 02:00 repeats 1.4 → no state written
        (t0 + timedelta(hours=3), "unavailable"),
        (t0 + timedelta(hours=4, minutes=3), "1.2"),
    ]
    out = pk.hours_from_state_changes(changes, t0.replace(second=0), t0.replace(second=0) + timedelta(hours=6))
    h = lambda i: datetime(2026, 11, 5, i, tzinfo=timezone.utc)  # noqa: E731
    assert out == {h(0): 1.5, h(1): 1.4, h(2): 1.4, h(4): 1.2, h(5): 1.2}
    # a stopped HA: the last price is not carried over half a day
    far = pk.hours_from_state_changes([(t0, "1.5")], t0, t0 + timedelta(hours=20))
    assert len(far) == 13


def test_local_naive_netto_for_the_meter():
    series = pk.parse_prices(_attrs({0: 1.23, 1: 2.46}))
    assert pk.local_naive(series, WAW, 1 / 1.23) == {datetime(2026, 11, 5, 0): 1.0, datetime(2026, 11, 5, 1): 2.0}


# ── meter cost hour by hour ────────────────────────────────────────────────────

def _meter_hours(day=date(2026, 12, 1), kwh=2.0):  # Tuesday: C13 working day
    return [(datetime(day.year, day.month, day.day, h), kwh) for h in range(24)]


def test_cost_hours_with_pstryk_price_and_distribution_included():
    t = mt.resolve("C13")
    prices = {datetime(2026, 12, 1, h): 1.0 for h in range(24)}
    c = mt.cost_hours(t, _meter_hours(), prices, price_includes_distribution=True)
    assert c["energy"] == 48.0 and c["dist"] == 0.0 and c["dynamic_hours"] == 24 and c["tariff_hours"] == 0
    assert c["zones"]["morning"]["kwh"] == 12.0  # 7–13 on a working day — zones stay


def test_cost_hours_adds_distribution_and_falls_back_to_tariff():
    t = mt.resolve("C13")
    prices = {datetime(2026, 12, 1, h): 1.0 for h in range(12)}  # half the day priced
    c = mt.cost_hours(t, _meter_hours(), prices, price_includes_distribution=False)
    assert c["dynamic_hours"] == 12 and c["tariff_hours"] == 12
    # 00–11 at 1.0, 12 at the C13 morning price, 13–15 / 21–23 off-peak, 16–20 afternoon
    energy = 12 * 2 * 1.0 + 2 * 1.01 + 6 * 2 * 0.932 + 5 * 2 * 1.25
    assert abs(c["energy"] - round(energy, 2)) < 0.011
    dist = sum(2 * (t["dist"][mt.zone_at("C13", w)] + mt.PER_KWH_FEES_NETTO) for w, _ in _meter_hours())
    assert abs(c["dist"] - round(dist, 2)) < 0.011
    assert "dynamic_hours" not in mt.cost_hours(t, _meter_hours())  # tariff pricing unchanged


def test_meter_summary_prices_energy_from_pstryk():
    hours = _meter_hours(date(2026, 12, 1)) + _meter_hours(date(2026, 12, 2))
    prices = {w: 0.8 for w, _ in hours}
    cfg = {"tariff": "C13", "energy_price": "pstryk", "contract_kw": 10}
    s = mw.summarize(hours, cfg, datetime(2026, 12, 3, 9), prices)
    assert s["energy_price"] == {"source": "pstryk", "includes_distribution": True, "hours": 48}
    assert s["current_month"]["energy"] == round(96 * 0.8, 2) and s["current_month"]["dist"] == 0.0
    assert s["current_month"]["dynamic_hours"] == 48
    s2 = mw.summarize(hours, {**cfg, "pstryk_includes_distribution": False}, datetime(2026, 12, 3, 9), prices)
    assert s2["current_month"]["dist"] > 0 and s2["energy_price"]["includes_distribution"] is False
    tariff_only = mw.summarize(hours, {"tariff": "C13"}, datetime(2026, 12, 3, 9), prices)
    assert tariff_only["energy_price"]["source"] == "tariff" and "dynamic_hours" not in tariff_only["current_month"]


def test_meter_c11_with_pstryk_all_in_needs_no_invoice_prices():
    hours = _meter_hours(date(2026, 12, 1))
    prices = {w: 0.8 for w, _ in hours}
    s = mw.summarize(hours, {"tariff": "C11", "energy_price": "pstryk"}, datetime(2026, 12, 2, 9), prices)
    assert s["tariff"]["needs_input"] is False
    s = mw.summarize(hours, {"tariff": "C11", "energy_price": "pstryk", "pstryk_includes_distribution": False},
                     datetime(2026, 12, 2, 9), prices)
    assert s["tariff"]["needs_input"] is True  # C11 distribution must come from the invoice


# ── AI context ─────────────────────────────────────────────────────────────────

def test_prompt_lines_today_and_missing_tomorrow():
    buy = pk.parse_prices(_attrs(dict(enumerate(LIVE))))
    sell = pk.parse_prices(_attrs({h: 0.56 + h * 0.02 for h in range(24)}))
    text = "\n".join(pk.prompt_lines(buy, sell, NOW))
    assert "Pstryk" in text and "G13" in text  # tells the model there are no G1x zones
    assert "Teraz (12:00): 1.07 PLN/kWh" in text
    assert "min 1.07 o 12:00" in text and "max 2.29 o 07:00" in text
    assert "najtańsze godziny: 12:00 1.07, 11:00 1.07, 10:00 1.21" in text
    assert "najdroższe godziny: 07:00 2.29, 06:00 2.10, 08:00 2.07" in text
    assert "Jutro: ceny jeszcze nieopublikowane" in text
    assert "koszt całkowity" in text
    assert "BEZ dystrybucji" in "\n".join(pk.prompt_lines(buy, None, NOW, includes_distribution=False))


def test_tariff_prompt_context_for_pstryk_replaces_zones():
    buy = pk.parse_prices(_attrs(dict(enumerate(LIVE)), {h: 0.9 for h in range(24)}))
    ctx = tp.build_tariff_prompt_context("g13", "pstryk", NOW, hourly_prices=pk.serialize(buy))
    assert ctx.tariff_type == "dynamic" and ctx.current_price == 1.072
    assert ctx.cheapest_price == 1.072 and ctx.most_expensive_price == 2.291
    assert "Najtańsze godziny: 12:00 1.07" in ctx.schedule_text
    assert "Jutro (24 h): średnio 0.90" in ctx.schedule_text
    assert "07:00" not in tp.render_next_zones("g13", "pstryk", NOW, 2, pk.serialize(buy))
    assert tp.render_next_zones("g13", "pstryk", NOW, 2, pk.serialize(buy)).splitlines()[0] == "+1h (13:00): pstryk (1.26 PLN/kWh)"
    empty = tp.build_tariff_prompt_context("g13", "pstryk", NOW)
    assert "brak cen" in empty.schedule_text
    assert tp.build_tariff_prompt_context("g13", "tauron", NOW).current_zone != "dynamic"
