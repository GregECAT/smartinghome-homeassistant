"""Pstryk meter over the Pstryk API (pstryk_api.py — pure).

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome" / "pstryk_api.py"
_spec = importlib.util.spec_from_file_location("pstryk_api_pure", _PATH)
pa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa)

UTC = timezone.utc
NOW = datetime(2026, 11, 5, 15, 7, tzinfo=UTC)


def _frame(start, minutes=60, imp=0.3, exp=0.0, live=False, cost=None, key="meterValues"):
    end = start + timedelta(minutes=minutes)
    fr = {"start": start.isoformat().replace("+00:00", "Z"), "end": end.isoformat().replace("+00:00", "Z"),
          "metrics": {key: {"energy_active_import_register": imp, "energy_active_export_register": exp,
                            "energy_balance": None if imp is None else imp - exp}}}
    if cost is not None:
        fr["metrics"]["cost"] = cost
    if live:
        fr["is_live"] = True
    return fr


def test_url_is_utc_and_encoded():
    u = pa.url("meter_values,cost", "hour", datetime(2026, 10, 31, 23, tzinfo=UTC), NOW)
    assert u.startswith(pa.API_URL + "?metrics=meter_values%2Ccost&resolution=hour")
    assert "window_start=2026-10-31T23%3A00%3A00Z" in u and "window_end=2026-11-05T15%3A07%3A00Z" in u


def test_parse_frames_both_metric_spellings_and_cost():
    h = datetime(2026, 11, 5, 10, tzinfo=UTC)
    data = {"frames": [
        _frame(h, cost={"energy_cost_net": 0.31, "service_cost_net": 0.033, "vat": "0.08", "note": "x"}),
        _frame(h + timedelta(hours=1), key="meter_values", imp=None),
        {"start": "bad"},
    ], "summary": {}}
    fr = pa.parse_frames(data)
    assert len(fr) == 2
    assert fr[0]["imp"] == 0.3 and fr[0]["cost"] == {"energy_cost_net": 0.31, "service_cost_net": 0.033, "vat": 0.08}
    assert fr[1]["imp"] is None
    assert pa.parse_frames({"error": "x"}) == [] and pa.parse_frames(None) == []


def test_merge_hours_skips_empty_frames_and_reports_earliest_change():
    h = datetime(2026, 11, 5, 10, tzinfo=UTC)
    stored = {}
    frames = pa.parse_frames({"frames": [_frame(h), _frame(h + timedelta(hours=1), imp=None),
                                         _frame(h + timedelta(hours=2), live=True, imp=0.1)]})
    first, changed = pa.merge_hours(stored, frames, NOW)
    assert changed and first == int(h.timestamp()) // 3600 and len(stored) == 2
    assert pa.merge_hours(stored, frames, NOW) == (None, False)  # same data → no rewrite
    # the running hour completes
    done = pa.parse_frames({"frames": [_frame(h + timedelta(hours=2), imp=0.32)]})
    first2, _ = pa.merge_hours(stored, done, NOW)
    assert first2 == first + 2 and stored[first2]["live"] is False and stored[first2]["i"] == 0.32


def test_statistic_rows_cumulative_without_the_running_hour():
    h = datetime(2026, 11, 5, 10, tzinfo=UTC)
    stored = {}
    pa.merge_hours(stored, pa.parse_frames({"frames": [
        _frame(h, imp=0.3), _frame(h + timedelta(hours=1), imp=0.5), _frame(h + timedelta(hours=2), imp=0.2, live=True)]}), NOW)
    rows = pa.statistic_rows(stored, "i", None)
    assert [(r[1], r[2]) for r in rows] == [(0.3, 0.3), (0.5, 0.8)]
    k1 = int((h + timedelta(hours=1)).timestamp()) // 3600
    assert [(r[1], r[2]) for r in pa.statistic_rows(stored, "i", k1)] == [(0.5, 0.8)]


def test_live_power_from_last_complete_minute():
    m = datetime(2026, 11, 5, 15, 0, tzinfo=UTC)
    frames = pa.parse_frames({"frames": [
        _frame(m, minutes=1, imp=0.005), _frame(m + timedelta(minutes=1), minutes=1, imp=0.006),
        _frame(m + timedelta(minutes=2), minutes=1, imp=0.001, live=True)]})
    w, at = pa.live_power(frames)
    assert w == 360.0 and at == m + timedelta(minutes=1)
    assert pa.last_data(frames) == m + timedelta(minutes=2)
    assert pa.live_power([]) == (None, None)


def test_period_sums_pstryk_cost():
    h = datetime(2026, 11, 1, 0, tzinfo=UTC)
    cost = {"energy_cost_net": 0.2, "service_cost_net": 0.03, "var_dist_cost_net": 0.05,
            "fix_dist_cost_net": 0.1, "power_fee_cost_net": 0.03, "vat": 0.0943, "energy_import_cost": 0.5043}
    stored = {}
    pa.merge_hours(stored, pa.parse_frames({"frames": [_frame(h + timedelta(hours=i), cost=cost) for i in range(10)]}), NOW)
    p = pa.period(stored, h, h + timedelta(hours=5))
    assert p["hours"] == 5 and p["priced_hours"] == 5 and abs(p["kwh"] - 1.5) < 1e-9
    assert p["net"] == 2.05 and p["gross"] == 2.52
    empty = pa.period({}, h, h + timedelta(days=1))
    assert empty["net"] is None and empty["gross"] is None


def test_retry_after():
    assert pa.retry_after("120") == 120 and pa.retry_after("5") == 60 and pa.retry_after(None) == pa.DEFAULT_RETRY_S


def test_period_without_vat_field_adds_23_percent_and_excise():
    # live shape (2026-11-05): no vat / energy_import_cost, excise under "excise"
    h = datetime(2026, 11, 5, 10, tzinfo=UTC)
    cost = {"energy_cost_net": 0.475, "service_cost_net": 0.0533, "var_dist_cost_net": 0.1167,
            "power_fee_cost_net": 0, "excise": 0.0017, "energy_sold_value": 0}
    stored = {}
    pa.merge_hours(stored, pa.parse_frames({"frames": [_frame(h + timedelta(hours=i), cost=cost) for i in range(6)]}), NOW)
    p = pa.period(stored, h, h + timedelta(hours=6))
    parts = 6 * (0.475 + 0.0533 + 0.1167)
    assert abs(p["net"] - round(parts + 6 * 0.0017, 2)) < 0.006
    assert abs(p["gross"] - round(parts * 1.23 + 6 * 0.0017, 2)) < 0.006


def test_hour_gross_and_cost_rows_with_fallback():
    h = datetime(2026, 11, 5, 10, tzinfo=UTC)
    cost = {"energy_cost_net": 0.4, "service_cost_net": 0.05, "var_dist_cost_net": 0.1, "excise": 0.002}
    stored = {}
    pa.merge_hours(stored, pa.parse_frames({"frames": [
        _frame(h, cost=cost), _frame(h + timedelta(hours=1), imp=0.5)]}), NOW)
    k0 = int(h.timestamp()) // 3600
    assert abs(pa.hour_gross(stored[k0]) - (0.55 * 1.23 + 0.002)) < 1e-9
    assert pa.hour_gross(stored[k0 + 1]) is None
    fn = lambda k, r: pa.hour_gross(r) if pa.hour_gross(r) is not None else r["i"] * 2.0  # noqa: E731
    rows = pa.statistic_rows(stored, fn, None)
    assert abs(rows[0][1] - 0.6785) < 1e-4 and abs(rows[1][2] - (0.6785 + 1.0)) < 1e-3
