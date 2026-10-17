# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Installation showcase: signature, payload whitelist, privacy, plan, daily rows, voltage means.

Run: uvx --python 3.14 --with pytest pytest -q tests  (no Home Assistant needed)
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
sc = importlib.import_module("smartinghome_pure.showcase")

SECRET = "a" * 64
NOW = datetime(2026, 10, 17, 14, 7, tzinfo=timezone(timedelta(hours=2)))


def _payload(**kw):
    base = dict(
        config={"enabled": True, "url": sc.DEFAULT_URL, "title": "Dom autora",
                "description": "GoodWe GW8K-ET, magazyn 10,24 kWh, PV ok. 11 kWp, G13 Tauron",
                "location": "pod Oświęcimiem"},
        data={"pv_w": "3456.4", "load_w": 812, "grid_meter_w": 1500, "battery_inv_w": -1100,
              "soc": "63.6", "pv_today": "21.43", "load_today": 9.1, "import_today": 1.2,
              "export_today": 12.7},
        now=NOW, version="1.74.0",
    )
    base.update(kw)
    return sc.build_payload(**base)


# ── signature ──

def test_signature_is_hmac_sha256_of_timestamp_dot_body():
    body = b'{"a":1}'
    expected = hmac.new(SECRET.encode(), b"1792238820." + body, hashlib.sha256).hexdigest()
    assert sc.sign(SECRET, 1792238820, body) == expected


def test_signed_request_headers_match_body():
    body, headers = sc.signed_request(SECRET, {"schema": 1, "x": "zażółć"}, 1792238820)
    assert headers[sc.HEADER_TIMESTAMP] == "1792238820"
    assert headers[sc.HEADER_SIGNATURE] == "sha256=" + sc.sign(SECRET, 1792238820, body)
    assert json.loads(body) == {"schema": 1, "x": "zażółć"}
    assert "zażółć".encode() in body  # UTF-8, not \\u escapes


def test_verify_rejects_tampering_wrong_secret_and_old_timestamp():
    body, h = sc.signed_request(SECRET, {"schema": 1}, 1_000_000)
    sig = h[sc.HEADER_SIGNATURE]
    assert sc.verify(SECRET, 1_000_000, body, sig, now=1_000_100)
    assert not sc.verify(SECRET, 1_000_000, body + b" ", sig, now=1_000_100)
    assert not sc.verify("b" * 64, 1_000_000, body, sig, now=1_000_100)
    assert not sc.verify(SECRET, 1_000_000, body, sig, now=1_000_000 + 601)


def test_encode_is_deterministic():
    assert sc.encode({"b": 1, "a": [1, 2]}) == sc.encode({"a": [1, 2], "b": 1}) == b'{"a":[1,2],"b":1}'


# ── payload ──

def test_payload_signs_and_units():
    p = _payload()
    assert p["schema"] == 1 and p["integration_version"] == "1.74.0"
    live = p["live"]
    assert live["pv_w"] == 3456 and live["load_w"] == 812 and live["soc"] == 64
    assert live["grid_w"] == -1500        # meter + export → payload − export
    assert live["battery_w"] == 1100      # inverter − charge → payload + charge
    assert p["today"] == {"pv_kwh": 21.43, "load_kwh": 9.1, "import_kwh": 1.2, "export_kwh": 12.7}
    assert p["installation"]["location"] == "pod Oświęcimiem"
    assert p["installation"]["description"].startswith("GoodWe GW8K-ET, magazyn 10,24 kWh")
    assert "daily" not in p and "deposit" not in p


def test_payload_whitelist_drops_unknown_inputs():
    p = _payload(data={"pv_w": 100, "entity_id": "sensor.goodwe_pv", "serial": "9010KETU218W0123"},
                 config={"description": "x", "latitude": 50.03, "ppe": "590310600000000000"})
    blob = sc.encode(p).decode()
    for leaked in ("sensor.", "9010KETU", "50.03", "590310600", "latitude", "ppe", "entity_id"):
        assert leaked not in blob


def test_missing_values_are_null_not_zero():
    p = _payload(data={"pv_w": None, "soc": "unavailable"})
    assert p["live"]["pv_w"] is None and p["live"]["soc"] is None and p["live"]["grid_w"] is None
    assert p["today"]["pv_kwh"] is None


def test_clean_text_strips_identifiers():
    raw = ("Falownik SN 9010KETU218W0123 na 192.168.1.50:8123, sensor.goodwe_battery_power, "
           "mail jan@example.com, https://ha.example.com, 50.0345, 19.2098, PPE 590310600012345678 — GW8K-ET G13")
    out = sc.clean_text(raw, 300)
    for leaked in ("9010KETU", "192.168", "sensor.", "jan@", "https", "50.0345", "19.2098", "590310600"):
        assert leaked not in out
    assert "GW8K-ET" in out and "G13" in out


def test_clean_text_limits_length():
    assert len(sc.clean_text("x" * 500, 80)) == 80


def test_price_block_labels():
    p = _payload(price={"rce_mwh": 412.345, "rce_sell_kwh": 0.50718, "tariff": "g13",
                        "provider": "tauron", "zone": "afternoon_peak", "buy_kwh": 1.1234})
    assert p["price"] == {"rce_pln_mwh": 412.35, "rce_sell_pln_kwh": 0.5072, "tariff": "G13",
                          "provider": "Tauron", "zone": "afternoon_peak",
                          "zone_label": "szczyt popołudniowy", "buy_pln_kwh": 1.1234}


def test_autopilot_block_reason_and_plan():
    hours = [{"start": (NOW.replace(tzinfo=None) + timedelta(minutes=30 * i)).strftime("%Y-%m-%d %H:%M"),
              "zone": "off_peak", "buy": 0.63, "sell": 0.41, "action": "charge_grid" if i < 2 else "hold",
              "soc_start": 40 + i, "soc_end": 41.4 + i} for i in range(-3, 20)]
    p = _payload(autopilot={
        "enabled": True, "strategy": "max_profit", "strategy_label": "💰 Max Zysk",
        "action": "charge_grid",
        "actions": ["🛡️ Strażnik importu OK",
                    "💰 Arbitraż: ⚡ Ładuj z sieci 3000 W — strefa tania, zakup 0.63 / sprzedaż 0.41 zł/kWh, "
                    "SOC 40→60% (sensor.goodwe_battery_power)"],
        "plan_hours": hours,
    })
    ap = p["autopilot"]
    assert ap["action"] == "charge_grid" and ap["enabled"] is True
    assert ap["reason"].startswith("⚡ Ładuj z sieci 3000 W") and "sensor." not in ap["reason"]
    assert len(ap["plan"]) == sc.PLAN_SLOTS
    assert ap["plan"][0]["start"] == "2026-10-17T14:07"  # slots that already ended are skipped
    assert set(ap["plan"][0]) == {"start", "action", "zone", "buy", "sell", "soc"}
    assert ap["plan"][-1]["soc"] == round(41.4 + 11)


def test_plan_unknown_action_is_masked():
    rows = sc.plan_slots([{"start": "2026-10-17 15:00", "action": "turn_on_switch.boiler", "soc_end": 50}], NOW)
    assert rows[0]["action"] == "other"


def test_deposit_block_current_month():
    status = {"current_month": "2026-10", "balance_now": 120.5, "data_until": "2026-10-17T13:00:00",
              "months": [{"month": "2026-09", "deposit": 50},
                         {"month": "2026-10", "export_kwh": 210.4, "import_kwh": 80.2, "deposit": 61.234,
                          "deposit_used": 40, "energy_charge": 52.1}]}
    d = sc.deposit_block(status)
    assert d == {"month": "2026-10", "export_kwh": 210.4, "import_kwh": 80.2, "earned_pln": 61.23,
                 "used_pln": 40.0, "energy_charge_pln": 52.1, "balance_pln": 120.5,
                 "data_until": "2026-10-17T13:00"}
    assert sc.deposit_block({"error": "no_source"}) is None
    assert sc.deposit_block({"current_month": "2026-10", "months": []}) is None


def test_daily_rows_30_days_with_gaps_and_resets():
    today = date(2026, 10, 17)
    series = {
        "pv": [("2026-10-16", 30.5), ("2026-10-15", -1200.0), ("2026-09-17", 12.0)],
        "load": [("2026-10-16", 15.25)],
        "import": [("2026-10-16", 999.0)],  # counter jump → empty, not zero
        "export": [],
        "bogus": [("2026-10-16", 1.0)],
    }
    rows = sc.daily_rows(series, today)
    assert len(rows) == 30 and rows[0]["date"] == "2026-09-17" and rows[-1]["date"] == "2026-10-16"
    assert rows[-1] == {"date": "2026-10-16", "pv_kwh": 30.5, "load_kwh": 15.25,
                        "import_kwh": None, "export_kwh": None}
    assert rows[-2]["pv_kwh"] is None and rows[0]["pv_kwh"] == 12.0


def test_full_payload_fits_the_server_limit():
    daily = sc.daily_rows({k: [((date(2026, 10, 17) - timedelta(days=i)).isoformat(), 12.34) for i in range(1, 31)]
                           for k in ("pv", "load", "import", "export")}, date(2026, 10, 17))
    hours = [{"start": f"2026-10-17 {14 + i // 2:02d}:{30 * (i % 2):02d}", "action": "hold",
              "zone": "off_peak", "buy": 0.63, "sell": 0.41, "soc_end": 50} for i in range(12)]
    p = _payload(daily=daily, deposit={"month": "2026-10"}, voltages=[236.1, 238.0, None],
                 price={"rce_mwh": 400, "tariff": "g13", "zone": "off_peak"},
                 autopilot={"enabled": True, "action": "hold", "actions": ["x" * 1000], "plan_hours": hours})
    body = sc.encode(p)
    assert len(body) < sc.MAX_BODY_BYTES
    assert p["live"]["voltage_10min"] == [236.1, 238.0, None]


# ── voltages ──

def test_rolling_mean_window_and_invalid_samples():
    rm = sc.RollingMean(window_s=600)
    rm.add(0, (230, 240, 0))
    rm.add(300, (232, "unavailable", 0))
    assert rm.means(300) == [231.0, 240.0, None]
    rm.add(700, (250, 250, 250))  # first sample is now older than 10 min
    assert rm.means(700) == [241.0, 250.0, 250.0]
