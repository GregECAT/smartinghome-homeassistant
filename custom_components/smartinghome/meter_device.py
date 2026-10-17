# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Independent energy meter (Shelly EM / 3EM / Pro 3EM, Eastron SDM via Modbus, any HA meter).

Pure Python (no Home Assistant imports) so it can be unit-tested directly.

The meter is read-only: Smarting HOME never writes to it. It works without an
inverter (meter-only site) and next to one (second opinion on voltages, export of
a micro-inverter the integration does not know about).

- propose_mapping(): auto-detection — entity registry rows → meter roles, shown to
  the user for approval (never applied silently).
- tan_phi*(): reactive power ratio tg φ = Q / P (live and over the import hours).
- voltage_source() / alert_overlay(): which voltages the report and the alert use.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Callable

# ── Roles (settings keys of the "meter" block) ──────────────────────────────
PHASES = ("l1", "l2", "l3")

# role → (Polish label, kind). kind drives the entity filter in the panel.
ROLES: dict[str, tuple[str, str]] = {
    "energy_stat": ("Energia pobrana z sieci (import)", "energy"),
    "export_stat": ("Energia oddana do sieci (eksport)", "energy"),
    "power_entity": ("Moc całkowita", "power"),
    **{f"power_{p}": (f"Moc {p.upper()}", "power") for p in PHASES},
    **{f"voltage_{p}": (f"Napięcie {p.upper()}", "voltage") for p in PHASES},
    **{f"current_{p}": (f"Prąd {p.upper()}", "current") for p in PHASES},
    "pf_entity": ("Współczynnik mocy (całkowity)", "power_factor"),
    **{f"pf_{p}": (f"Współczynnik mocy {p.upper()}", "power_factor") for p in PHASES},
    "apparent_power_entity": ("Moc pozorna (VA)", "apparent_power"),
    "reactive_power_entity": ("Moc bierna (var)", "reactive_power"),
    "reactive_energy_stat": ("Energia bierna pobrana (kvarh)", "reactive_energy"),
    "frequency_entity": ("Częstotliwość", "frequency"),
}
# Roles that hold a list of statistics summed hour by hour (Shelly 3EM Gen1 has
# per-channel counters only — no three-phase total)
LIST_ROLES = ("import_stats", "export_stats")

METER_DEFAULTS: dict[str, Any] = {
    **{role: "" for role in ROLES},
    "import_stats": [],
    "export_stats": [],
    # Shelly and Eastron report import as positive power (export negative)
    "power_sign": "import_positive",
    # Voltage report / alert: "auto" = inverter when there is one, else the meter
    "voltage_source": "auto",
    # Net-billing: price the hourly-balanced import when export is measured
    "balance_hourly": True,
    "meter_device": "",
}

# tg φ above which Polish DSO tariffs typically bill inductive reactive energy
# (tg φ0 = 0.4 unless the contract says otherwise).
# NOT VERIFIED against the tariff regulation: the texts available for this change
# (rozporządzenie systemowe 2025, NC RfG, ustawa OZE) do not contain the billing rule.
# The panel shows tg φ and a warning only — no money estimate.
TAN_PHI_LIMIT = 0.4


# ── Units ───────────────────────────────────────────────────────────────────
_SCALE = {
    "power": {"W": 1.0, "kW": 1000.0, "MW": 1e6},
    "reactive_power": {"var": 1.0, "VAr": 1.0, "kvar": 1000.0, "kVAr": 1000.0},
    "apparent_power": {"VA": 1.0, "kVA": 1000.0},
    "energy": {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0},
    "reactive_energy": {"varh": 0.001, "VArh": 0.001, "kvarh": 1.0, "kVArh": 1.0, "Kvarh": 1.0},
}


def to_base(value: float | None, unit: str | None, kind: str) -> float | None:
    """Value in the base unit of its kind (W, var, VA, kWh, kvarh); unknown unit → as is."""
    if value is None:
        return None
    return value * _SCALE.get(kind, {}).get(unit or "", 1.0)


def num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ── Auto-detection ──────────────────────────────────────────────────────────
# Shelly unique_id (Home Assistant shelly integration):
#   Gen2+ RPC:  "<mac>-<key>:<id>-<sub_key>", e.g. "AABBCC…-em:0-a_act_power"
#   Gen1 block: "<mac>-<block>_<channel>-<attribute>", e.g. "…-emeter_0-energyReturned"
# Sub-keys as defined in homeassistant/components/shelly/sensor.py (RPC_SENSORS,
# BLOCK_SENSORS). Voltage / current / per-phase energy are disabled by default there.
_SHELLY_RPC = re.compile(r"-(em|emdata|em1|em1data):(\d+)-([a-z_]+)$")
_SHELLY_BLOCK = re.compile(r"-emeter_(\d+)-(power|voltage|current|powerFactor|energy|energyReturned)$")

_RPC_EM = {
    "total_act_power": "power_entity",
    "total_aprt_power": "apparent_power_entity",
    **{f"{c}_act_power": f"power_{p}" for c, p in zip("abc", PHASES)},
    **{f"{c}_voltage": f"voltage_{p}" for c, p in zip("abc", PHASES)},
    **{f"{c}_current": f"current_{p}" for c, p in zip("abc", PHASES)},
    **{f"{c}_pf": f"pf_{p}" for c, p in zip("abc", PHASES)},
    "a_freq": "frequency_entity",
}
_RPC_EMDATA = {"total_act": "energy_stat", "total_act_ret": "export_stat"}
# Single-phase meters (Shelly Pro EM, EM Gen3): channel 0 is the grid CT
_RPC_EM1 = {
    "act_power": "power_entity", "aprt_power": "apparent_power_entity",
    "voltage": "voltage_l1", "current": "current_l1", "pf": "pf_entity", "freq": "frequency_entity",
}
_RPC_EM1DATA = {"total_act_energy": "energy_stat", "total_act_ret_energy": "export_stat"}

# Unique ids of the Eastron template in docs/eastron_sdm_modbus.yaml
OWN_UID_PREFIX = "sh_meter_"


def _norm(*parts: Any) -> str:
    text = " ".join(str(p or "") for p in parts).lower()
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+", "_", text).strip("_")


_PHASE_RE = (
    re.compile(r"(?:^|_)(?:l|phase_?|faza_?|ph)([123])(?:_|$)"),
    re.compile(r"(?:^|_)(?:phase|faza|channel|kanal|kanał)_?([abc])(?:_|$)"),
)


def phase_of(text: str) -> str | None:
    """'l1'..'l3' from a normalised name (L1, phase_2, faza_3, phase_a …), else None."""
    for rx in _PHASE_RE:
        m = rx.search(text)
        if m:
            g = m.group(1)
            return "l" + (g if g.isdigit() else str("abc".index(g) + 1))
    return None


def kind_of(row: dict[str, Any]) -> str | None:
    """Measured quantity of an entity from its device class and unit."""
    dc = (row.get("device_class") or "").lower()
    unit = row.get("unit") or ""
    if dc in ("energy", "reactive_energy", "power", "reactive_power", "apparent_power",
              "voltage", "current", "power_factor", "frequency"):
        if dc == "energy" and unit in _SCALE["reactive_energy"]:
            return "reactive_energy"
        return dc
    if unit in _SCALE["energy"]:
        return "energy"
    if unit in _SCALE["reactive_energy"]:
        return "reactive_energy"
    if unit in _SCALE["power"]:
        return "power"
    if unit in _SCALE["reactive_power"]:
        return "reactive_power"
    if unit in _SCALE["apparent_power"]:
        return "apparent_power"
    if unit in ("V", "mV"):
        return "voltage"
    if unit in ("A", "mA"):
        return "current"
    if unit == "Hz":
        return "frequency"
    text = _norm(row.get("entity_id"), row.get("original_name"))
    if "power_factor" in text or "cos_phi" in text or "cosphi" in text:
        return "power_factor"
    return None


_EXPORT_WORDS = ("export", "returned", "_ret_", "oddan", "feed_in", "feedin", "eksport", "zwrot")
_IMPORT_WORDS = ("import", "consum", "pobor", "pobór", "pobran", "total_active_energy")
_NOT_METER = ("demand", "maximum", "_max_", "_min_", "thd", "resettable", "daily", "monthly",
              "today", "yesterday", "this_month", "average", "avg")


def classify_by_name(row: dict[str, Any]) -> tuple[str | None, str]:
    """Role from name, unit and device class (Modbus, other integrations). (role, reason)."""
    kind = kind_of(row)
    if kind is None:
        return None, ""
    text = "_" + _norm(row.get("entity_id"), row.get("original_name"), row.get("translation_key")) + "_"
    if any(w in text for w in _NOT_METER):
        return None, ""
    phase = phase_of(text)
    state_class = row.get("state_class") or ""
    is_export = any(w in text for w in _EXPORT_WORDS)
    is_import = any(w in text for w in _IMPORT_WORDS)
    if kind == "energy":
        if state_class not in ("total", "total_increasing") or phase:
            return None, ""
        if is_export:
            return "export_stat", "energia oddana (nazwa)"
        if is_import:
            # Eastron "Total kWh" is import + export — never taken as import
            return "energy_stat", "energia pobrana (nazwa)"
        return None, ""
    if kind == "reactive_energy":
        if state_class in ("total", "total_increasing") and is_import and not is_export and not phase:
            return "reactive_energy_stat", "energia bierna pobrana (nazwa)"
        return None, ""
    if kind in ("power", "voltage", "current", "power_factor") and is_export:
        return None, ""
    if kind == "power":
        return (f"power_{phase}", "moc fazy (nazwa)") if phase else ("power_entity", "moc całkowita (nazwa)")
    if kind in ("voltage", "current"):
        return (f"{kind}_{phase}", f"{kind} fazy (nazwa)") if phase else (None, "")
    if kind == "power_factor":
        return (f"pf_{phase}", "cos φ fazy (nazwa)") if phase else ("pf_entity", "cos φ (nazwa)")
    if kind == "reactive_power" and not phase:
        return "reactive_power_entity", "moc bierna (nazwa)"
    if kind == "apparent_power" and not phase:
        return "apparent_power_entity", "moc pozorna (nazwa)"
    if kind == "frequency":
        return "frequency_entity", "częstotliwość (nazwa)"
    return None, ""


def classify(row: dict[str, Any]) -> tuple[str | None, str, int]:
    """(role, reason, score) of one registry row; score 3 = exact unique_id, 1 = name."""
    uid = row.get("unique_id") or ""
    platform = row.get("platform") or ""
    if uid.startswith(OWN_UID_PREFIX):
        role = uid[len(OWN_UID_PREFIX):]
        if role in ROLES:
            return role, "szablon Smarting HOME", 3
    if platform == "shelly":
        m = _SHELLY_RPC.search(uid)
        if m:
            comp, cid, sub = m.group(1), m.group(2), m.group(3)
            table = {"em": _RPC_EM, "emdata": _RPC_EMDATA, "em1": _RPC_EM1, "em1data": _RPC_EM1DATA}[comp]
            role = table.get(sub)
            if role and (comp in ("em", "emdata") or cid == "0"):
                return role, f"Shelly {comp}:{cid} {sub}", 3
            return None, "", 0
        m = _SHELLY_BLOCK.search(uid)
        if m:
            return f"_block:{m.group(1)}:{m.group(2)}", "Shelly Gen1", 3
    role, reason = classify_by_name(row)
    return role, reason, (1 if role else 0)


def _block_roles(channels: dict[int, dict[str, str]]) -> dict[str, Any]:
    """Shelly Gen1 EM (2 channels, one voltage) / 3EM (3 channels = 3 phases)."""
    out: dict[str, Any] = {}
    if len(channels) >= 3:
        for ch, attrs in channels.items():
            if ch > 2:
                continue
            p = PHASES[ch]
            for attr, role in (("power", f"power_{p}"), ("voltage", f"voltage_{p}"),
                               ("current", f"current_{p}"), ("powerFactor", f"pf_{p}")):
                if attr in attrs:
                    out[role] = attrs[attr]
        for attr, role in (("energy", "import_stats"), ("energyReturned", "export_stats")):
            ids = [channels[c][attr] for c in sorted(channels) if c <= 2 and attr in channels[c]]
            if len(ids) == 3:
                out[role] = ids
    elif 0 in channels:
        attrs = channels[0]
        for attr, role in (("power", "power_entity"), ("voltage", "voltage_l1"), ("current", "current_l1"),
                           ("powerFactor", "pf_entity"), ("energy", "energy_stat"),
                           ("energyReturned", "export_stat")):
            if attr in attrs:
                out[role] = attrs[attr]
    return out


def _group_key(row: dict[str, Any]) -> str:
    """One meter = one Shelly MAC (phase sub-devices included), else one HA device."""
    uid = row.get("unique_id") or ""
    if row.get("platform") == "shelly" and "-" in uid:
        return "shelly:" + uid.split("-", 1)[0].lower()
    return row.get("device_id") or f"_{row.get('platform')}"


def propose_mapping(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Meter proposals per device from entity registry rows (sensor domain).

    rows: entity_id, platform, device_id, device_name, unique_id, original_name,
    translation_key, unit, device_class, state_class, disabled (bool).
    A role two entities of one device claim equally is left out (listed in
    "ambiguous") — the user picks it by hand rather than getting a guess.
    """
    devices: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if (row.get("entity_id") or "").startswith("sensor."):
            devices.setdefault(_group_key(row), []).append(row)
    proposals = []
    for dev_id, dev_rows in devices.items():
        best: dict[str, tuple[int, dict[str, Any], str]] = {}
        ambiguous: dict[str, list[str]] = {}
        channels: dict[int, dict[str, str]] = {}
        for row in dev_rows:
            role, reason, score = classify(row)
            if not role:
                continue
            if role.startswith("_block:"):
                _, ch, attr = role.split(":")
                channels.setdefault(int(ch), {})[attr] = row["entity_id"]
                continue
            cur = best.get(role)
            if cur is None or score > cur[0]:
                best[role] = (score, row, reason)
                ambiguous.pop(role, None)
            elif score == cur[0]:
                ambiguous.setdefault(role, [cur[1]["entity_id"]]).append(row["entity_id"])
        mapping: dict[str, Any] = {r: v[1]["entity_id"] for r, v in best.items() if r not in ambiguous}
        reasons = {r: v[2] for r, v in best.items() if r not in ambiguous}
        if channels:
            block = _block_roles(channels)
            mapping.update(block)
            reasons.update({r: "Shelly Gen1 (kanał licznika)" for r in block})
        if not mapping:
            continue
        by_id = {r["entity_id"]: r for r in dev_rows}
        flat = [e for v in mapping.values() for e in (v if isinstance(v, list) else [v])]
        disabled = sorted(e for e in flat if by_id.get(e, {}).get("disabled"))
        notes = []
        has_phases = any(f"power_{p}" in mapping or f"voltage_{p}" in mapping for p in PHASES)
        if channels and len(channels) >= 3 and "import_stats" in mapping:
            notes.append("Shelly 3EM (Gen1) nie ma licznika sumarycznego — import i eksport to suma "
                         "liczników trzech faz (bez bilansowania między fazami).")
        if "energy_stat" not in mapping and "import_stats" not in mapping:
            notes.append("Nie znaleziono licznika energii pobranej — wybierz statystykę kWh ręcznie.")
        if disabled:
            notes.append("Część encji jest wyłączona w Home Assistant (Shelly domyślnie wyłącza "
                         "napięcie i prąd) — włącz je, inaczej nie będzie odczytów ani statystyk.")
        if has_phases and not any(f"voltage_{p}" in mapping for p in PHASES):
            notes.append("Brak encji napięcia faz — raport napięciowy z tego licznika nie powstanie.")
        platform = dev_rows[0].get("platform") or ""
        # Newer HA puts Shelly phase entities on "Phase A/B/C" sub-devices: name and
        # id of the meter come from the shortest device name (the main device)
        main = min(dev_rows, key=lambda r: (len(r.get("device_name") or "~" * 99), r.get("device_name") or ""))
        exact = all(v[0] == 3 for r, v in best.items() if r in mapping) and not ambiguous
        proposals.append({
            "device_id": main.get("device_id") or "",
            "device_name": main.get("device_name") or platform,
            "platform": platform,
            "mapping": mapping,
            "reasons": reasons,
            "ambiguous": ambiguous,
            "disabled": disabled,
            "confidence": "exact" if exact else "name",
            "notes": notes,
        })
    # Most complete meters first
    proposals.sort(key=lambda p: (-len(p["mapping"]), p["device_name"]))
    return proposals


# ── tg φ (reactive power ratio) ─────────────────────────────────────────────
def tan_phi(p_w: float | None, q_var: float | None = None, s_va: float | None = None,
            pf: float | None = None) -> float | None:
    """tg φ = |Q| / P for drawn power (P > 0). From Q, else from S and P, else from cos φ.

    Only Q keeps the sign (inductive / capacitive); S and cos φ give the magnitude.
    """
    if p_w is not None and p_w <= 0:
        return None
    if p_w and q_var is not None:
        return abs(q_var) / p_w
    if p_w and s_va is not None and s_va >= p_w:
        return math.sqrt(s_va * s_va - p_w * p_w) / p_w
    if pf is not None:
        c = abs(pf)
        c = c / 100 if c > 1.5 else c  # some meters report %
        if 0 < c <= 1:
            return math.sqrt(1 - c * c) / c
    return None


def tan_phi_period(hours: list[tuple[datetime, float, float]], limit: float = TAN_PHI_LIMIT) -> dict[str, Any]:
    """tg φ over the hours with import, from (hour, P kWh, Q kvarh) — Q < 0 is capacitive.

    Inductive kvarh over active kWh is what the DSO compares with tg φ0; capacitive
    energy is listed separately (DSO tariffs usually bill it whatever its amount).
    """
    p_sum = q_ind = q_cap = 0.0
    n = over = cap_hours = 0
    for _t, p, q in hours:
        if p is None or q is None or p <= 0:
            continue
        n += 1
        p_sum += p
        if q >= 0:
            q_ind += q
            if q / p > limit:
                over += 1
        else:
            q_cap += -q
            cap_hours += 1
    return {
        "hours": n,
        "p_kwh": round(p_sum, 2),
        "q_ind_kvarh": round(q_ind, 2),
        "q_cap_kvarh": round(q_cap, 2),
        "tan_phi": round(q_ind / p_sum, 3) if p_sum > 0 else None,
        "hours_over": over,
        "cap_hours": cap_hours,
        "limit": limit,
    }


# ── Import / export ─────────────────────────────────────────────────────────
def sum_hours(series: list[list[tuple[datetime, float]]]) -> list[tuple[datetime, float]]:
    """Hour-by-hour sum of several kWh series (Shelly Gen1 3EM per-phase counters)."""
    if len(series) == 1:
        return list(series[0])
    acc: dict[datetime, float] = {}
    count: dict[datetime, int] = {}
    for s in series:
        for t, k in s:
            acc[t] = acc.get(t, 0.0) + k
            count[t] = count.get(t, 0) + 1
    # An hour is complete only when every counter reported it
    return [(t, acc[t]) for t in sorted(acc) if count[t] == len(series)]


def balance_hours(imp: list[tuple[datetime, float]], exp: list[tuple[datetime, float]]
                  ) -> tuple[list[tuple[datetime, float]], list[tuple[datetime, float]]]:
    """Hourly-balanced import and export (net of each hour), as net-billing settles it."""
    e = dict(exp)
    out_i, out_e = [], []
    for t, k in imp:
        net = k - e.get(t, 0.0)
        out_i.append((t, max(net, 0.0)))
        out_e.append((t, max(-net, 0.0)))
    return out_i, out_e


def exchange_month(imp: list[tuple[datetime, float]], exp: list[tuple[datetime, float]],
                   start: datetime, end: datetime) -> dict[str, Any]:
    """Import, export and balance of [start, end): raw and hourly-balanced."""
    i = [(t, k) for t, k in imp if start <= t < end]
    e = [(t, k) for t, k in exp if start <= t < end]
    bi, be = balance_hours(i, e)
    imp_kwh, exp_kwh = sum(k for _, k in i), sum(k for _, k in e)
    return {
        "import_kwh": round(imp_kwh, 2),
        "export_kwh": round(exp_kwh, 2),
        "net_kwh": round(imp_kwh - exp_kwh, 2),
        "balanced_import_kwh": round(sum(k for _, k in bi), 2),
        "balanced_export_kwh": round(sum(k for _, k in be), 2),
        "hours": len(i),
    }


# ── Phases ──────────────────────────────────────────────────────────────────
def unbalance_pct(values: list[float]) -> float | None:
    """Max deviation from the mean, % of the mean (as voltage_report's approximation)."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return None
    avg = sum(vals) / len(vals)
    return round(max(abs(v - avg) for v in vals) / abs(avg) * 100, 2) if avg else None


def phase_asymmetry(volts: dict[str, float | None], powers: dict[str, float | None]) -> dict[str, Any]:
    """Voltage unbalance and load spread between phases (live values)."""
    v = {p: x for p, x in volts.items() if x is not None and x > 100}
    w = {p: x for p, x in powers.items() if x is not None}
    out: dict[str, Any] = {"voltage_unbalance_pct": None, "voltage_spread_v": None,
                           "power_spread_w": None, "power_max_phase": None, "power_min_phase": None}
    if len(v) >= 2:
        out["voltage_unbalance_pct"] = unbalance_pct(list(v.values()))
        out["voltage_spread_v"] = round(max(v.values()) - min(v.values()), 1)
    if len(w) >= 2:
        out["power_spread_w"] = round(max(w.values()) - min(w.values()))
        out["power_max_phase"] = max(w, key=w.get)
        out["power_min_phase"] = min(w, key=w.get)
    return out


def day_extremes(rows: dict[str, list[tuple[float | None, float | None]]],
                 live: dict[str, float | None] | None = None) -> dict[str, dict[str, float | None]]:
    """Min / max of today per role from (min, max) statistic rows, widened by the live value."""
    out: dict[str, dict[str, float | None]] = {}
    for role, items in rows.items():
        lo = [x for x, _ in items if x is not None]
        hi = [x for _, x in items if x is not None]
        cur = (live or {}).get(role)
        if cur is not None:
            lo.append(cur)
            hi.append(cur)
        out[role] = {"min": round(min(lo), 1) if lo else None, "max": round(max(hi), 1) if hi else None}
    return out


# ── Voltage source (report, alert) ──────────────────────────────────────────
VOLTAGE_SOURCES = ("auto", "inverter", "meter")


def voltage_source(pref: str | None, grid_only: bool, inverter_ok: bool, meter_ok: bool) -> str | None:
    """'inverter', 'meter' or None. With an inverter it stays the default; the meter is
    the alternative (chosen explicitly) and the only source on a meter-only site."""
    if pref == "meter" and meter_ok:
        return "meter"
    if pref == "inverter" and inverter_ok and not grid_only:
        return "inverter"
    if not grid_only and inverter_ok:
        return "inverter"
    return "meter" if meter_ok else None


def meter_voltage_ids(cfg: dict[str, Any]) -> dict[str, str]:
    """{"L1": entity_id, …} of the meter's phase voltages that are set."""
    return {p.upper(): cfg.get(f"voltage_{p}") for p in PHASES if cfg.get(f"voltage_{p}")}


def grid_power_canonical(power_w: float | None, sign: str | None) -> float | None:
    """Meter power → project convention (+ export / − import)."""
    if power_w is None:
        return None
    return power_w if sign == "export_positive" else -power_w


def alert_overlay(
    data: dict[str, Any],
    cfg: dict[str, Any] | None,
    read: Callable[[str], tuple[float | None, str]],
    grid_only: bool,
    voltage_keys: tuple[str, str, str],
    grid_power_key: str,
    frequency_key: str,
) -> tuple[dict[str, Any], str | None]:
    """Coordinator data with the meter's voltages when the meter is the voltage source.

    Returns (data for the alert checks, source). The coordinator's own data is not
    touched — the inverter control (voltage guard, export limit) never sees these.
    read(entity_id) → (value, unit).
    """
    cfg = cfg or {}
    ids = meter_voltage_ids(cfg)
    meter_v = {p: read(eid)[0] for p, eid in ids.items()}
    meter_ok = any(v is not None and v > 100 for v in meter_v.values())
    inverter_ok = any((num(data.get(k)) or 0) > 100 for k in voltage_keys)
    src = voltage_source(cfg.get("voltage_source"), grid_only, inverter_ok, meter_ok)
    if src != "meter":
        return data, src
    out = dict(data)
    for phase, key in zip(("L1", "L2", "L3"), voltage_keys):
        out[key] = meter_v.get(phase)
    if num(out.get(grid_power_key)) is None and cfg.get("power_entity"):
        value, unit = read(cfg["power_entity"])
        out[grid_power_key] = grid_power_canonical(to_base(value, unit, "power"), cfg.get("power_sign"))
    if num(out.get(frequency_key)) is None and cfg.get("frequency_entity"):
        out[frequency_key] = read(cfg["frequency_entity"])[0]
    return out, src
