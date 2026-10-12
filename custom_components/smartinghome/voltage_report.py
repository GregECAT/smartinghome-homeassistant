# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Grid voltage report — evidence for a complaint to the distribution operator.

Pure Python. Input: recorder statistics of the three phase voltages (hourly
mean/max for the long history, 5-minute means for the recent days) and the
hourly mean grid power (+ export). Output: per-day figures, EN 50160 weekly
check (95 % of 10-minute means within 230 V ± 10 %), and hours with a high
voltage while the house was not exporting (the grid itself is too high).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

UN = 230.0
LOW = UN * 0.9     # 207 V
HIGH = UN * 1.1    # 253 V


def ten_minute_means(rows: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
    """Aligned 10-minute means (hh:00, hh:10 …) from 5-minute means."""
    by_slot: dict[datetime, list[float]] = {}
    for start, mean in rows:
        slot = start.replace(minute=start.minute // 10 * 10, second=0, microsecond=0)
        by_slot.setdefault(slot, []).append(mean)
    return [(t, sum(v) / len(v)) for t, v in sorted(by_slot.items()) if len(v) == 2]


def build_report(
    hourly: dict[str, list[tuple[datetime, float | None, float | None]]],
    five_min: dict[str, list[tuple[datetime, float]]],
    grid_hourly: dict[datetime, float],
    limit_v: float = HIGH,
) -> dict[str, Any]:
    """hourly/five_min keyed by phase ("L1".."L3"), local naive timestamps (period start)."""
    days: dict[str, dict[str, Any]] = {}

    def day(t: datetime) -> dict[str, Any]:
        return days.setdefault(t.date().isoformat(), {
            "date": t.date().isoformat(), "max_v": 0.0, "max_hour_mean": 0.0,
            "hours_max_over": 0, "hours_mean_over": 0, "hours_high_no_export": 0,
            "ten_min_over": None, "ten_min_total": None,
        })

    hour_over: dict[datetime, set[str]] = {}
    hour_mean_over: dict[datetime, set[str]] = {}
    no_export_high: list[dict[str, Any]] = []
    for phase, rows in hourly.items():
        for start, mean, mx in rows:
            d = day(start)
            if mx is not None:
                d["max_v"] = max(d["max_v"], round(mx, 1))
                if mx > limit_v:
                    hour_over.setdefault(start, set()).add(phase)
            if mean is not None:
                d["max_hour_mean"] = max(d["max_hour_mean"], round(mean, 1))
                if mean > limit_v:
                    hour_mean_over.setdefault(start, set()).add(phase)
                grid = grid_hourly.get(start)
                # High voltage while the house takes energy (or exports almost nothing)
                if mean >= limit_v - 3 and grid is not None and grid <= 100:
                    no_export_high.append({"hour": start.isoformat(timespec="minutes"), "phase": phase,
                                           "mean_v": round(mean, 1), "grid_w": round(grid)})
    for start in hour_over:
        day(start)["hours_max_over"] += 1
    for start in hour_mean_over:
        day(start)["hours_mean_over"] += 1
    for item in no_export_high:
        day(datetime.fromisoformat(item["hour"]))["hours_high_no_export"] += 1

    # 10-minute means (recent days): count intervals above the limit / outside ±10 %
    weeks: dict[str, dict[str, int]] = {}
    worst_10: list[dict[str, Any]] = []
    for phase, rows in five_min.items():
        for t, m in ten_minute_means(rows):
            d = day(t)
            d["ten_min_total"] = (d["ten_min_total"] or 0) + 1
            d["ten_min_over"] = (d["ten_min_over"] or 0) + (1 if m > limit_v else 0)
            iso = t.isocalendar()
            w = weeks.setdefault(f"{iso[0]}-W{iso[1]:02d}", {"total": 0, "outside": 0})
            w["total"] += 1
            if not LOW <= m <= HIGH:
                w["outside"] += 1
            if m > limit_v:
                worst_10.append({"start": t.isoformat(timespec="minutes"), "phase": phase,
                                 "mean_v": round(m, 1), "grid_w": round(grid_hourly.get(
                                     t.replace(minute=0), 0))})
    week_rows = [
        {"week": k, "intervals": v["total"], "outside": v["outside"],
         "within_pct": round(100 * (v["total"] - v["outside"]) / v["total"], 2) if v["total"] else None,
         "ok": v["total"] > 0 and (v["total"] - v["outside"]) / v["total"] >= 0.95}
        for k, v in sorted(weeks.items())
    ]
    day_rows = sorted(days.values(), key=lambda d: d["date"])
    worst_10.sort(key=lambda x: -x["mean_v"])
    return {
        "limit_v": limit_v,
        "phases": phase_balance(hourly, five_min, grid_hourly),
        "days": day_rows,
        "weeks": week_rows,
        "worst_10min": worst_10[:50],
        "high_without_export": sorted(no_export_high, key=lambda x: -x["mean_v"])[:50],
        "summary": {
            "days": len(day_rows),
            "days_over": sum(1 for d in day_rows if d["hours_max_over"] or (d["ten_min_over"] or 0)),
            "max_v": max((d["max_v"] for d in day_rows), default=None),
            "hours_max_over": sum(d["hours_max_over"] for d in day_rows),
            "hours_mean_over": sum(d["hours_mean_over"] for d in day_rows),
            "ten_min_over": sum(d["ten_min_over"] or 0 for d in day_rows),
            "hours_high_no_export": len(no_export_high),
            "first_day": day_rows[0]["date"] if day_rows else None,
        },
    }


UNBALANCE_PCT = 2.0   # PN-EN 50160: negative-sequence unbalance ≤ 2 % for 95 % of 10-min means / week
SPREAD_V = 5.0        # hours with the phases this far apart are listed


def _lvur(vals: list[float]) -> float:
    """Voltage unbalance from the three magnitudes (NEMA: max deviation from the mean, %).

    The inverter gives no phase angles, so the EN 50160 negative-sequence factor can't be
    computed exactly; for a low-voltage grid this is the usual close approximation.
    """
    avg = sum(vals) / len(vals)
    return max(abs(v - avg) for v in vals) / avg * 100 if avg else 0.0


def phase_balance(
    hourly: dict[str, list[tuple[datetime, float | None, float | None]]],
    five_min: dict[str, list[tuple[datetime, float]]],
    grid_hourly: dict[datetime, float],
) -> dict[str, Any]:
    """Phase-to-phase differences: per day, which phase is high, with / without export."""
    phases = sorted(hourly)
    by_hour: dict[datetime, dict[str, float]] = {}
    for ph in phases:
        for start, mean, _mx in hourly[ph]:
            if mean is not None:
                by_hour.setdefault(start, {})[ph] = mean
    hours = [(t, v) for t, v in sorted(by_hour.items()) if len(v) == len(phases) == 3]
    days: dict[str, dict[str, Any]] = {}
    highest = {ph: 0 for ph in phases}
    spread_export: list[float] = []
    spread_idle: list[float] = []
    top: list[dict[str, Any]] = []
    for t, v in hours:
        vals = [v[ph] for ph in phases]
        spread = max(vals) - min(vals)
        hi = max(phases, key=lambda ph: v[ph])
        lo = min(phases, key=lambda ph: v[ph])
        highest[hi] += 1
        grid = grid_hourly.get(t)
        exporting = grid is not None and grid > 300
        (spread_export if exporting else spread_idle).append(spread)
        d = days.setdefault(t.date().isoformat(), {
            "date": t.date().isoformat(), "hours": 0, "max_spread": 0.0, "sum_spread": 0.0,
            "hours_spread_over": 0, "max_unbalance_pct": 0.0, "high": {ph: 0 for ph in phases},
        })
        d["hours"] += 1
        d["sum_spread"] += spread
        d["max_spread"] = max(d["max_spread"], round(spread, 1))
        d["max_unbalance_pct"] = max(d["max_unbalance_pct"], round(_lvur(vals), 2))
        d["high"][hi] += 1
        if spread >= SPREAD_V:
            d["hours_spread_over"] += 1
            top.append({"hour": t.isoformat(timespec="minutes"), "spread_v": round(spread, 1),
                        "high": hi, "low": lo, **{ph: round(v[ph], 1) for ph in phases},
                        "grid_w": round(grid) if grid is not None else None})
    day_rows = []
    for d in sorted(days.values(), key=lambda x: x["date"]):
        d["avg_spread"] = round(d.pop("sum_spread") / d["hours"], 1)
        d["mostly_high"] = max(d["high"], key=d["high"].get)
        day_rows.append(d)

    # 10-minute means (recent days): EN 50160-style weekly check of the unbalance
    tens: dict[datetime, dict[str, float]] = {}
    for ph, rows in five_min.items():
        for t, m in ten_minute_means(rows):
            tens.setdefault(t, {})[ph] = m
    weeks: dict[str, dict[str, int]] = {}
    for t, v in tens.items():
        if len(v) != 3:
            continue
        iso = t.isocalendar()
        w = weeks.setdefault(f"{iso[0]}-W{iso[1]:02d}", {"total": 0, "over": 0})
        w["total"] += 1
        if _lvur(list(v.values())) > UNBALANCE_PCT:
            w["over"] += 1
    week_rows = [
        {"week": k, "intervals": w["total"], "over": w["over"],
         "within_pct": round(100 * (w["total"] - w["over"]) / w["total"], 2),
         "ok": (w["total"] - w["over"]) / w["total"] >= 0.95}
        for k, w in sorted(weeks.items()) if w["total"]
    ]

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 1) if xs else None

    n = len(hours)
    top.sort(key=lambda x: -x["spread_v"])
    return {
        "days": day_rows,
        "weeks": week_rows,
        "top_hours": top[:50],
        "summary": {
            "hours": n,
            "avg_spread": avg([s for s in spread_export + spread_idle]),
            "avg_spread_export": avg(spread_export),
            "avg_spread_no_export": avg(spread_idle),
            "max_spread": max((d["max_spread"] for d in day_rows), default=None),
            "max_unbalance_pct": max((d["max_unbalance_pct"] for d in day_rows), default=None),
            "hours_spread_over": sum(d["hours_spread_over"] for d in day_rows),
            "highest_share": {ph: round(100 * c / n, 1) for ph, c in highest.items()} if n else {},
            "spread_limit_v": SPREAD_V,
            "unbalance_limit_pct": UNBALANCE_PCT,
        },
    }


def window_start(now: datetime, days: int) -> datetime:
    return (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


# ── Export for a complaint (PRO: const.FEATURE_VOLTAGE_REPORT_EXPORT) ─────────
# Detail lists only the export uses — the panel's basic view never needs them.
EXPORT_ONLY_KEYS = ("worst_10min", "high_without_export")
EXPORT_ONLY_PHASE_KEYS = ("top_hours",)


def basic_view(report: dict[str, Any]) -> dict[str, Any]:
    """The report without the detail lists that only the CSV export uses (FREE)."""
    out = {k: v for k, v in report.items() if k not in EXPORT_ONLY_KEYS}
    if isinstance(report.get("phases"), dict):
        out["phases"] = {k: v for k, v in report["phases"].items() if k not in EXPORT_ONLY_PHASE_KEYS}
    return out


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def export_csv(report: dict[str, Any]) -> str:
    """Measurements for the complaint attachment (semicolon CSV, decimal comma)."""
    lines = ["dzien;max_V;max_srednia_godz_V;godziny_powyzej;srednie_10min_powyzej;wysokie_bez_eksportu"]

    def row(*vals: Any) -> None:
        lines.append(";".join(_cell(v) for v in vals))

    for d in report.get("days") or []:
        row(d.get("date"), d.get("max_v"), d.get("max_hour_mean"), d.get("hours_max_over"),
            d.get("ten_min_over"), d.get("hours_high_no_export"))
    lines += ["", "poczatek_10min;faza;srednia_V;moc_sieci_W"]
    for w in report.get("worst_10min") or []:
        row(w.get("start"), w.get("phase"), w.get("mean_v"), w.get("grid_w"))
    lines += ["", "godzina;faza;srednia_V;moc_sieci_W (bez eksportu)"]
    for h in report.get("high_without_export") or []:
        row(h.get("hour"), h.get("phase"), h.get("mean_v"), h.get("grid_w"))
    pb = report.get("phases") or {}
    lines += ["", "dzien;srednia_roznica_faz_V;maks_roznica_V;godziny_roznicy_ponad_5V;maks_asymetria_pct;najwyzsza_faza"]
    for d in pb.get("days") or []:
        row(d.get("date"), d.get("avg_spread"), d.get("max_spread"), d.get("hours_spread_over"),
            d.get("max_unbalance_pct"), d.get("mostly_high"))
    lines += ["", "godzina;roznica_V;L1_V;L2_V;L3_V;najwyzsza;najnizsza;moc_sieci_W"]
    for h in pb.get("top_hours") or []:
        row(h.get("hour"), h.get("spread_v"), h.get("L1"), h.get("L2"), h.get("L3"),
            h.get("high"), h.get("low"), h.get("grid_w"))
    return "\n".join(lines).replace(".", ",")


def _fmt(x: Any, digits: int = 1) -> str:
    return "—" if x is None else f"{float(x):.{digits}f}".replace(".", ",")


def complaint_text(report: dict[str, Any], today: str) -> str:
    """Complaint to the distribution operator (Polish), EN 50160 figures from the report."""
    s = report.get("summary") or {}
    lim = report.get("limit_v") or HIGH
    lim_s = _cell(lim)
    worst = sorted((d for d in report.get("days") or [] if d.get("hours_max_over") or d.get("ten_min_over")),
                   key=lambda d: -(d.get("max_v") or 0))[:10]
    pb = report.get("phases") or {}
    ps = pb.get("summary") or {}
    share = sorted((ps.get("highest_share") or {}).items(), key=lambda kv: -kv[1])
    bad_weeks = [w["week"] for w in pb.get("weeks") or [] if not w.get("ok")]
    phase_text = ""
    if ps.get("hours"):
        phase_text = (
            f"\nAsymetria napięć między fazami ({ps['hours']} godzin pomiarów):\n"
            f"- średnia różnica między najwyższą a najniższą fazą: {_fmt(ps.get('avg_spread'))} V "
            f"(maksymalnie {_fmt(ps.get('max_spread'))} V),\n"
            f"- gdy instalacja nie oddaje energii do sieci: {_fmt(ps.get('avg_spread_no_export'))} V, "
            f"przy oddawaniu: {_fmt(ps.get('avg_spread_export'))} V,\n"
            f"- najwyższe napięcie najczęściej na fazie {share[0][0] if share else '—'} "
            f"({_fmt(share[0][1]) if share else '—'}% godzin),\n"
            f"- godzin z różnicą faz co najmniej {_fmt(ps.get('spread_limit_v'), 0)} V: {ps.get('hours_spread_over') or 0},\n"
            f"- największa asymetria napięć: {_fmt(ps.get('max_unbalance_pct'), 2)}% "
            f"(dopuszczalne wg PN-EN 50160: 2%)"
            + (f"; tygodnie poza normą: {', '.join(bad_weeks)}" if bad_weeks else "") + ".\n"
            "Falownik oddaje moc symetrycznie na trzy fazy, więc trwała różnica napięć — także bez "
            "oddawania energii — wskazuje na nierównomierne obciążenie sieci lub stan przewodu neutralnego.\n"
        )
    worst_lines = "\n".join(
        f"- {d['date']}: maks. {_fmt(d.get('max_v'))} V, godzin powyżej {lim_s} V: {d.get('hours_max_over') or 0}"
        for d in worst
    ) or "- brak"
    max_v = s.get("max_v")
    return (
        "Reklamacja parametrów jakościowych energii elektrycznej — za wysokie napięcie w sieci\n\n"
        f"Zgłaszam, że w moim punkcie poboru napięcie w sieci przekracza wartość dopuszczalną 230 V + 10% ({lim_s} V).\n"
        "Pomiary pochodzą z falownika instalacji fotowoltaicznej (pomiar ciągły, wszystkie trzy fazy).\n\n"
        f"Okres: {s.get('first_day') or '—'} – {today}\n"
        f"- najwyższe zmierzone napięcie: {_fmt(max_v) if max_v is not None else '—'} V,\n"
        f"- dni z napięciem powyżej {lim_s} V: {s.get('days_over') or 0},\n"
        f"- godziny z napięciem powyżej {lim_s} V: {s.get('hours_max_over') or 0} "
        f"(w tym ze średnią godzinową powyżej {lim_s} V: {s.get('hours_mean_over') or 0}),\n"
        f"- średnie 10-minutowe powyżej {lim_s} V w ostatnich 10 dniach: {s.get('ten_min_over') or 0},\n"
        f"- godziny z napięciem co najmniej {_cell(lim - 3)} V, gdy instalacja nie oddawała energii do sieci: "
        f"{s.get('hours_high_no_export') or 0}.\n\n"
        "Dni z najwyższym napięciem:\n"
        f"{worst_lines}\n"
        f"{phase_text}\n"
        f"Przy średniej 10-minutowej powyżej {lim_s} V falownik zgodnie z normą PN-EN 50549-1 odłącza się od sieci, "
        "przez co tracę produkcję energii.\n"
        "Napięcie jest wysokie również w godzinach, w których instalacja nie oddaje energii, co wskazuje na "
        "ustawienia sieci (np. zaczepy transformatora SN/nN).\n\n"
        "Proszę o:\n"
        "1. wykonanie pomiarów jakości napięcia w moim punkcie poboru zgodnie z PN-EN 50160,\n"
        "2. doprowadzenie napięcia do wartości dopuszczalnych"
        + (", sprawdzenie rozkładu obciążenia na fazy i ciągłości przewodu neutralnego" if ps.get("hours") else "")
        + ",\n"
        "3. udzielenie bonifikaty za niedotrzymanie parametrów jakościowych energii, jeśli przysługuje.\n\n"
        "W załączniku: zestawienie pomiarów (CSV)."
    )
