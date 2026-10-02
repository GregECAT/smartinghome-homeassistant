"""Energy ledger — where every kWh came from and went to, and what it cost.

Instantaneous powers are split into flows each coordinator cycle:

    PV → dom / bateria / sieć,  bateria → dom / sieć,  sieć → dom / bateria

PV feeds the house first, then the battery, then the grid. The battery keeps
a running origin mix (PV vs grid) so energy it later gives back to the house
can be credited to the right source. Import/export kWh and money come from
the grid meter's lifetime counters (billing truth) × the price at that moment.

Daily records persist in HA storage (survive restarts). When today's record
is missing (first install, lost store) it is rebuilt from the recorder.

Sign convention (project canonical): grid + export / − import,
battery + discharge / − charge.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
SCHEMA = 3  # bump → stored days are dropped and rebuilt from the recorder
KEEP_DAYS = 400
MAX_STEP_S = 180          # longer gaps are not integrated (HA was down)
MAX_METER_STEP_KWH = 5.0  # meter jump sanity limit per cycle
SAVE_EVERY_S = 300

# HEMS score weights (renormalised over the factors that apply today)
SCORE_WEIGHTS: dict[str, float] = {
    "savings": 0.25,      # money saved vs. no PV / no battery
    "peak": 0.25,         # house on battery (not grid) in expensive zones
    "tariff": 0.15,       # share of grid import bought in the cheap zone
    "autarky": 0.15,      # house consumption covered by own PV
    "self_consumption": 0.10,  # PV not exported directly
    "pv": 0.10,           # PV production vs. forecast so far
}
SCORE_LABELS = ((80, "Doskonale"), (60, "Dobrze"), (40, "Przeciętnie"), (0, "Słabo"))

_FLOW_KEYS = (
    "pv", "load", "load_day", "load_night",
    "pv_home", "pv_bat", "pv_grid", "bat_home", "bat_home_pv", "bat_grid",
    "grid_home", "grid_bat", "charge", "discharge",
    "peak_load", "peak_grid_home",
)
RECONCILE_MIN_KWH = 0.3  # below this the counters and the integration agree
_STATE_KEYS = ("soc_start", "soc_end", "capacity")  # per-day states, never summed
_MONEY_KEYS = (
    "import", "export", "import_offpeak", "import_cost", "export_revenue",
    "baseline_cost", "flow_import", "flow_export",
)


@dataclass
class Sample:
    """One instant of the energy system (canonical signs)."""

    ts: datetime
    pv_w: float
    bat_w: float
    grid_w: float
    soc: float | None
    buy_price: float
    sell_price: float
    is_peak: bool
    sun_up: bool
    import_total: float | None = None
    export_total: float | None = None
    mppt_w: tuple[float | None, ...] = ()   # per-MPPT PV power (pv1..pv4)


@dataclass
class DayRecord:
    """Accumulated energy (kWh) and money (zł) for one local day."""

    day: str
    values: dict[str, float] = field(default_factory=dict)

    def add(self, key: str, amount: float) -> None:
        if amount:
            self.values[key] = self.values.get(key, 0.0) + amount

    def get(self, key: str) -> float:
        return float(self.values.get(key, 0.0))


def split_flows(pv_w: float, bat_w: float, grid_w: float) -> dict[str, float]:
    """Instantaneous power flows (W) — PV serves the house first, then battery, then grid."""
    pv = max(0.0, pv_w)
    home = max(0.0, pv + bat_w - grid_w)
    pv_home = min(pv, home)
    rest_home = home - pv_home
    pv_rest = pv - pv_home
    flows = dict.fromkeys(
        ("home", "pv_home", "pv_bat", "pv_grid", "bat_home", "bat_grid", "grid_home", "grid_bat"), 0.0
    )
    flows["home"] = home
    flows["pv_home"] = pv_home
    if bat_w < 0:  # charging
        charge = -bat_w
        flows["pv_bat"] = min(pv_rest, charge)
        flows["grid_bat"] = charge - flows["pv_bat"]
        flows["pv_grid"] = pv_rest - flows["pv_bat"]
    else:  # discharging / idle
        flows["bat_home"] = min(bat_w, rest_home)
        flows["bat_grid"] = bat_w - flows["bat_home"]
        flows["pv_grid"] = pv_rest
    flows["grid_home"] = max(0.0, rest_home - flows["bat_home"])
    return flows


def _pct(num: float, den: float) -> float:
    return max(0.0, min(100.0, num / den * 100.0))


class EnergyLedger:
    """Daily energy/money accounting with persistence and recorder backfill."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.hass = hass
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.energy_ledger")
        self._days: dict[str, DayRecord] = {}
        self._loaded = False
        self._last: Sample | None = None
        self._last_save = 0.0
        self._dirty = False
        # Battery content by origin (kWh, AC side) — rescaled to SOC each cycle
        self._pool_pv = 0.0
        self._pool_grid = 0.0
        self._capacity_kwh = 10.2

    # ── persistence ──────────────────────────────────────────────

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load() or {}
        if data.get("schema") != SCHEMA:
            data = {}  # older format (no coverage/SOC fields) — rebuild from history
        for day, values in (data.get("days") or {}).items():
            self._days[day] = DayRecord(day, {k: float(v) for k, v in values.items()})
        pool = data.get("pool") or {}
        self._pool_pv = float(pool.get("pv", 0.0))
        self._pool_grid = float(pool.get("grid", 0.0))

    async def async_save(self, force: bool = False) -> None:
        now = dt_util.utcnow().timestamp()
        if not force and (not self._dirty or now - self._last_save < SAVE_EVERY_S):
            return
        cutoff = (dt_util.now().date() - timedelta(days=KEEP_DAYS)).isoformat()
        for day in [d for d in self._days if d < cutoff]:
            del self._days[day]
        await self._store.async_save({
            "schema": SCHEMA,
            "days": {d: {k: round(v, 5) for k, v in r.values.items()} for d, r in self._days.items()},
            "pool": {"pv": round(self._pool_pv, 4), "grid": round(self._pool_grid, 4)},
        })
        self._last_save = now
        self._dirty = False

    def has_day(self, day: date) -> bool:
        return day.isoformat() in self._days

    # ── accumulation ─────────────────────────────────────────────

    def _record(self, day: date) -> DayRecord:
        key = day.isoformat()
        if key not in self._days:
            self._days[key] = DayRecord(key)
        return self._days[key]

    def _sync_pool(self, soc: float | None) -> None:
        if soc is None:
            return
        target = max(0.0, soc) / 100.0 * self._capacity_kwh
        total = self._pool_pv + self._pool_grid
        if total <= 1e-6:
            # Unknown origin — assume half/half until real charging tells us more
            self._pool_pv = self._pool_grid = target / 2
        else:
            scale = target / total
            self._pool_pv *= scale
            self._pool_grid *= scale

    def update(self, sample: Sample, capacity_kwh: float | None = None) -> None:
        """Integrate from the previous sample to this one."""
        if capacity_kwh and capacity_kwh > 0:
            self._capacity_kwh = capacity_kwh
        prev, self._last = self._last, sample
        if prev is None:
            self._sync_pool(sample.soc)
            return
        rec = self._record(sample.ts.date())
        if sample.soc is not None:
            rec.values.setdefault("soc_start", float(sample.soc))
            rec.values["soc_end"] = float(sample.soc)
            rec.values["capacity"] = self._capacity_kwh
        if prev.ts.date() != sample.ts.date():
            prev = None  # midnight: start the new day's integration fresh
        if prev is not None:
            step_s = (sample.ts - prev.ts).total_seconds()
            if 0 < step_s <= MAX_STEP_S:
                self._integrate(rec, prev, sample, step_s / 3600.0)
            self._meter(rec, prev, sample)
        self._sync_pool(sample.soc)
        self._dirty = True

    def reconcile(
        self, day: date, counted: dict[str, float | None], *,
        buy_price: float, sell_price: float, is_peak: bool, elapsed_min: float,
        mppt_weights: tuple[float | None, ...] = (),
    ) -> dict[str, float]:
        """Add what the sample integration missed today (inverter outage, HA restart).

        counted — kWh since local midnight from the inverter's lifetime counters:
        pv / load / charge / discharge / import / export. Whatever the counters show
        above the ledger is booked as one aggregate interval: PV serves the house
        first, then the battery, then the grid (same rule as split_flows). Running
        it again adds nothing — afterwards the ledger matches the counters.
        """
        rec = self._record(day)
        v = rec.values

        def miss(key: str) -> float:
            c = counted.get(key)
            return max(0.0, float(c) - v.get(key, 0.0)) if c is not None else 0.0

        pv, load = miss("pv"), miss("load")
        chg, dis = miss("charge"), miss("discharge")
        imp, exp = miss("import"), miss("export")
        if counted.get("import") is not None and counted.get("export") is not None:
            # The inverter's battery counters undercount (2026-10-02: SOC 99 → 10 %
            # ≈ 9.4 kWh, the counter 7.4; a midday charge 12 → 99 % missing from
            # it). PV, load and the grid meter are reliable — the battery's share
            # of the gap is what balances them.
            net = load + exp - pv - imp  # + battery gave energy, − battery took it
            dis, chg = max(net, 0.0), max(-net, 0.0)
        if max(pv, load, chg, dis) < RECONCILE_MIN_KWH and max(imp, exp) < RECONCILE_MIN_KWH:
            if self._topup_mppt(rec, mppt_weights):
                self._dirty = True
            return {}
        pv_home = min(pv, load)
        rest_home = load - pv_home
        pv_rest = pv - pv_home
        f = {
            "pv_home": pv_home,
            "pv_bat": min(pv_rest, chg),
            "bat_home": min(dis, rest_home),
        }
        f["grid_bat"] = max(0.0, chg - f["pv_bat"])
        f["pv_grid"] = max(0.0, pv_rest - f["pv_bat"])
        f["bat_grid"] = max(0.0, dis - f["bat_home"])
        f["grid_home"] = max(0.0, rest_home - f["bat_home"])
        rec.add("pv", pv)
        self._topup_mppt(rec, mppt_weights)
        rec.add("load", load)
        rec.add("load_day" if pv > RECONCILE_MIN_KWH else "load_night", load)
        for key, val in f.items():
            rec.add(key, val)
        rec.add("charge", chg)
        rec.add("discharge", dis)
        pool = self._pool_pv + self._pool_grid
        pv_share = self._pool_pv / pool if pool > 1e-6 else 0.5
        rec.add("bat_home_pv", f["bat_home"] * pv_share)
        rec.add("flow_import", f["grid_home"] + f["grid_bat"])
        rec.add("flow_export", f["pv_grid"] + f["bat_grid"])
        rec.add("baseline_cost", load * buy_price)
        if is_peak:
            rec.add("peak_load", load)
            rec.add("peak_grid_home", f["grid_home"])
        if imp > 0:
            rec.add("import", imp)
            rec.add("import_cost", imp * buy_price)
            if not is_peak:
                rec.add("import_offpeak", imp)
        if exp > 0:
            rec.add("export", exp)
            rec.add("export_revenue", exp * max(0.0, sell_price))
        rec.add("gap_load", load)
        rec.add("gap_pv", pv)  # per-MPPT energy of the gap is unknown
        # the day is complete again up to now
        v["covered_min"] = max(v.get("covered_min", 0.0), elapsed_min)
        self._dirty = True
        return {"pv": pv, "load": load, "charge": chg, "discharge": dis, "import": imp, "export": exp}

    @staticmethod
    def _topup_mppt(rec: DayRecord, weights_in: tuple[float | None, ...]) -> bool:
        """Per-MPPT energy short of the day's PV (a filled gap) → split by the strings' power now."""
        v = rec.values
        weights = [max(w or 0.0, 0.0) for w in weights_in]
        mppt_sum = sum(v.get(f"mppt{i + 1}", 0.0) for i in range(len(weights)))
        short = v.get("pv", 0.0) - mppt_sum
        if not weights or sum(weights) <= 50 or short <= 1.0:
            return False
        for idx, w in enumerate(weights):
            rec.add(f"mppt{idx + 1}", short * w / sum(weights))
        return True

    def update_gap(self) -> None:
        """Inputs unavailable this cycle — don't integrate across the gap."""
        self._last = None

    def _integrate(self, rec: DayRecord, prev: Sample, cur: Sample, hours: float) -> None:
        # Trapezoid over the step — flows from both ends averaged
        f0 = split_flows(prev.pv_w, prev.bat_w, prev.grid_w)
        f1 = split_flows(cur.pv_w, cur.bat_w, cur.grid_w)
        kwh = {k: (f0[k] + f1[k]) / 2 * hours / 1000.0 for k in f0}
        pv_kwh = (max(0.0, prev.pv_w) + max(0.0, cur.pv_w)) / 2 * hours / 1000.0
        load = kwh["home"]
        rec.add("covered_min", hours * 60)
        for idx, (w0, w1) in enumerate(zip(prev.mppt_w, cur.mppt_w)):
            if w0 is not None and w1 is not None:
                rec.add(f"mppt{idx + 1}", (max(0.0, w0) + max(0.0, w1)) / 2 * hours / 1000.0)
        rec.add("pv", pv_kwh)
        rec.add("load", load)
        rec.add("load_day" if cur.sun_up else "load_night", load)
        for key in ("pv_home", "pv_bat", "pv_grid", "bat_home", "bat_grid", "grid_home", "grid_bat"):
            rec.add(key, kwh[key])
        charge = kwh["pv_bat"] + kwh["grid_bat"]
        discharge = kwh["bat_home"] + kwh["bat_grid"]
        rec.add("charge", charge)
        rec.add("discharge", discharge)
        # Origin of what the battery gives to the house
        pool = self._pool_pv + self._pool_grid
        pv_share = self._pool_pv / pool if pool > 1e-6 else 0.5
        rec.add("bat_home_pv", kwh["bat_home"] * pv_share)
        self._pool_pv = max(0.0, self._pool_pv + kwh["pv_bat"] - discharge * pv_share)
        self._pool_grid = max(0.0, self._pool_grid + kwh["grid_bat"] - discharge * (1 - pv_share))
        # Money for flow-based import/export (used when there is no grid meter)
        flow_imp = kwh["grid_home"] + kwh["grid_bat"]
        flow_exp = kwh["pv_grid"] + kwh["bat_grid"]
        rec.add("flow_import", flow_imp)
        rec.add("flow_export", flow_exp)
        rec.add("baseline_cost", load * cur.buy_price)
        if cur.is_peak:
            rec.add("peak_load", load)
            rec.add("peak_grid_home", kwh["grid_home"])
        if cur.import_total is None or prev.import_total is None:
            self._book_import(rec, flow_imp, cur)
        if cur.export_total is None or prev.export_total is None:
            rec.add("export", flow_exp)
            rec.add("export_revenue", flow_exp * max(0.0, cur.sell_price))

    def _book_import(self, rec: DayRecord, kwh: float, cur: Sample) -> None:
        rec.add("import", kwh)
        rec.add("import_cost", kwh * cur.buy_price)
        if not cur.is_peak:
            rec.add("import_offpeak", kwh)

    def _meter(self, rec: DayRecord, prev: Sample, cur: Sample) -> None:
        """Billing-grade import/export from the meter's lifetime counters."""
        if cur.import_total is not None and prev.import_total is not None:
            d_imp = cur.import_total - prev.import_total
            if 0 < d_imp < MAX_METER_STEP_KWH:
                self._book_import(rec, d_imp, cur)
        if cur.export_total is not None and prev.export_total is not None:
            d_exp = cur.export_total - prev.export_total
            if 0 < d_exp < MAX_METER_STEP_KWH:
                rec.add("export", d_exp)
                # Net-billing: energy sold at a negative RCE is worth 0
                rec.add("export_revenue", d_exp * max(0.0, cur.sell_price))

    # ── reporting ────────────────────────────────────────────────

    def day(self, day: date) -> dict[str, float]:
        rec = self._days.get(day.isoformat())
        values = dict(rec.values) if rec else {}
        for key in _FLOW_KEYS + _MONEY_KEYS:
            values.setdefault(key, 0.0)
        return values

    def period(self, start: date, end: date) -> dict[str, float]:
        """Sum of daily records in [start, end]."""
        total: dict[str, float] = dict.fromkeys(_FLOW_KEYS + _MONEY_KEYS, 0.0)
        days = 0
        for key, rec in self._days.items():
            if start.isoformat() <= key <= end.isoformat():
                days += 1
                for k, v in rec.values.items():
                    if k not in _STATE_KEYS:
                        total[k] = total.get(k, 0.0) + v
        total["days"] = days
        return total

    @staticmethod
    def summarise(v: dict[str, float]) -> dict[str, Any]:
        """Derived KPIs (kWh, zł, %) for a day or a period."""
        load = v.get("load", 0.0)
        pv = v.get("pv", 0.0)
        imp = v.get("import", 0.0)
        net_cost = v.get("import_cost", 0.0) - v.get("export_revenue", 0.0)
        baseline = v.get("baseline_cost", 0.0)
        out: dict[str, Any] = {
            "pv_kwh": round(pv, 2),
            "load_kwh": round(load, 2),
            "load_day_kwh": round(v.get("load_day", 0.0), 2),
            "load_night_kwh": round(v.get("load_night", 0.0), 2),
            "import_kwh": round(imp, 2),
            "export_kwh": round(v.get("export", 0.0), 2),
            "pv_to_home_kwh": round(v.get("pv_home", 0.0), 2),
            "pv_to_battery_kwh": round(v.get("pv_bat", 0.0), 2),
            "pv_to_grid_kwh": round(v.get("pv_grid", 0.0), 2),
            "battery_to_home_kwh": round(v.get("bat_home", 0.0), 2),
            "battery_to_grid_kwh": round(v.get("bat_grid", 0.0), 2),
            "grid_to_home_kwh": round(v.get("grid_home", 0.0), 2),
            "grid_to_battery_kwh": round(v.get("grid_bat", 0.0), 2),
            "battery_charge_kwh": round(v.get("charge", 0.0), 2),
            "battery_discharge_kwh": round(v.get("discharge", 0.0), 2),
            "import_cost_pln": round(v.get("import_cost", 0.0), 2),
            "export_revenue_pln": round(v.get("export_revenue", 0.0), 2),
            "net_cost_pln": round(net_cost, 2),
            "baseline_cost_pln": round(baseline, 2),
            "savings_pln": round(baseline - net_cost, 2),
            "avg_buy_price": round(v.get("import_cost", 0.0) / imp, 3) if imp > 0.05 else None,
            "avg_sell_price": (
                round(v.get("export_revenue", 0.0) / v["export"], 3) if v.get("export", 0.0) > 0.05 else None
            ),
            "autarky_pct": (
                round(_pct(v.get("pv_home", 0.0) + v.get("bat_home_pv", 0.0), load), 1) if load > 0.05 else None
            ),
            "self_consumption_pct": (
                round(_pct(v.get("pv_home", 0.0) + v.get("pv_bat", 0.0), pv), 1) if pv > 0.05 else None
            ),
            "peak_battery_pct": (
                round(100.0 - _pct(v.get("peak_grid_home", 0.0), v["peak_load"]), 1)
                if v.get("peak_load", 0.0) > 0.1 else None
            ),
            "offpeak_import_pct": round(_pct(v.get("import_offpeak", 0.0), imp), 1) if imp > 0.2 else None,
            "savings_pct": round(_pct(baseline - net_cost, baseline), 1) if baseline > 0.2 else None,
        }
        # Battery efficiency over the day: what came out + change of stored energy, per kWh put in
        charge = v.get("charge", 0.0)
        if "soc_start" in v and "soc_end" in v and charge > 2.0:
            stored = (v["soc_end"] - v["soc_start"]) / 100.0 * v.get("capacity", 10.2)
            out["battery_efficiency_pct"] = round(max(0.0, min(100.0, (v.get("discharge", 0.0) + stored) / charge * 100)), 1)
            out["soc_start"] = round(v["soc_start"])
        else:
            out["battery_efficiency_pct"] = None
        out["coverage_h"] = round(v.get("covered_min", 0.0) / 60, 1)
        out["pv_mppt_kwh"] = [round(v.get(f"mppt{i}", 0.0), 2) for i in range(1, 5) if f"mppt{i}" in v]
        if "days" in v:
            out["days"] = int(v["days"])
        return out

    @staticmethod
    def score(kpi: dict[str, Any], pv_vs_forecast_pct: float | None) -> dict[str, Any]:
        """HEMS efficiency score 0–100 from today's KPIs."""
        factors = {
            "savings": kpi.get("savings_pct"),
            "peak": kpi.get("peak_battery_pct"),
            "tariff": kpi.get("offpeak_import_pct"),
            "autarky": kpi.get("autarky_pct"),
            "self_consumption": kpi.get("self_consumption_pct"),
            "pv": pv_vs_forecast_pct,
        }
        used = {k: v for k, v in factors.items() if v is not None}
        weight = sum(SCORE_WEIGHTS[k] for k in used)
        value = round(sum(SCORE_WEIGHTS[k] * v for k, v in used.items()) / weight) if weight else None
        label = next((name for limit, name in SCORE_LABELS if value is not None and value >= limit), None)
        return {
            "score": value,
            "label": label,
            "factors": {k: (round(v) if v is not None else None) for k, v in factors.items()},
            "weights": SCORE_WEIGHTS,
        }

    # ── recorder backfill ────────────────────────────────────────

    async def async_backfill_day(
        self,
        day: date,
        entity_ids: dict[str, str],
        price_at: Callable[[datetime, float | None], tuple[float, float, bool]],
        capacity_kwh: float,
    ) -> bool:
        """Rebuild one day's record from recorder history (1-minute resampling).

        entity_ids: pv, bat, grid, soc, import_total, export_total, rce → entity_id.
        price_at(t, rce_pln_mwh) → (buy zł/kWh, sell zł/kWh, is_peak).
        The live sample chain is left untouched.
        """
        try:
            from homeassistant.components.recorder import get_instance, history
        except ImportError:
            return False
        start = dt_util.start_of_local_day(day)
        end = min(start + timedelta(days=1), dt_util.now())
        ids = [e for e in entity_ids.values() if e]
        if not ids or end <= start:
            return False
        try:
            states = await get_instance(self.hass).async_add_executor_job(
                history.get_significant_states,
                self.hass, start, end, ids, None, True, False, False, True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ledger backfill %s: recorder query failed: %s", day, err)
            return False

        series: dict[str, list[tuple[datetime, float]]] = {}
        for key, eid in entity_ids.items():
            points: list[tuple[datetime, float | None]] = []
            for st in states.get(eid, []) if eid else []:
                try:
                    points.append((st.last_updated, float(st.state)))
                except (TypeError, ValueError):
                    points.append((st.last_updated, None))  # unavailable → gap until next value
            series[key] = points
        # Need real power data for most of the period, not just a start value
        def numeric(key: str) -> int:
            return sum(1 for _, v in series.get(key) or [] if v is not None)

        if numeric("pv") < 10 or numeric("grid") < 10:
            return False

        def value_at(key: str, t: datetime) -> float | None:
            points = series.get(key) or []
            lo, hi, found = 0, len(points) - 1, None
            while lo <= hi:
                mid = (lo + hi) // 2
                if points[mid][0] <= t:
                    found = points[mid][1]
                    lo = mid + 1
                else:
                    hi = mid - 1
            return found

        sun_up = _sun_checker(self.hass)
        saved_last = self._last
        self._days.pop(day.isoformat(), None)
        self._last = None
        t = start
        while t < end:
            pv_w, bat_w, grid_w = value_at("pv", t), value_at("bat", t), value_at("grid", t)
            if pv_w is None or bat_w is None or grid_w is None:
                # No data yet (e.g. inverter added to HA later that day) — a gap, not zeros
                self.update_gap()
                t += timedelta(minutes=1)
                continue
            buy, sell, peak = price_at(t, value_at("rce", t))
            self.update(Sample(
                ts=dt_util.as_local(t),
                pv_w=pv_w,
                bat_w=bat_w,
                grid_w=grid_w,
                soc=value_at("soc", t),
                buy_price=buy, sell_price=sell, is_peak=peak, sun_up=sun_up(t),
                import_total=value_at("import_total", t),
                export_total=value_at("export_total", t),
                mppt_w=tuple(value_at(f"pv{i}", t) for i in range(1, 5) if entity_ids.get(f"pv{i}")),
            ), capacity_kwh)
            t += timedelta(minutes=1)
        self._last = saved_last
        self._dirty = True
        _LOGGER.info("Energy ledger: rebuilt %s from recorder history", day)
        return True

    def as_dict(self) -> dict[str, Any]:
        return {d: asdict(r) for d, r in self._days.items()}


def _sun_checker(hass: HomeAssistant) -> Callable[[datetime], bool]:
    """Approximate 'sun above horizon' for past timestamps from today's sun times."""
    sun = hass.states.get("sun.sun")
    rising = _parse_dt(sun.attributes.get("next_rising")) if sun else None
    setting = _parse_dt(sun.attributes.get("next_setting")) if sun else None
    if not rising or not setting:
        return lambda t: 7 <= dt_util.as_local(t).hour < 18
    r, s = dt_util.as_local(rising), dt_util.as_local(setting)
    r_min, s_min = r.hour * 60 + r.minute, s.hour * 60 + s.minute

    def check(t: datetime) -> bool:
        loc = dt_util.as_local(t)
        return r_min <= loc.hour * 60 + loc.minute < s_min

    return check


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return dt_util.parse_datetime(str(value))
