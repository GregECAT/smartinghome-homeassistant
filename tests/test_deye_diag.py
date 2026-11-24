"""Deye diagnostics: register decoding, the program-flag bits, the energy-balance sign check.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
diag = importlib.import_module("smartinghome_pure.deye_diag")


def _raw(flags=(1, 0, 0, 0, 1, 1), tou=0x00FF, grid_charging=1, current=40):
    regs = {0x0080: current, 0x0081: 0, 0x0082: grid_charging,
            0x008D: 1, 0x008E: 2, 0x008F: 12000, 0x0090: 0, 0x0091: 1, 0x0092: tou, 0x0093: 0}
    for n, (start, soc) in enumerate(zip((0, 600, 1300, 1500, 2200, 2300), (80, 20, 30, 20, 80, 80))):
        regs[0x0094 + n] = start
        regs[0x009A + n] = 5000
        regs[0x00A0 + n] = 4900
        regs[0x00A6 + n] = soc
        regs[0x00AC + n] = flags[n]
    return regs


def test_decode_registers_names_the_settings():
    d = diag.decode_registers(_raw())
    assert d["work_mode"] == "Zero Export To CT" and d["energy_pattern"] == "Load First"
    assert d["solar_sell"] is True and d["grid_charging"] is True and d["grid_charging_current_a"] == 40
    assert d["time_of_use"]["enabled"] and len(d["time_of_use"]["days"]) == 7
    assert [p["start"] for p in d["programs"]] == ["00:00", "06:00", "13:00", "15:00", "22:00", "23:00"]
    assert d["programs"][0]["flags"]["charge"] == "Grid" and not d["programs"][0]["flags"]["extra_bits"]


def test_program_flags_beyond_charging_are_flagged():
    f = diag.decode_program_flags(0x0021)
    assert f["charge"] == "Grid" and f["sell"] is True and f["extra_bits"] == "0x0020"
    f = diag.decode_program_flags(0x000D)  # charge + mode "Charge" (kellerza bits 2–4)
    assert f["mode"] == "Charge" and f["extra_bits"] == "0x000C"


def test_findings_warn_when_a_select_write_would_clear_bits():
    regs = diag.decode_registers(_raw(flags=(0x21, 0, 0, 0, 1, 1)))
    text = " ".join(diag.findings(regs, None, None))
    assert "program 1 = 0x0021" in text and "z maską" in text


def test_findings_clean_firmware_and_installer_settings():
    regs = diag.decode_registers(_raw(tou=0x00FE, grid_charging=0, current=0))
    text = " ".join(diag.findings(regs, None, 900.0, {"sensor.inverter_pv_power": 1200.0}))
    assert "Time of Use jest wyłączony" in text
    assert "tylko bitów ładowania" in text
    assert "0x0082 = 0" in text and "0 A" in text
    assert "Zero Export To CT" in text
    assert "15 min" in text and "20 min" in text


def test_sign_check_finds_deye_convention():
    # Day: PV 6000, house 1500, battery charging 3000, export 1500.
    # Deye raw: battery + discharge → −3000; grid + import → −1500
    s = diag.sign_check(pv=6000, load=1500, grid=-1500, battery=-3000)
    assert s["determined"]
    assert s["grid"] == "+ pobór / − eksport"
    assert s["battery"] == "+ rozładowanie / − ładowanie"
    text = " ".join(diag.findings(None, s, None))
    assert "odwrotny niż oczekuje Smarting HOME" in text


def test_sign_check_goodwe_like_convention():
    # Night: house 800 from battery 500 + grid import 300; raw grid + export → −300
    s = diag.sign_check(pv=0, load=800, grid=-300, battery=500)
    assert s["grid"] == "+ eksport / − pobór" and s["battery"] == "+ rozładowanie / − ładowanie"
    assert "zgodny z oczekiwaniem" in " ".join(diag.findings(None, s, None))


def test_sign_check_cannot_decide_when_flows_are_small():
    s = diag.sign_check(pv=2000, load=2000, grid=20, battery=-30)
    assert not s["determined"] and "poniżej 100 W" in s["note"]
    s = diag.sign_check(pv=None, load=500, grid=0, battery=0)
    assert not s["determined"]


def test_sign_check_rejects_a_balance_that_does_not_close():
    s = diag.sign_check(pv=5000, load=500, grid=100, battery=200)
    assert not s["determined"] and "nie domyka" in s["note"]
