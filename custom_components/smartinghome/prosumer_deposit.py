"""Prosumer deposit (net-billing) — balance, expiry and what an exported kWh is worth.

Pure Python (no Home Assistant imports).

Rules (Tauron "model netbilling-godzinowy – znowelizowany", ustawa o OZE):
- energy fed into the grid in each hour is valued at the market price RCE of
  that hour (a negative price counts as 0); the month's sum × 1.23 is the
  deposit, credited to the account in the next month;
- the deposit pays only for the ENERGY part of later bills (not distribution,
  not fixed fees), oldest first;
- it is valid for 12 months from crediting; what is left then can be refunded
  up to 30 % of the value of the energy fed in that month, the rest is lost.

So an exported kWh is worth RCE × 1.23 only if the deposit it creates will be
used; when the deposit already exceeds the energy bills of the coming year,
the extra kWh is worth at most the 30 % refund.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

COEFFICIENT = 1.23
VALIDITY_MONTHS = 12
REFUND_SHARE = 0.30


def month_key(d: date | datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(key: str, n: int) -> str:
    y, m = int(key[:4]), int(key[5:7]) - 1 + n
    return f"{y + m // 12:04d}-{m % 12 + 1:02d}"


def hourly_rce(quarters: list[tuple[datetime, float]]) -> dict[datetime, float]:
    """PSE 15-minute RCE (local period END, zł/MWh) → {local hour start: mean zł/MWh}."""
    buckets: dict[datetime, list[float]] = {}
    for end, price in quarters:
        start = (end - timedelta(minutes=15)).replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(start, []).append(price)
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def monthly_flows(
    exports: list[tuple[datetime, float]],
    imports: list[tuple[datetime, float]],
    rce: dict[datetime, float],
    energy_price_gross: Callable[[datetime], float],
) -> dict[str, dict[str, float]]:
    """Per month: exported kWh and its deposit value, imported kWh and its energy charge (gross)."""
    out: dict[str, dict[str, float]] = {}

    def row(t: datetime) -> dict[str, float]:
        return out.setdefault(month_key(t), {
            "export_kwh": 0.0, "deposit": 0.0, "import_kwh": 0.0, "energy_charge": 0.0,
            "export_unpriced_kwh": 0.0,
        })

    for t, kwh in exports:
        r = row(t)
        r["export_kwh"] += kwh
        price = rce.get(t)
        if price is None:
            r["export_unpriced_kwh"] += kwh
            continue
        r["deposit"] += kwh * max(price, 0.0) / 1000 * COEFFICIENT
    for t, kwh in imports:
        r = row(t)
        r["import_kwh"] += kwh
        r["energy_charge"] += kwh * energy_price_gross(t)
    for r in out.values():
        for k in r:
            r[k] = round(r[k], 3 if k.endswith("kwh") else 2)
    return out


def _run(
    flows: dict[str, dict[str, float]], first_month: str, until: str,
    opening: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Deposit account month by month up to `until` → (credits, month rows)."""
    credits: list[dict[str, Any]] = []
    for o in opening or []:
        try:
            amt = float(o.get("amount") or 0)
            credited = str(o.get("month"))[:7]
        except (TypeError, ValueError, AttributeError):
            continue
        if amt > 0 and len(credited) == 7:
            credits.append({"earned": add_months(credited, -1), "credited": credited,
                            "expires": add_months(credited, VALIDITY_MONTHS - 1),
                            "amount": round(amt, 2), "left": amt, "opening": True})
    rows: list[dict[str, Any]] = []
    month = min([first_month] + [c["credited"] for c in credits])
    while month <= until:
        f = flows.get(month, {}) if month >= first_month else {}
        # 1. Deposits past their 12 months: refund ≤ 30 % of their value, the rest is lost
        for c in credits:
            if c["expires"] < month and c["left"] > 0.005 and "lost" not in c:
                refund = min(c["left"], c["amount"] * REFUND_SHARE)
                c["refund"], c["lost"], c["left"] = round(refund, 2), round(c["left"] - refund, 2), 0.0
        # 2. The month's energy bill uses credited, valid deposits — oldest first
        charge = float(f.get("energy_charge") or 0.0)
        used = 0.0
        for c in sorted(credits, key=lambda c: c["credited"]):
            if charge - used <= 0.005:
                break
            if c["credited"] <= month <= c["expires"] and c["left"] > 0:
                take = min(c["left"], charge - used)
                c["left"] -= take
                used += take
        # 3. The deposit earned this month is credited next month
        earned = float(f.get("deposit") or 0.0)
        if earned > 0:
            credits.append({"earned": month, "credited": add_months(month, 1),
                            "expires": add_months(month, VALIDITY_MONTHS),
                            "amount": round(earned, 2), "left": earned})
        if month >= first_month:
            rows.append({
                "month": month,
                "export_kwh": f.get("export_kwh", 0.0), "deposit": round(earned, 2),
                "import_kwh": f.get("import_kwh", 0.0), "energy_charge": round(charge, 2),
                "deposit_used": round(used, 2), "to_pay_energy": round(charge - used, 2),
                "balance_after": round(sum(c["left"] for c in credits), 2),
                "unpriced_kwh": f.get("export_unpriced_kwh", 0.0),
            })
        month = add_months(month, 1)
    return credits, rows


def settle(
    flows: dict[str, dict[str, float]],
    *,
    first_month: str,
    current_month: str,
    opening: list[dict[str, Any]] | None = None,
    projection: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Deposit balance today, what expires, and what an exported kWh is worth now.

    flows: actual months (deposit earned, energy charge billed); months before
    first_month (start of hourly net-billing) are ignored. projection: expected
    flows after current_month — tells whether today's deposits get used before
    they expire. opening: deposits from before the tracked period,
    [{"month": "YYYY-MM" (credited), "amount": zł}].
    """
    credits, rows = _run(flows, first_month, current_month, opening)
    future, _ = _run({**flows, **(projection or {})}, first_month,
                     add_months(current_month, VALIDITY_MONTHS + 1), opening)
    fut = {(c["earned"], c["credited"]): c for c in future}
    open_credits = []
    for c in credits:
        if c["left"] <= 0.005:
            continue
        f = fut.get((c["earned"], c["credited"]), {})
        open_credits.append({
            "earned": c["earned"], "credited": c["credited"], "expires": c["expires"],
            "amount": c["amount"], "left_now": round(c["left"], 2),
            "accruing": c["earned"] == current_month,
            "projected_lost": f.get("lost", 0.0), "projected_refund": f.get("refund", 0.0),
        })
    cur = fut.get((current_month, add_months(current_month, 1)))
    if cur is not None and cur.get("lost", 0.0) > 0.005:
        # The deposit earned now won't all be used — an extra kWh is worth the refund at most
        export_value_factor = REFUND_SHARE if cur.get("refund", 0.0) < cur["amount"] * REFUND_SHARE - 0.005 else 0.0
    else:
        export_value_factor = 1.0
    return {
        "months": rows,
        "credits": open_credits,
        "balance_now": round(sum(c["left_now"] for c in open_credits if not c["accruing"]), 2),
        "accruing_now": round(sum(c["left_now"] for c in open_credits if c["accruing"]), 2),
        "expiring_3m": round(sum(c["left_now"] for c in open_credits
                                 if c["expires"] <= add_months(current_month, 2)), 2),
        "lost_total": round(sum(c.get("lost", 0.0) for c in credits), 2),
        "refund_total": round(sum(c.get("refund", 0.0) for c in credits), 2),
        "projected_lost": round(sum(c["projected_lost"] for c in open_credits), 2),
        "export_value_factor": export_value_factor,
    }


def project(
    flows: dict[str, dict[str, float]], current_month: str, months: int = VALIDITY_MONTHS + 1,
) -> dict[str, dict[str, float]]:
    """Expected flows of the coming months: the same calendar month of the latest year with data."""
    by_cal: dict[str, dict[str, float]] = {}
    for key in sorted(flows):
        if key < current_month and (flows[key].get("import_kwh") or flows[key].get("export_kwh")):
            by_cal[key[5:7]] = flows[key]
    if not by_cal:
        return {}
    avg = {k: sum(f.get(k, 0.0) for f in by_cal.values()) / len(by_cal)
           for k in ("deposit", "energy_charge", "export_kwh", "import_kwh")}
    out = {}
    for i in range(1, months + 1):
        key = add_months(current_month, i)
        src = by_cal.get(key[5:7], avg)
        out[key] = {k: src.get(k, 0.0) for k in ("deposit", "energy_charge", "export_kwh", "import_kwh")}
    return out


def reconcile(months: list[dict[str, Any]], invoices: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Compare computed deposits with the ones on invoices {"YYYY-MM" (earned): zł}."""
    out = []
    by_month = {m["month"]: m for m in months}
    for key, value in sorted((invoices or {}).items()):
        try:
            inv = float(value)
        except (TypeError, ValueError):
            continue
        calc = by_month.get(str(key)[:7], {}).get("deposit")
        out.append({"month": str(key)[:7], "invoice": round(inv, 2),
                    "computed": calc, "diff": round(calc - inv, 2) if calc is not None else None})
    return out
