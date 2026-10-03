# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Grid voltage guard — keep the inverter on the grid without burning energy.

The inverter disconnects when the 10-minute mean of a phase voltage goes above
253 V (230 V + 10 %, EN 50549-1). Exporting raises the voltage at the
connection point, so the guard watches the rolling 10-minute mean and, while
energy flows to the grid:
  1. lets the battery take it (charging enabled) while the battery isn't full,
  2. leaves the boiler to W4b (it switches on at ≥ 252 V with PV export),
  3. then lowers the grid export limit step by step — less export pulls the
     voltage down. The limit goes back up once the voltage has stayed low.
Loads that would only burn the energy (air conditioning) are not switched on.

Every period with the 10-minute mean above the limit is recorded — the voltage
report uses them for a complaint to the distribution system operator.
Settings "voltage_guard":
  {enabled, target_v, limit_v, release_v, step_w, min_export_w}
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "target_v": 251.5,     # act when the 10-min mean reaches this
    "limit_v": 253.0,      # inverter disconnect threshold (10-min mean)
    "release_v": 250.0,    # raise the export limit again below this
    "step_w": 1000,        # export limit change per step
    "min_export_w": 0,     # never limit export below this
}
WINDOW_S = 600             # 10-minute mean
MIN_COVERAGE_S = 300       # a mean needs ≥ 5 min of samples
HIGH_HOLD_S = 120          # high mean must last this long before acting
LOW_HOLD_S = 600           # … and low this long before raising the limit
CHANGE_EVERY_S = 180       # export limit changes at most every 3 min (inverter register)
CHARGE_EVERY_S = 600       # re-enable charging at most every 10 min
FAST_MARGIN_V = 2.0        # instant voltage ≥ limit + 2 V acts without waiting for the mean
EXPORTING_W = 200
EVENT_GAP_S = 60           # an exceedance ends after the mean stays below the limit this long


@dataclass
class VoltageInput:
    v: tuple[float, float, float]
    export_w: float          # + = to the grid
    pv_w: float
    soc: float
    battery_w: float         # + = discharge, − = charge
    export_limit_w: float | None = None  # current inverter export limit (for the first step)


@dataclass
class VoltageGuard:
    cfg: dict[str, Any] = field(default_factory=lambda: dict(DEFAULTS))
    cap_w: int | None = None               # export limit we set (None = not limiting)
    _samples: list[deque] = field(default_factory=lambda: [deque(), deque(), deque()])
    _high_since: float | None = None
    _low_since: float | None = None
    _last_change: float = float("-inf")
    _last_charge: float = float("-inf")
    _event: dict[str, Any] | None = None
    _event_below_since: float | None = None
    reason: str = ""
    last_mean: list[float | None] = field(default_factory=lambda: [None, None, None])

    def configure(self, cfg: dict[str, Any] | None) -> None:
        merged = dict(DEFAULTS)
        merged.update({k: v for k, v in (cfg or {}).items() if v is not None})
        self.cfg = merged

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    # ── 10-minute means ──
    def add_sample(self, now: float, v: tuple[float, float, float]) -> None:
        for i, val in enumerate(v):
            q = self._samples[i]
            if val and 150 < val < 300:  # 0 / garbage when the inverter is offline
                q.append((now, float(val)))
            while q and now - q[0][0] > WINDOW_S:
                q.popleft()

    def means(self, now: float) -> list[float | None]:
        out: list[float | None] = []
        for q in self._samples:
            if len(q) < 3 or now - q[0][0] < MIN_COVERAGE_S:
                out.append(None)
            else:
                out.append(sum(x for _, x in q) / len(q))
        self.last_mean = [round(m, 1) if m is not None else None for m in out]
        return out

    # ── decisions ──
    def decide(self, inp: VoltageInput, now: float | None = None) -> list[tuple[str, Any, str]]:
        """→ [(action, value, reason)], action ∈ {"charge", "cap", "release"}."""
        now = time.time() if now is None else now
        self.add_sample(now, inp.v)
        means = self.means(now)
        known = [m for m in means if m is not None]
        mean = max(known) if known else None
        inst = max(inp.v) if inp.v else 0.0
        target = float(self.cfg["target_v"])
        limit = float(self.cfg["limit_v"])
        release = float(self.cfg["release_v"])
        step = max(int(self.cfg["step_w"]), 100)
        floor = max(int(self.cfg["min_export_w"]), 0)
        exporting = inp.export_w > EXPORTING_W
        out: list[tuple[str, Any, str]] = []

        if not self.enabled:
            if self.cap_w is not None:
                self.cap_w = None
                out.append(("release", None, "strażnik napięcia wyłączony"))
            return out

        high = exporting and (
            (mean is not None and mean >= target) or inst >= limit + FAST_MARGIN_V
        )
        fast = exporting and inst >= limit + FAST_MARGIN_V
        if high:
            self._low_since = None
            if self._high_since is None:
                self._high_since = now
        else:
            self._high_since = None
            low = mean is None or mean <= release or not exporting
            if low:
                if self._low_since is None:
                    self._low_since = now
            else:
                self._low_since = None

        v_txt = f"{mean:.1f} V (śr. 10 min)" if mean is not None else f"{inst:.0f} V"
        if high and (fast or now - self._high_since >= HIGH_HOLD_S):
            # 1. The battery takes the energy while it can
            if inp.soc < 98 and inp.battery_w > -300 and now - self._last_charge >= CHARGE_EVERY_S:
                self._last_charge = now
                self.reason = f"{v_txt} przy eksporcie {inp.export_w:.0f} W — ładuję baterię zamiast oddawać"
                out.append(("charge", None, self.reason))
                return out
            # 2. Export limit, step by step
            if now - self._last_change >= CHANGE_EVERY_S:
                base = self.cap_w if self.cap_w is not None else int(inp.export_w)
                if inp.export_limit_w is not None and self.cap_w is None:
                    base = min(base, int(inp.export_limit_w))
                new_cap = max(floor, (base - step) // 100 * 100)
                if self.cap_w is None or new_cap < self.cap_w:
                    self.cap_w = new_cap
                    self._last_change = now
                    self.reason = (f"{v_txt} przy eksporcie {inp.export_w:.0f} W — "
                                   f"ograniczam oddawanie do {new_cap} W, żeby falownik się nie wyłączył")
                    out.append(("cap", new_cap, self.reason))
            return out

        if self.cap_w is not None:
            if inp.pv_w < 100:
                self.cap_w = None
                self._last_change = now
                self.reason = "koniec produkcji PV — limit oddawania przywrócony"
                out.append(("release", None, self.reason))
            elif (self._low_since is not None and now - self._low_since >= LOW_HOLD_S
                    and now - self._last_change >= CHANGE_EVERY_S):
                new_cap = self.cap_w + int(step * 1.5)
                self._last_change = now
                self._low_since = now  # next step only after another quiet period
                if new_cap >= max(int(inp.pv_w) + step, 2 * step):
                    self.cap_w = None
                    self.reason = f"napięcie spadło do {v_txt} — limit oddawania przywrócony"
                    out.append(("release", None, self.reason))
                else:
                    self.cap_w = new_cap
                    self.reason = f"napięcie spadło do {v_txt} — podnoszę limit oddawania do {new_cap} W"
                    out.append(("cap", new_cap, self.reason))
        return out

    # ── exceedance log ──
    def track_exceedance(self, inp: VoltageInput, now: float | None = None) -> dict[str, Any] | None:
        """Call after decide(). Returns a finished exceedance (10-min mean > limit) to persist."""
        now = time.time() if now is None else now
        limit = float(self.cfg["limit_v"])
        means = self.last_mean
        known = [(i, m) for i, m in enumerate(means) if m is not None]
        above = [(i, m) for i, m in known if m > limit]
        inst = max(inp.v) if inp.v else 0.0
        if above:
            self._event_below_since = None
            phase, m = max(above, key=lambda x: x[1])
            if self._event is None:
                self._event = {
                    "start": now, "end": now, "max_mean": m, "max_v": inst,
                    "phases": sorted({f"L{i + 1}" for i, _ in above}),
                    "export_w": round(inp.export_w), "pv_w": round(inp.pv_w),
                    "max_export_w": round(inp.export_w), "capped": self.cap_w is not None,
                }
            else:
                e = self._event
                e["end"] = now
                e["max_mean"] = max(e["max_mean"], m)
                e["max_v"] = max(e["max_v"], inst)
                e["phases"] = sorted(set(e["phases"]) | {f"L{i + 1}" for i, _ in above})
                e["max_export_w"] = max(e["max_export_w"], round(inp.export_w))
                e["capped"] = e["capped"] or self.cap_w is not None
            return None
        if self._event is None:
            return None
        if self._event_below_since is None:
            self._event_below_since = now
            return None
        if now - self._event_below_since < EVENT_GAP_S:
            return None
        done, self._event, self._event_below_since = self._event, None, None
        done["minutes"] = round((done["end"] - done["start"]) / 60 + 0.5, 1)
        done["max_mean"] = round(done["max_mean"], 1)
        done["max_v"] = round(done["max_v"], 1)
        return done

    def status(self) -> dict[str, Any]:
        return {**self.cfg, "cap_w": self.cap_w, "reason": self.reason, "mean_10min": self.last_mean,
                "exceeding": self._event is not None}
