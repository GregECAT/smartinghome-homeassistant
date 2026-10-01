"""Meter-side tariffs for the "Energia i koszty" panel (homes and businesses).

Pure Python (no Home Assistant imports) so it can be unit-tested directly.

Prices are NETTO (zł). The panel shows netto for businesses (VAT is
deductible) and brutto (× 1.23) for homes. Every price is a default that
the user overrides from their own invoice — business energy prices in
particular are contract-specific.

Zone schedules (TAURON Dystrybucja 2026):
- C13 / G13: morning peak 7–13 on working days; afternoon peak 19–22
  (Apr–Sep) or 16–21 (Oct–Mar); every other hour, weekends and public
  holidays are off-peak.
- G12: off-peak 13–15 and 22–06 every day. G12w: G12 + whole weekends.
- C11 / G11: one zone.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

VAT: float = 1.23
PER_KWH_FEES_NETTO: float = 0.0332 + 0.0073 + 0.0030  # jakościowa + OZE + kogeneracyjna

ZONE_LABELS: dict[str, str] = {
    "flat": "Całodobowa",
    "morning": "Szczyt przedpołudniowy",
    "afternoon": "Szczyt popołudniowy",
    "peak": "Strefa dzienna",
    "off_peak": "Pozostałe godziny",
}

# energy / dist: netto zł/kWh per zone. fixed: netto zł/month.
# contract_rate: netto zł per kW of contracted power per month (C tariffs).
# needs_input: defaults are not verified for this tariff — user must enter
#              the prices from the invoice before costs are shown.
METER_TARIFFS: dict[str, dict[str, Any]] = {
    "C11": {
        "label": "C11 — firma, jednostrefowa",
        "kind": "business",
        "zones": ["flat"],
        "energy": {"flat": 0.0},
        "dist": {"flat": 0.0},
        "fixed": {"handlowa": 0.0, "abonament": 0.0, "mocowa": 0.0},
        "contract_rate": 0.0,
        "needs_input": True,
    },
    "C13": {
        # Distribution: TAURON Dystrybucja 2026 (invoice E/TM2/…/0002/26).
        # Energy: example business contract (same invoice) — override it.
        "label": "C13 — firma, trzystrefowa",
        "kind": "business",
        "zones": ["morning", "afternoon", "off_peak"],
        "energy": {"morning": 1.01, "afternoon": 1.25, "off_peak": 0.932},
        "dist": {"morning": 0.2155, "afternoon": 0.3192, "off_peak": 0.1527},
        "fixed": {"handlowa": 35.0, "abonament": 2.28, "mocowa": 10.31},
        "contract_rate": 5.73,
        "needs_input": False,
    },
    "G11": {
        "label": "G11 — dom, jednostrefowa",
        "kind": "home",
        "zones": ["flat"],
        "energy": {"flat": round(0.6175 / VAT, 4)},
        "dist": {"flat": 0.2464},
        "fixed": {"handlowa": 0.0, "abonament": 4.56, "mocowa": 24.05, "stala": 10.86},
        "contract_rate": 0.0,
        "needs_input": False,
    },
    "G12": {
        "label": "G12 — dom, dwustrefowa",
        "kind": "home",
        "zones": ["peak", "off_peak"],
        "energy": {"peak": 0.5447, "off_peak": 0.4146},
        "dist": {"peak": 0.2841, "off_peak": 0.0558},
        "fixed": {"handlowa": 0.0, "abonament": 4.56, "mocowa": 24.05, "stala": 10.86},
        "contract_rate": 0.0,
        "needs_input": False,
    },
    "G12w": {
        "label": "G12w — dom, dwustrefowa + weekendy",
        "kind": "home",
        "zones": ["peak", "off_peak"],
        "energy": {"peak": 0.6220, "off_peak": 0.4130},
        "dist": {"peak": 0.3298, "off_peak": 0.0512},
        "fixed": {"handlowa": 0.0, "abonament": 4.56, "mocowa": 24.05, "stala": 10.86},
        "contract_rate": 0.0,
        "needs_input": False,
    },
    "G13": {
        "label": "G13 — dom, trzystrefowa",
        "kind": "home",
        "zones": ["morning", "afternoon", "off_peak"],
        "energy": {
            "morning": round(0.5803 / VAT, 4),
            "afternoon": round(0.9631 / VAT, 4),
            "off_peak": round(0.5240 / VAT, 4),
        },
        "dist": {"morning": 0.2203, "afternoon": 0.3898, "off_peak": 0.0392},
        "fixed": {"handlowa": 0.0, "abonament": 4.56, "mocowa": 24.05, "stala": 10.86},
        "contract_rate": 0.0,
        "needs_input": False,
    },
}

FIXED_LABELS: dict[str, str] = {
    "handlowa": "Opłata handlowa",
    "abonament": "Opłata abonamentowa",
    "mocowa": "Opłata mocowa",
    "stala": "Opłata stała sieciowa",
}


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month, day = divmod(h + l_ - 7 * m + 114, 31)
    return date(year, month, day + 1)


@lru_cache(maxsize=16)
def polish_holidays(year: int) -> frozenset[date]:
    """Statutory public holidays in Poland (Wigilia is a holiday since 2025)."""
    easter = _easter(year)
    days = {
        date(year, 1, 1), date(year, 1, 6), date(year, 5, 1), date(year, 5, 3),
        date(year, 8, 15), date(year, 11, 1), date(year, 11, 11),
        date(year, 12, 25), date(year, 12, 26),
        easter, easter + timedelta(days=1),
        easter + timedelta(days=49),  # Zielone Świątki
        easter + timedelta(days=60),  # Boże Ciało
    }
    if year >= 2025:
        days.add(date(year, 12, 24))
    return frozenset(days)


def is_free_day(day: date) -> bool:
    return day.weekday() >= 5 or day in polish_holidays(day.year)


def zone_at(tariff: str, when: datetime) -> str:
    """Zone of a tariff for the hour that starts at `when` (local time)."""
    h = when.hour
    if tariff in ("C13", "G13"):
        if is_free_day(when.date()):
            return "off_peak"
        if 7 <= h < 13:
            return "morning"
        summer = 4 <= when.month <= 9
        if (19 <= h < 22) if summer else (16 <= h < 21):
            return "afternoon"
        return "off_peak"
    if tariff in ("G12", "G12w"):
        if tariff == "G12w" and is_free_day(when.date()):
            return "off_peak"
        return "off_peak" if (13 <= h < 15 or h >= 22 or h < 6) else "peak"
    return "flat"


def resolve(tariff: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tariff definition with the user's invoice prices merged over the defaults."""
    base = METER_TARIFFS.get(tariff) or METER_TARIFFS["C11"]
    o = overrides or {}
    out = {
        "id": tariff if tariff in METER_TARIFFS else "C11",
        "label": base["label"],
        "kind": base["kind"],
        "zones": list(base["zones"]),
        "energy": dict(base["energy"]),
        "dist": dict(base["dist"]),
        "fixed": dict(base["fixed"]),
        "contract_rate": base["contract_rate"],
        "per_kwh_fees": PER_KWH_FEES_NETTO,
    }
    for part in ("energy", "dist", "fixed"):
        for key, value in (o.get(part) or {}).items():
            if key in out[part] and _num(value) is not None:
                out[part][key] = _num(value)
    if _num(o.get("contract_rate")) is not None:
        out["contract_rate"] = _num(o["contract_rate"])
    if _num(o.get("per_kwh_fees")) is not None:
        out["per_kwh_fees"] = _num(o["per_kwh_fees"])
    out["needs_input"] = bool(base["needs_input"]) and not any(
        (_num(v) or 0) > 0 for v in (o.get("energy") or {}).values()
    )
    return out


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f >= 0 else None


def variable_price(t: dict[str, Any], zone: str) -> tuple[float, float]:
    """(energy netto, distribution incl. per-kWh fees netto) per kWh in a zone."""
    return t["energy"].get(zone, 0.0), t["dist"].get(zone, 0.0) + t["per_kwh_fees"]


def monthly_fixed(t: dict[str, Any], contract_kw: float | None) -> dict[str, float]:
    """Fixed netto fees per month, incl. contracted power for C tariffs."""
    fees = {k: float(v) for k, v in t["fixed"].items() if v}
    if t["contract_rate"] and contract_kw:
        fees["moc_umowna"] = round(t["contract_rate"] * float(contract_kw), 2)
    return fees


def cost_hours(
    t: dict[str, Any], hours: list[tuple[datetime, float]]
) -> dict[str, Any]:
    """Aggregate kWh and netto cost per zone for (local hour start, kWh) pairs."""
    zones: dict[str, dict[str, float]] = {
        z: {"kwh": 0.0, "energy": 0.0, "dist": 0.0} for z in t["zones"]
    }
    for when, kwh in hours:
        z = zone_at(t["id"], when)
        bucket = zones.setdefault(z, {"kwh": 0.0, "energy": 0.0, "dist": 0.0})
        e, d = variable_price(t, z)
        bucket["kwh"] += kwh
        bucket["energy"] += kwh * e
        bucket["dist"] += kwh * d
    kwh = sum(b["kwh"] for b in zones.values())
    energy = sum(b["energy"] for b in zones.values())
    dist = sum(b["dist"] for b in zones.values())
    return {
        "zones": {z: {k: round(v, 3) for k, v in b.items()} for z, b in zones.items()},
        "kwh": round(kwh, 3),
        "energy": round(energy, 2),
        "dist": round(dist, 2),
        "variable": round(energy + dist, 2),
    }


def catalog() -> list[dict[str, Any]]:
    """Tariff list for the panel's settings form."""
    return [
        {
            "id": tid,
            "label": t["label"],
            "kind": t["kind"],
            "zones": t["zones"],
            "energy": t["energy"],
            "dist": t["dist"],
            "fixed": t["fixed"],
            "contract_rate": t["contract_rate"],
            "needs_input": t["needs_input"],
        }
        for tid, t in METER_TARIFFS.items()
    ]
