# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Deye Time of Use — what Smarting HOME would write to the inverter (pure, no HA imports).

Deye 3-phase hybrids (SG04LP3, SG01HP3; ha-solarman profile ``deye_p3``) run a weekly
table of 6 slots. Slot N starts at "Program N Time" and lasts until the next slot
starts (slot 6 wraps to slot 1). Each slot has a battery SOC and a charge source:

- charge source "Grid": the battery charges from the grid up to the slot SOC,
- charge source "Disabled": the battery discharges into the house down to the slot SOC
  (below it the battery rests, PV can still charge it).

Smarting HOME maps its intents (EnergyManager.intent) onto the slot that is running now
and leaves the other five slots as the installer set them:

    general      the installer's slot as it is (self-consumption)
    home         charge source off, SOC = the installer's reserve
    charge_grid  charge source "Grid", SOC = target (100 % unless the planner gives one)
    hold         charge source off, SOC = SOC when the hold began (no discharge)
    sell         not supported on Deye — treated as home (needs "Export First")
    pv_export    not supported on Deye — treated as home (needs 0 A charge current)

Only one slot ever differs from the installer's table, the running one; when the slot
ends it goes back. Targets are sticky per (intent, slot), so a 15-minute re-assert or a
changing SOC never produces another write.

This module never writes anything. TouShadow counts the writes the controller WOULD
make, so the write budget can be measured on real installs before any write exists.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

SLOT_COUNT = 6
DAY_MINUTES = 24 * 60

CHARGING_DISABLED = "Disabled"
CHARGING_GRID = "Grid"

# Budget for the future write mode: Deye stores settings in non-volatile memory,
# so writes must stay rare. The shadow mode only measures against it.
DAY_WRITE_BUDGET = 48
HOUR_WRITE_BUDGET = 6
MAX_CLOCK_DRIFT_S = 300.0

INTENT_LABELS: dict[str, str] = {
    "general": "autokonsumpcja",
    "home": "bateria zasila dom",
    "charge_grid": "ładowanie z sieci",
    "hold": "trzymanie baterii",
    "sell": "sprzedaż z magazynu",
    "pv_export": "eksport PV z odłożeniem ładowania",
}

CHARGING_LABELS: dict[str, str] = {
    "Disabled": "bez ładowania z sieci",
    "Grid": "ładowanie z sieci",
    "Generator": "ładowanie z generatora",
    "Both": "ładowanie z sieci i generatora",
}

# Intents the Deye table cannot express: what is done instead and why
_UNSUPPORTED: dict[str, str] = {
    "sell": "Sprzedaż z magazynu nie jest dostępna dla Deye (wymaga trybu Export First) — bateria zasila dom.",
    "pv_export": "Eksport PV z odłożeniem ładowania nie jest dostępny dla Deye — bateria zasila dom, PV dalej ją ładuje.",
}


@dataclass(frozen=True)
class Slot:
    """One Time of Use program as the inverter reports it (None = unreadable)."""

    start: int | None  # minutes after midnight
    soc: int | None
    charging: str | None


@dataclass(frozen=True)
class TouTable:
    """The Deye Time of Use table and the settings the preview depends on."""

    slots: tuple[Slot, ...]
    enabled: bool | None = None  # "Time of Use" select is not "Disabled"
    grid_charging: bool | None = None  # "Battery Grid Charging" switch
    low_soc: int | None = None  # "Battery Low SOC"
    clock_drift_s: float | None = None  # inverter clock − Home Assistant clock


@dataclass(frozen=True)
class Write:
    """One register write: slot is 1-based, field is "soc" or "charging"."""

    slot: int
    field: str
    old: Any
    new: Any

    def describe(self) -> str:
        if self.field == "soc":
            return f"slot {self.slot}: SOC {_fmt(self.old)} → {_fmt(self.new)} %"
        return (
            f"slot {self.slot}: {CHARGING_LABELS.get(str(self.old), self.old)} → "
            f"{CHARGING_LABELS.get(str(self.new), self.new)}"
        )


@dataclass
class ShadowResult:
    """What the controller would do now (nothing is written)."""

    available: bool
    intent: str = ""
    slot: int | None = None  # 1-based
    window: str = ""  # "13:00–15:00"
    target_soc: int | None = None
    target_charging: str | None = None
    writes: list[Write] = field(default_factory=list)
    writes_today: int = 0
    over_budget: bool = False
    problems: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "intent": self.intent,
            "intent_label": INTENT_LABELS.get(self.intent, self.intent),
            "slot": self.slot,
            "window": self.window,
            "target_soc": self.target_soc,
            "target_charging": self.target_charging,
            "target_charging_label": CHARGING_LABELS.get(self.target_charging or "", self.target_charging),
            "writes": [w.describe() for w in self.writes],
            "writes_today": self.writes_today,
            "over_budget": self.over_budget,
            "problems": list(self.problems),
            "note": self.note,
        }


def _fmt(value: Any) -> str:
    return "?" if value is None else str(value)


# ── Parsing Home Assistant states (strings) ─────────────────────────────────

_UNKNOWN = (None, "", "unknown", "unavailable")


def parse_time(value: Any) -> int | None:
    """"HH:MM" / "HH:MM:SS" → minutes after midnight."""
    if value in _UNKNOWN:
        return None
    parts = str(value).split(":")
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


def parse_int(value: Any) -> int | None:
    if value in _UNKNOWN:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def parse_float(value: Any) -> float | None:
    if value in _UNKNOWN:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_option(value: Any) -> str | None:
    return None if value in _UNKNOWN else str(value)


def parse_switch(value: Any) -> bool | None:
    if value == "on":
        return True
    if value == "off":
        return False
    return None


def parse_tou_enabled(value: Any) -> bool | None:
    """The "Time of Use" select: "Disabled" or a day set ("Week", "Weekdays", ...)."""
    if value in _UNKNOWN:
        return None
    return str(value) != "Disabled"


# ── Table geometry ──────────────────────────────────────────────────────────


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slots_valid(table: TouTable) -> bool:
    """Six readable start times that go once around the clock."""
    if len(table.slots) != SLOT_COUNT or any(s.start is None for s in table.slots):
        return False
    starts = [s.start for s in table.slots]
    total = sum((starts[(i + 1) % SLOT_COUNT] - starts[i]) % DAY_MINUTES for i in range(SLOT_COUNT))
    return total == DAY_MINUTES


def active_slot(table: TouTable, minute_of_day: int) -> int | None:
    """0-based index of the slot running at minute_of_day (None if the table is invalid)."""
    if not slots_valid(table):
        return None
    starts = [s.start for s in table.slots]
    for i in range(SLOT_COUNT):
        begin, end = starts[i], starts[(i + 1) % SLOT_COUNT]
        if begin == end:
            continue  # empty slot
        if begin < end:
            if begin <= minute_of_day < end:
                return i
        elif minute_of_day >= begin or minute_of_day < end:
            return i
    return None


def slot_window(table: TouTable, index: int) -> str:
    begin = table.slots[index].start
    end = table.slots[(index + 1) % SLOT_COUNT].start
    if begin is None or end is None:
        return ""
    return f"{_hhmm(begin)}–{_hhmm(end)}"


# ── Targets ─────────────────────────────────────────────────────────────────


def _clamp_soc(value: float, low_soc: int | None) -> int:
    return int(max(low_soc or 0, min(100, value)))


def reserve_soc(table: TouTable) -> int:
    """The installer's discharge floor: the lowest SOC of slots without grid charging."""
    floors = [s.soc for s in table.slots if s.charging == CHARGING_DISABLED and s.soc is not None]
    return _clamp_soc(min(floors) if floors else (table.low_soc or 0), table.low_soc)


def slot_target(
    intent: str,
    base: Slot,
    *,
    reserve: int,
    low_soc: int | None,
    soc_now: float | None,
    charge_target: int = 100,
) -> tuple[int | None, str | None, str]:
    """(SOC, charge source, note) the running slot should get for an intent."""
    if intent == "charge_grid":
        return _clamp_soc(charge_target, low_soc), CHARGING_GRID, ""
    if intent == "hold":
        if soc_now is None:
            return base.soc, base.charging, "Brak odczytu SOC — slot zostaje bez zmian."
        return _clamp_soc(math.ceil(soc_now), low_soc), CHARGING_DISABLED, ""
    if intent in ("home", "sell", "pv_export"):
        return reserve, CHARGING_DISABLED, _UNSUPPORTED.get(intent, "")
    return base.soc, base.charging, ""  # general and anything unknown: installer's slot


def diff_writes(
    desired: list[tuple[int | None, str | None]],
    actual: list[tuple[int | None, str | None]],
) -> list[Write]:
    """Register writes that turn `actual` into `desired` (SOC before charge source)."""
    writes: list[Write] = []
    for i, ((soc, charging), (old_soc, old_charging)) in enumerate(zip(desired, actual)):
        if soc is not None and soc != old_soc:
            writes.append(Write(i + 1, "soc", old_soc, soc))
        if charging is not None and charging != old_charging:
            writes.append(Write(i + 1, "charging", old_charging, charging))
    return writes


def problems_for(table: TouTable, intent: str) -> tuple[list[str], bool]:
    """(messages, blocking): what stops the controller, or limits what it can do."""
    problems: list[str] = []
    blocking = False
    if not slots_valid(table):
        problems.append(
            "Nie da się odczytać harmonogramu Time of Use (godziny programów 1–6) "
            "z integracji ha-solarman."
        )
        blocking = True
    if table.enabled is False:
        problems.append(
            "Time of Use jest wyłączony w falowniku. Sterowanie wymagałoby jego włączenia "
            "za zgodą użytkownika."
        )
        blocking = True
    if table.clock_drift_s is not None and abs(table.clock_drift_s) > MAX_CLOCK_DRIFT_S:
        problems.append(
            f"Zegar falownika różni się od zegara Home Assistant o {abs(table.clock_drift_s) / 60:.0f} min — "
            "harmonogram działałby w złych godzinach."
        )
        blocking = True
    if intent == "charge_grid" and table.grid_charging is False:
        problems.append(
            "Ładowanie z sieci jest wyłączone w ustawieniach baterii (Battery Grid Charging) — "
            "falownik nie naładowałby baterii z sieci."
        )
    return problems, blocking


# ── Shadow controller ───────────────────────────────────────────────────────


class TouShadow:
    """Runs the Deye controller without writing: what it would write, and how often."""

    def __init__(self, day_budget: int = DAY_WRITE_BUDGET, hour_budget: int = HOUR_WRITE_BUDGET) -> None:
        self.day_budget = day_budget
        self.hour_budget = hour_budget
        # Slots the controller would have changed: index → (SOC, charge source)
        self._applied: dict[int, tuple[int | None, str | None]] = {}
        self._sticky: tuple[str, int, tuple[int | None, str | None, str]] | None = None
        self._write_times: deque[datetime] = deque()
        self._day: str = ""
        self._writes_today: int = 0
        self.last: ShadowResult = ShadowResult(available=False)

    @property
    def writes_today(self) -> int:
        return self._writes_today

    def update(
        self,
        table: TouTable,
        *,
        intent: str,
        soc_now: float | None,
        now: datetime,
        charge_target: int = 100,
    ) -> ShadowResult:
        """`now` is Home Assistant local time (the inverter runs the table on its own clock)."""
        day = now.strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self._writes_today = day, 0
        while self._write_times and now - self._write_times[0] > timedelta(hours=1):
            self._write_times.popleft()

        problems, blocking = problems_for(table, intent)
        index = None if blocking else active_slot(table, now.hour * 60 + now.minute)
        if index is None:
            self.last = ShadowResult(
                available=False, intent=intent, problems=problems, writes_today=self._writes_today,
            )
            return self.last

        key = (intent, index)
        if self._sticky and self._sticky[:2] == key:
            target = self._sticky[2]
        else:
            target = slot_target(
                intent,
                table.slots[index],
                reserve=reserve_soc(table),
                low_soc=table.low_soc,
                soc_now=soc_now,
                charge_target=charge_target,
            )
            self._sticky = (intent, index, target)
        soc, charging, note = target

        base = [(s.soc, s.charging) for s in table.slots]
        desired = [(soc, charging) if i == index else base[i] for i in range(SLOT_COUNT)]
        actual = [self._applied.get(i, base[i]) for i in range(SLOT_COUNT)]
        writes = diff_writes(desired, actual)

        over_budget = False
        if writes:
            over_budget = (
                self._writes_today + len(writes) > self.day_budget
                or len(self._write_times) + len(writes) > self.hour_budget
            )
            self._writes_today += len(writes)
            self._write_times.extend([now] * len(writes))
        self._applied = {i: desired[i] for i in range(SLOT_COUNT) if desired[i] != base[i]}

        self.last = ShadowResult(
            available=True,
            intent=intent,
            slot=index + 1,
            window=slot_window(table, index),
            target_soc=soc,
            target_charging=charging,
            writes=writes,
            writes_today=self._writes_today,
            over_budget=over_budget,
            problems=problems,
            note=note,
        )
        return self.last


def describe_writes(result: ShadowResult) -> str:
    """One log line for the Autopilot decision log."""
    label = INTENT_LABELS.get(result.intent, result.intent)
    changes = "; ".join(w.describe() for w in result.writes)
    line = f"Deye — podgląd, nic nie zapisano ({label}, slot {result.slot} {result.window}): {changes}"
    if result.over_budget:
        line += f" ⚠️ ponad budżet zapisów ({result.writes_today} dziś)"
    return line
