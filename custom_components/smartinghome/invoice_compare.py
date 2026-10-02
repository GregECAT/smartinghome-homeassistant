"""Invoice vs Home Assistant — the monthly bill rebuilt from hourly meter data.

Pure Python. Builds the lines of a Tauron home invoice ("Sprzedaż energii",
"Dystrybucja energii", deposit, "Do zapłaty") from eLicznik hourly energy and
the tariff prices, and compares them with the figures typed in from the
invoice. Seller fees (energy + opłata handlowa) go to "Sprzedaż"; network fees
(variable distribution, per-kWh fees, abonament, mocowa, stała, contracted
power) to "Dystrybucja".
"""
from __future__ import annotations

from calendar import monthrange
from datetime import datetime
from typing import Any

from . import meter_tariffs as mt

SALE_FIXED = {"handlowa"}
FIELDS = ("import_kwh", "export_kwh", "sale_gross", "dist_gross", "total_gross", "deposit", "to_pay")


def _month(t: datetime) -> str:
    return f"{t.year:04d}-{t.month:02d}"


def monthly_bills(
    imports: list[tuple[datetime, float]],
    exports: list[tuple[datetime, float]],
    tariff: dict[str, Any],
    contract_kw: float | None,
    deposit_used: dict[str, float],
    now: datetime,
    meter_kwh: dict[str, dict[str, float]] | None = None,
) -> list[dict[str, Any]]:
    vat = mt.VAT if tariff.get("kind") == "home" else 1.0
    fixed = mt.monthly_fixed(tariff, contract_kw)
    by_month: dict[str, list[tuple[datetime, float]]] = {}
    for t, k in imports:
        by_month.setdefault(_month(t), []).append((t, k))
    exp: dict[str, float] = {}
    for t, k in exports:
        exp[_month(t)] = exp.get(_month(t), 0.0) + k
    current = _month(now)
    out = []
    for month in sorted(by_month):
        rows = by_month[month]
        if sum(k for _, k in rows) <= 0:
            continue
        y, m = int(month[:4]), int(month[5:])
        days = monthrange(y, m)[1]
        days_with_data = len({t.date() for t, _ in rows})
        share = 1.0
        if month == current:
            share = now.day / days
        elif days_with_data < days - 1:
            share = days_with_data / days
        c = mt.cost_hours(tariff, rows)
        sale_fixed = sum(v for k, v in fixed.items() if k in SALE_FIXED) * share
        dist_fixed = sum(v for k, v in fixed.items() if k not in SALE_FIXED) * share
        sale = (c["energy"] + sale_fixed) * vat
        dist = (c["dist"] + dist_fixed) * vat
        used = float(deposit_used.get(month, 0.0))
        bill = {
            "month": month,
            "partial": month == current or share < 1.0,
            "import_kwh": round(c["kwh"], 1),
            "zones_kwh": {z: round(v["kwh"], 1) for z, v in c["zones"].items()},
            "export_kwh": round(exp.get(month, 0.0), 1),
            "energy_net": round(c["energy"], 2),
            "dist_var_net": round(c["dist"], 2),
            "sale_gross": round(sale, 2),
            "dist_gross": round(dist, 2),
            "total_gross": round(sale + dist, 2),
            "deposit": round(used, 2),
            "to_pay": round(sale + dist - used, 2),
        }
        if meter_kwh and month in meter_kwh:
            bill["inverter_meter"] = {k: round(v, 1) for k, v in meter_kwh[month].items()}
        out.append(bill)
    return out


def compare(bills: list[dict[str, Any]], entries: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Attach the invoice figures and the differences (HA − invoice) per field."""
    entries = entries or {}
    for bill in bills:
        inv = entries.get(bill["month"])
        if not isinstance(inv, dict):
            continue
        clean = {}
        for f in FIELDS:
            try:
                clean[f] = float(inv[f])
            except (KeyError, TypeError, ValueError):
                continue
        if not clean:
            continue
        bill["invoice"] = clean
        bill["diff"] = {f: round(bill[f] - v, 2) for f, v in clean.items() if f in bill}
    return bills
