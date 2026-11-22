# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Space heating from the buffer — weather curve, room correction, 3-point valve, CH pump,
hot-water charging pump.

Pure Python, no Home Assistant imports (heating_ha.py executes the commands).

Home Assistant is never the boiler's safety (the thermal discharge valve, the boiler's
thermostats, the Laddomat and the Shelly guard script are). What this module adds on
top can only take heat AWAY from the boiler, never hold it in:
- heat dump (radiators only): boiler overheating → mixing valve fully open + CH pump on,
- supply limiter: supply far above its maximum → the valve is closed in steps,
- a probe lost during a fire → CH pump on.

Modes: "off" (measure only — nothing is ever switched), "manual" (the user drives the
valve / pump from the panel, safety stays on), "auto", "summer" (valve closed, pump
off, a short weekly run against seizing).

Valve: a 3-point (floating) actuator, e.g. ESBE ARA661 (120 s / 90°, no position
feedback). Velocity PI: every evaluation a pulse proportional to the error (I) and to its
change (P), then a pause for the transport delay and the probe in its pocket to catch
up. The position is estimated from the pulses; a full close at the start ("homing")
makes it known, every drive into an end stop re-syncs it.

Hot-water charging pump (the coil in the tank fed from the buffer top or straight from
the boiler): differential control in "auto" and "summer" — on when the source is
dhw_on_diff_k warmer than the tank and the tank fell dhw_hyst_k below its target, off at
the target or when the difference drops under dhw_off_diff_k; off at once (no minimum
run time) when the source turns colder than the tank, so the tank never heats the
buffer back. A hot boiler tops the tank up to its target in every mode but "off" (with
dhw_dump up to dhw_max_c — only with a thermostatic mixer on the hot-water outlet).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .heating_model import MODES, AlarmSpec

# valve
DEADBAND_K = 0.8
KI_S = 1.2            # s of drive per K of error, each evaluation
KP_S = 3.0            # s of drive per K of change of the error since the last evaluation
MIN_PULSE_S = 2.0
MAX_PULSE_S = 15.0
SETTLE_S = 60.0       # after a pulse: transport delay + the probe in its pocket
OVERRUN_S = 15.0      # drive allowed past the estimated end stop before "saturated"
END_SLACK_S = 0.5     # rounding slack when judging that the overrun is done
MAX_RUN_PER_H = 900.0
HOME_EXTRA_S = 10.0
VALVE_EVERY_S = 20.0
LOCK_REASON = "wyłącznik awaryjny w panelu"
HAND_REASON = "przełącznik RĘKA w panelu"
DUMP_REDRIVE_S = 300.0      # heat dump: drive fully open again every 5 min (the estimate may be wrong)
RETRY_S = 120.0             # an actuator that didn't take a command: next try after this
RETRY_SAFETY_S = 30.0

# pump
PUMP_MIN_ON_S = 300.0
PUMP_MIN_OFF_S = 300.0
VALVE_PARK_AFTER_S = 120.0   # the CH pump off this long → close the mixing valve (no gravity flow to the radiators)
FAN_DELAY_S = 120.0          # a unit heater's fan follows the CH pump this late, on and off (warm coil, no cold draught)
EXERCISE_EVERY_S = 7 * 86400
EXERCISE_S = 60.0
PUMP_RETRY_S = 60.0
DHW_EXERCISE_S = 30.0       # the weekly run of the hot-water pump: short, the coil may be colder than the tank

# room correction
ROOM_I_TAU_S = 7200.0
ROOM_I_MAX = 4.0
ROOM_CORR_MAX = 6.0
ROOM_I_UNWIND = 8.0         # the memory pushing against the house (≥ ROOM_I_WRONG_K off) fades this many times faster
ROOM_I_WRONG_K = 0.3

# heating on / off by the house: on below target + ON_K, off at target + OFF_K
ROOM_ON_K = 0.5
ROOM_OFF_K = 1.0

OUT_FAST_TAU_S = 900.0
OUT_DAMPED_TAU_S = 6 * 3600.0


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(max(v, lo), hi)


def curve_target(cfg: dict[str, Any], t_out: float, room_target: float) -> float:
    """Weather curve: T_supply = T_room + slope·(T_room − T_out) + shift, within [min, max]."""
    t = room_target + cfg["curve_slope"] * (room_target - t_out) + cfg["curve_shift"]
    return _clamp(t, cfg["supply_min_c"], cfg["supply_max_c"])


def in_hours(start: int, end: int, local_hour: float) -> bool:
    """start ≤ hour < end, across midnight when start > end; start == end is an empty window."""
    if start == end:
        return False
    return (start <= local_hour or local_hour < end) if start > end else (start <= local_hour < end)


def in_night(cfg: dict[str, Any], local_hour: float) -> bool:
    return in_hours(cfg["night_from"], cfg["night_to"], local_hour)


def dhw_windows(cfg: dict[str, Any]) -> list[tuple[int, int]]:
    """The hot-water charging windows that are switched on (from ≠ to)."""
    pairs = [(cfg["dhw_win1_from"], cfg["dhw_win1_to"]), (cfg["dhw_win2_from"], cfg["dhw_win2_to"])]
    return [(a, b) for a, b in pairs if a != b]


def fan_follow(mode: str, pump_on: bool | None, pump_for_s: float, fan_on: bool | None,
               dump: bool = False) -> tuple[str | None, float]:
    """A unit heater's fan on the CH circuit (behind the CH pump) follows the pump — whatever switched the pump:
    the module, HAND, the Shelly's own guard script — FAN_DELAY_S late both ways: on once the pump has run that
    long (the coil is warm by then, no cold draught), off once it has stood that long (the coil still holds hot
    water). During a heat dump (the boiler too hot) it starts at once: the water is hot and every radiator,
    the heater too, has to take heat now. pump_for_s: how long the pump has been in its present state.
    → (command, seconds left before the fan follows). No command: mode off (measurements only), the pump's state
    unknown, the fan's socket offline, the fan already right, or the delay still running."""
    if mode == "off" or pump_on is None or fan_on is None or pump_on == fan_on:
        return None, 0.0
    if dump and pump_on:
        return "on", 0.0
    left = FAN_DELAY_S - max(pump_for_s, 0.0)
    if left > 0:
        return None, left
    return ("on" if pump_on else "off"), 0.0


def night_now(cfg: dict[str, Any], local_hour: float) -> bool:
    """The night (setback) target is in force."""
    return bool(cfg.get("night_enabled")) and in_night(cfg, local_hour)


def room_target_now(cfg: dict[str, Any], local_hour: float) -> float:
    """The house target in force: day, or the night one (never above the day target)."""
    day = cfg["room_target_c"]
    return min(cfg["room_night_c"], day) if night_now(cfg, local_hour) else day


class Ema:
    def __init__(self, tau_s: float) -> None:
        self.tau_s = tau_s
        self.value: float | None = None
        self.ts: float | None = None

    def add(self, value: float | None, now: float) -> float | None:
        if value is None:
            return self.value
        if self.value is None or self.ts is None:
            self.value, self.ts = value, now
            return value
        dt = max(now - self.ts, 0.0)
        a = 1.0 - math.exp(-dt / self.tau_s) if dt > 0 else 0.0
        self.value += a * (value - self.value)
        self.ts = now
        return self.value


# ── valve ────────────────────────────────────────────────────────────────────

@dataclass
class ValveCommand:
    direction: str      # "open" (hotter supply) / "close"
    seconds: float
    reason: str
    kind: str = "auto"  # auto / home / safety / manual / park (closed while the CH pump stands)


@dataclass
class ValveController:
    run_s: float = 120.0
    position: float = 50.0          # % open (estimated)
    known: bool = False
    busy_until: float = 0.0
    settle_until: float = 0.0
    last_eval: float = 0.0
    last_err: float | None = None
    over_open_s: float = 0.0
    over_close_s: float = 0.0
    saturated: str = ""
    retry_at: float = 0.0
    runs: deque = field(default_factory=lambda: deque(maxlen=400))
    moves: deque = field(default_factory=lambda: deque(maxlen=30))

    def dump(self) -> dict[str, Any]:
        return {"position": round(self.position, 1), "known": self.known,
                "over_open_s": self.over_open_s, "over_close_s": self.over_close_s,
                "moves": list(self.moves)}

    def restore(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        try:
            self.position = _clamp(float(data.get("position", 50.0)), 0.0, 100.0)
            self.known = bool(data.get("known"))
            self.over_open_s = float(data.get("over_open_s") or 0.0)
            self.over_close_s = float(data.get("over_close_s") or 0.0)
        except (TypeError, ValueError):
            self.known = False
        self.moves.extend(m for m in (data.get("moves") or []) if isinstance(m, dict))

    def run_last_hour(self, now: float) -> float:
        return sum(s for t, s in self.runs if now - t < 3600)

    def apply(self, cmd: ValveCommand, now: float) -> None:
        """Book a move into the position estimate (also for manual moves)."""
        delta = cmd.seconds / self.run_s * 100.0
        if cmd.direction == "open":
            beyond = max(self.position + delta - 100.0, 0.0)
            self.position = min(self.position + delta, 100.0)
            self.over_open_s += beyond / 100.0 * self.run_s
            self.over_close_s = 0.0
        else:
            beyond = max(delta - self.position, 0.0)
            self.position = max(self.position - delta, 0.0)
            self.over_close_s += beyond / 100.0 * self.run_s
            self.over_open_s = 0.0
            if cmd.kind == "home" or self.over_close_s >= 5.0:
                self.position, self.known = 0.0, True
        if cmd.kind == "home":
            self.position, self.known = 0.0, True
        self.busy_until = now + cmd.seconds
        self.settle_until = now + cmd.seconds + SETTLE_S
        self.runs.append((now, cmd.seconds))
        self.moves.append({"ts": now, "dir": cmd.direction, "s": round(cmd.seconds, 1),
                           "pos": round(self.position, 0), "kind": cmd.kind, "reason": cmd.reason})

    def undo(self, cmd: ValveCommand) -> None:
        """The actuator didn't take the command — take it back out of the estimate."""
        delta = cmd.seconds / self.run_s * 100.0
        self.position = _clamp(self.position + (-delta if cmd.direction == "open" else delta), 0.0, 100.0)
        self.busy_until = self.settle_until = 0.0
        if self.moves:
            self.moves.pop()
        if self.runs:
            self.runs.pop()
        if cmd.kind == "home":
            self.known = False

    def failed(self, now: float, safety: bool = False) -> None:
        """The actuator didn't take the command — don't hammer it every tick."""
        self.retry_at = now + (RETRY_SAFETY_S if safety else RETRY_S)

    def home(self) -> ValveCommand:
        return ValveCommand("close", self.run_s + HOME_EXTRA_S,
                            "kalibracja położenia — dojazd do pełnego zamknięcia", "home")

    def at_end(self, direction: str) -> bool:
        # a full-stroke move books its overrun as 14.999… s (float), not 15 — without the slack the same
        # end got a second 15 s drive on the next tick
        if direction == "open":
            return self.position >= 100.0 and self.over_open_s >= OVERRUN_S - END_SLACK_S
        return self.position <= 0.0 and self.over_close_s >= OVERRUN_S - END_SLACK_S

    def step(self, now: float, target: float | None, actual: float | None) -> ValveCommand | None:
        if (now < self.busy_until or now < self.settle_until or now < self.retry_at
                or now - self.last_eval < VALVE_EVERY_S):
            return None
        self.last_eval = now
        if not self.known:
            return self.home()
        if target is None or actual is None:
            self.last_err = None
            return None
        err = target - actual
        if abs(err) <= DEADBAND_K:
            self.last_err, self.saturated = err, ""
            return None
        d_err = 0.0 if self.last_err is None else err - self.last_err
        self.last_err = err
        drive = KI_S * err + KP_S * d_err
        if drive * err <= 0:
            return None                       # the error is already closing fast — wait
        if abs(drive) < MIN_PULSE_S:
            drive = math.copysign(MIN_PULSE_S, err)
        drive = _clamp(drive, -MAX_PULSE_S, MAX_PULSE_S)
        direction = "open" if drive > 0 else "close"
        if self.at_end(direction):
            self.saturated = direction        # fully open and still too cold (or the opposite)
            return None
        self.saturated = ""
        if self.run_last_hour(now) + abs(drive) > MAX_RUN_PER_H:
            return None
        return ValveCommand(direction, round(abs(drive), 1),
                            f"zasilanie {actual:.1f} °C, zadane {target:.1f} °C")

    def force(self, direction: str, reason: str, now: float, step_s: float | None = None) -> ValveCommand | None:
        """Safety moves: no settle time, no hourly limit; stop once in the end stop."""
        if now < self.busy_until or now < self.retry_at or self.at_end(direction):
            return None
        if step_s is None:
            room = (100.0 - self.position) if direction == "open" else self.position
            step_s = room / 100.0 * self.run_s + OVERRUN_S
        return ValveCommand(direction, round(min(step_s, self.run_s + OVERRUN_S), 1), reason, "safety")


# ── pump ─────────────────────────────────────────────────────────────────────

@dataclass
class PumpController:
    last_change: float = 0.0
    last_on: float = 0.0
    exercise_until: float = 0.0
    last_cmd: str = ""
    last_cmd_ts: float = 0.0

    def dump(self) -> dict[str, Any]:
        return {"last_change": self.last_change, "last_on": self.last_on}

    def restore(self, data: dict[str, Any] | None) -> None:
        if isinstance(data, dict):
            self.last_change = float(data.get("last_change") or 0.0)
            self.last_on = float(data.get("last_on") or 0.0)

    def note(self, on: bool, now: float) -> None:
        self.last_change = now
        if on:
            self.last_on = now

    def decide(self, now: float, current: bool | None, want: bool | None, reason: str,
               force_on: bool = False, urgent: bool = False) -> tuple[str, str] | None:
        """urgent: switch off without waiting for the minimum run time."""
        if current is None:
            return None
        if current:
            self.last_on = now
        if force_on:
            return None if current else self._attempt("on", reason, now)
        if want is None or want == current:
            return None
        hold = PUMP_MIN_ON_S if current else PUMP_MIN_OFF_S
        if self.last_change and now - self.last_change < hold and not (urgent and current):
            return None
        return self._attempt("on" if want else "off", reason, now)

    def _attempt(self, cmd: str, reason: str, now: float) -> tuple[str, str] | None:
        """A relay that doesn't follow (offline, stuck) gets the same command once a minute, not every tick."""
        if cmd == self.last_cmd and now - self.last_cmd_ts < PUMP_RETRY_S:
            return None
        self.last_cmd, self.last_cmd_ts = cmd, now
        return cmd, reason


# ── controller ───────────────────────────────────────────────────────────────

@dataclass
class ControlInput:
    now: float
    local_hour: float
    t_supply: float | None = None
    t_top: float | None = None
    t_bottom: float | None = None    # buffer bottom — stands in where the top has no probe (never warmer than it)
    t_boiler: float | None = None
    t_room: float | None = None
    t_out: float | None = None
    pump_on: bool | None = None
    pump_configured: bool = False
    valve_configured: bool = False
    overheat: bool = False           # model alarm "heating_overheat" active
    probe_fault: bool = False        # boiler / buffer probe without a valid reading for ≥ 60 s
    room_licensed: bool = True       # room correction is a PRO feature
    t_dhw: float | None = None
    dhw_pump_on: bool | None = None
    dhw_pump_configured: bool = False


@dataclass
class DhwPlan:
    want: bool = False
    force: bool = False          # boiler too hot: take heat into the tank (ignores the minimum off time)
    urgent: bool = False         # off now, no minimum run time (the tank would heat the source)
    reason: str = ""
    source_c: float | None = None
    dump: bool = False           # above the normal target (dhw_dump)


@dataclass
class ControlOutput:
    mode: str
    room_target: float
    night: bool = False              # the night (setback) target is in force
    supply_target: float | None = None
    curve_c: float | None = None
    room_corr: float = 0.0
    t_out: float | None = None
    t_out_damped: float | None = None
    heating_needed: bool = False
    dump: bool = False
    limiter: bool = False
    valve: ValveCommand | None = None
    pump: tuple[str, str] | None = None
    dhw: DhwPlan = field(default_factory=DhwPlan)
    dhw_pump: tuple[str, str] | None = None
    # why the CH pump is held on by a safety rule ("dump" / "hot" / "probe"), also in "off" where it is not acted on
    pump_forced: str = ""
    log: list[str] = field(default_factory=list)
    alarms: list[AlarmSpec] = field(default_factory=list)
    note: str = ""


class HeatingController:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.valve = ValveController(run_s=cfg["valve_run_s"])
        self.pump = PumpController()
        self.dhw_pump = PumpController()
        self.dhw_charging = False
        self.out_fast = Ema(OUT_FAST_TAU_S)
        self.out_damped = Ema(OUT_DAMPED_TAU_S)
        self.room_i = 0.0
        self._room_ts: float | None = None
        self._night: bool | None = None
        self._target: float | None = None       # the house target the room memory was built for
        self.needed = False
        self.hot = False
        self.dumping = False
        self.limiting = False
        self._dump_drive_ts: float | None = None
        self._invert = cfg["valve_invert"]
        self.mode = cfg["mode"]
        self.last: ControlOutput | None = None

    def configure(self, cfg: dict[str, Any]) -> list[str]:
        log = []
        if cfg["mode"] != self.mode:
            log.append(f"Tryb: {MODES.get(cfg['mode'], cfg['mode'])}")
            self.room_i = 0.0
            self.mode = cfg["mode"]
        if cfg["valve_run_s"] != self.valve.run_s or cfg["valve_invert"] != self._invert:
            self.valve.run_s = cfg["valve_run_s"]
            self._invert = cfg["valve_invert"]
            self.valve.known = False        # actuator changed: find the end stop again
        self.cfg = cfg
        return log

    def dump_state(self) -> dict[str, Any]:
        return {"valve": self.valve.dump(), "pump": self.pump.dump(), "dhw_pump": self.dhw_pump.dump(),
                "room_i": round(self.room_i, 3),
                "out_damped": self.out_damped.value, "out_damped_ts": self.out_damped.ts, "mode": self.mode,
                "run_s": self.valve.run_s, "invert": self._invert}

    def restore(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        self.valve.restore(data.get("valve"))
        self.pump.restore(data.get("pump"))
        self.dhw_pump.restore(data.get("dhw_pump"))
        # the actuator the position was estimated with — a restart must not look like a new actuator
        if isinstance(data.get("run_s"), (int, float)) and data["run_s"] > 0:
            self.valve.run_s = float(data["run_s"])
        if isinstance(data.get("invert"), bool):
            self._invert = data["invert"]
        try:
            self.room_i = _clamp(float(data.get("room_i") or 0.0), -ROOM_I_MAX, ROOM_I_MAX)
        except (TypeError, ValueError):
            self.room_i = 0.0
        if data.get("out_damped") is not None:
            self.out_damped.value = float(data["out_damped"])
            self.out_damped.ts = float(data.get("out_damped_ts") or 0.0) or None
        if data.get("mode") in MODES:
            self.mode = data["mode"]

    # ── one evaluation ──────────────────────────────────────────────────────
    def step(self, inp: ControlInput) -> ControlOutput:
        out = self._step(inp)
        self._apply_locks(inp, out)
        return out

    def _apply_locks(self, inp: ControlInput, out: ControlOutput) -> None:
        """The panel's emergency switches win over everything, safety rules included: a locked
        pump is only ever switched off, a locked valve is not moved. A safety rule that wanted
        a locked element raises an alarm instead. A pump in HAND runs whatever the mode wants."""
        locks = self.cfg.get("lockout") or {}
        acting = self.cfg["mode"] != "off"
        blocked: list[str] = []
        if locks.get("pump") and inp.pump_configured:
            if acting and out.pump_forced:
                blocked.append("pompa CO")
            out.pump = ("off", LOCK_REASON) if inp.pump_on is True else None
        if locks.get("dhw_pump") and inp.dhw_pump_configured:
            if acting and out.dhw.force and out.dhw.want:
                blocked.append("pompa CWU")
            out.dhw_pump = ("off", LOCK_REASON) if inp.dhw_pump_on is True else None
        if locks.get("valve") and inp.valve_configured:
            if acting and (self.dumping or self.limiting):
                blocked.append("zawór")
            out.valve = None
        hand = self.cfg.get("force_on") or {}
        if hand.get("pump") and inp.pump_configured and not locks.get("pump"):
            out.pump = ("on", HAND_REASON) if inp.pump_on is False else None
        if hand.get("dhw_pump") and inp.dhw_pump_configured and not locks.get("dhw_pump"):
            out.dhw_pump = ("on", HAND_REASON) if inp.dhw_pump_on is False else None
        if blocked:
            why = ("kocioł gorący" if self.hot else
                   "brak odczytu sondy kotła lub bufora" if inp.probe_fault else "ogranicznik zasilania")
            out.alarms.append(AlarmSpec(
                "heating_lock_blocks_safety", "critical" if self.hot else "warning",
                "Wyłącznik awaryjny blokuje zabezpieczenie",
                f"Zabezpieczenie ({why}) chce włączyć: {', '.join(blocked)}, ale wyłącznik awaryjny w panelu to blokuje.",
                "Gdy instalacja ma wodę, odblokuj w panelu: Sterowanie → Wyłączniki awaryjne."))

    def _step(self, inp: ControlInput) -> ControlOutput:
        cfg, now = self.cfg, inp.now
        mode = cfg["mode"]
        room_target = room_target_now(cfg, inp.local_hour)
        night = night_now(cfg, inp.local_hour)
        out = ControlOutput(mode=mode, room_target=room_target, night=night)
        if self._night is not None and night != self._night and mode == "auto":
            out.log.append(f"🌙 Noc: zadana w domu {room_target:.1f} °C (do {cfg['night_to']}:00)" if night
                           else f"☀️ Dzień: zadana w domu {room_target:.1f} °C")
        self._night = night
        out.t_out = self.out_fast.add(inp.t_out, now)
        out.t_out_damped = self.out_damped.add(inp.t_out, now)

        # heating needed: cold enough outside, heat in the buffer, the room not overheated
        limit = cfg["heating_limit_c"]
        damped = out.t_out_damped
        cold = damped is None or damped < (limit + 1.0 if self.needed else limit)
        buffer_ok = inp.t_top is None or inp.t_top >= room_target + 5.0
        room_ok = inp.t_room is None or inp.t_room < room_target + (ROOM_OFF_K if self.needed else ROOM_ON_K)
        self.needed = cold and buffer_ok and room_ok
        out.heating_needed = self.needed and mode == "auto"

        # supply target: curve + room correction, never above what the buffer can give
        t_out = out.t_out if out.t_out is not None else 0.0
        if out.t_out is None:
            out.note = "brak temperatury zewnętrznej — krzywa liczona dla 0 °C"
        out.curve_c = round(curve_target(cfg, t_out, room_target), 1)
        corr = 0.0
        flowing = inp.pump_on is True or not inp.pump_configured
        # a new target (set by hand, or day ↔ night) starts the room memory afresh: what was learnt for the
        # old one would push the wrong way (a warm night target left +4 K in a house that is already warm)
        if self._target is not None and abs(room_target - self._target) >= 0.05:
            self.room_i = 0.0
        self._target = room_target
        if (mode == "auto" and cfg["room_correction"] and inp.room_licensed and inp.t_room is not None
                and cfg["room_gain"] > 0):
            err = room_target - inp.t_room
            if self._room_ts is not None:
                dt = min(max(now - self._room_ts, 0.0), 600.0)
                step = cfg["room_gain"] * err * dt / ROOM_I_TAU_S
                wrong = self.room_i * err < 0 and abs(err) >= ROOM_I_WRONG_K   # memory against the house
                if wrong:
                    step *= ROOM_I_UNWIND
                if flowing and self.needed:
                    self.room_i = _clamp(self.room_i + step, -ROOM_I_MAX, ROOM_I_MAX)
                elif wrong:
                    # not heating now, but the memory would still push the wrong way when heating resumes:
                    # let it fade, never past zero
                    i = self.room_i + step
                    self.room_i = max(i, 0.0) if self.room_i > 0 else min(i, 0.0)
            self._room_ts = now
            corr = _clamp(cfg["room_gain"] * err + self.room_i, -ROOM_CORR_MAX, ROOM_CORR_MAX)
        else:
            self._room_ts = None
        out.room_corr = round(corr, 2)
        target = _clamp(out.curve_c + corr, cfg["supply_min_c"], cfg["supply_max_c"])
        if inp.t_top is not None:
            target = min(target, max(inp.t_top - 2.0, cfg["supply_min_c"]))
        out.supply_target = round(target, 1)

        # safety states (computed in every mode, acted on in all but "off"). "Hot" is a superset of the
        # Shelly guard's rule (heating_shelly.guard_config: boiler ≥ alarm − 2, buffer top ≥ full + 4,
        # released 3 K lower), so Home Assistant never works against the script on the pump's Shelly.
        # Without a top probe the bottom stands in: it is never warmer than the top, so it can only
        # trigger later than the top would — never a false alarm.
        tb = inp.t_boiler
        tt = inp.t_top if inp.t_top is not None else inp.t_bottom
        boiler_hot = cfg["alarm_c"] - 2.0
        buffer_hot = min(cfg["buffer_full_c"] + 4.0, 95.0)
        pair = tb is not None and tt is not None and tb >= cfg["warn_c"] and tt >= cfg["buffer_full_c"]
        hot_now = (inp.overheat or pair or (tb is not None and tb >= boiler_hot)
                   or (tt is not None and tt >= buffer_hot))
        if self.hot and not hot_now:
            hot_now = ((tb is not None and tb >= boiler_hot - 3.0) or (tt is not None and tt >= buffer_hot - 3.0)
                       or (tb is not None and tt is not None and tb >= cfg["warn_c"] - 3.0
                           and tt >= cfg["buffer_full_c"] - 3.0))
        dump_now = bool(cfg["safety_dump"]) and hot_now
        if hot_now != self.hot and mode != "off":
            out.log.append(("⚠️ Zrzut ciepła do domu: zawór otwarty, pompa CO włączona" if dump_now
                            else "⚠️ Kocioł gorący — pompa CO wymuszona") if hot_now
                           else "Kocioł poniżej progów — koniec wymuszenia pompy" + (" i zrzutu ciepła" if self.dumping else ""))
        self.hot = hot_now
        if not dump_now:
            self._dump_drive_ts = None
        self.dumping = dump_now
        lim_now = inp.t_supply is not None and (
            inp.t_supply > cfg["supply_max_c"] + 3.0 or (self.limiting and inp.t_supply > cfg["supply_max_c"] + 1.0))
        lim_now = lim_now and not dump_now
        if lim_now != self.limiting and mode != "off":
            out.log.append(f"Ogranicznik: zasilanie {inp.t_supply:.1f} °C > {cfg['supply_max_c']:.0f} °C — zamykam zawór"
                           if lim_now else "Ogranicznik zasilania zwolniony")
        self.limiting = lim_now
        out.dump, out.limiter = self.dumping, self.limiting
        if self.limiting:
            out.alarms.append(AlarmSpec(
                "heating_supply_hot", "warning", f"Zasilanie CO za gorące: {inp.t_supply:.0f} °C",
                f"Zasilanie {inp.t_supply:.1f} °C przy maksimum {cfg['supply_max_c']:.0f} °C.",
                "Automatyka przymyka zawór. Sprawdź siłownik (kierunek, sprzęgło) i tryb ręczny zaworu.",
                hold_s=60))

        out.dhw = self._dhw_plan(inp)
        if inp.pump_configured:
            out.pump_forced = ("dump" if self.dumping else "hot") if self.hot else ("probe" if inp.probe_fault else "")

        if mode == "off":
            self.last = out
            return out

        # safety actions — they may only take heat away from the boiler
        force_pump = self.hot or inp.probe_fault
        pump_reason = ("zrzut ciepła — kocioł gorący" if self.dumping else "kocioł gorący — odbiór ciepła"
                       if self.hot else "brak odczytu sondy kotła/bufora")
        if inp.valve_configured:
            if self.dumping:
                # a full run regardless of the estimate (it may be wrong), again every few minutes,
                # pre-empting a running close (homing, summer)
                due = self._dump_drive_ts is None or now - self._dump_drive_ts >= DUMP_REDRIVE_S
                if due and now >= self.valve.retry_at:
                    out.valve = ValveCommand("open", round(self.valve.run_s + OVERRUN_S, 1),
                                             "zrzut ciepła — kocioł gorący", "safety")
                    self._dump_drive_ts = now
            elif self.limiting:
                out.valve = self.valve.force("close", "ogranicznik temperatury zasilania", now, MAX_PULSE_S)
        if inp.pump_configured and force_pump:
            out.pump = self.pump.decide(now, inp.pump_on, True, pump_reason, force_on=True)
        if inp.dhw_pump_configured and out.dhw.force:
            out.dhw_pump = self.dhw_pump.decide(now, inp.dhw_pump_on, True, out.dhw.reason, force_on=True)

        if mode == "manual":
            self.last = out
            return out

        if inp.dhw_pump_configured and not out.dhw.force:
            out.dhw_pump = self._dhw_auto(inp, out.dhw)

        if mode == "summer":
            if inp.pump_configured and not force_pump:
                if self.pump.exercise_until and now >= self.pump.exercise_until:
                    self.pump.exercise_until = 0.0
                    if inp.pump_on:
                        out.pump = ("off", "koniec przepłukania pompy")
                elif inp.pump_on is False and now - self.pump.last_on > EXERCISE_EVERY_S:
                    out.pump = self.pump._attempt("on", "przepłukanie pompy (raz w tygodniu, latem)", now)
                    if out.pump:
                        self.pump.exercise_until = now + EXERCISE_S
                elif not self.pump.exercise_until:
                    out.pump = self.pump.decide(now, inp.pump_on, False, "lato — bez grzania domu")
            if inp.valve_configured and out.valve is None and not (self.dumping or self.limiting):
                if not self.valve.known:
                    ready = now >= self.valve.busy_until and now >= self.valve.retry_at
                    out.valve = self.valve.home() if ready else None
                elif not self.valve.at_end("close"):
                    out.valve = self.valve.force("close", "lato — zawór zamknięty", now)
                    if out.valve:
                        out.valve.kind = "auto"
            self.last = out
            return out

        # auto
        if inp.pump_configured and not force_pump:
            out.pump = self.pump.decide(now, inp.pump_on, self.needed, self._pump_reason(inp, room_target))
        if inp.valve_configured and out.valve is None and not (self.dumping or self.limiting):
            if self.needed and flowing:
                out.valve = self.valve.step(now, out.supply_target, inp.t_supply)
            elif not self.valve.known and now >= self.valve.busy_until and now >= self.valve.retry_at:
                out.valve = self.valve.home()
            elif (inp.pump_configured and inp.pump_on is False and now - self.pump.last_on >= VALVE_PARK_AFTER_S
                  and not self.valve.at_end("close")):
                # A standing pump with an open valve still lets the buffer's hot water rise into the radiators by
                # gravity (bobrek, night 21–22.11.2026: ~17 kWh without the pump, the night setback lost). Closed,
                # the valve also opens step by step when heating resumes instead of sending the buffer's top
                # straight to cold radiators.
                out.valve = self.valve.force("close", "pompa CO stoi — zawór zamknięty (bez przepływu grawitacyjnego)", now)
                if out.valve:
                    out.valve.kind = "park"        # logged once, an ordinary retry if the actuator doesn't follow
        self.last = out
        return out

    # ── hot-water charging pump ─────────────────────────────────────────────
    def _dhw_plan(self, inp: ControlInput) -> DhwPlan:
        """What the hot-water pump should do (computed in every mode, acted on in all but "off")."""
        cfg = self.cfg
        boiler = cfg["dhw_source"] == "boiler_out"
        src_name = "kocioł" if boiler else "bufor"
        t_src = inp.t_boiler if boiler else inp.t_top
        if not boiler and t_src is None and inp.t_bottom is not None:
            # no top probe: the bottom is the coolest water in the buffer — charging starts a little later,
            # but never with water colder than the tank
            t_src, src_name = inp.t_bottom, "bufor (dół)"
        t_dhw = inp.t_dhw
        plan = DhwPlan(source_c=t_src)
        if cfg["dhw_mode"] == "schedule":
            return self._dhw_by_clock(inp, plan, t_src, src_name, boiler)
        if t_src is None or t_dhw is None:
            self.dhw_charging = False
            plan.urgent = True
            plan.reason = f"brak odczytu sondy {'CWU' if t_dhw is None else 'kotła' if boiler else 'bufora'}"
            return plan
        diff = t_src - t_dhw
        target, cap = cfg["dhw_target_c"], cfg["dhw_max_c"]
        if t_dhw >= cap:
            self.dhw_charging = False
            plan.urgent = True
            plan.reason = f"CWU {t_dhw:.0f} °C — górny limit {cap:.0f} °C"
            return plan
        if diff < 0:
            self.dhw_charging = False
            plan.urgent = True
            plan.reason = f"{src_name} ({t_src:.0f} °C) chłodniejszy niż CWU ({t_dhw:.0f} °C)"
            return plan
        limit = cap if cfg["dhw_dump"] else target
        if self.hot and diff >= cfg["dhw_off_diff_k"] and t_dhw < limit:
            # taking heat away from a hot boiler — the tank is a heat sink up to its limit
            self.dhw_charging = True
            plan.want = plan.force = True
            plan.dump = t_dhw >= target
            plan.reason = ("zrzut ciepła do CWU — kocioł gorący" if cfg["dhw_dump"]
                           else "kocioł gorący — CWU dogrzewana do zadanej")
            return plan
        # a pump that already runs (after a restart, by hand) is judged as charging: no gap down to the hysteresis
        charging = self.dhw_charging or inp.dhw_pump_on is True
        if charging:
            keep = diff >= cfg["dhw_off_diff_k"] and t_dhw < target
        else:
            keep = diff >= cfg["dhw_on_diff_k"] and t_dhw <= target - cfg["dhw_hyst_k"]
        self.dhw_charging = keep
        plan.want = keep
        if keep:
            plan.reason = f"ładowanie CWU: {src_name} {t_src:.0f} °C, CWU {t_dhw:.0f} °C → {target:.0f} °C"
        elif t_dhw >= target or (not charging and t_dhw > target - cfg["dhw_hyst_k"]):
            plan.reason = (f"CWU naładowana ({t_dhw:.0f} °C)" if t_dhw >= target
                           else f"CWU {t_dhw:.0f} °C — ładowanie od {target - cfg['dhw_hyst_k']:.0f} °C")
        else:
            need = t_dhw + (cfg["dhw_off_diff_k"] if charging else cfg["dhw_on_diff_k"])
            plan.reason = f"{src_name} za chłodny ({t_src:.0f} °C, potrzeba ≥ {need:.0f} °C)"
        return plan

    def _dhw_by_clock(self, inp: ControlInput, plan: DhwPlan, t_src: float | None, src_name: str,
                      boiler: bool) -> DhwPlan:
        """Charging by the clock, for a tank without a (working) probe: only inside the hour windows and
        only while the source is clearly warmer than the target — so the coil never cools the tank. With a
        tank probe it also stops at the target, at the upper limit and when the source gets cooler."""
        cfg = self.cfg
        wins = dhw_windows(cfg)
        hours = ", ".join(f"{a}–{b}" for a, b in wins) or "brak okien"
        t_dhw, target, cap = inp.t_dhw, cfg["dhw_target_c"], cfg["dhw_max_c"]
        if t_src is None:
            self.dhw_charging = False
            plan.urgent = True
            plan.reason = f"brak odczytu sondy {'kotła' if boiler else 'bufora'}"
            return plan
        if t_dhw is not None and t_dhw >= cap:
            self.dhw_charging = False
            plan.urgent = True
            plan.reason = f"CWU {t_dhw:.0f} °C — górny limit {cap:.0f} °C"
            return plan
        if t_dhw is not None and t_src < t_dhw:
            self.dhw_charging = False
            plan.urgent = True
            plan.reason = f"{src_name} ({t_src:.0f} °C) chłodniejszy niż CWU ({t_dhw:.0f} °C)"
            return plan
        limit = cap if cfg["dhw_dump"] else target
        if self.hot and t_dhw is not None and t_src - t_dhw >= cfg["dhw_off_diff_k"] and t_dhw < limit:
            # a hot boiler needs the tank as a heat sink at any hour — but only where the tank's probe can stop it
            self.dhw_charging = True
            plan.want = plan.force = True
            plan.dump = t_dhw >= target
            plan.reason = ("zrzut ciepła do CWU — kocioł gorący" if cfg["dhw_dump"]
                           else "kocioł gorący — CWU dogrzewana do zadanej")
            return plan
        if not any(in_hours(a, b, inp.local_hour) for a, b in wins):
            self.dhw_charging = False
            plan.reason = f"CWU według godzin ({hours}) — poza oknem ładowania"
            return plan
        charging = self.dhw_charging or inp.dhw_pump_on is True
        diff = cfg["dhw_off_diff_k"] if charging else cfg["dhw_on_diff_k"]
        need = (target if t_dhw is None else t_dhw) + diff
        keep = t_src >= need and (t_dhw is None or t_dhw < target)
        self.dhw_charging = keep
        plan.want = keep
        if keep:
            plan.reason = f"ładowanie CWU według godzin ({hours}): {src_name} {t_src:.0f} °C"
        elif t_dhw is not None and t_dhw >= target:
            plan.reason = f"CWU naładowana ({t_dhw:.0f} °C)"
        else:
            plan.reason = f"{src_name} za chłodny na ładowanie CWU ({t_src:.0f} °C, potrzeba ≥ {need:.0f} °C)"
        return plan

    def _dhw_auto(self, inp: ControlInput, plan: DhwPlan) -> tuple[str, str] | None:
        """"auto" / "summer": charging by the plan, plus a short weekly run against seizing."""
        p, now = self.dhw_pump, inp.now
        if inp.dhw_pump_on is None:
            return None
        if not p.last_on:
            p.last_on = now          # count the idle week from the first reading, not from 1970
        if p.exercise_until:
            if now < p.exercise_until and not plan.want:
                return None
            p.exercise_until = 0.0
            if inp.dhw_pump_on and not plan.want:
                return "off", "koniec przepłukania pompy CWU"
        if not plan.want and inp.dhw_pump_on is False and now - p.last_on > EXERCISE_EVERY_S:
            cmd = p._attempt("on", "przepłukanie pompy CWU (raz w tygodniu)", now)
            if cmd:
                p.exercise_until = now + DHW_EXERCISE_S
            return cmd
        return p.decide(now, inp.dhw_pump_on, plan.want, plan.reason, urgent=plan.urgent)

    def _pump_reason(self, inp: ControlInput, room_target: float) -> str:
        if self.needed:
            return "jest zapotrzebowanie na ciepło"
        damped = self.out_damped.value
        if damped is not None and damped >= self.cfg["heating_limit_c"]:
            return f"ciepło na zewnątrz (średnio {damped:.1f} °C)"
        if inp.t_top is not None and inp.t_top < room_target + 5.0:
            return f"bufor za zimny ({inp.t_top:.0f} °C)"
        if inp.t_room is not None:
            return f"w domu ciepło ({inp.t_room:.1f} °C)"
        return "brak zapotrzebowania"

    def manual_valve(self, direction: str, seconds: float, now: float) -> ValveCommand | None:
        if now < self.valve.busy_until:
            return None
        return ValveCommand(direction, round(_clamp(seconds, 1.0, self.valve.run_s + OVERRUN_S), 1),
                            "ręcznie z panelu", "manual")

    def manual_valve_to(self, target: float, now: float) -> ValveCommand | None:
        """Manual mode: drive the valve to target % open, timed from the position estimate. 0 % and 100 % get
        the overrun, so the estimate is re-anchored in the end stop; an unknown position is homed first.
        None: still moving, or already there (within 1 %)."""
        v = self.valve
        if now < v.busy_until:
            return None
        if not v.known:
            return v.home()
        target = _clamp(float(target), 0.0, 100.0)
        if target <= 0.0 or target >= 100.0:
            direction = "close" if target <= 0.0 else "open"
            if v.at_end(direction):
                return None
            room = v.position if direction == "close" else 100.0 - v.position
            seconds = room / 100.0 * v.run_s + OVERRUN_S
        else:
            delta = target - v.position
            if abs(delta) < 1.0:
                return None
            direction = "open" if delta > 0 else "close"
            seconds = abs(delta) / 100.0 * v.run_s
        return ValveCommand(direction, round(_clamp(seconds, 1.0, v.run_s + OVERRUN_S), 1),
                            f"ręcznie z panelu — na {target:.0f} %", "manual")
