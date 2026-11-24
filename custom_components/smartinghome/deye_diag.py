# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Deye diagnostics — decoding and checks, read only (pure, no HA imports).

What a Deye install has to tell us before Smarting HOME may ever write to it:

- the raw Time of Use registers (0x0092–0x00B1): in the per-program register
  0x00AC–0x00B1 ha-solarman knows only bits 0–1 (grid / generator charging), while
  kellerza/sunsynk also uses bits 2–4 ("mode") and bit 5 ("sell"). If this firmware
  sets them, ha-solarman's select would clear them on a write;
- the sign of grid and battery power, from the energy balance pv ± battery ± grid = load;
- work mode, energy pattern, solar sell and the grid-charging settings the installer set.

Register numbers: ha-solarman deye_p3.yaml, cross-checked with kellerza/sunsynk
three_phase_common.py (141 = 0x008D ... 177 = 0x00B1).
"""

from __future__ import annotations

from typing import Any

# Holding-register blocks read once per diagnostics download (FC03, nothing is written)
REGISTER_BLOCKS: tuple[tuple[int, int], ...] = (
    (0x0080, 3),   # grid charging current, generator charging, grid charging
    (0x008D, 5),   # energy pattern, work mode, max sell power, (0x0090), solar sell
    (0x0092, 32),  # Time of Use: enable/days, (0x0093), 6 × time, power, voltage, SOC, flags
)

WORK_MODES = {0: "Export First", 1: "Zero Export To Load", 2: "Zero Export To CT"}
ENERGY_PATTERNS = {0: "Battery First", 1: "Load First"}
CHARGE_SOURCES = {0: "Disabled", 1: "Grid", 2: "Generator", 3: "Both"}
PROGRAM_MODES = {0: "None", 1: "General", 2: "Backup", 3: "Charge"}  # kellerza, bits 2–4
DAYS = ("pon", "wt", "śr", "czw", "pt", "sob", "nd")

# Smarting HOME's convention for the mapped grid power (grid_sign.py)
EXPECTED_GRID = "+ eksport / − pobór"


def decode_program_flags(value: int) -> dict[str, Any]:
    """Register 0x00AC + n: bits 0–1 charge source; bits 2–4 mode and bit 5 sell (kellerza)."""
    extra = value & ~0x03
    return {
        "raw": value,
        "hex": f"0x{value:04X}",
        "charge": CHARGE_SOURCES.get(value & 0x03, "?"),
        "mode": PROGRAM_MODES.get((value >> 2) & 0x07, f"? ({(value >> 2) & 0x07})"),
        "sell": bool(value & 0x20),
        "extra_bits": f"0x{extra:04X}" if extra else "",
    }


def decode_tou(value: int) -> dict[str, Any]:
    """Register 0x0092: bit 0 = Time of Use on, bits 1–7 = Monday … Sunday."""
    return {
        "raw": value,
        "hex": f"0x{value:04X}",
        "enabled": bool(value & 0x01),
        "days": [day for i, day in enumerate(DAYS) if value & (1 << (i + 1))],
    }


def decode_time(value: int) -> str:
    """Program start time, stored as decimal HHMM (1330 = 13:30)."""
    hours, minutes = divmod(int(value), 100)
    if 0 <= hours < 24 and 0 <= minutes < 60:
        return f"{hours:02d}:{minutes:02d}"
    return f"? ({value})"


def decode_registers(regs: dict[int, int]) -> dict[str, Any]:
    """Named view of the raw blocks (missing registers are left out)."""
    out: dict[str, Any] = {}

    def get(address: int) -> int | None:
        value = regs.get(address)
        return int(value) if value is not None else None

    if (v := get(0x0080)) is not None:
        out["grid_charging_current_a"] = v
    if (v := get(0x0082)) is not None:
        out["grid_charging"] = bool(v)
    if (v := get(0x008D)) is not None:
        out["energy_pattern"] = ENERGY_PATTERNS.get(v, f"? ({v})")
    if (v := get(0x008E)) is not None:
        out["work_mode"] = WORK_MODES.get(v, f"? ({v})")
    if (v := get(0x008F)) is not None:
        out["max_sell_power_raw"] = v  # × 1 W (LV) or × 10 W (HV), see ha-solarman "mod"
    if (v := get(0x0091)) is not None:
        out["solar_sell"] = bool(v)
    if (v := get(0x0092)) is not None:
        out["time_of_use"] = decode_tou(v)
    programs = []
    for n in range(6):
        program: dict[str, Any] = {"program": n + 1}
        if (v := get(0x0094 + n)) is not None:
            program["start"] = decode_time(v)
        if (v := get(0x009A + n)) is not None:
            program["power_raw"] = v
        if (v := get(0x00A6 + n)) is not None:
            program["soc"] = v
        if (v := get(0x00AC + n)) is not None:
            program["flags"] = decode_program_flags(v)
        if len(program) > 1:
            programs.append(program)
    if programs:
        out["programs"] = programs
    return out


def sign_check(
    pv: float | None,
    load: float | None,
    grid: float | None,
    battery: float | None,
    min_w: float = 100.0,
) -> dict[str, Any]:
    """Which signs make pv + b·battery + g·grid = load (b, g = ±1).

    g = +1: raw grid power is positive on import; b = +1: raw battery power is positive
    on discharge. A sign is reported only when that power is at least `min_w` and the
    other choice clearly breaks the balance — one snapshot, so treat it as a hint.
    """
    if None in (pv, load, grid, battery):
        return {"determined": False, "note": "Brak odczytu mocy PV, zużycia, sieci albo baterii."}
    tolerance = max(150.0, 0.1 * max(load, pv, 1.0))

    def residual(g: int, b: int) -> float:
        return abs(pv + b * battery + g * grid - load)

    best = min((residual(g, b), g, b) for g in (1, -1) for b in (1, -1))
    result: dict[str, Any] = {
        "inputs_w": {"pv": pv, "load": load, "grid": grid, "battery": battery},
        "residual_w": round(best[0]),
        "tolerance_w": round(tolerance),
        "grid": None,
        "battery": None,
    }
    if best[0] > tolerance:
        result["determined"] = False
        result["note"] = (
            "Bilans się nie domyka przy żadnym znaku (odczyty z różnych chwil?) — "
            "pobierz diagnostykę jeszcze raz przy stałym zużyciu."
        )
        return result

    def clear(variable: str) -> bool:
        if variable == "g":
            plus, minus = min(residual(1, b) for b in (1, -1)), min(residual(-1, b) for b in (1, -1))
            power = grid
        else:
            plus, minus = min(residual(g, 1) for g in (1, -1)), min(residual(g, -1) for g in (1, -1))
            power = battery
        return abs(power) >= min_w and max(plus, minus) > tolerance

    if clear("g"):
        result["grid"] = "+ pobór / − eksport" if best[1] == 1 else "+ eksport / − pobór"
    if clear("b"):
        result["battery"] = "+ rozładowanie / − ładowanie" if best[2] == 1 else "+ ładowanie / − rozładowanie"
    result["determined"] = bool(result["grid"] or result["battery"])
    if not result["determined"]:
        result["note"] = (
            f"Moc sieci i baterii poniżej {min_w:.0f} W — znaku nie da się ustalić. "
            "Pobierz diagnostykę, gdy bateria się ładuje albo rozładowuje i jest wymiana z siecią."
        )
    return result


def findings(
    registers: dict[str, Any] | None,
    signs: dict[str, Any] | None,
    clock_drift_s: float | None,
    stale: dict[str, float] | None = None,
) -> list[str]:
    """Plain-language conclusions for the owner (Polish)."""
    out: list[str] = []
    if registers:
        tou = registers.get("time_of_use")
        if tou is not None and not tou["enabled"]:
            out.append("Time of Use jest wyłączony w falowniku (rejestr 0x0092, bit 0 = 0).")
        programs = registers.get("programs") or []
        extra = [p for p in programs if p.get("flags", {}).get("extra_bits")]
        if extra:
            listed = ", ".join(
                f"program {p['program']} = {p['flags']['hex']}"
                f" (tryb {p['flags']['mode']}, sprzedaż {'tak' if p['flags']['sell'] else 'nie'})"
                for p in extra
            )
            out.append(
                f"Rejestry programów mają ustawione bity poza ładowaniem: {listed}. Select „Program N Charging” "
                "z ha-solarman zapisuje cały rejestr i wyzerowałby je — sterowanie musi zapisywać z maską."
            )
        elif any("flags" in p for p in programs):
            out.append(
                "Rejestry programów używają tylko bitów ładowania (0–1) — zapis przez select ha-solarman "
                "nie zmieni innych ustawień na tym firmware."
            )
        if registers.get("grid_charging") is False:
            out.append("Ładowanie z sieci jest wyłączone (0x0082 = 0) — falownik nie naładuje baterii z sieci.")
        if registers.get("grid_charging_current_a") == 0:
            out.append("Prąd ładowania z sieci = 0 A (0x0080) — ładowanie z sieci nie zadziała.")
        if "work_mode" in registers:
            out.append(
                f"Tryb pracy: {registers['work_mode']}, energy pattern: {registers.get('energy_pattern', '?')}, "
                f"sprzedaż PV: {'włączona' if registers.get('solar_sell') else 'wyłączona'}."
            )
    if signs and signs.get("determined"):
        if signs.get("grid"):
            same = signs["grid"] == EXPECTED_GRID
            out.append(
                f"Znak mocy sieci Deye: {signs['grid']} — "
                + ("zgodny z oczekiwaniem Smarting HOME." if same else
                   f"odwrotny niż oczekuje Smarting HOME ({EXPECTED_GRID}); trzeba go odwrócić.")
            )
        if signs.get("battery"):
            out.append(f"Znak mocy baterii Deye: {signs['battery']}.")
    elif signs and signs.get("note"):
        out.append(signs["note"])
    if clock_drift_s is not None and abs(clock_drift_s) > 300:
        out.append(f"Zegar falownika różni się od Home Assistant o {abs(clock_drift_s) / 60:.0f} min.")
    for name, age in (stale or {}).items():
        out.append(f"{name}: brak nowych danych od {age / 60:.0f} min.")
    return out
