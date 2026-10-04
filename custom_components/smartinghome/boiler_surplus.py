# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Boiler on PV surplus — heat water instead of selling midday energy cheaply.

Water is normally heated by the central-heating furnace; the electric boiler
is only a sink for PV energy the house can't use: in the midday window, with
the battery full and energy flowing to the grid, the boiler is switched on.
When the surplus is gone — the battery starts covering the boiler, the house
draws from the grid, the battery drops below full or the window ends — it is
switched off again. High grid voltage while exporting (inverter close to its
disconnect limit) also turns it on.

This module is the only automatic owner of the boiler: the voltage / surplus
cascades and the peak load guard leave it alone, and the old time schedule
automation stays disabled. Settings "boiler_surplus":
  {enabled, entity, window: [start_h, end_h], min_soc, min_export_w}
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "entity": "switch.bojler_3800",
    "window": [10, 16],     # local hours [start, end)
    "min_soc": 98.0,        # battery treated as full
    "min_export_w": 3000,   # export needed to switch on (boiler ≈ 3.8 kW)
}
ON_HOLD_S = 180            # conditions must hold this long to switch on
OFF_HOLD_S = 120           # … or off
MIN_ON_S = 300             # no relay cycling: stay on ≥ 5 min, off ≥ 10 min
MIN_OFF_S = 600
VOLTAGE_ON = 252.0         # V — export is pushing the grid voltage up


@dataclass
class BoilerInput:
    hour: int
    soc: float
    export_w: float          # + = to the grid
    import_w: float          # + = from the grid
    battery_w: float         # + = discharge
    max_voltage: float
    state: str               # "on" / "off" / "unavailable"
    export_cap_w: float | None = None  # export limit set by the voltage guard (None = none)


@dataclass
class BoilerSurplus:
    cfg: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    owned: bool = False            # we switched it on
    _since: dict[str, float] = field(default_factory=dict)
    _last_switch: float = float("-inf")
    reason: str = ""
    mode: str = ""                 # "surplus" (battery full) / "voltage" (taking what the grid won't)

    def can_switch_on(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return self.enabled and now - self._last_switch >= MIN_OFF_S

    def force_on(self, reason: str, now: float | None = None) -> None:
        """The voltage guard switches the boiler on instead of curtailing PV."""
        self._last_switch = time.time() if now is None else now
        self.owned, self.mode, self.reason = True, "voltage", reason
        self._since.clear()

    def configure(self, cfg: dict[str, Any] | None) -> None:
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in (cfg or {}).items() if v is not None})
        self.cfg = merged

    @property
    def entity(self) -> str:
        return str(self.cfg.get("entity") or "")

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled")) and bool(self.entity)

    def _held(self, key: str, active: bool, hold: float, now: float) -> bool:
        if not active:
            self._since.pop(key, None)
            return False
        return now - self._since.setdefault(key, now) >= hold

    def decide(self, inp: BoilerInput, now: float | None = None) -> tuple[str, str] | None:
        """→ ("turn_on" | "turn_off", reason) or None."""
        now = time.time() if now is None else now
        if not self.enabled or inp.state not in ("on", "off"):
            return None
        start, end = (list(self.cfg.get("window") or [10, 16]) + [10, 16])[:2]
        in_window = int(start) <= inp.hour < int(end)
        min_soc = float(self.cfg.get("min_soc", 98))
        min_export = float(self.cfg.get("min_export_w", 3000))

        if inp.state == "off":
            self.owned = False  # off (by us or by hand) — nothing of ours is running
            surplus = in_window and inp.soc >= min_soc and inp.export_w >= min_export
            # high voltage counts only when the export is PV — not the battery being sold
            high_v = inp.max_voltage >= VOLTAGE_ON and inp.export_w >= 1000 and inp.battery_w <= 300
            # The export is held at the guard's limit while the battery takes little: the PV
            # is being curtailed — the real surplus is bigger than the export shows
            capped = (inp.export_cap_w is not None and inp.export_w >= inp.export_cap_w - 300
                      and inp.battery_w >= -800)
            if (self._held("on", surplus or high_v or capped, ON_HOLD_S if surplus else 60, now)
                    and now - self._last_switch >= MIN_OFF_S):
                self._last_switch, self.owned = now, True
                self.mode = "surplus" if surplus else "voltage"
                self._since.clear()
                if capped and not surplus:
                    self.reason = (f"oddawanie ograniczone do {inp.export_cap_w:.0f} W, PV przycinane — "
                                   f"grzeję wodę zamiast tracić produkcję")
                    return "turn_on", self.reason
                self.reason = (
                    f"bateria {inp.soc:.0f}%, do sieci {inp.export_w:.0f} W — grzeję wodę z nadwyżki PV"
                    if surplus else
                    f"napięcie {inp.max_voltage:.0f} V przy eksporcie {inp.export_w:.0f} W — odbieram nadwyżkę"
                )
                return "turn_on", self.reason
            return None

        # state == "on": only switch off what we switched on (manual use is left alone)
        if not self.owned:
            return None
        if self.mode == "voltage":
            # on for the voltage / a curtailed PV: stays on while PV feeds it; off once the
            # limit is gone and the voltage is down while the battery still wants the energy
            quiet = (inp.export_cap_w is None and inp.max_voltage < VOLTAGE_ON - 2
                     and inp.soc < min_soc - 6)
            gone = quiet or inp.import_w > 300 or inp.battery_w > 800
        else:
            gone = (
                not in_window and inp.max_voltage < VOLTAGE_ON
            ) or inp.import_w > 300 or inp.battery_w > 800 or inp.soc < min_soc - 6
        if self._held("off", gone, OFF_HOLD_S, now) and now - self._last_switch >= MIN_ON_S:
            self._last_switch, self.owned = now, False
            self._since.clear()
            mode, self.mode = self.mode, ""
            if inp.import_w > 300:
                why = f"dom bierze {inp.import_w:.0f} W z sieci"
            elif inp.battery_w > 800:
                why = f"bateria oddaje {inp.battery_w:.0f} W — nadwyżka PV się skończyła"
            elif mode == "voltage":
                why = "napięcie spadło, bateria znów potrzebuje energii"
            elif inp.soc < min_soc - 6:
                why = f"bateria spadła do {inp.soc:.0f}%"
            else:
                why = "koniec okna południowego"
            self.reason = why
            return "turn_off", why
        return None

    def status(self) -> dict[str, Any]:
        return {**self.cfg, "owned": self.owned, "mode": self.mode, "reason": self.reason}
