# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Peak load guard — keeps big loads off the grid in expensive tariff hours.

The battery covers the house only up to its max discharge power (≈ 3.7 kW on
bobrek) and only down to the BMS floor. Anything above that in a tariff peak
is bought at the peak price. Configured devices (settings "peak_load_control"):

  mode "block_peak" — deferrable load (e.g. boiler): in a peak it runs only on
                      PV surplus; when the house draws from the battery or the
                      grid it is switched off for the rest of the peak.
  mode "shed"       — comfort load (e.g. heater): switched off in a peak only
                      when the grid has to cover the house (battery at its max
                      power or empty), one device at a time.
  mode "off"        — not managed.

Everything switched off is switched back on when the peak ends. A device the
user turns back on during the peak is left alone until the next peak.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

IMPORT_TRIGGER_W = 300     # grid import that counts as "the grid covers the house"
BATTERY_TRIGGER_W = 300    # battery discharge that counts as "not on PV surplus"
HOLD_S = 60                # condition must last this long before switching
STEP_S = 60                # one shed step per minute

DEFAULT_DEVICES = [
    {"entity": "switch.drugie_gniazdko", "name": "Grzejnik", "power_w": 2000, "mode": "shed"},
    {"entity": "switch.klimatyzacja_socket_1", "name": "Klimatyzator", "power_w": 1200, "mode": "off"},
]


@dataclass
class GuardInput:
    in_peak: bool
    grid_import_w: float        # + = import
    battery_w: float            # + = discharge
    battery_max_w: float
    battery_blocked: bool       # BMS limit 0 A or SOC at the floor
    states: dict[str, str]      # entity → "on"/"off"/"unavailable"


@dataclass
class LoadGuard:
    devices: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    shed: dict[str, tuple[str, float]] = field(default_factory=dict)  # entity → (reason, when switched off)
    overridden: set[str] = field(default_factory=set)       # user switched back on — hands off
    _cond_since: dict[str, float] = field(default_factory=dict)
    _last_step: float = 0.0

    def configure(self, cfg: dict[str, Any] | None) -> None:
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", True))
        devices = cfg.get("devices")
        self.devices = [d for d in (devices if devices is not None else DEFAULT_DEVICES) if d.get("entity")]

    def _held(self, key: str, active: bool, now: float) -> bool:
        if not active:
            self._cond_since.pop(key, None)
            return False
        since = self._cond_since.setdefault(key, now)
        return now - since >= HOLD_S

    def decide(self, inp: GuardInput, now: float | None = None) -> list[tuple[str, str, str]]:
        """→ [(entity, "turn_off"/"turn_on", reason)] to execute now."""
        now = time.time() if now is None else now
        actions: list[tuple[str, str, str]] = []
        managed = {d["entity"]: d for d in self.devices if d.get("mode") in ("block_peak", "shed")}

        # The user turned a device back on — respect it until the peak ends
        # (grace period: HA may still report "on" right after our turn_off)
        for entity, (_reason, since) in list(self.shed.items()):
            if inp.states.get(entity) == "on" and now - since > 90:
                self.overridden.add(entity)
                self.shed.pop(entity)

        if not inp.in_peak or not self.enabled:
            for entity in list(self.shed):
                if inp.states.get(entity) == "off":
                    actions.append((entity, "turn_on", "koniec szczytu" if inp.in_peak is False else "strażnik wyłączony"))
                self.shed.pop(entity)
            self.overridden.clear()
            self._cond_since.clear()
            return actions

        grid_covers = inp.grid_import_w > IMPORT_TRIGGER_W and (
            inp.battery_blocked or inp.battery_w >= 0.85 * inp.battery_max_w
        )
        off_surplus = inp.grid_import_w > IMPORT_TRIGGER_W or inp.battery_w > BATTERY_TRIGGER_W

        # Deferrable loads: off whenever the house isn't running on PV surplus
        if self._held("block", off_surplus, now):
            for entity, dev in managed.items():
                if (dev["mode"] == "block_peak" and inp.states.get(entity) == "on"
                        and entity not in self.overridden and entity not in self.shed):
                    self.shed[entity] = ("block_peak", now)
                    actions.append((entity, "turn_off", f"szczyt — {dev.get('name', entity)} poczeka na tanią strefę"))

        # Comfort loads: one at a time, only when the grid covers the house
        if self._held("shed", grid_covers, now) and now - self._last_step >= STEP_S:
            for entity, dev in managed.items():
                if (dev["mode"] == "shed" and inp.states.get(entity) == "on"
                        and entity not in self.overridden and entity not in self.shed):
                    self.shed[entity] = ("shed", now)
                    self._last_step = now
                    actions.append((
                        entity, "turn_off",
                        f"szczyt — dom {inp.grid_import_w:.0f} W z sieci ponad moc baterii, "
                        f"wyłączam {dev.get('name', entity)} do końca szczytu",
                    ))
                    break
        return actions

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "devices": self.devices,
            "shed": {e: r for e, (r, _t) in self.shed.items()},
            "overridden": sorted(self.overridden),
        }
