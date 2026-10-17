"""Energia i koszty with an independent meter: export / balance, tg φ, settings.

Prosumer without an inverter integration (micro-inverter + Shelly Pro 3EM) and a
business on C13 with a meter only (reactive energy).

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"


def _stub(name: str, **attrs) -> None:
    mod = sys.modules.get(name) or types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


_passthrough = lambda *a, **k: (lambda f: f)  # noqa: E731
_stub("voluptuous", Required=lambda *a, **k: a[0], Optional=lambda *a, **k: a[0])
_stub("homeassistant")
_stub("homeassistant.components")
_stub("homeassistant.components.websocket_api", websocket_command=_passthrough,
      async_response=lambda f: f, async_register_command=lambda *a, **k: None)
sys.modules["homeassistant.components"].websocket_api = sys.modules["homeassistant.components.websocket_api"]
_stub("homeassistant.core", HomeAssistant=object, callback=lambda f: f)

_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
mw = importlib.import_module("smartinghome_pure.meter_ws")
md = importlib.import_module("smartinghome_pure.meter_device")

NOW = datetime(2026, 10, 17, 12, 0)  # noqa: DTZ001


def _day(start: datetime, days: int, imp_kwh: float, exp_noon_kwh: float):
    imp, exp = [], []
    for i in range(days * 24):
        t = start + timedelta(hours=i)
        sunny = 10 <= t.hour < 15
        imp.append((t, 0.1 if sunny else imp_kwh))
        exp.append((t, exp_noon_kwh if sunny else 0.0))
    return imp, exp


def test_settings_keep_old_keys_and_add_meter_roles():
    cfg = mw.meter_settings({"meter": {"energy_stat": "tauron_amiplus:x_consumption", "import_stats": "bad"}})
    assert cfg["energy_stat"] == "tauron_amiplus:x_consumption"  # backward compatible: import
    assert cfg["export_stat"] == "" and cfg["voltage_l1"] == "" and cfg["reactive_energy_stat"] == ""
    assert cfg["import_stats"] == [] and cfg["power_sign"] == "import_positive"
    assert cfg["voltage_source"] == "auto" and cfg["tariff"] == "C13"


def test_prosumer_export_and_hourly_balance():
    imp, exp = _day(datetime(2026, 10, 1), 16, 0.5, 1.2)
    out = mw.meter_extras(imp, exp, [], NOW)
    cm = out["exchange"]["current_month"]
    # 16 days × (19 h × 0.5 + 5 h × 0.1) import, 16 × 5 × 1.2 export
    assert cm["import_kwh"] == 160.0 and cm["export_kwh"] == 96.0 and cm["net_kwh"] == 64.0
    assert cm["balanced_import_kwh"] == 152.0 and cm["balanced_export_kwh"] == 88.0
    assert out["exchange"]["previous_month"]["hours"] == 0
    assert "reactive" not in out


def test_reactive_from_kvarh_counter_pairs_import_hours():
    t = datetime(2026, 10, 2, 8)  # noqa: DTZ001
    imp = [(t + timedelta(hours=i), 4.0) for i in range(10)]
    kvarh = [(t + timedelta(hours=i), 2.4) for i in range(8)]  # last two hours missing
    rows, src = mw._reactive_hours({}, imp, kvarh, [], [])
    assert src == "energy" and len(rows) == 8
    r = mw.meter_extras(imp, [], rows, NOW)["reactive"]["current_month"]
    assert r["tan_phi"] == 0.6 and r["hours_over"] == 8


def test_reactive_from_mean_power_keeps_capacitive_sign():
    t = datetime(2026, 10, 3, 1)  # noqa: DTZ001
    p_mean = [(t, 800.0), (t + timedelta(hours=1), 900.0)]        # W, import positive (Shelly / Eastron)
    q_mean = [(t, -600.0), (t + timedelta(hours=1), 200.0)]       # var, < 0 capacitive
    rows, src = mw._reactive_hours({"power_sign": "import_positive"}, [], [], p_mean, q_mean)
    assert src == "power"
    r = md.tan_phi_period(rows)
    assert r["q_cap_kvarh"] == 0.6 and r["q_ind_kvarh"] == 0.2 and r["cap_hours"] == 1
    # A meter that reports export as positive power
    rows, _ = mw._reactive_hours({"power_sign": "export_positive"}, [], [], [(t, -800.0)], [(t, 100.0)])
    assert rows == [(t, 0.8, 0.1)]


def test_summed_sources_label():
    known = {f"sensor.ch_{c}": {"id": f"sensor.ch_{c}", "name": c, "unit": "kWh"} for c in "abc"}
    src = mw._stat_source(["sensor.ch_a", "sensor.ch_b", "sensor.ch_c"], known, "Shelly 3EM")
    assert src["ids"] == ["sensor.ch_a", "sensor.ch_b", "sensor.ch_c"]
    assert "suma 3" in src["name"]
    assert mw._stat_source(["sensor.missing"], known, "x") is None
