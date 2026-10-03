"""PV forecast confidence learned from the installation's history."""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome" / "pv_calibration.py"
_spec = importlib.util.spec_from_file_location("pv_calibration", _p)
pc = importlib.util.module_from_spec(_spec)
sys.modules["pv_calibration"] = pc
_spec.loader.exec_module(pc)


def _days(pairs):
    d0 = date(2026, 9, 1)
    return [(d0 + timedelta(days=i), f, a) for i, (f, a) in enumerate(pairs)]


def test_too_few_days_uses_the_configured_confidence():
    model = pc.learn(_days([(40, 38), (35, 30)]))
    assert pc.confidence(model, 40, 0.7) == (0.7, "default")


def test_sunny_days_trusted_more_than_cloudy():
    sunny = [(40, 39), (42, 40), (41, 37), (39, 38), (43, 41), (40, 36)]
    cloudy = [(8, 3), (9, 5), (7, 2.5), (10, 4), (8, 6), (6, 2)]
    model = pc.learn(_days(sunny + cloudy))
    c_sun, k_sun = pc.confidence(model, 41, 0.7)
    c_cld, k_cld = pc.confidence(model, 8, 0.7)
    assert (k_sun, k_cld) == ("sunny", "cloudy")
    assert c_sun > 0.85 and c_cld < 0.5


def test_broken_rows_are_ignored():
    good = [(40, 38), (30, 25), (35, 33), (20, 15), (38, 37), (25, 22), (33, 30)]
    frozen = [(26, 178.7), (26, 178.7)]
    model = pc.learn(_days(good + frozen + [(0.2, 5)]))
    assert model["samples"] == len(good)


def test_daily_rows_join_morning_forecast_and_pv():
    d = date(2026, 10, 1)
    fc = [(d, 6, 30.0), (d, 7, 31.7), (d, 8, 33.0)]
    pv = [(d, h, 1000.0 if 8 <= h < 16 else 0.0) for h in range(24)]
    assert pc.daily_rows(fc, pv, date(2026, 10, 3)) == [(d, 31.7, 8.0)]
