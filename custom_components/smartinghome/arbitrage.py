"""Battery arbitrage planner for the "Max Zysk" strategy.

Plans the next ~30 hours hour by hour with dynamic programming over the
battery's stored energy. Each hour the battery may charge (from PV surplus
first, then grid), hold, cover the house or export. The plan minimises
  grid import × tariff price − export × RCE sell price + wear/margin × discharge
so it charges in cheap tariff hours and spends the energy where it is worth
most: avoiding expensive tariff hours at home or selling at the best RCE hours.

Pure Python (no Home Assistant imports) so it can be unit-tested.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .const import RCE_PROSUMER_COEFFICIENT, TariffType, WINTER_MONTHS
from .tariff_prompt import (
    _get_current_g12_zone,
    _get_current_g12n_zone,
    _get_current_g13_zone,
)
from .const import PGE_G11_PRICE, TAURON_G11_PRICE, EnergyProvider

# Actions the executor maps to inverter modes
ACT_CHARGE_GRID = "charge_grid"   # EMS charge_battery (PV + grid)
ACT_PV_CHARGE = "pv_charge"       # general mode — PV surplus charges the battery
ACT_HOLD = "hold"                 # battery standby — house on grid, energy kept
ACT_HOME = "home"                 # general mode — battery covers the house
ACT_DISCHARGE = "discharge"       # EMS discharge_battery at a set power (home + export)

ACTION_LABELS = {
    ACT_CHARGE_GRID: "⚡ Ładuj z sieci",
    ACT_PV_CHARGE: "☀️ Ładuj z PV",
    ACT_HOLD: "⏸️ Trzymaj",
    ACT_HOME: "🏠 Zasilaj dom",
    ACT_DISCHARGE: "💰 Sprzedaż",
}


@dataclass
class ArbitrageParams:
    """User-tunable parameters (panel → settings "arbitrage_params")."""

    reserve_soc: float = 15.0     # % arbitrage never discharges below (outside peaks)
    peak_floor_soc: float = 5.0   # % in tariff peaks the house runs on the battery down to this
    peak_import_penalty: float = 5.0  # zł/kWh — grid import in a peak is "forbidden"
    pv_confidence: float = 0.7    # share of the PV forecast the plan relies on
    charge_margin: float = 0.9    # plan with 90 % of max charge power (executed at 100 %)
    max_soc: float = 100.0        # % upper limit for grid charging
    min_profit: float = 0.10      # zł/kWh required on top of costs for a cycle
    wear_cost: float = 0.25       # zł per kWh discharged (battery degradation)
    charge_kw: float = 3.7        # max battery charge power
    discharge_kw: float = 3.7     # max battery discharge power
    eff_charge: float = 0.95
    eff_discharge: float = 0.95
    capacity_kwh: float = 10.2
    horizon_h: int = 30
    slot_minutes: int = 30        # plan resolution (RCE is published per 15 min)
    step_kwh: float = 0.2

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ArbitrageParams":
        params = cls()
        for key, val in (data or {}).items():
            if hasattr(params, key):
                try:
                    setattr(params, key, type(getattr(params, key))(val))
                except (TypeError, ValueError):
                    pass
        params.reserve_soc = min(max(params.reserve_soc, 5.0), 90.0)
        params.max_soc = min(max(params.max_soc, params.reserve_soc + 5), 100.0)
        # DOD 95 % → the inverter never goes below 5 %
        params.peak_floor_soc = min(max(params.peak_floor_soc, 5.0), params.reserve_soc)
        params.pv_confidence = min(max(params.pv_confidence, 0.0), 1.0)
        return params


@dataclass
class HourInput:
    start: datetime
    duration: float      # hours (first slot is the rest of the current hour)
    buy: float           # zł/kWh tariff price (gross)
    sell: float          # zł/kWh prosumer RCE sell price
    load_kwh: float
    pv_kwh: float
    zone: str = ""
    no_import: bool = False   # tariff peak: house must run on the battery


@dataclass
class HourPlan:
    start: str
    zone: str
    buy: float
    sell: float
    action: str
    soc_start: float
    soc_end: float
    battery_kwh: float    # + charge / − discharge (stored energy change)
    grid_import: float
    grid_export: float
    cost: float
    power_w: int = 0


@dataclass
class ArbitragePlan:
    hours: list[HourPlan] = field(default_factory=list)
    total_cost: float = 0.0
    baseline_cost: float = 0.0   # same horizon with the battery idle
    note: str = ""
    charge_start: str = ""       # first planned grid charge (local time)
    battery_until: str = ""      # next peak: battery runs out at (or "" = lasts)
    next_peak: str = ""          # next peak block start–end

    @property
    def first(self) -> HourPlan | None:
        return self.hours[0] if self.hours else None

    def as_dict(self, limit: int = 30) -> dict[str, Any]:
        return {
            "hours": [asdict(h) for h in self.hours[:limit]],
            "total_cost": round(self.total_cost, 2),
            "baseline_cost": round(self.baseline_cost, 2),
            "savings": round(self.baseline_cost - self.total_cost, 2),
            "note": self.note,
            "charge_start": self.charge_start,
            "battery_until": self.battery_until,
            "next_peak": self.next_peak,
        }


# ─────────────────────────────────────────────────────────────────────────────
#  Inputs
# ─────────────────────────────────────────────────────────────────────────────

def buy_price(dt: datetime, tariff: str, provider: str) -> tuple[str, float]:
    """(zone, gross price zł/kWh) of the user's tariff at this hour."""
    tariff = str(tariff or TariffType.G13).lower()
    if tariff == TariffType.G13:
        zone, price = _get_current_g13_zone(dt.hour, dt.month, dt.weekday())
        return str(zone), float(price)
    if tariff in (TariffType.G12, TariffType.G12W):
        zone, price = _get_current_g12_zone(dt.hour, dt.month, dt.weekday(), provider)
        return str(zone), float(price)
    if tariff == TariffType.G12N:
        zone, price = _get_current_g12n_zone(dt.hour, dt.weekday())
        return str(zone), float(price)
    flat = PGE_G11_PRICE if provider == EnergyProvider.PGE else TAURON_G11_PRICE
    return "flat", float(flat)


def rce_hourly(prices: list[dict[str, Any]] | None) -> dict[tuple[date, int], float]:
    """RCE PSE v2 'prices' attribute (15-min) → {(date, hour): PLN/MWh}."""
    buckets: dict[tuple[date, int], list[float]] = {}
    for entry in prices or []:
        try:
            day = date.fromisoformat(str(entry.get("business_date") or str(entry.get("dtime", ""))[:10]))
            hour = int(str(entry.get("period", ""))[:2])
            value = float(entry.get("rce_pln"))
        except (TypeError, ValueError):
            continue
        buckets.setdefault((day, hour), []).append(value)
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def rce_slots(
    prices: list[dict[str, Any]] | None, slot_minutes: int = 30
) -> dict[tuple[date, int, int], float]:
    """RCE PSE v2 'prices' (15-min) → {(date, hour, slot index in hour): PLN/MWh}."""
    buckets: dict[tuple[date, int, int], list[float]] = {}
    for entry in prices or []:
        try:
            day = date.fromisoformat(str(entry.get("business_date") or str(entry.get("dtime", ""))[:10]))
            period = str(entry.get("period", ""))
            hour, minute = int(period[:2]), int(period[3:5])
            value = float(entry.get("rce_pln"))
        except (TypeError, ValueError):
            continue
        buckets.setdefault((day, hour, minute // slot_minutes), []).append(value)
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def pv_distribution(
    day_total_kwh: float, sunrise_h: float, sunset_h: float, hours: list[int]
) -> dict[int, float]:
    """Spread a daily PV total over daylight hours with a sine profile."""
    if day_total_kwh <= 0 or sunset_h <= sunrise_h:
        return {h: 0.0 for h in hours}
    weights: dict[int, float] = {}
    span = sunset_h - sunrise_h
    for h in range(24):
        mid = h + 0.5
        x = (mid - sunrise_h) / span
        weights[h] = math.sin(math.pi * x) if 0 < x < 1 else 0.0
    total_w = sum(weights[h] for h in hours) or 1.0
    return {h: day_total_kwh * weights[h] / total_w for h in hours}


def build_inputs(
    now: datetime,
    *,
    tariff: str,
    provider: str,
    rce: dict[tuple[date, int], float],
    load_profile_kw: list[float],
    pv_today_remaining_kwh: float,
    pv_tomorrow_kwh: float,
    sunrise_h: float,
    sunset_h: float,
    horizon_h: int,
    slot_minutes: int = 60,
    rce_slot: dict[tuple[date, int, int], float] | None = None,
) -> list[HourInput]:
    """Plan slots (slot_minutes long, aligned to the clock) from now to now + horizon.

    `now` may be timezone-aware (recommended): slots are stepped in UTC and
    converted back to local wall-clock time, so DST changes (last Sunday of
    March/October) neither drop nor duplicate tariff slots.
    """
    from datetime import timezone as _tz

    slot_minutes = slot_minutes if slot_minutes in (15, 30, 60) else 60
    tz = now.tzinfo
    today = now.date()
    today_hours = list(range(now.hour, 24))
    pv_today = pv_distribution(
        pv_today_remaining_kwh, max(sunrise_h, now.hour + now.minute / 60), sunset_h, today_hours
    )
    pv_later = pv_distribution(pv_tomorrow_kwh, sunrise_h, sunset_h, list(range(24)))
    known_by_hour: dict[int, float] = {}
    for (_, h), v in sorted(rce.items()):
        known_by_hour[h] = v  # latest known day wins as fallback
    rce_slot = rce_slot or {}

    def local(t_utc: datetime) -> datetime:
        return t_utc.astimezone(tz).replace(tzinfo=None) if tz else t_utc

    # first boundary after now on the slot grid (:00/:30 …)
    minute_floor = (now.minute // slot_minutes) * slot_minutes
    boundary = now.replace(minute=minute_floor, second=0, microsecond=0) + timedelta(minutes=slot_minutes)
    start_utc = now.astimezone(_tz.utc) if tz else now
    first_end_utc = boundary.astimezone(_tz.utc) if tz else boundary
    end_utc = start_utc + timedelta(hours=horizon_h)
    step = timedelta(minutes=slot_minutes)

    out: list[HourInput] = []
    t = start_utc
    while t < end_utc:
        slot_end = first_end_utc if not out else t + step
        duration = (slot_end - t).total_seconds() / 3600
        if duration <= 0.02:
            t = slot_end
            continue
        lt = local(t)
        zone, buy = buy_price(lt, tariff, provider)
        rce_mwh = rce_slot.get(
            (lt.date(), lt.hour, lt.minute // slot_minutes) if slot_minutes < 60 else (None, None, None),
            rce.get((lt.date(), lt.hour), known_by_hour.get(lt.hour, 400.0)),
        )
        sell = max(rce_mwh, 0.0) / 1000 * RCE_PROSUMER_COEFFICIENT
        hourly_pv = pv_today.get(lt.hour, 0.0) if lt.date() == today else pv_later.get(lt.hour, 0.0)
        pv = hourly_pv * duration
        load = max(load_profile_kw[lt.hour % 24], 0.0) * duration
        out.append(HourInput(lt, duration, buy, sell, load, pv, zone))
        t = slot_end

    # Tariff peaks: any slot pricier than the cheapest tariff slot of that day
    cheapest: dict[date, float] = {}
    for h in out:
        d = h.start.date()
        cheapest[d] = min(cheapest.get(d, h.buy), h.buy)
    for h in out:
        h.no_import = h.buy > cheapest[h.start.date()] + 0.01
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Optimisation
# ─────────────────────────────────────────────────────────────────────────────

def _flows(delta: float, d: float, p: ArbitrageParams) -> tuple[float, float, float]:
    """(import kWh, export kWh, discharged kWh) for a stored-energy change."""
    surplus = max(-d, 0.0)
    deficit = max(d, 0.0)
    if delta >= 0:
        bat_in = delta / p.eff_charge
        from_pv = min(surplus, bat_in)
        from_grid = bat_in - from_pv
        return deficit + from_grid, surplus - from_pv, 0.0
    out = -delta * p.eff_discharge
    to_home = min(out, deficit)
    return deficit - to_home, surplus + out - to_home, -delta


def optimize(soc_pct: float, inputs: list[HourInput], p: ArbitrageParams) -> ArbitragePlan:
    """Dynamic programming over stored energy (step_kwh grid)."""
    plan = ArbitragePlan()
    if not inputs:
        plan.note = "Brak danych wejściowych"
        return plan
    cap = p.capacity_kwh
    step = p.step_kwh
    e0 = min(max(soc_pct, 0.0), 100.0) / 100 * cap
    e_res = p.reserve_soc / 100 * cap
    e_peak = min(p.peak_floor_soc, p.reserve_soc) / 100 * cap
    e_max = p.max_soc / 100 * cap
    lo = min(e0, e_peak)
    levels = [lo + i * step for i in range(int((e_max - lo) / step) + 1)]
    if levels[-1] < e_max - 1e-6:
        levels.append(e_max)
    # Start state: nearest level to current energy
    start = min(range(len(levels)), key=lambda i: abs(levels[i] - e0))
    n = len(inputs)
    cycle_cost = p.wear_cost + p.min_profit
    # Energy left at the end is worth at least avoiding a cheap import later
    terminal_value = max(0.0, min(h.buy for h in inputs[-12:]) * p.eff_discharge - cycle_cost)

    INF = float("inf")
    value = [[INF] * len(levels) for _ in range(n + 1)]
    choice = [[-1] * len(levels) for _ in range(n)]
    for i, e in enumerate(levels):
        value[n][i] = -terminal_value * max(e - e_res, 0.0)

    for t in range(n - 1, -1, -1):
        h = inputs[t]
        d = h.load_kwh - h.pv_kwh
        floor = e_peak if h.no_import else e_res
        penalty = p.peak_import_penalty if h.no_import else 0.0
        # time buffer: plan slower than the inverter really charges
        max_up = p.charge_kw * p.charge_margin * h.duration * p.eff_charge
        max_dn = p.discharge_kw * h.duration
        for i, e in enumerate(levels):
            best = INF
            best_j = i
            for j, e2 in enumerate(levels):
                delta = e2 - e
                if delta > max_up + 1e-9 or -delta > max_dn + 1e-9:
                    continue
                if delta < 0 and e2 < floor - 1e-9:
                    continue  # never discharge below the reserve (5 % in peaks)
                imp, exp, dis = _flows(delta, d, p)
                cost = imp * (h.buy + penalty) - exp * h.sell + dis * cycle_cost + value[t + 1][j]
                if delta > 0:
                    # tie-break: same price → charge as late as possible (battery not
                    # sitting full for hours)
                    cost += 1e-4 * (n - t) * delta
                if cost < best - 1e-9:
                    best, best_j = cost, j
            value[t][i] = best
            choice[t][i] = best_j

    # Baseline: battery idle whole horizon
    plan.baseline_cost = sum(max(h.load_kwh - h.pv_kwh, 0) * h.buy - max(h.pv_kwh - h.load_kwh, 0) * h.sell for h in inputs)

    i = start
    total = 0.0
    for t, h in enumerate(inputs):
        j = choice[t][i]
        e, e2 = levels[i], levels[j]
        delta = e2 - e
        d = h.load_kwh - h.pv_kwh
        imp, exp, _dis = _flows(delta, d, p)
        cost = imp * h.buy - exp * h.sell
        total += cost
        action, power = classify(delta, d, h.duration, p)
        plan.hours.append(HourPlan(
            start=h.start.strftime("%Y-%m-%d %H:%M"),
            zone=h.zone,
            buy=round(h.buy, 3),
            sell=round(h.sell, 3),
            action=action,
            soc_start=round(e / cap * 100, 1),
            soc_end=round(e2 / cap * 100, 1),
            battery_kwh=round(delta, 2),
            grid_import=round(imp, 2),
            grid_export=round(exp, 2),
            cost=round(cost, 2),
            power_w=power,
        ))
        i = j
    plan.total_cost = total
    _summarise(plan, inputs, p)
    return plan


def _summarise(plan: ArbitragePlan, inputs: list[HourInput], p: ArbitrageParams) -> None:
    """Charge start, next peak and whether the battery lasts through it."""
    for h in plan.hours:
        if h.action == ACT_CHARGE_GRID:
            plan.charge_start = h.start[11:16] if h.start[:10] == plan.hours[0].start[:10] else h.start[5:16]
            break
    # next peak block
    idx = [i for i, h in enumerate(inputs) if h.no_import]
    if not idx:
        return
    first = idx[0]
    last = first
    while last + 1 < len(inputs) and inputs[last + 1].no_import:
        last += 1
    end_t = inputs[last].start + timedelta(hours=inputs[last].duration)
    plan.next_peak = f"{inputs[first].start.strftime('%H:%M')}–{end_t.strftime('%H:%M')}"
    for i in range(first, last + 1):
        if plan.hours[i].grid_import > 0.05:
            plan.battery_until = plan.hours[i].start[11:16]
            break


def classify(delta: float, d: float, duration: float, p: ArbitrageParams) -> tuple[str, int]:
    """Map a planned stored-energy change to an inverter action (+ power W)."""
    eps = 0.05 * duration
    if delta > eps:
        bat_in = delta / p.eff_charge
        from_grid = bat_in - min(max(-d, 0.0), bat_in)
        power = int(round(bat_in / duration * 1000 / 100) * 100)
        # ignore tiny grid top-ups (PV surplus will do) — avoids flapping modes
        return (ACT_CHARGE_GRID, max(power, 300)) if from_grid > 0.3 * duration else (ACT_PV_CHARGE, 0)
    if delta < -eps:
        out = -delta * p.eff_discharge
        power = int(round(out / duration * 1000 / 100) * 100)
        deficit = max(d, 0.0)
        if out - deficit > eps:
            return ACT_DISCHARGE, max(power, 300)        # exporting
        if out >= 0.8 * deficit:
            return ACT_HOME, 0                           # battery follows the house
        return ACT_DISCHARGE, max(power, 300)            # partial: fixed battery power
    if d > eps:
        return ACT_HOLD, 0
    return ACT_PV_CHARGE, 0


def season_hint(month: int) -> str:
    return "zima" if month in WINTER_MONTHS else "lato"
