"""Voltage guard: battery first, then the export limit; released when the voltage stays low.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
vg = importlib.import_module("smartinghome_pure.voltage_guard")
vr = importlib.import_module("smartinghome_pure.voltage_report")


def _run(guard, seconds, v, export_w, soc=100.0, battery_w=0.0, pv_w=6000.0, t0=0.0):
    actions = []
    t = t0
    while t < t0 + seconds:
        inp = vg.VoltageInput(v=(v, v - 1, v - 2), export_w=export_w, pv_w=pv_w, soc=soc, battery_w=battery_w)
        actions += [(t, a, val) for a, val, _ in guard.decide(inp, now=t)]
        guard.track_exceedance(inp, now=t)
        t += 30
    return actions, t


def test_no_action_below_target():
    g = vg.VoltageGuard()
    actions, _ = _run(g, 1800, 250.0, 4000)
    assert actions == []
    assert g.cap_w is None


def test_battery_first_then_export_limit_steps():
    g = vg.VoltageGuard()
    actions, _ = _run(g, 900, 252.5, 4000, soc=80.0)
    kinds = [a for _, a, _ in actions]
    assert kinds[0] == "charge"
    assert "cap" in kinds
    caps = [val for _, a, val in actions if a == "cap"]
    assert caps[0] == 3000 and caps == sorted(caps, reverse=True)
    # at most one limit change every 3 minutes
    times = [t for t, a, _ in actions if a == "cap"]
    assert all(b - a >= vg.CHANGE_EVERY_S for a, b in zip(times, times[1:]))


def test_limit_released_after_quiet_period_and_at_dusk():
    g = vg.VoltageGuard()
    _, t = _run(g, 600, 253.0, 5000)
    assert g.cap_w is not None
    actions, t = _run(g, 2400, 248.0, 2000, t0=t)
    assert any(a in ("cap", "release") for _, a, _ in actions)
    g2 = vg.VoltageGuard()
    _, t = _run(g2, 600, 253.0, 5000)
    actions, _ = _run(g2, 60, 240.0, 0, pv_w=0, t0=t)
    assert ("release" in [a for _, a, _ in actions]) and g2.cap_w is None


def test_exceedance_logged_once():
    g = vg.VoltageGuard()
    _, t = _run(g, 1200, 254.0, 5000)
    done = None
    t_end = t + 600
    while t < t_end and done is None:
        inp = vg.VoltageInput(v=(240.0, 240.0, 240.0), export_w=0, pv_w=3000, soc=100, battery_w=0)
        g.decide(inp, now=t)
        done = g.track_exceedance(inp, now=t)
        t += 30
    assert done is not None and done["max_mean"] > 253 and "L1" in done["phases"]


def test_disabled_releases_cap():
    g = vg.VoltageGuard()
    _run(g, 600, 253.0, 5000)
    g.configure({"enabled": False})
    out = g.decide(vg.VoltageInput(v=(253.0,) * 3, export_w=5000, pv_w=6000, soc=100, battery_w=0), now=10_000)
    assert out and out[0][0] == "release" and g.cap_w is None


def test_report_counts_and_no_export_hours():
    from datetime import datetime, timedelta
    t0 = datetime(2026, 10, 1, 0, 0)
    hourly = {"L1": [(t0 + timedelta(hours=h), 254.0 if h == 13 else 245.0, 255.0 if h == 13 else 247.0)
                     for h in range(24)]}
    grid = {t0 + timedelta(hours=h): (-500.0 if h == 13 else 1000.0) for h in range(24)}
    five = {"L1": [(t0 + timedelta(minutes=5 * i), 254.0 if 156 <= i < 160 else 245.0) for i in range(288)]}
    r = vr.build_report(hourly, five, grid, 253.0)
    d = r["days"][0]
    assert d["hours_max_over"] == 1 and d["hours_mean_over"] == 1
    assert d["hours_high_no_export"] == 1
    assert d["ten_min_over"] == 2
    assert r["summary"]["days_over"] == 1
