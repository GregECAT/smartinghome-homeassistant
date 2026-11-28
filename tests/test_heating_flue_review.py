# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
"""Each fire's flue figures (time-weighted mean, 30-minute peak, time over the cleaning limit) and the exchanger
check built on them: the limit before a cleaning is logged, the clean-exchanger baseline after one, two hot fires
in a row = due; logging / undoing a cleaning; who may book it.

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""
from __future__ import annotations

import ast
import importlib
import sys
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hm = importlib.import_module("smartinghome_pure.heating_model")

CFG = {"enabled": True, "buffer_l": 1000, "entities": {"flue_gas": "sensor.flue"}}
H = 3600.0


def _burn(model, t0, profile, step=10.0):
    """profile: [(seconds, flue at the start, flue at the end)] — a linear ramp per part; returns the end time."""
    model.state = "ignition"
    t = t0
    for secs, a, b in profile:
        n = int(secs // step)
        for i in range(n):
            model._track_session(70.0, t, a + (b - a) * i / max(n - 1, 1))
            model.state = "burning"
            t += step
    return t


def _end(model, t):
    model.state = "idle"
    model._track_session(40.0, t, 60.0)


# ── the fire's figures ─────────────────────────────────────────────────────────

def test_flue_figures_mean_peak_and_hot_time():
    m = hm.HeatingModel(CFG)
    t = _burn(m, 1_000_000.0, [(1800, 50, 250), (3600, 300, 300), (1800, 350, 350), (1800, 200, 200)])
    s = m.session
    assert s["flue_peak_c"] == pytest.approx(350, abs=1)           # the hottest 30 minutes, not one reading
    assert s["flue_hot_c"] == 300                                  # the cleaning limit when the fire started
    assert s["flue_hot_s"] / 60 == pytest.approx(90, abs=1)        # 60 min at 300 + 30 min at 350
    # the mean counts only the burning (≥ 100 °C): the cold first minutes of the ramp are left out
    burn_min = s["flue_burn_s"] / 60
    assert burn_min == pytest.approx(120 + 30 * 150 / 200, abs=1)   # 22.5 min of the ramp + 2 h
    # (175 °C × 22.5 + 300 × 60 + 350 × 30 + 200 × 30) / 142.5 min
    assert s["flue_avg_c"] == pytest.approx(270, abs=1)
    _end(m, t)
    done = m.sessions[-1]
    assert done["flue_peak_c"] == s["flue_peak_c"] and done["flue_avg_c"] == s["flue_avg_c"]


def test_a_single_spike_is_no_peak():
    m = hm.HeatingModel(CFG)
    _burn(m, 1_000_000.0, [(3600, 250, 250), (20, 450, 450), (1800, 250, 250)])
    assert m.session["flue_max_c"] == 450
    assert m.session["flue_peak_c"] < 260                         # a 20-second jump moves the 30-min mean by ~2 K


def test_a_gap_in_the_readings_is_not_counted():
    m = hm.HeatingModel(CFG)
    t = _burn(m, 1_000_000.0, [(1800, 300, 300)])
    t += 1800                                                     # HA down for 30 min
    _burn(m, t, [(1800, 300, 300)])
    assert m.session["flue_burn_s"] / 60 == pytest.approx(60, abs=1)


def test_no_flue_sensor_no_figures():
    m = hm.HeatingModel({"enabled": True})
    m.state = "ignition"
    for i in range(400):
        m._track_session(70.0, 1_000_000.0 + i * 10, None)
        m.state = "burning"
    assert "flue_avg_c" not in m.session and "flue_peak_c" not in m.session


# ── the exchanger check ────────────────────────────────────────────────────────

def _fire(start, peak, burn_h=6.0):
    return {"start": start, "end": start + burn_h * H, "flue_peak_c": peak, "flue_avg_c": peak - 60,
            "flue_burn_s": burn_h * H, "flue_hot_s": 1800.0, "flue_hot_c": 300.0}


def _cfg(**kw):
    return hm.normalize({**CFG, **kw})


def test_before_a_cleaning_the_limit_from_the_settings():
    cfg = _cfg()
    r = hm.flue_review([_fire(0, 280), _fire(86400, 320), _fire(2 * 86400, 335)], [], cfg)
    assert r["basis"] == "limit" and r["limit_c"] == 300
    assert r["due"] and r["peaks"] == [320, 335]
    r = hm.flue_review([_fire(0, 320), _fire(86400, 335), _fire(2 * 86400, 290)], [], cfg)
    assert not r["due"]                                           # the last fire was below the limit


def test_short_fires_do_not_count():
    cfg = _cfg()
    fires = [_fire(0, 330), _fire(86400, 340), _fire(2 * 86400, 150, burn_h=0.5)]
    r = hm.flue_review(fires, [], cfg)
    assert r["due"]                                               # the short burn-out is skipped, not "below"
    assert len(r["recent"]) == 2


def test_after_a_cleaning_learning_then_baseline_then_due():
    cfg = _cfg(flue_rise_k=30)
    clean = 10 * 86400.0
    old = [_fire(0, 340), _fire(86400, 345)]
    r = hm.flue_review(old + [_fire(clean + 3600, 250)], [clean], cfg)
    assert r["basis"] == "learning" and r["limit_c"] is None and not r["due"]
    assert r["baseline_n"] == 1 and r["fires_since"] == 1

    after = [_fire(clean + 3600, 250), _fire(clean + 86400, 260)]
    r = hm.flue_review(old + after, [clean], cfg)
    assert r["basis"] == "baseline" and r["baseline_c"] == 255 and r["limit_c"] == 285
    assert not r["due"] and not r["high_after_clean"]
    assert [x["baseline"] for x in r["recent"][-2:]] == [True, True]

    r = hm.flue_review(old + after + [_fire(clean + 2 * 86400, 290), _fire(clean + 3 * 86400, 280)], [clean], cfg)
    assert not r["due"]
    r = hm.flue_review(old + after + [_fire(clean + 2 * 86400, 290), _fire(clean + 3 * 86400, 300)], [clean], cfg)
    assert r["due"] and r["peaks"] == [290, 300]


def test_still_hot_after_a_cleaning():
    cfg = _cfg()
    clean = 86400.0
    r = hm.flue_review([_fire(clean + 3600, 325), _fire(clean + 86400, 335)], [clean], cfg)
    assert r["baseline_c"] == 330 and r["high_after_clean"]


# ── logging a cleaning ─────────────────────────────────────────────────────────

def test_log_and_undo_a_cleaning_survives_a_restart():
    m = hm.HeatingModel(CFG)
    m.flue_notified = 5.0
    m.log_clean(1000.0)
    assert m.cleanings == [1000.0] and m.flue_notified is None
    m.flue_notified = 2000.0
    copy = hm.HeatingModel(CFG)
    copy.restore(m.dump())
    assert copy.cleanings == [1000.0] and copy.flue_notified == 2000.0
    assert m.undo_clean(1000.0 + 49 * H) is None                  # too late to take it back
    assert m.undo_clean(1000.0 + 47 * H) == 1000.0 and m.cleanings == []
    assert m.undo_clean(1000.0 + 47 * H) is None


def test_settings_defaults_and_limits():
    c = hm.normalize({})
    assert c["flue_clean_notify"] is True and c["flue_clean_c"] == 300 and c["flue_rise_k"] == 30
    c = hm.normalize({"flue_clean_notify": False, "flue_clean_c": 900, "flue_rise_k": 1})
    assert c["flue_clean_notify"] is False and c["flue_clean_c"] == 500 and c["flue_rise_k"] == 10


def test_any_household_member_may_book_a_cleaning():
    src = (_DIR / "heating_ha.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_household_command")
    ns = {"hm": hm, "Any": object}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "heating_ha.py", "exec"), ns)  # noqa: S102
    allowed = ns["_household_command"]
    assert allowed({}, {"action": "log_clean"}) and allowed({}, {"action": "undo_clean"})
    assert not allowed({}, {"action": "valve_home"})
