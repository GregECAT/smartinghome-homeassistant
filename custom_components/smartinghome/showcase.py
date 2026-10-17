# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Installation showcase ("Wizytówka instalacji") — payload and signature.

Pure Python (no Home Assistant imports) so it can be unit-tested; the Home
Assistant side (timer, data sources, HTTP) is showcase_service.py.

Opt-in, off by default. Every 5 minutes the integration POSTs a small JSON with
aggregated numbers only to the configured URL (smartinghome.pl by default), where
a page shows the installation live. Never included: entity ids, device / serial
numbers, HA instance id, IP, coordinates, address, PPE. The location is the free
text the owner typed in the settings (e.g. "pod Oświęcimiem").

Wire format (schema 1):
  POST <url>, Content-Type: application/json, body = UTF-8 JSON (exact bytes signed)
  X-SH-Timestamp: <unix seconds>
  X-SH-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<body>")>

Sign conventions in the payload (independent of the inverter's own):
  grid_w     + import from the grid / − export to the grid
  battery_w  + charging / − discharging
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

SCHEMA = 1
DEFAULT_URL = "https://smartinghome.pl/wp-json/smarting/v1/showcase"
SEND_INTERVAL_S = 300          # live data every 5 minutes
DAILY_INTERVAL_S = 3600        # 30-day aggregates once an hour
DAILY_DAYS = 30
PLAN_SLOTS = 12                # 12 × 30 min = the next 6 hours
VOLTAGE_WINDOW_S = 600         # phase voltages: 10-minute means
MAX_BODY_BYTES = 32_000        # the server rejects anything bigger
DESCRIPTION_MAX = 300
LOCATION_MAX = 80
TITLE_MAX = 80
REASON_MAX = 240

HEADER_TIMESTAMP = "X-SH-Timestamp"
HEADER_SIGNATURE = "X-SH-Signature"

ACTIONS = ("charge_grid", "pv_charge", "pv_export", "hold", "home", "discharge")

ZONE_LABELS = {
    "off_peak": "pozaszczytowa",
    "morning_peak": "szczyt przedpołudniowy",
    "afternoon_peak": "szczyt popołudniowy",
    "peak": "szczytowa",
    "flat": "całodobowa",
    "dynamic": "dynamiczna",
    "dynamic_now": "dynamiczna (cena bieżąca)",  # past the published day-ahead prices
}

TARIFF_LABELS = {"g11": "G11", "g12": "G12", "g12w": "G12w", "g12n": "G12n", "g13": "G13",
                 "dynamic": "dynamiczna"}
PROVIDER_LABELS = {"tauron": "Tauron", "pge": "PGE"}

# Never let these out, even inside free text typed by the owner or a log line
_ENTITY_RE = re.compile(
    r"\b(?:sensor|switch|number|select|binary_sensor|input_[a-z_]+|automation|script|"
    r"button|climate|light|water_heater|device_tracker|person|zone|camera|update)\.[a-z0-9_]+\b"
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
# Serial / PPE-like tokens: 10+ characters of digits, or 8+ of upper-case letters
# mixed with at least 3 digits (e.g. 9010KETU218W0123). "GW8K-ET" or "G13" stay.
_SERIAL_RE = re.compile(r"\b(?:\d{10,}|(?=[A-Z0-9]*\d[A-Z0-9]*\d[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,})\b")
_COORD_RE = re.compile(r"\b-?\d{1,3}[.,]\d{4,}\b")
_WS_RE = re.compile(r"\s+")


# ─────────────────────────────────────────────────────────────────────────────
#  Signature
# ─────────────────────────────────────────────────────────────────────────────

def sign(secret: str, timestamp: int, body: bytes) -> str:
    """Hex HMAC-SHA256 of "<timestamp>.<body>" with the shared secret."""
    msg = str(int(timestamp)).encode() + b"." + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def signed_request(secret: str, payload: dict[str, Any], timestamp: int) -> tuple[bytes, dict[str, str]]:
    """(body bytes, headers) ready to POST. The body is the exact signed byte string."""
    body = encode(payload)
    return body, {
        "Content-Type": "application/json; charset=utf-8",
        HEADER_TIMESTAMP: str(int(timestamp)),
        HEADER_SIGNATURE: "sha256=" + sign(secret, timestamp, body),
    }


def verify(secret: str, timestamp: int, body: bytes, signature: str, now: int,
           window_s: int = 600) -> bool:
    """Server-side check (mirrors the PHP endpoint) — used by the tests."""
    if abs(int(now) - int(timestamp)) > window_s:
        return False
    sig = signature[7:] if signature.startswith("sha256=") else signature
    return hmac.compare_digest(sign(secret, timestamp, body), sig)


def encode(payload: dict[str, Any]) -> bytes:
    """Compact, deterministic JSON (UTF-8, Polish letters kept)."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


# ─────────────────────────────────────────────────────────────────────────────
#  Sanitising
# ─────────────────────────────────────────────────────────────────────────────

def clean_text(value: Any, limit: int) -> str:
    """Free text safe to publish: no entity ids, IPs, e-mails, URLs, serials, coordinates."""
    text = str(value or "")
    for rx in (_URL_RE, _EMAIL_RE, _ENTITY_RE, _IPV6_RE, _IPV4_RE, _COORD_RE, _SERIAL_RE):
        text = rx.sub("", text)
    text = "".join(ch for ch in text if ch.isprintable())
    text = _WS_RE.sub(" ", text).strip(" ,;·-—")
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _num(value: Any, digits: int = 0) -> float | int | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return int(round(f)) if digits == 0 else round(f, digits)


def _nonneg(value: Any) -> int | None:
    f = _num(value)
    return max(int(f), 0) if f is not None else None


def _kwh(value: Any) -> float | None:
    f = _num(value, 2)
    return f if f is not None and 0 <= f < 10_000 else None


# ─────────────────────────────────────────────────────────────────────────────
#  Voltage means
# ─────────────────────────────────────────────────────────────────────────────

class RollingMean:
    """Per-phase mean over the last `window_s` seconds (samples fed by the coordinator)."""

    def __init__(self, window_s: float = VOLTAGE_WINDOW_S, phases: int = 3) -> None:
        self.window_s = window_s
        self.phases = phases
        self._samples: list[tuple[float, tuple[float | None, ...]]] = []

    def add(self, t: float, values: Iterable[Any]) -> None:
        vals = []
        for v in list(values)[: self.phases]:
            f = _num(v, 1)
            vals.append(f if f is not None and 150 < f < 300 else None)  # 0 V / unknown = no sample
        self._samples.append((t, tuple(vals)))
        self._trim(t)

    def _trim(self, now: float) -> None:
        while self._samples and now - self._samples[0][0] > self.window_s:
            self._samples.pop(0)

    def means(self, now: float) -> list[float | None]:
        self._trim(now)
        out: list[float | None] = []
        for i in range(self.phases):
            vals = [s[1][i] for s in self._samples if i < len(s[1]) and s[1][i] is not None]
            out.append(round(sum(vals) / len(vals), 1) if vals else None)
        return out


# ─────────────────────────────────────────────────────────────────────────────
#  Payload pieces
# ─────────────────────────────────────────────────────────────────────────────

def plan_slots(hours: list[dict[str, Any]] | None, now: datetime, n: int = PLAN_SLOTS) -> list[dict[str, Any]]:
    """Next n plan slots (action, buy/sell price, SOC at the end) from the arbitrage plan."""
    out: list[dict[str, Any]] = []
    cutoff = now.replace(tzinfo=None) - timedelta(minutes=30)
    for h in hours or []:
        try:
            start = datetime.strptime(str(h.get("start"))[:16], "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            continue
        if start <= cutoff:
            continue  # stale slot from an older plan
        action = str(h.get("action") or "")
        out.append({
            "start": start.strftime("%Y-%m-%dT%H:%M"),
            "action": action if action in ACTIONS else "other",
            "zone": str(h.get("zone") or "") if str(h.get("zone") or "") in ZONE_LABELS else "",
            "buy": _num(h.get("buy"), 3),
            "sell": _num(h.get("sell"), 3),
            "soc": _num(h.get("soc_end"), 0),
        })
        if len(out) >= n:
            break
    return out


def short_reason(actions: list[Any] | None, note: Any = "") -> str:
    """One sentence of why: the planner's line if there is one, else the first action."""
    lines = [str(a) for a in actions or [] if a]
    pick = next((a for a in lines if "Arbitraż" in a), lines[0] if lines else str(note or ""))
    pick = pick.replace("💰 Arbitraż:", "").strip()
    return clean_text(pick, REASON_MAX)


def deposit_block(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Current month of the prosumer deposit (prosumer_deposit.settle via deposit_service)."""
    if not status or status.get("error"):
        return None
    month = str(status.get("current_month") or "")
    row = next((m for m in status.get("months") or [] if m.get("month") == month), None)
    if row is None:
        return None
    return {
        "month": month,
        "export_kwh": _kwh(row.get("export_kwh")),
        "import_kwh": _kwh(row.get("import_kwh")),
        "earned_pln": _num(row.get("deposit"), 2),
        "used_pln": _num(row.get("deposit_used"), 2),
        "energy_charge_pln": _num(row.get("energy_charge"), 2),
        "balance_pln": _num(status.get("balance_now"), 2),
        "data_until": str(status.get("data_until") or "")[:16] or None,
    }


def daily_rows(
    series: dict[str, list[tuple[str, float]]], today: date, days: int = DAILY_DAYS,
) -> list[dict[str, Any]]:
    """Per-day kWh for the `days` complete days before today.

    series: {"pv"|"load"|"import"|"export": [(YYYY-MM-DD, kWh change of that day)]}
    (recorder long-term statistics, period "day"). Counter resets and gaps give
    negative or huge changes — those days are left empty (None), not zero.
    """
    caps = {"pv": 200.0, "load": 300.0, "import": 300.0, "export": 300.0}
    by_key: dict[str, dict[str, float]] = {}
    for key, rows in series.items():
        if key not in caps:
            continue
        m = by_key.setdefault(key, {})
        for day, change in rows or []:
            f = _num(change, 2)
            if f is not None and 0 <= f < caps[key]:
                m[str(day)[:10]] = m.get(str(day)[:10], 0.0) + f
    out = []
    for i in range(days, 0, -1):
        d = (today - timedelta(days=i)).isoformat()
        row = {"date": d}
        for key in caps:
            v = by_key.get(key, {}).get(d)
            row[f"{key}_kwh"] = round(v, 2) if v is not None else None
        out.append(row)
    return out


def build_payload(
    *,
    config: dict[str, Any],
    data: dict[str, Any],
    now: datetime,
    version: str,
    voltages: list[float | None] | None = None,
    autopilot: dict[str, Any] | None = None,
    price: dict[str, Any] | None = None,
    deposit: dict[str, Any] | None = None,
    daily: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The JSON sent to the showcase endpoint.

    data: already-resolved live numbers (see showcase_service._live_numbers):
      pv_w, load_w, grid_meter_w (+ export / − import), battery_inv_w (+ discharge / − charge),
      soc, pv_today, load_today, import_today, export_today.
    autopilot: {enabled, strategy, strategy_label, action, actions, note, plan_hours}
    price: {rce_mwh, rce_sell_kwh, tariff, provider, zone, buy_kwh}
    Only whitelisted, aggregated fields are copied — nothing else from the inputs.
    """
    meter = _num(data.get("grid_meter_w"))
    bat = _num(data.get("battery_inv_w"))
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "integration_version": clean_text(version, 20),
        "installation": {
            "title": clean_text(config.get("title"), TITLE_MAX),
            "description": clean_text(config.get("description"), DESCRIPTION_MAX),
            "location": clean_text(config.get("location"), LOCATION_MAX),
        },
        "live": {
            "pv_w": _nonneg(data.get("pv_w")),
            "load_w": _nonneg(data.get("load_w")),
            "grid_w": -meter if meter is not None else None,
            "battery_w": -bat if bat is not None else None,
            "soc": _num(data.get("soc"), 0),
            "voltage_10min": [v for v in (voltages or [])][:3],
        },
        "today": {
            "pv_kwh": _kwh(data.get("pv_today")),
            "load_kwh": _kwh(data.get("load_today")),
            "import_kwh": _kwh(data.get("import_today")),
            "export_kwh": _kwh(data.get("export_today")),
        },
    }
    if price:
        tariff = str(price.get("tariff") or "").lower()
        zone = str(price.get("zone") or "")
        payload["price"] = {
            "rce_pln_mwh": _num(price.get("rce_mwh"), 2),
            "rce_sell_pln_kwh": _num(price.get("rce_sell_kwh"), 4),
            "tariff": TARIFF_LABELS.get(tariff, ""),
            "provider": PROVIDER_LABELS.get(str(price.get("provider") or "").lower(), ""),
            "zone": zone if zone in ZONE_LABELS else "",
            "zone_label": ZONE_LABELS.get(zone, ""),
            "buy_pln_kwh": _num(price.get("buy_kwh"), 4),
        }
    if autopilot is not None:
        action = str(autopilot.get("action") or "")
        payload["autopilot"] = {
            "enabled": bool(autopilot.get("enabled")),
            "strategy": clean_text(autopilot.get("strategy"), 40),
            "strategy_label": clean_text(autopilot.get("strategy_label"), 60),
            "action": action if action in ACTIONS else ("other" if action else ""),
            "reason": short_reason(autopilot.get("actions"), autopilot.get("note")),
            "plan": plan_slots(autopilot.get("plan_hours"), now),
        }
    if deposit:
        payload["deposit"] = deposit
    if daily is not None:
        payload["daily"] = daily
    return payload


def preview(payload: dict[str, Any]) -> str:
    """Pretty JSON for the settings card ("what exactly leaves the house")."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
