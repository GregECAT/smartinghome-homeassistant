"""Independent energy meter: auto-detection, tg φ, voltage source, import / export.

Run: python3 -m pytest tests/  (Python ≥ 3.11, no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
md = importlib.import_module("smartinghome_pure.meter_device")
vr = importlib.import_module("smartinghome_pure.voltage_report")

MAC = "AABBCCDDEEFF"


def _shelly(sub: str, comp: str = "em:0", *, unit="W", dc="power", sc="measurement",
            device="dev-main", disabled=False, eid=None) -> dict:
    return {
        "entity_id": eid or f"sensor.shellypro3em_{sub}",
        "platform": "shelly",
        "device_id": device,
        "device_name": "Shelly Pro 3EM" if device == "dev-main" else f"Shelly Pro 3EM {device}",
        "unique_id": f"{MAC}-{comp}-{sub}",
        "original_name": None,
        "translation_key": None,
        "unit": unit,
        "device_class": dc,
        "state_class": sc,
        "disabled": disabled,
    }


def _pro3em_rows() -> list[dict]:
    rows = [
        _shelly("total_act_power"),
        _shelly("total_aprt_power", unit="VA", dc="apparent_power"),
        _shelly("total_act", "emdata:0", unit="kWh", dc="energy", sc="total_increasing"),
        _shelly("total_act_ret", "emdata:0", unit="kWh", dc="energy", sc="total_increasing"),
        _shelly("a_freq", unit="Hz", dc="frequency", device="Phase A"),
        # per-phase totals must not be taken as the meter's import
        _shelly("a_total_act_energy", "emdata:0", unit="kWh", dc="energy", sc="total_increasing", device="Phase A"),
    ]
    for c, dev in zip("abc", ("Phase A", "Phase B", "Phase C")):
        rows += [
            _shelly(f"{c}_act_power", device=dev),
            _shelly(f"{c}_voltage", unit="V", dc="voltage", device=dev, disabled=True),
            _shelly(f"{c}_current", unit="A", dc="current", device=dev, disabled=True),
            _shelly(f"{c}_pf", unit="", dc="power_factor", device=dev),
        ]
    return rows


# ── auto-detection ──────────────────────────────────────────────────────────
def test_shelly_pro3em_is_one_meter_with_all_roles():
    props = md.propose_mapping(_pro3em_rows())
    assert len(props) == 1  # phase sub-devices belong to the same meter (one MAC)
    p = props[0]
    m = p["mapping"]
    assert p["device_name"] == "Shelly Pro 3EM" and p["device_id"] == "dev-main"
    assert m["energy_stat"] == "sensor.shellypro3em_total_act"
    assert m["export_stat"] == "sensor.shellypro3em_total_act_ret"
    assert m["power_entity"] == "sensor.shellypro3em_total_act_power"
    assert m["apparent_power_entity"] == "sensor.shellypro3em_total_aprt_power"
    assert m["frequency_entity"] == "sensor.shellypro3em_a_freq"
    for c, ph in zip("abc", ("l1", "l2", "l3")):
        assert m[f"power_{ph}"] == f"sensor.shellypro3em_{c}_act_power"
        assert m[f"voltage_{ph}"] == f"sensor.shellypro3em_{c}_voltage"
        assert m[f"current_{ph}"] == f"sensor.shellypro3em_{c}_current"
        assert m[f"pf_{ph}"] == f"sensor.shellypro3em_{c}_pf"
    assert "sensor.shellypro3em_a_total_act_energy" not in m.values()
    assert p["confidence"] == "exact"
    # Shelly disables voltage / current by default — the user is told to enable them
    assert "sensor.shellypro3em_a_voltage" in p["disabled"]
    assert any("wyłączona" in n for n in p["notes"])


def test_shelly_gen1_3em_sums_three_channel_counters():
    rows = []
    for ch in range(3):
        for attr, unit, dc, sc in (("power", "W", "power", "measurement"), ("voltage", "V", "voltage", "measurement"),
                                   ("energy", "kWh", "energy", "total_increasing"),
                                   ("energyReturned", "kWh", "energy", "total_increasing")):
            rows.append({"entity_id": f"sensor.shellyem3_channel_{'abc'[ch]}_{attr.lower()}", "platform": "shelly",
                         "device_id": "gen1", "device_name": "Shelly 3EM", "unique_id": f"{MAC}-emeter_{ch}-{attr}",
                         "unit": unit, "device_class": dc, "state_class": sc, "disabled": False})
    p = md.propose_mapping(rows)[0]
    m = p["mapping"]
    assert m["import_stats"] == [f"sensor.shellyem3_channel_{c}_energy" for c in "abc"]
    assert m["export_stats"] == [f"sensor.shellyem3_channel_{c}_energyreturned" for c in "abc"]
    assert m["voltage_l3"] == "sensor.shellyem3_channel_c_voltage"
    assert "energy_stat" not in m and "power_entity" not in m
    assert any("suma" in n for n in p["notes"])


def test_shelly_gen1_em_single_phase_uses_channel_0_only():
    rows = [{"entity_id": f"sensor.shellyem_channel_{ch + 1}_{a.lower()}", "platform": "shelly", "device_id": "em",
             "device_name": "Shelly EM", "unique_id": f"{MAC}-emeter_{ch}-{a}", "unit": u, "device_class": dc,
             "state_class": sc}
            for ch in (0, 1)
            for a, u, dc, sc in (("power", "W", "power", "measurement"), ("energy", "kWh", "energy", "total_increasing"))]
    m = md.propose_mapping(rows)[0]["mapping"]
    assert m == {"power_entity": "sensor.shellyem_channel_1_power", "energy_stat": "sensor.shellyem_channel_1_energy"}


def test_shelly_plug_is_not_a_meter():
    rows = [_shelly("apower", "switch:0", eid="sensor.plug_power", device="plug"),
            _shelly("aenergy", "switch:0", unit="kWh", dc="energy", sc="total_increasing",
                    eid="sensor.plug_energy", device="plug"),
            _shelly("voltage", "switch:0", unit="V", dc="voltage", eid="sensor.plug_voltage", device="plug")]
    for r in rows:
        r["unique_id"] = r["unique_id"].replace(MAC, "112233445566")
    assert md.propose_mapping(rows) == []


def _modbus(name: str, unit: str, dc: str | None, sc: str = "measurement", uid: str | None = None) -> dict:
    slug = name.lower().replace(" ", "_")
    return {"entity_id": f"sensor.{slug}", "platform": "modbus", "device_id": "", "device_name": "",
            "unique_id": uid, "original_name": name, "unit": unit, "device_class": dc, "state_class": sc}


def test_eastron_template_unique_ids_map_exactly():
    rows = [_modbus("Licznik import", "kWh", "energy", "total_increasing", "sh_meter_energy_stat"),
            _modbus("Licznik napięcie 1", "V", "voltage", uid="sh_meter_voltage_l1"),
            _modbus("Licznik moc bierna", "var", "reactive_power", uid="sh_meter_reactive_power_entity"),
            _modbus("Licznik kvarh", "kvarh", None, "total_increasing", "sh_meter_reactive_energy_stat")]
    p = md.propose_mapping(rows)[0]
    assert p["mapping"] == {"energy_stat": "sensor.licznik_import", "voltage_l1": "sensor.licznik_napięcie_1",
                            "reactive_power_entity": "sensor.licznik_moc_bierna",
                            "reactive_energy_stat": "sensor.licznik_kvarh"}
    assert p["confidence"] == "exact"


def test_modbus_by_name_and_no_guessing():
    rows = [
        _modbus("SDM630 Total Import kWh", "kWh", "energy", "total_increasing"),
        _modbus("SDM630 Total Export kWh", "kWh", "energy", "total_increasing"),
        _modbus("SDM630 Total kWh", "kWh", "energy", "total_increasing"),   # import + export
        _modbus("SDM630 Phase 1 voltage", "V", "voltage"),
        _modbus("SDM630 L2 voltage", "V", "voltage"),
        _modbus("SDM630 Average line to neutral volts", "V", "voltage"),   # not a phase
        _modbus("SDM630 Total system power", "W", "power"),
        _modbus("SDM630 Maximum total system power demand", "W", "power"),
        _modbus("SDM630 Total Import kVArh", "kvarh", None, "total_increasing"),
        _modbus("SDM630 Total system power factor", "", "power_factor"),
    ]
    p = md.propose_mapping(rows)[0]
    m = p["mapping"]
    assert m["energy_stat"] == "sensor.sdm630_total_import_kwh"
    assert m["export_stat"] == "sensor.sdm630_total_export_kwh"
    assert m["voltage_l1"] == "sensor.sdm630_phase_1_voltage"
    assert m["voltage_l2"] == "sensor.sdm630_l2_voltage"
    assert m["power_entity"] == "sensor.sdm630_total_system_power"
    assert m["reactive_energy_stat"] == "sensor.sdm630_total_import_kvarh"
    assert m["pf_entity"] == "sensor.sdm630_total_system_power_factor"
    assert "sensor.sdm630_total_kwh" not in m.values()
    assert p["confidence"] == "name"


def test_two_equal_candidates_are_left_for_the_user():
    rows = [_modbus("Meter A import energy", "kWh", "energy", "total_increasing"),
            _modbus("Meter B import energy", "kWh", "energy", "total_increasing"),
            _modbus("Meter A power", "W", "power"),
            _modbus("Meter A L1 voltage", "V", "voltage")]
    p = md.propose_mapping(rows)[0]
    assert "energy_stat" not in p["mapping"]
    assert sorted(p["ambiguous"]["energy_stat"]) == ["sensor.meter_a_import_energy", "sensor.meter_b_import_energy"]
    assert any("ręcznie" in n for n in p["notes"])


def test_phase_names():
    assert md.phase_of("_sdm_l3_voltage_") == "l3"
    assert md.phase_of("shelly_phase_b_active_power") == "l2"
    assert md.phase_of("faza_1_napiecie") == "l1"
    assert md.phase_of("total_system_power") is None


# ── tg φ ────────────────────────────────────────────────────────────────────
def test_tan_phi_live():
    assert md.tan_phi(1000, q_var=400) == pytest.approx(0.4)
    assert md.tan_phi(1000, q_var=-500) == pytest.approx(0.5)          # capacitive: magnitude
    assert md.tan_phi(3000, s_va=5000) == pytest.approx(4000 / 3000)   # Q from S and P
    assert md.tan_phi(None, pf=0.8) == pytest.approx(0.75)
    assert md.tan_phi(None, pf=80) == pytest.approx(0.75)              # meter in %
    assert md.tan_phi(-2000, q_var=100) is None                        # exporting
    assert md.tan_phi(1000) is None


def test_tan_phi_period_only_import_hours():
    t = datetime(2026, 10, 1)  # noqa: DTZ001
    hours = [(t, 2.0, 1.0),                     # tg 0.5 > 0.4
             (t + timedelta(hours=1), 2.0, 0.4),  # 0.2
             (t + timedelta(hours=2), 1.0, -0.3),  # capacitive
             (t + timedelta(hours=3), 0.0, 0.2)]   # no import → skipped
    r = md.tan_phi_period(hours)
    assert r["hours"] == 3 and r["p_kwh"] == 5.0
    assert r["q_ind_kvarh"] == 1.4 and r["q_cap_kvarh"] == 0.3
    assert r["tan_phi"] == pytest.approx(0.28)
    assert r["hours_over"] == 1 and r["cap_hours"] == 1 and r["limit"] == 0.4


# ── voltage source ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("pref,grid_only,inv,met,expected", [
    ("auto", True, False, True, "meter"),        # meter-only site
    ("auto", True, False, False, None),
    ("auto", False, True, True, "inverter"),     # inverter stays the default
    ("auto", False, False, True, "meter"),       # inverter has no voltages → meter
    ("meter", False, True, True, "meter"),       # meter chosen as the alternative
    ("meter", False, True, False, "inverter"),   # chosen but empty → falls back
    ("inverter", True, True, True, "meter"),     # no inverter on a grid-only site
    (None, False, True, False, "inverter"),
])
def test_voltage_source(pref, grid_only, inv, met, expected):
    assert md.voltage_source(pref, grid_only, inv, met) == expected


KEYS = ("sensor.on_grid_l1_voltage", "sensor.on_grid_l2_voltage", "sensor.on_grid_l3_voltage")
GRID, FREQ = "sensor.meter_active_power_total", "sensor.on_grid_l1_frequency"
CFG = {"voltage_l1": "sensor.m_v1", "voltage_l2": "sensor.m_v2", "voltage_l3": "sensor.m_v3",
       "power_entity": "sensor.m_p", "frequency_entity": "sensor.m_f", "power_sign": "import_positive"}
STATES = {"sensor.m_v1": (251.0, "V"), "sensor.m_v2": (249.5, "V"), "sensor.m_v3": (248.0, "V"),
          "sensor.m_p": (1.2, "kW"), "sensor.m_f": (50.01, "Hz")}


def _read(eid):
    return STATES.get(eid, (None, ""))


def test_alert_overlay_meter_only_site():
    data = {"other": 1}
    out, src = md.alert_overlay(data, CFG, _read, True, KEYS, GRID, FREQ)
    assert src == "meter"
    assert [out[k] for k in KEYS] == [251.0, 249.5, 248.0]
    assert out[GRID] == -1200.0  # 1.2 kW drawn → project convention − import, in W
    assert out[FREQ] == 50.01
    assert data == {"other": 1}  # coordinator data untouched


def test_alert_overlay_keeps_inverter_by_default():
    data = {KEYS[0]: "247.1", KEYS[1]: "246.0", KEYS[2]: "245.0", GRID: "3000"}
    out, src = md.alert_overlay(data, CFG, _read, False, KEYS, GRID, FREQ)
    assert src == "inverter" and out is data
    out, src = md.alert_overlay(data, {**CFG, "voltage_source": "meter"}, _read, False, KEYS, GRID, FREQ)
    assert src == "meter" and out[KEYS[0]] == 251.0
    assert out[GRID] == "3000"  # the inverter's grid power stays


# ── import / export ─────────────────────────────────────────────────────────
def test_sum_hours_needs_every_counter():
    t = datetime(2026, 10, 1)  # noqa: DTZ001
    a = [(t, 0.5), (t + timedelta(hours=1), 0.5)]
    b = [(t, 0.25)]
    assert md.sum_hours([a, b]) == [(t, 0.75)]


def test_balance_and_exchange_month():
    t = datetime(2026, 10, 1, 10)  # noqa: DTZ001
    imp = [(t, 1.0), (t + timedelta(hours=1), 0.2)]
    exp = [(t, 0.3), (t + timedelta(hours=1), 1.5)]
    bi, be = md.balance_hours(imp, exp)
    assert [k for _, k in bi] == [0.7, 0.0]
    assert [round(k, 3) for _, k in be] == [0.0, 1.3]
    m = md.exchange_month(imp, exp, datetime(2026, 10, 1), datetime(2026, 11, 1))  # noqa: DTZ001
    assert m["import_kwh"] == 1.2 and m["export_kwh"] == 1.8 and m["net_kwh"] == -0.6
    assert m["balanced_import_kwh"] == 0.7 and m["balanced_export_kwh"] == 1.3


def test_phase_asymmetry_and_day_extremes():
    a = md.phase_asymmetry({"l1": 240.0, "l2": 230.0, "l3": 232.0}, {"l1": 3000, "l2": 200, "l3": None})
    assert a["voltage_spread_v"] == 10.0
    assert a["voltage_unbalance_pct"] == pytest.approx(2.57, abs=0.01)
    assert a["power_spread_w"] == 2800 and a["power_max_phase"] == "l1"
    ext = md.day_extremes({"voltage_l1": [(228.0, 249.0), (230.0, 251.5)]}, {"voltage_l1": 253.2})
    assert ext["voltage_l1"] == {"min": 228.0, "max": 253.2}


def test_units():
    assert md.to_base(1.5, "kW", "power") == 1500
    assert md.to_base(2000, "Wh", "energy") == 2.0
    assert md.to_base(3, "kvar", "reactive_power") == 3000


# ── complaint text from a meter on a site without PV ────────────────────────
def test_complaint_text_meter_without_pv():
    report = {"summary": {"max_v": 256.0, "days_over": 2, "first_day": "2026-10-01"}, "limit_v": 253.0,
              "days": [], "phases": {}, "source": "meter", "grid_only": True}
    text = vr.complaint_text(report, "2026-10-17")
    assert "licznika energii" in text
    assert "falownik" not in text.lower()
    assert "nie oddawała energii" not in text
    pv = vr.complaint_text({**report, "source": "inverter", "grid_only": False}, "2026-10-17")
    assert "z falownika" in pv and "PN-EN 50549-1" in pv
