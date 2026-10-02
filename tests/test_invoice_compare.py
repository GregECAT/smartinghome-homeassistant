"""Invoice vs HA: the August 2026 home invoice (G13, Bobrek) rebuilt from hourly data.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
mt = importlib.import_module("smartinghome_pure.meter_tariffs")
ic = importlib.import_module("smartinghome_pure.invoice_compare")

PRICES = {
    "energy": {"morning": 0.4246, "afternoon": 0.7047, "off_peak": 0.3834},
    "dist": {"morning": 0.2203, "afternoon": 0.3898, "off_peak": 0.0392},
    "fixed": {"handlowa": 56.50, "abonament": 4.56, "mocowa": 24.05, "stala": 10.86},
}


def _august(zone_kwh: dict[str, float]) -> list[tuple[datetime, float]]:
    """Spread each zone's kWh evenly over that zone's hours in August 2026."""
    hours: dict[str, list[datetime]] = {"morning": [], "afternoon": [], "off_peak": []}
    t = datetime(2026, 8, 1)
    while t.month == 8:
        hours[mt.zone_at("G13", t)].append(t)
        t += timedelta(hours=1)
    return [(h, zone_kwh[z] / len(hs)) for z, hs in hours.items() for h in hs]


def test_august_invoice_rebuilt():
    tariff = mt.resolve("G13", PRICES)
    imports = _august({"morning": 64.0, "afternoon": 50.0, "off_peak": 430.0})
    exports = [(datetime(2026, 8, 10, 12), 147.0)]
    bills = ic.monthly_bills(imports, exports, tariff, 11, {"2026-08": 43.90}, datetime(2026, 10, 2, 12))
    b = bills[0]
    assert b["month"] == "2026-08" and not b["partial"]
    # Invoice: Sprzedaż 349,05 · Dystrybucja 139,71 · razem 488,76 · do zapłaty 444,86
    assert abs(b["sale_gross"] - 349.05) < 0.5
    assert abs(b["dist_gross"] - 139.71) < 0.5
    assert abs(b["to_pay"] - 444.86) < 1.0
    ic.compare(bills, {"2026-08": {"import_kwh": 544, "sale_gross": 349.05, "to_pay": "444.86"}})
    assert set(b["diff"]) == {"import_kwh", "sale_gross", "to_pay"} and abs(b["diff"]["import_kwh"]) < 0.1
