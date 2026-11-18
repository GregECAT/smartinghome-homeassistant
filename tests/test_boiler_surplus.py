"""W4b boiler on PV surplus: the hot-water limit from the heating module's tank probe.

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
bs = importlib.import_module("smartinghome_pure.boiler_surplus")


def _surplus(state="off", water_c=None, **kw):
    base = dict(hour=12, soc=100.0, export_w=4000.0, import_w=0.0, battery_w=0.0,
                max_voltage=240.0, state=state, water_c=water_c)
    base.update(kw)
    return bs.BoilerInput(**base)


def _run(boiler, inp, seconds, t0=0.0, step=30):
    """Feed the same input; stop at the first action (the relay state changes after it)."""
    out = []
    t = t0
    while t < t0 + seconds:
        d = boiler.decide(inp, now=t)
        t += step
        if d:
            out.append((t - step, d))
            break
    return out, t


def _boiler(limit=None):
    b = bs.BoilerSurplus()
    b.configure({"enabled": True, "entity": "switch.bojler", "water_max_c": limit})
    return b


def test_unknown_water_temperature_behaves_as_before():
    b = _boiler(60)
    actions, _ = _run(b, _surplus(water_c=None), 400, t0=1000)
    assert [d[0] for _, d in actions] == ["turn_on"]
    assert b.owned and not b.water_hot


def test_no_limit_configured_ignores_water_temperature():
    for limit in (None, 0):
        b = _boiler(limit)
        actions, _ = _run(b, _surplus(water_c=75.0), 400, t0=1000)
        assert [d[0] for _, d in actions] == ["turn_on"], limit


def test_hot_tank_is_not_switched_on():
    b = _boiler(60)
    actions, _ = _run(b, _surplus(water_c=60.0), 1200, t0=1000)
    assert actions == []
    assert b.water_hot and not b.can_switch_on(now=5000)
    # voltage path (high export voltage) doesn't switch it on either
    actions, _ = _run(b, _surplus(water_c=62.0, max_voltage=254.0), 600, t0=3000)
    assert actions == []


def test_cooler_tank_is_switched_on():
    b = _boiler(60)
    actions, _ = _run(b, _surplus(water_c=48.5), 400, t0=1000)
    assert [d[0] for _, d in actions] == ["turn_on"]


def test_owned_boiler_switched_off_when_water_reaches_limit():
    b = _boiler(60)
    _, t = _run(b, _surplus(water_c=50.0), 400, t0=1000)
    assert b.owned
    # still within the minimum on time and the off hold: nothing yet
    actions, t2 = _run(b, _surplus(state="on", water_c=61.0), 60, t0=t)
    assert actions == []
    actions, _ = _run(b, _surplus(state="on", water_c=61.0), 600, t0=t2)
    assert len(actions) == 1
    when, (service, reason) = actions[0]
    assert service == "turn_off" and "61 °C" in reason
    assert when - b._since.get("off", when) >= 0 and not b.owned


def test_manual_boiler_left_alone_when_water_hot():
    b = _boiler(60)
    actions, _ = _run(b, _surplus(state="on", water_c=70.0), 900, t0=1000)
    assert actions == []   # we didn't switch it on — not ours to switch off


def test_status_reports_water_hot():
    b = _boiler(55)
    b.decide(_surplus(water_c=56.0), now=10)
    st = b.status()
    assert st["water_hot"] is True and st["water_max_c"] == 55
