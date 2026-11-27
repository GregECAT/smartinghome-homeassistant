# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Home Assistant side of the solid-fuel boiler module.

Pure logic: heating_model.py (probes, buffer, boiler state, fires, alarms),
heating_control.py (weather curve, mixing valve, CH pump, modes), heating_advisor.py
(building model, buffer runs out at, light by, how much wood), heating_shelly.py
(local Shelly RPC: valve pulses timed by the device, the guard script).

- its own loop (every 10 s) — independent of the energy strategy controller: an
  error there must not stop the boiler room and the other way round,
- control: mode "off" never switches anything; "manual" = the panel's buttons; "auto"
  and "summer" drive the valve, the CH pump and the hot-water charging pump. A valve
  pulse goes to a Shelly cover as Cover.Open/Close with a duration (an HA restart
  mid-pulse can't leave it running);
  any other actuator gets HA services and a timed stop,
- HA is never the boiler's safety: it may only take heat away (heat dump, pump on),
- entities (device "Ogrzewanie"), added when the module is switched on (no restart),
- alarms through the configured notification channels; critical ones always also as a
  persistent notification — boiler safety is never gated by the license,
- sessions, the valve position, the building model and the decision log in its own
  Store (not in settings.json),
- the "Ogrzewanie" sidebar panel (frontend/heating.js), registered only while the
  module is on; WebSocket smartinghome/heating/status, …/save, …/command, …/guard.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import timedelta
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfMass, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from . import heating_advisor as adv
from . import heating_control as hc
from . import heating_model as hm
from . import heating_shelly as hs
from .const import (
    DOMAIN,
    FEATURE_ADVANCED_ALERTS,
    FEATURE_HEATING_ADVISOR,
    FEATURE_HEATING_AUTO,
    MANUFACTURER,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

SETTINGS_KEY = "heating"
_KEY = "_heating"
_PLATFORMS_KEY = "_heating_platforms"
# HA 2026.11 links a device to its parent by the parent's registry id (DeviceInfo `via_device_id`); the older
# `via_device` (the parent's identifiers) is deprecated there and stops working in 2027.8. Older HA knows only
# `via_device` — and refuses a device info with a key it does not know — so the form is chosen at runtime.
_VIA_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__optional_keys__", frozenset())
SIGNAL_UPDATE = f"{DOMAIN}_heating_update"
TICK = timedelta(seconds=10)
SETTINGS_TTL_S = 60
SAVE_EVERY_S = 600
CRITICAL_REMINDER_S = 30 * 60
FORECAST_EVERY_S = 1800
FORECAST_TIMEOUT_S = 10.0
PROBE_FORCE_S = 60.0          # like the Shelly guard: a boiler / buffer probe lost this long → CH pump on
MAXTIME_TTL_S = 3600.0
SAVE_DELAY_S = 10.0
ADVISOR_EVERY_S = 60
RELOAD_NOTIFY_GAP_S = 90 * 60
FAN_RETRY_S = 60.0            # the heater fan's socket refused a command: try again after this long
GUARD_RETRY_S = 60.0          # a locked / unlocked CH pump whose Shelly did not answer: try the guard again
LOG_LEN = 100
SOURCE = "Kotłownia"

HEATING_PANEL_URL = f"{DOMAIN}-ogrzewanie"
HEATING_PANEL_TITLE = "Ogrzewanie"
HEATING_PANEL_ICON = "mdi:fire"
HEATING_PANEL_FILENAME = "heating.js"
HEATING_PANEL_ELEMENT = "smartinghome-heating-panel"


def _hhmm(ts: float | None) -> str:
    if ts is None:
        return "—"
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")


# ── manager ──────────────────────────────────────────────────────────────────

class HeatingManager:
    def __init__(self, hass: HomeAssistant) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store = Store(hass, 1, f"{DOMAIN}.heating")
        self.cfg: dict[str, Any] = hm.normalize(None)
        self.model = hm.HeatingModel()
        self.ctrl = hc.HeatingController(self.cfg)
        self.building = adv.BuildingModel()
        self.snapshot: dict[str, Any] = {}
        self.control: dict[str, Any] = {}
        self.advice: dict[str, Any] = {}
        self.log: deque[dict[str, Any]] = deque(maxlen=LOG_LEN)
        self.forecast: list[tuple[float, float]] = []
        self._forecast_ts = 0.0
        self._weather_now: float | None = None
        self._weather_f = False
        self._advice_ts = 0.0
        self._advised_for: int | None = None
        self._reload_wait = False                 # "dołóż": the buffer had no room for a full load during this fire
        self._reload_sent = 0.0
        self._lit_armed: bool | None = None       # "the boiler is lit" waits for the flue to cross FLUE_FIRE_C
        self._flue_prev: float | None = None
        self._last_tick: float | None = None
        self._settings_ts = 0.0
        self._last_save = time.monotonic()
        self._sessions_seen = 0
        self._notified: dict[str, float] = {}     # alarm id → monotonic time of the last message
        self._panel = False
        self._added: set[tuple[str, str]] = set()
        self._unsub = None
        self._stop_unsub = None          # HA-timed stop of a non-Shelly actuator
        self._chain_unsub = None         # next device-timed chunk of a long Shelly move
        self._final_unsub = None
        self._maxtime: dict[str, tuple[float, float | None]] = {}
        self._lock = asyncio.Lock()
        # the guard script state a CH-pump emergency switch asks for (True = running), until the Shelly confirms
        self._guard_want: bool | None = None
        self._guard_retry = 0.0
        self._guard_busy = False
        self._guard_checked = False
        self._fan_retry = 0.0            # monotonic time before which a refused heater-fan command is not repeated
        self._fan_failed = False         # a refusal is logged once, until the socket takes a command again

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_track_time_interval

        raw = await self._store.async_load() or {}
        if isinstance(raw, dict) and "model" in raw:
            self.model.restore(raw.get("model"))
            self.ctrl.restore(raw.get("control"))
            self.building.restore(raw.get("building"))
            self.log.extend(e for e in (raw.get("log") or []) if isinstance(e, dict))
        else:
            self.model.restore(raw)            # first (phase 1) layout: the model only
        self._sessions_seen = len(self.model.sessions)
        await self.async_reload_settings(force=True)
        self._unsub = async_track_time_interval(self.hass, self._tick, TICK)
        from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE

        async def _final(_event: Any) -> None:
            await self._save()

        self._final_unsub = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_FINAL_WRITE, _final)

    async def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._final_unsub:
            self._final_unsub()
            self._final_unsub = None
        if self._chain_unsub or self._stop_unsub:
            # a move is still running: stop it now (the next start re-homes the valve)
            self.ctrl.valve.known = False
            try:
                await self._valve_stop()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Heating: stopping the valve on unload failed: %s", err)
        await self._save()

    def _data(self) -> dict[str, Any]:
        control = self.ctrl.dump_state()
        if self._chain_unsub is not None:
            # in the middle of a chunked move: after a restart the position is not known
            control["valve"] = {**control["valve"], "known": False}
        return {"model": self.model.dump(), "control": control,
                "building": self.building.dump(), "log": list(self.log)}

    async def _save(self) -> None:
        try:
            await self._store.async_save(self._data())
            self._last_save = time.monotonic()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Heating: saving state failed: %s", err)

    def _save_soon(self) -> None:
        """After a valve move / pump change: written within seconds (and flushed at HA shutdown)."""
        self._store.async_delay_save(self._data, SAVE_DELAY_S)

    async def async_reload_settings(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._settings_ts < SETTINGS_TTL_S:
            return
        from .settings_io import read_async

        try:
            settings = await read_async(self.hass)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Heating: settings read failed: %s", err)
            return
        self._settings_ts = time.monotonic()
        await self.async_apply(settings.get(SETTINGS_KEY))

    async def async_apply(self, raw: dict[str, Any] | None) -> None:
        self.cfg = hm.normalize(raw)
        self.model.configure(self.cfg)
        for line in self.ctrl.configure(self.cfg):
            self._log(line)
        if self.enabled and self.cfg["lockout"]["pump"] and not self._guard_checked:
            self._guard_checked = True
            self._guard_want = False
        if self.enabled:
            await self._async_show_panel()
            self.ensure_entities()
            await self._tick()
        else:
            self._hide_panel()
            self.snapshot, self.control, self.advice = {}, {}, {}
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)

    async def async_save(self, updates: dict[str, Any]) -> None:
        """Partial update of settings "heating" (panel, select / number entities)."""
        from .settings_io import read_async, write_async

        current = (await read_async(self.hass)).get(SETTINGS_KEY)
        merged = {**(current if isinstance(current, dict) else {}), **updates}
        stored = hm.to_settings(merged)
        await write_async(self.hass, {SETTINGS_KEY: stored})
        self._settings_ts = time.monotonic()
        await self.async_apply(stored)
        await self._save()

    def _log(self, text: str, now: float | None = None) -> None:
        self.log.append({"ts": now or time.time(), "text": text})
        _LOGGER.info("Heating: %s", text)

    def has(self, feature: str) -> bool:
        for data in self.hass.data.get(DOMAIN, {}).values():
            lic = data.get("license_manager") if isinstance(data, dict) else None
            if lic is not None:
                return bool(lic.has_feature(feature))
        return False

    # ── loop ─────────────────────────────────────────────────────────────────
    async def _tick(self, _now: Any = None) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            await self._run_tick()

    def _target_at(self, ts: float) -> float:
        """The house target at a moment (day / night schedule) — for the advisor's forecast."""
        local = dt_util.as_local(dt_util.utc_from_timestamp(ts))
        return hc.room_target_now(self.cfg, local.hour + local.minute / 60.0, local.weekday())

    def _valve_configured(self) -> bool:
        c = self.cfg
        if c["valve_type"] == "cover":
            return bool(c["valve_cover"])
        if c["valve_type"] == "switches":
            return bool(c["valve_open_switch"] and c["valve_close_switch"])
        return False

    def _grid_outage(self) -> bool:
        """0 V on every known grid phase of the inverter (alert_engine uses the same rule)."""
        for data in self.hass.data.get(DOMAIN, {}).values():
            coord = data.get("coordinator") if isinstance(data, dict) else None
            values = getattr(coord, "data", None) if coord is not None else None
            if not isinstance(values, dict):
                continue
            volts = [hm._f(values.get(k)) for k in (SENSOR_GRID_VOLTAGE_L1, SENSOR_GRID_VOLTAGE_L2,
                                                    SENSOR_GRID_VOLTAGE_L3)]
            known = [v for v in volts if v is not None]
            if known:
                return max(known) <= 50.0
        return False

    async def _run_tick(self) -> None:
        try:
            await self.async_reload_settings()
            if not self.enabled:
                return
            cfg = self.cfg
            now = time.time()
            raw: dict[str, Any] = {}
            for role, entity_id in cfg["entities"].items():
                if not entity_id:
                    raw[role] = None
                    continue
                st = self.hass.states.get(entity_id)
                raw[role] = st.state if st is not None else "unavailable"
            raw["grid_outage"] = self._grid_outage()
            self.model.update(raw, now)
            for note in self.model.notes:       # weighed wood attached to a fire / dropped
                self._log(note, now)
            self.model.notes.clear()
            advisor_on = self.has(FEATURE_HEATING_ADVISOR)
            entity = self._read_weather_now()
            t_out = self.model.t("outdoor")
            t_out_source = "czujnik"
            if t_out is None and self._weather_now is not None:
                t_out, t_out_source = self._weather_now, "pogoda"
            active = {a.id: a for a in self.model.active_alarms()}
            pump_on = self.model.pump_on
            dhw_pump_on = self.model.dhw_pump_on
            dhw_pump_configured = bool(cfg["entities"]["dhw_pump_switch"])
            local = dt_util.now()
            out = self.ctrl.step(hc.ControlInput(
                now=now, local_hour=local.hour + local.minute / 60.0, weekday=local.weekday(),
                t_supply=self.model.t("heating_supply"), t_top=self.model.t("buffer_top"),
                t_bottom=self.model.t("buffer_bottom"),
                t_boiler=self.model.t("boiler_out"), t_room=self.model.t("room"), t_out=t_out,
                pump_on=pump_on, pump_configured=bool(cfg["entities"]["pump_switch"]),
                valve_configured=self._valve_configured(),
                overheat="heating_overheat" in active,
                probe_fault=any(cfg["entities"][r] and self.model.probes[r].fault
                                and self.model.probes[r].bad_for(now) >= PROBE_FORCE_S
                                for r in ("boiler_out", "buffer_top")),
                room_licensed=self.has(FEATURE_HEATING_AUTO),
                t_dhw=self.model.t("dhw"), dhw_pump_on=dhw_pump_on, dhw_pump_configured=dhw_pump_configured,
                firing=(True if self.model.state in hm.FIRE_STATES or self.model.session is not None
                        else None if self.model.state == "unknown" else False),
            ))
            for line in out.log:
                self._log(line, now)
            self.model.external = out.alarms
            self.model.room_target = out.room_target
            self.model.dump_active = bool(out.dump or out.dhw.force)
            raised, cleared = self.model.evaluate_alarms(now)
            if out.valve:
                await self._exec_valve(out.valve, now)
            if out.pump:
                await self._exec_pump(out.pump, now)
            if out.dhw_pump:
                await self._exec_pump(out.dhw_pump, now, dhw=True)
            fan = await self._sync_fan(now, dump=out.dump, local_hour=local.hour + local.minute / 60.0)
            # the forecast only after the actuators — and never longer than a few seconds
            await self._fetch_forecast(now, entity, advisor_on)

            # building model (heat loss against the whole house: storeys weighted by area); the house's share
            # of each fire (for kWh per kg). An hour with the hot-water tank charging from the buffer is not
            # the house's draw either.
            firing = self.model.state in hm.FIRE_STATES or self.model.session is not None
            dhw_charging = dhw_pump_on is True and cfg["dhw_source"] == "buffer_top"
            if self.model.buffer:
                self.building.add(now, self.model.buffer.get("total_kwh"), self.model.house_temp(), t_out,
                                  firing or dhw_charging, pump_on if cfg["entities"]["pump_switch"] else None)
            dt = 0.0 if self._last_tick is None else min(max(now - self._last_tick, 0.0), 60.0)
            self._last_tick = now
            # the house's draw from the buffer's discharge without a fire — until the hourly model is fitted it is
            # what books the house's share of a fire (the heat the wood really gave, not only what the tanks kept)
            t_house = self.model.house_temp()
            if (not firing and not dhw_charging and self.model.discharge_kw is not None
                    and t_house is not None and t_out is not None):
                self.building.observe_draw(self.model.discharge_kw, t_house - t_out, dt)
            if self.model.session is not None:
                s = self.model.session
                kw = self.building.house_kw(t_house if t_house is not None else out.room_target, t_out)
                if kw is None:          # nothing to go by yet: booked once there is (at that moment's draw)
                    s["house_gap_s"] = round(float(s.get("house_gap_s") or 0.0) + dt, 1)
                else:
                    gap = float(s.pop("house_gap_s", 0.0) or 0.0)
                    s["house_kwh"] = round(float(s.get("house_kwh") or 0.0) + kw * (dt + gap) / 3600.0, 2)
            # weighed fires: a new fire closes the previous fire's cycle — the house's draw from history (the wood
            # at the kWh/kg from the settings, minus what the tanks kept, over the hours until this fire)
            fire_now = self.model.session
            if fire_now is not None and self.building.cycle_start != fire_now.get("start"):
                note = self.building.close_cycle(self.model.sessions[-1] if self.model.sessions else None, fire_now,
                                                 cfg["wood_kwh_per_kg"] * cfg["efficiency"])
                if note:
                    self._log(note, now)
                self.building.open_cycle(float(fire_now["start"]))
            self.building.cycle_add(None if t_house is None or t_out is None else t_house - t_out, dt)

            status = self.model.status(now)
            learned = adv.learned_kwh_per_kg(self.model.sessions)
            per_kg = learned or cfg["wood_kwh_per_kg"] * cfg["efficiency"]
            if learned and status.get("buffer"):
                buf = status["buffer"]
                buf["kwh_per_kg"], buf["wood_max_kg"] = learned, round(buf["space_kwh"] / learned, 1)
            # what the weighed wood of the fire burning now will still give: kg × kWh/kg − what it has given so far
            fire_left = None
            fs = status.get("session")
            if fs and fs.get("wood_kg") and hm.fully_weighed(fs):     # only a reload weighed: unknown
                given = fs.get("heat_kwh")
                if given is None and fs.get("stored_start_kwh") is not None:
                    stored = [x["energy_kwh"] for x in (status.get("buffer"), status.get("dhw")) if x]
                    given = sum(stored) - float(fs["stored_start_kwh"]) if stored else None
                fire_left = max(float(fs["wood_kg"]) * per_kg - float(given or 0.0), 0.0)
            if advisor_on:
                if now - self._advice_ts >= ADVISOR_EVERY_S or not self.advice or self.advice.get("locked"):
                    self.advice = adv.advise(cfg, status, self.building, now, self._target_at, self.forecast,
                                             t_out, per_kg, fire_left)
                    self.advice["learned_per_kg"] = learned
                    self.advice["forecast_hours"] = len(self.forecast)
                    self._advice_ts = now
            else:
                self.advice = {"locked": True, "building": self.building.status()}

            valve = self.ctrl.valve
            self.control = {
                "mode": cfg["mode"], "mode_label": hm.MODES.get(cfg["mode"], cfg["mode"]),
                "room_target": round(out.room_target, 1), "night": out.night, "setback": out.setback,
                "supply_target": out.supply_target, "curve_c": out.curve_c, "room_corr": out.room_corr,
                "room_licensed": self.has(FEATURE_HEATING_AUTO),
                "t_out": None if out.t_out is None else round(out.t_out, 1),
                "t_out_damped": None if out.t_out_damped is None else round(out.t_out_damped, 1),
                "t_out_source": t_out_source if t_out is not None else None,
                # both outdoor readings for the panel: the sensor (what control uses when there is one) and the forecast
                "t_out_sensor": None if self.model.t("outdoor") is None else round(self.model.t("outdoor"), 1),
                "t_out_weather": None if self._weather_now is None else round(self._weather_now, 1),
                "heating_needed": out.heating_needed, "dump": out.dump, "limiter": out.limiter, "note": out.note,
                "valve": {"configured": self._valve_configured(), "position": round(valve.position),
                          "known": valve.known, "saturated": valve.saturated, "busy": now < valve.busy_until,
                          "run_last_h_s": round(valve.run_last_hour(now))},
                "pump": {"configured": bool(cfg["entities"]["pump_switch"]), "on": pump_on,
                         "forced": out.pump_forced,
                         "forced_active": bool(out.pump_forced) and cfg["mode"] != "off" and not cfg["lockout"]["pump"]
                         and not cfg["force_on"]["pump"]},
                "lockout": dict(cfg["lockout"]),
                "force_on": dict(cfg["force_on"]),
                "dhw_pump": {"configured": dhw_pump_configured, "on": dhw_pump_on, "want": out.dhw.want,
                             "reason": out.dhw.reason, "dump": out.dhw.dump,
                             "source_c": None if out.dhw.source_c is None else round(out.dhw.source_c, 1)},
                "fan": fan,
            }
            self.snapshot = {**status, "control": self.control, "advice": self.advice,
                             "log": list(self.log)[-30:], "valve_moves": list(valve.moves)[-15:]}
            async_dispatcher_send(self.hass, SIGNAL_UPDATE)
            await self._notify(raised, cleared)
            await self._advisor_notify(now)
            await self._lit_notify(now)
            if (self._guard_want is not None and not self._guard_busy
                    and time.monotonic() >= self._guard_retry):
                self._guard_retry = time.monotonic() + GUARD_RETRY_S
                self.hass.async_create_task(self._guard_sync())
            if (len(self.model.sessions) != self._sessions_seen
                    or time.monotonic() - self._last_save > SAVE_EVERY_S):
                self._sessions_seen = len(self.model.sessions)
                await self._save()
        except Exception:  # noqa: BLE001 — never let one bad tick stop the loop
            _LOGGER.exception("Heating tick failed")

    def _read_weather_now(self) -> str:
        entity = self.cfg["weather_entity"] or next(iter(sorted(self.hass.states.async_entity_ids("weather"))), "")
        st = self.hass.states.get(entity) if entity else None
        self._weather_now = None
        self._weather_f = False
        if st is not None:
            self._weather_f = str(st.attributes.get("temperature_unit") or "°C").endswith("F")
            temp = hm._f(st.attributes.get("temperature"))
            if temp is not None and self._weather_f:
                temp = (temp - 32.0) / 1.8
            self._weather_now = temp
        return entity

    async def _fetch_forecast(self, now: float, entity: str, with_forecast: bool) -> None:
        if not with_forecast or not entity or now - self._forecast_ts < FORECAST_EVERY_S:
            return
        self._forecast_ts = now
        try:
            async with asyncio.timeout(FORECAST_TIMEOUT_S):
                res = await self.hass.services.async_call(
                    "weather", "get_forecasts", {"entity_id": entity, "type": "hourly"},
                    blocking=True, return_response=True)
            items = ((res or {}).get(entity) or {}).get("forecast") or []
            fc = []
            for item in items:
                when = dt_util.parse_datetime(str(item.get("datetime") or ""))
                temp = hm._f(item.get("temperature"))
                if when is not None and temp is not None:
                    fc.append((when.timestamp(), (temp - 32.0) / 1.8 if self._weather_f else temp))
            if fc:
                self.forecast = sorted(fc)
        except Exception as err:  # noqa: BLE001 — no hourly forecasts / a hung weather service
            _LOGGER.debug("Heating: hourly forecast from %s unavailable: %s", entity, err)

    # ── actuators ────────────────────────────────────────────────────────────
    def _cancel_motion(self) -> None:
        for name in ("_stop_unsub", "_chain_unsub"):
            unsub = getattr(self, name)
            if unsub:
                unsub()
                setattr(self, name, None)

    async def _maxtime_of(self, target: hs.ShellyTarget, cover: str) -> float | None:
        cached = self._maxtime.get(cover)
        if cached and time.monotonic() - cached[0] < MAXTIME_TTL_S:
            return cached[1]
        value = await hs.cover_maxtime(self.hass, target)
        self._maxtime[cover] = (time.monotonic(), value)
        return value

    async def _shelly_move(self, target: hs.ShellyTarget, cmd: hc.ValveCommand) -> bool:
        """A move timed by the Shelly itself, in chunks within the cover's maxtime (a duration can't exceed
        it). After an HA restart at most the current chunk finishes — nothing is left running."""
        cover, invert = self.cfg["valve_cover"], self.cfg["valve_invert"]
        maxtime = await self._maxtime_of(target, cover)
        chunk_max = max((maxtime - 1.0) if maxtime else 55.0, 1.0)
        first = min(cmd.seconds, chunk_max)
        if not await hs.cover_pulse(self.hass, cover, cmd.direction, first, invert=invert):
            return False
        self._chain(cover, cmd.direction, cmd.seconds - first, first, chunk_max, invert)
        return True

    def _chain(self, cover: str, direction: str, remaining: float, after: float, chunk_max: float,
               invert: bool) -> None:
        from homeassistant.helpers.event import async_call_later

        if remaining < 0.5:
            self._chain_unsub = None
            return

        async def _next(_now: Any) -> None:
            self._chain_unsub = None
            part = min(remaining, chunk_max)
            if await hs.cover_pulse(self.hass, cover, direction, part, invert=invert):
                self._chain(cover, direction, remaining - part, part, chunk_max, invert)
            else:
                self.ctrl.valve.known = False      # the move broke off: the estimate is wrong
                self._log("⚠️ Zawór: ruch przerwany (Shelly nie przyjął kolejnego odcinka) — kalibracja przy następnej okazji")
                self._save_soon()

        self._chain_unsub = async_call_later(self.hass, after + 0.3, _next)

    def _schedule_stop(self, seconds: float, coro_factory: Any) -> None:
        from homeassistant.helpers.event import async_call_later

        if self._stop_unsub:
            self._stop_unsub()

        async def _stop(_now: Any) -> None:
            self._stop_unsub = None
            try:
                await coro_factory()
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Heating: stopping the valve failed: %s", err)

        self._stop_unsub = async_call_later(self.hass, seconds, _stop)

    async def _exec_valve(self, cmd: hc.ValveCommand, now: float) -> bool:
        cfg = self.cfg
        ok = False
        self._cancel_motion()        # a new command replaces whatever is still running (e.g. a dump pre-empts a close)
        try:
            if cfg["valve_type"] == "cover" and cfg["valve_cover"]:
                target = await hs.cover_target(self.hass, cfg["valve_cover"])
                # a Shelly cover never falls back to HA services: an RPC timeout may hide a move that ran
                ok = await self._shelly_move(target, cmd) if target is not None else await self._cover_services(cmd)
            elif cfg["valve_type"] == "switches":
                ok = await self._switch_pulse(cmd)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Heating: valve %s %.0f s failed: %s", cmd.direction, cmd.seconds, err)
            ok = False
        verb = "otwieranie" if cmd.direction == "open" else "zamykanie"
        if ok:
            self.ctrl.valve.apply(cmd, now)
            if cmd.kind != "auto":
                self._log(f"Zawór: {verb} {cmd.seconds:.0f} s — {cmd.reason}", now)
            self._save_soon()
        else:
            self.ctrl.valve.failed(now, safety=cmd.kind == "safety")
            self._log(f"⚠️ Zawór nie przyjął polecenia ({verb} {cmd.seconds:.0f} s)", now)
        return ok

    async def _cover_services(self, cmd: hc.ValveCommand) -> bool:
        cover = self.cfg["valve_cover"]
        opening = (cmd.direction == "open") != self.cfg["valve_invert"]
        await self.hass.services.async_call("cover", "open_cover" if opening else "close_cover",
                                            {"entity_id": cover}, blocking=True)
        self._schedule_stop(cmd.seconds, lambda: self.hass.services.async_call(
            "cover", "stop_cover", {"entity_id": cover}, blocking=True))
        return True

    async def _switch_pulse(self, cmd: hc.ValveCommand) -> bool:
        open_sw, close_sw = self.cfg["valve_open_switch"], self.cfg["valve_close_switch"]
        if self.cfg["valve_invert"]:
            open_sw, close_sw = close_sw, open_sw
        on_sw, off_sw = (open_sw, close_sw) if cmd.direction == "open" else (close_sw, open_sw)
        if not on_sw:
            return False
        if off_sw:      # interlock: the other direction is off first
            await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": off_sw}, blocking=True)
        await self.hass.services.async_call("homeassistant", "turn_on", {"entity_id": on_sw}, blocking=True)
        self._schedule_stop(cmd.seconds, lambda: self.hass.services.async_call(
            "homeassistant", "turn_off", {"entity_id": on_sw}, blocking=True))
        return True

    async def _valve_stop(self) -> bool:
        cfg = self.cfg
        self._cancel_motion()
        self.ctrl.valve.busy_until = 0.0
        if cfg["valve_type"] == "cover" and cfg["valve_cover"]:
            if await hs.cover_stop(self.hass, cfg["valve_cover"]):
                return True
            await self.hass.services.async_call("cover", "stop_cover", {"entity_id": cfg["valve_cover"]},
                                                blocking=True)
            return True
        if cfg["valve_type"] == "switches":
            ids = [e for e in (cfg["valve_open_switch"], cfg["valve_close_switch"]) if e]
            if ids:
                await self.hass.services.async_call("homeassistant", "turn_off", {"entity_id": ids}, blocking=True)
                return True
        return False

    async def _exec_pump(self, cmd: tuple[str, str], now: float, dhw: bool = False) -> bool:
        state, reason = cmd
        entity = self.cfg["entities"]["dhw_pump_switch" if dhw else "pump_switch"]
        name = "Pompa CWU" if dhw else "Pompa CO"
        try:
            await self.hass.services.async_call("homeassistant", "turn_on" if state == "on" else "turn_off",
                                                {"entity_id": entity}, blocking=True)
        except Exception as err:  # noqa: BLE001
            self._log(f"⚠️ {name} nie przyjęła polecenia ({state}): {err}", now)
            return False
        (self.ctrl.dhw_pump if dhw else self.ctrl.pump).note(state == "on", now)
        self._log(f"{name} {'włączona' if state == 'on' else 'wyłączona'} — {reason}", now)
        self._save_soon()
        return True

    async def _sync_fan(self, now: float, dump: bool = False, local_hour: float | None = None) -> dict[str, Any]:
        """The unit heater's fan follows the CH pump FAN_DELAY_S late, on and off (hc.fan_follow); the delay
        runs from the pump entity's last state change. In the quiet hours (fan_quiet), and while it is switched
        off in the panel (fan_off), it stays off, heat dumps aside. A socket that refused a command is tried
        again after FAN_RETRY_S, not every tick; an offline socket is left alone until it is back."""
        cfg = self.cfg
        if local_hour is None:
            local = dt_util.now()
            local_hour = local.hour + local.minute / 60.0
        quiet = cfg["fan_quiet"] and hc.in_hours(cfg["fan_quiet_from"], cfg["fan_quiet_to"], local_hour)
        ents = self.cfg["entities"]
        fan, pump = ents["fan_switch"], ents["pump_switch"]
        if not fan:
            return {"configured": False}
        fst = self.hass.states.get(fan)
        pst = self.hass.states.get(pump) if pump else None
        fan_on = hm.switch_state(fst.state if fst is not None else None)
        pump_on = hm.switch_state(pst.state if pst is not None else None)
        pump_for = now - pst.last_changed.timestamp() if pst is not None else 0.0
        off = cfg["fan_off"]
        want, wait = hc.fan_follow(self.cfg["mode"], pump_on, pump_for, fan_on, dump, quiet, off)
        info: dict[str, Any] = {"configured": True, "name": self.cfg["fan_name"], "on": fan_on,
                                "online": fan_on is not None, "follows": bool(pump), "power_w": self.model.fan_w,
                                "delay_s": round(hc.FAN_DELAY_S), "wait_s": round(wait),
                                "wait_to": ("on" if pump_on else "off") if wait > 0 else None, "dump": dump,
                                "off": off, "quiet": bool(quiet), "quiet_enabled": cfg["fan_quiet"],
                                "quiet_from": cfg["fan_quiet_from"], "quiet_to": cfg["fan_quiet_to"]}
        if want is None or time.monotonic() < self._fan_retry:
            return info
        name = f"{self.cfg['fan_name']} — wentylator"
        try:
            await self.hass.services.async_call("homeassistant", f"turn_{want}", {"entity_id": fan}, blocking=True)
        except Exception as err:  # noqa: BLE001 — retried from the tick
            self._fan_retry = time.monotonic() + FAN_RETRY_S
            if not self._fan_failed:
                self._fan_failed = True
                self._log(f"⚠️ {name} nie przyjął polecenia ({want}): {err}", now)
            return info
        self._fan_retry, self._fan_failed = 0.0, False
        mins = f"{max(pump_for, hc.FAN_DELAY_S) / 60:.0f} min"
        what = ("włączony od razu — zrzut ciepła z kotła" if want == "on" and dump
                else "wyłączony w panelu — nie rusza z pompą CO" if off
                else f"wyłączony — noc, nie rusza z pompą CO do {cfg['fan_quiet_to']}:00" if quiet
                else f"włączony — pompa CO pracuje od {mins}" if want == "on" else f"wyłączony — pompa CO stoi od {mins}")
        self._log(f"{name} {what}", now)
        info["on"] = want == "on"
        return info

    async def _guard_sync(self, now: float | None = None) -> bool:
        """Bring the guard script on the CH pump's Shelly to the state the emergency switch asks for.
        False when the Shelly did not answer (tried again every GUARD_RETRY_S from the tick)."""
        want = self._guard_want
        if want is None:
            return True
        self._guard_busy = True
        try:
            res = await hs.guard_set_enabled(self.hass, self.cfg, want)
        except Exception as err:  # noqa: BLE001 — retried from the tick
            _LOGGER.warning("Heating guard sync failed: %s", err)
            res = "error"
        finally:
            self._guard_busy = False
        if want != self._guard_want:            # changed meanwhile — the next attempt follows the new wish
            return True
        if res == "error":
            self._guard_retry = time.monotonic() + GUARD_RETRY_S
            return False
        self._guard_want = None
        if res in ("stopped", "started"):       # "none": no script installed — nothing to say
            self._log("Skrypt bezpieczeństwa na Shelly pompy " + ("uruchomiony" if want else "zatrzymany"), now)
        return True

    async def _set_switch(self, element: Any, position: Any) -> dict[str, Any]:
        """A pump's switch in the panel, like the AUTO–0–HAND switch in the box: "auto" (the module
        decides), "off" (emergency lock — nothing switches it on) or "on" (HAND — runs whatever the mode)."""
        if element not in hm.FORCE_ELEMENTS:
            raise ValueError("Przełącznik AUTO / 0 / RĘKA mają tylko pompy")
        if position not in ("auto", "off", "on"):
            raise ValueError("Nieznana pozycja przełącznika")
        name = {"pump": "Pompa CO", "dhw_pump": "Pompa CWU"}[element]
        now = time.time()
        was_hand = bool(self.cfg["force_on"].get(element))
        lock = {**self.cfg["lockout"], element: position == "off"}
        hand = {**self.cfg["force_on"], element: position == "on"}
        if lock != self.cfg["lockout"] or hand != self.cfg["force_on"]:
            await self.async_save({"lockout": lock, "force_on": hand})
            valve_note = ""
            if element == "pump" and self.cfg["mode"] in ("auto", "summer") and self._valve_configured():
                if position == "on":
                    valve_note = f" — zawór na {hc.HAND_VALVE_PCT:.0f} %"
                elif position == "auto" and was_hand:
                    valve_note = " — zawór zamykany, potem reguluje moduł"
            self._log({"off": f"🔒 {name}: pozycja 0 — zablokowana wyłącznikiem awaryjnym w panelu",
                       "on": f"✋ {name}: pozycja RĘKA — pracuje na stałe z panelu{valve_note}",
                       "auto": f"🔓 {name}: pozycja AUTO — steruje moduł{valve_note}"}[position], now)
        if element == "pump" and self.cfg["entities"]["pump_switch"]:
            self._guard_want = position != "off"
            if not await self._guard_sync(now):
                self._log("Shelly pompy CO nie odpowiada — skrypt bezpieczeństwa (jeśli jest wgrany) zostanie "
                          + ("zatrzymany" if position == "off" else "uruchomiony") + ", gdy Shelly wróci do sieci", now)
        await self._tick()
        return self.ws_payload(True)

    async def _set_lock(self, element: Any, on: bool) -> dict[str, Any]:
        """A panel emergency switch: the element stays off (a valve stays put) whatever wants it on —
        the automation, the safety rules and, for the CH pump, the guard script on its Shelly."""
        if element not in hm.LOCK_ELEMENTS:
            raise ValueError("Nieznany element wyłącznika awaryjnego")
        name = {"pump": "Pompa CO", "dhw_pump": "Pompa CWU", "valve": "Siłownik zaworu"}[element]
        now = time.time()
        if self.cfg["lockout"].get(element) != on:
            await self.async_save({"lockout": {**self.cfg["lockout"], element: on}})
            self._log(f"🔒 {name}: zablokowany wyłącznikiem awaryjnym w panelu" if on
                      else f"🔓 {name}: odblokowany w panelu", now)
        if on and element == "valve" and self._valve_configured():
            try:
                await self._valve_stop()
            except Exception as err:  # noqa: BLE001
                self._log(f"⚠️ Zawór nie przyjął polecenia stop: {err}", now)
        if element == "pump" and self.cfg["entities"]["pump_switch"]:
            self._guard_want = not on
            if not await self._guard_sync(now):
                self._log("Shelly pompy CO nie odpowiada — skrypt bezpieczeństwa (jeśli jest wgrany) zostanie "
                          + ("zatrzymany" if on else "uruchomiony") + ", gdy Shelly wróci do sieci", now)
        await self._tick()
        return self.ws_payload(True)

    async def async_command(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        """Panel buttons. Valve / pump by hand only in the manual mode (auto would undo it)."""
        if action in ("lock", "unlock"):
            if data.get("element") in hm.FORCE_ELEMENTS:
                return await self._set_switch(data.get("element"), "off" if action == "lock" else "auto")
            return await self._set_lock(data.get("element"), action == "lock")
        if action == "set_switch":
            return await self._set_switch(data.get("element"), data.get("position"))
        now = time.time()
        mode = self.cfg["mode"]
        async with self._lock:
            if action in ("valve_open", "valve_close", "valve_to") or action == "valve_home":
                if self.cfg["lockout"]["valve"]:
                    raise ValueError("Siłownik zaworu jest zablokowany wyłącznikiem awaryjnym")
            if action in ("valve_open", "valve_close"):
                if mode != "manual":
                    raise ValueError("Ręczne sterowanie zaworem działa w trybie „Ręczne”")
                if not self._valve_configured():
                    raise ValueError("Nie skonfigurowano siłownika zaworu")
                cmd = self.ctrl.manual_valve("open" if action == "valve_open" else "close",
                                             float(data.get("seconds") or 5.0), now)
                if cmd is None:
                    raise ValueError("Zawór jest w ruchu — poczekaj na koniec poprzedniego polecenia")
                if not await self._exec_valve(cmd, now):
                    raise ValueError("Siłownik nie przyjął polecenia")
            elif action == "valve_to":
                if mode != "manual":
                    raise ValueError("Ręczne sterowanie zaworem działa w trybie „Ręczne”")
                if not self._valve_configured():
                    raise ValueError("Nie skonfigurowano siłownika zaworu")
                target = hm._f(data.get("percent"))
                if target is None or not 0.0 <= target <= 100.0:
                    raise ValueError("Podaj otwarcie zaworu 0–100 %")
                if now < self.ctrl.valve.busy_until:
                    raise ValueError("Zawór jest w ruchu — poczekaj na koniec poprzedniego polecenia")
                cmd = self.ctrl.manual_valve_to(target, now)
                if cmd is None:
                    raise ValueError(f"Zawór już jest na ok. {self.ctrl.valve.position:.0f} %")
                if not await self._exec_valve(cmd, now):
                    raise ValueError("Siłownik nie przyjął polecenia")
            elif action == "valve_stop":
                if mode == "off":
                    raise ValueError("W trybie „Wyłączone” integracja niczego nie przełącza")
                if not await self._valve_stop():
                    raise ValueError("Nie skonfigurowano siłownika zaworu")
                self._log("Zawór: zatrzymany z panelu", now)
            elif action == "valve_home":
                if mode == "off" or not self._valve_configured():
                    raise ValueError("Kalibracja wymaga skonfigurowanego siłownika i trybu innego niż „Wyłączone”")
                if not await self._exec_valve(self.ctrl.valve.home(), now):
                    raise ValueError("Siłownik nie przyjął polecenia")
            elif action in ("pump_on", "pump_off"):
                if action == "pump_on" and self.cfg["lockout"]["pump"]:
                    raise ValueError("Pompa CO jest zablokowana wyłącznikiem awaryjnym")
                if action == "pump_off" and self.cfg["force_on"]["pump"]:
                    raise ValueError("Pompa CO jest w pozycji RĘKA — przełącz ją na AUTO")
                if mode != "manual":
                    raise ValueError("Ręczne sterowanie pompą działa w trybie „Ręczne”")
                if not self.cfg["entities"]["pump_switch"]:
                    raise ValueError("Nie wybrano przekaźnika pompy CO")
                if not await self._exec_pump(("on" if action == "pump_on" else "off", "ręcznie z panelu"), now):
                    raise ValueError("Przekaźnik pompy nie przyjął polecenia")
            elif action in ("dhw_pump_on", "dhw_pump_off"):
                if action == "dhw_pump_on" and self.cfg["lockout"]["dhw_pump"]:
                    raise ValueError("Pompa CWU jest zablokowana wyłącznikiem awaryjnym")
                if action == "dhw_pump_off" and self.cfg["force_on"]["dhw_pump"]:
                    raise ValueError("Pompa CWU jest w pozycji RĘKA — przełącz ją na AUTO")
                if mode != "manual":
                    raise ValueError("Ręczne sterowanie pompą CWU działa w trybie „Ręczne”")
                if not self.cfg["entities"]["dhw_pump_switch"]:
                    raise ValueError("Nie wybrano przekaźnika pompy CWU")
                if not await self._exec_pump(("on" if action == "dhw_pump_on" else "off", "ręcznie z panelu"), now,
                                             dhw=True):
                    raise ValueError("Przekaźnik pompy CWU nie przyjął polecenia")
            elif action == "log_load":
                kg = hm._f(data.get("kg"))
                if kg is None or not 0.5 <= kg <= 200:
                    raise ValueError("Podaj masę drewna w kg (0,5–200)")
                booked = self.model.log_load(kg, now, data.get("target") or "auto")
                if booked is None:
                    raise ValueError("Kocioł nie pali, a ostatnie palenie skończyło się ponad 12 h temu — "
                                     "wybierz „Rozpalam teraz”, wpis poczeka na rozpalenie")
                where, fire = booked
                if where == "next":
                    self._log(f"Zważone przed rozpaleniem: {kg:.1f} kg — czeka na rozpalenie "
                              f"(do {hm.PENDING_LOAD_S // 3600} h)", now)
                else:
                    self._log(f"Załadunek: {kg:.1f} kg drewna — {'bieżące' if where == 'current' else 'ostatnie'} "
                              f"palenie od {_hhmm(fire.get('start'))}, razem {fire.get('wood_kg'):.1f} kg", now)
                self._advice_ts = 0.0           # the advice counts the new wood at once
                await self._save()
            elif action == "undo_load":
                undone = self.model.undo_load(now)
                if undone is None:
                    raise ValueError("Nie ma wpisu do cofnięcia")
                self._log(f"Cofnięto wpis: {undone[0]:.1f} kg drewna", now)
                self._advice_ts = 0.0
                await self._save()
            else:
                raise ValueError(f"Nieznane polecenie: {action}")
        await self._tick()
        return self.ws_payload(True)

    # ── notifications ────────────────────────────────────────────────────────
    async def _notify(self, raised: list[hm.AlarmSpec], cleared: list[hm.AlarmSpec]) -> None:
        now_m = time.monotonic()
        active = self.model.active_alarms()
        for alarm in active:
            # critical: a reminder while it lasts
            last = self._notified.get(alarm.id)
            if alarm.level == "critical" and last and now_m - last > CRITICAL_REMINDER_S:
                raised = [*raised, alarm]
        if any(a.id == "heating_overheat" for a in active):
            raised = [a for a in raised if a.id != "heating_hot"]   # the critical one says it all
        if not (raised or cleared):
            return
        ncfg = await self._ncfg()
        levels = ncfg.get("levels") or ["critical", "warning"]
        push = bool(ncfg.get("enabled")) and self.cfg.get("notify", True) is not False
        for alarm in raised:
            reminder = alarm.id in self._notified
            self._notified[alarm.id] = now_m
            title = ("Nadal: " if reminder and alarm.level == "critical" else "") + alarm.title
            message = alarm.message + (f"\n→ {alarm.action}" if alarm.action else "")
            sent: list[str] = []
            if push and alarm.level in levels:
                sent = await self._deliver(ncfg, key=alarm.id, level=alarm.level, title=title, message=message,
                                           email=alarm.level == "critical")
            if alarm.level == "critical" and "persistent" not in sent:
                # boiler safety: always visible in HA, whatever the notification settings
                await self._persistent(alarm.id, f"🔴 {title}", message)
        for alarm in cleared:
            if self._notified.pop(alarm.id, None) is None:
                continue
            if push and alarm.level in levels:
                await self._deliver(ncfg, key=alarm.id, level="resolved",
                                    title=f"{alarm.title.split(':')[0]} — wróciło do normy", message="Problem ustąpił.")
            # it may have been critical earlier (e.g. a probe lost during a fire) — dismissing is harmless
            await self._persistent(alarm.id, None, None)

    async def _deliver(self, ncfg: dict[str, Any], **kw: Any) -> list[str]:
        """One heating notification: to the Smarting HOME channels and also to the heating's own extra
        recipients (notify_extra, e.g. the people who light the boiler); a tap opens the heating panel."""
        from .notifier import async_deliver, push_targets

        ch = ncfg.get("channels") or {}
        targets = hc.push_recipients(push_targets(ncfg), self.cfg.get("notify_extra") or [], bool(ch.get("ha_push")))
        cfg = {**ncfg, "ha_push_entities": targets, "channels": {**ch, "ha_push": bool(targets)}}
        return await async_deliver(self.hass, cfg, source=SOURCE, url=f"/{HEATING_PANEL_URL}",
                                   advanced=self.has(FEATURE_ADVANCED_ALERTS), **kw)

    async def _lit_notify(self, now: float) -> None:
        """The flue has risen through FLUE_FIRE_C (100 °C): the fire has taken — the boiler can go from lighting
        to automatic. Once per fire (hc.lit_watch); with the wood booked so far or a reminder to weigh it."""
        m = self.model
        flue = m.t("flue_gas")
        self._lit_armed, say = hc.lit_watch(self._lit_armed, m.session is not None, self._flue_prev, flue,
                                            hm.FLUE_FIRE_C)
        if flue is not None:
            self._flue_prev = flue
        if not say or not self.cfg.get("lit_notify", True):
            return
        booked = (m.session or {}).get("wood_kg") or 0.0
        booked += sum(p.get("kg") or 0.0 for p in m.pending_loads)
        title = f"Kocioł się rozpalił — spaliny {flue:.0f} °C"
        message = ("Możesz przełączyć kocioł z rozpalania na tryb automatyczny.\n"
                   + hc.wood_reminder(round(booked, 1)))
        self._log(f"🔥 {title}", now)
        ncfg = await self._ncfg()
        if ncfg.get("enabled") and self.cfg.get("notify", True) is not False:
            await self._deliver(ncfg, key="heating_lit", level="info", title=title, message=message,
                                icon="🔥", interruption="active")

    async def _ncfg(self) -> dict[str, Any]:
        from .settings_io import read_async

        try:
            return (await read_async(self.hass)).get("notification_config") or {}
        except Exception:  # noqa: BLE001
            return {}

    async def _reload_notify(self, a: dict[str, Any], now: float) -> None:
        """While a fire burns: once the buffer has room for a full load again after it had none — "dołóż".
        Not at night (the night setback's hours) and not more often than RELOAD_NOTIFY_GAP_S."""
        self._reload_wait, say = hc.reload_watch(self._reload_wait, a)
        if not say:
            return
        if now - self._reload_sent < RELOAD_NOTIFY_GAP_S:
            return
        kg, pct, empty_at = a.get("full_kg"), a.get("full_pct"), a.get("empty_at")
        local = dt_util.as_local(dt_util.utc_from_timestamp(now))
        if hc.night_now(self.cfg, local.hour + local.minute / 60.0):
            self._log(f"Doradca: bufor ma miejsce na pełny załadunek (~{kg} kg) — w nocy bez powiadomienia", now)
            return
        self._reload_sent = now
        title = f"Dołóż drewna do kotła — ok. {kg} kg"
        message = (f"Bufor{f' ({pct} %)' if pct is not None else ''} ma już miejsce na pełny załadunek: ok. {kg} kg "
                   f"drewna — ciepło dla domu na {self.cfg['burn_hours']:g} h, nadwyżkę kotła przyjmie bufor.".replace(".5 h", ",5 h")
                   + (f" Bez dokładki ciepła wystarczy do ok. {_hhmm(empty_at)}." if empty_at else "")
                   + "\n" + hc.wood_reminder(None, before_fire=True))
        self._log(f"Doradca: dołóż ok. {kg} kg — bufor ma miejsce na pełny załadunek", now)
        ncfg = await self._ncfg()
        if ncfg.get("enabled") and self.cfg.get("notify", True) is not False:
            await self._deliver(ncfg, key="heating_reload", level="info", title=title, message=message)

    async def _advisor_notify(self, now: float) -> None:
        a = self.advice
        if not a or a.get("locked") or not self.cfg.get("advisor_notify", True):
            return
        if a.get("firing"):
            self._advised_for = None
            await self._reload_notify(a, now)
            return
        self._reload_wait = False
        light_at, empty_at = a.get("light_at"), a.get("empty_at")
        if light_at is None or empty_at is None or now < light_at - 1800:
            return
        key = int(empty_at // 3600)
        if self._advised_for is not None and abs(key - self._advised_for) <= 2:
            return
        self._advised_for = key
        kg = a.get("wood_kg")
        title = f"Rozpal w kotle do {_hhmm(light_at)}"
        message = f"Ciepła w buforze wystarczy do ok. {_hhmm(empty_at)}." + (
            f" Załaduj ok. {kg:.0f} kg drewna — tyle przyjmie bufor i CWU razem z ogrzewaniem domu." if kg else "") + (
            "\n" + hc.wood_reminder(None, before_fire=True))
        self._log(f"Doradca: {title.lower()} (bufor do {_hhmm(empty_at)}" + (f", ~{kg:.0f} kg)" if kg else ")"), now)
        ncfg = await self._ncfg()
        if ncfg.get("enabled") and self.cfg.get("notify", True) is not False:
            await self._deliver(ncfg, key="heating_light_by", level="info", title=title, message=message)

    async def _persistent(self, key: str, title: str | None, message: str | None) -> None:
        try:
            if title is None:
                await self.hass.services.async_call(
                    "persistent_notification", "dismiss", {"notification_id": f"smartinghome_{key}"})
            else:
                await self.hass.services.async_call(
                    "persistent_notification", "create",
                    {"title": title, "message": message, "notification_id": f"smartinghome_{key}"})
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Heating: persistent notification failed: %s", err)

    # ── sidebar panel ────────────────────────────────────────────────────────
    async def _async_show_panel(self) -> None:
        if self._panel:
            return
        from . import PANEL_WWW_DIR, _async_publish_js, _register_sidebar_panel

        www_dir = Path(self.hass.config.path("www")) / PANEL_WWW_DIR
        await self.hass.async_add_executor_job(lambda: www_dir.mkdir(parents=True, exist_ok=True))
        module_url = await _async_publish_js(
            self.hass, Path(__file__).parent / "frontend" / HEATING_PANEL_FILENAME, www_dir)
        if module_url is None:
            return
        _register_sidebar_panel(self.hass, HEATING_PANEL_URL, HEATING_PANEL_TITLE, HEATING_PANEL_ICON,
                                HEATING_PANEL_ELEMENT, module_url)
        self._panel = True

    def _hide_panel(self) -> None:
        if not self._panel:
            return
        remove_panel(self.hass)
        self._panel = False

    # ── entities ─────────────────────────────────────────────────────────────
    def ensure_entities(self) -> None:
        if not self.enabled:
            return
        platforms = self.hass.data.get(DOMAIN, {}).get(_PLATFORMS_KEY, {})
        for (entry_id, domain), (entry, add_entities) in list(platforms.items()):
            if (entry_id, domain) in self._added:
                continue
            if any(added_entry != entry_id for added_entry, _ in self._added):
                continue   # one set of heating entities, on the first entry
            self._added.add((entry_id, domain))
            if domain == "sensor":
                add_entities([HeatingSensor(self, entry, d) for d in SENSORS])
            elif domain == "binary_sensor":
                add_entities([HeatingBinarySensor(self, entry, d) for d in BINARY_SENSORS])
            elif domain == "select":
                add_entities([HeatingModeSelect(self, entry)])
            elif domain == "number":
                add_entities([HeatingNumber(self, entry, d) for d in NUMBERS])

    def forget_entry(self, entry_id: str) -> None:
        self._added = {k for k in self._added if k[0] != entry_id}

    def parent_link(self, entry: Any) -> dict[str, Any]:
        """How the "Ogrzewanie" device hangs under the integration's main device, in the form this HA
        understands. The main device is got or created by its identifiers first: the heating entities may be
        set up before the main sensors have registered it."""
        if not _VIA_DEVICE_ID:
            return {"via_device": (DOMAIN, entry.entry_id)}
        parent = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)})
        return {"via_device_id": parent.id}

    def ws_payload(self, admin: bool) -> dict[str, Any]:
        offset = dt_util.now().utcoffset()
        return {
            "enabled": self.enabled,
            "admin": admin,
            "config": self.cfg,
            "status": self.snapshot,
            "sessions": self.model.sessions[-30:],
            "monthly": hm.monthly_summary(self.model.sessions, offset.total_seconds() if offset else 0.0,
                                          self.cfg.get("wood_price_kg"),
                                          self.cfg["wood_kwh_per_kg"] * self.cfg["efficiency"]),
            "log": list(self.log)[-60:],
            "features": {"auto": self.has(FEATURE_HEATING_AUTO), "advisor": self.has(FEATURE_HEATING_ADVISOR)},
            "roles": {role: {"label": label, "kind": kind} for role, (label, kind) in hm.ROLES.items()},
            "profiles": {k: v["label"] for k, v in hm.PROFILES.items()},
            "profile_defaults": hm.PROFILES,
            "emitters": {k: v["label"] for k, v in hm.EMITTERS.items()},
            "emitter_defaults": hm.EMITTERS,
            "modes": hm.MODES,
            "valve_types": hm.VALVE_TYPES,
            "dhw_sources": hm.DHW_SOURCES,
            "dhw_modes": hm.DHW_MODES,
            "states": hm.STATE_LABELS,
            "weather_entities": sorted(self.hass.states.async_entity_ids("weather")),
        }


def remove_panel(hass: HomeAssistant) -> None:
    try:
        from homeassistant.components.frontend import async_remove_panel

        async_remove_panel(hass, HEATING_PANEL_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Heating panel not registered")
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        lovelace = hass.data.get(LOVELACE_DATA)
        if lovelace and HEATING_PANEL_URL in lovelace.dashboards:
            del lovelace.dashboards[HEATING_PANEL_URL]
    except Exception:  # noqa: BLE001
        pass


def get_manager(hass: HomeAssistant) -> HeatingManager | None:
    return hass.data.get(DOMAIN, {}).get(_KEY)


async def async_start(hass: HomeAssistant) -> None:
    async_register_ws(hass)
    if get_manager(hass) is not None:
        return
    manager = HeatingManager(hass)
    hass.data.setdefault(DOMAIN, {})[_KEY] = manager
    await manager.async_start()


async def async_stop(hass: HomeAssistant) -> None:
    manager = hass.data.get(DOMAIN, {}).pop(_KEY, None)
    if manager is not None:
        manager._hide_panel()
        await manager.async_stop()     # awaited: a reload must not read the Store before this write


@callback
def register_platform(hass: HomeAssistant, entry: Any, domain: str, add_entities: Any) -> None:
    """Called by the entity platforms: heating entities are added when the module is on."""
    platforms = hass.data.setdefault(DOMAIN, {}).setdefault(_PLATFORMS_KEY, {})
    platforms[(entry.entry_id, domain)] = (entry, add_entities)
    manager = get_manager(hass)
    if manager is not None:
        manager._added.discard((entry.entry_id, domain))   # platform (re)loaded: its entities are new
        manager.ensure_entities()


@callback
def unregister_entry(hass: HomeAssistant, entry_id: str) -> None:
    platforms = hass.data.get(DOMAIN, {}).get(_PLATFORMS_KEY, {})
    for key in [k for k in platforms if k[0] == entry_id]:
        platforms.pop(key, None)
    manager = get_manager(hass)
    if manager is not None:
        manager.forget_entry(entry_id)


# ── entities ─────────────────────────────────────────────────────────────────

def _get(snapshot: dict[str, Any], *path: str) -> Any:
    cur: Any = snapshot
    for p in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


SENSORS: tuple[dict[str, Any], ...] = (
    {"key": "heating_boiler_state", "device_class": SensorDeviceClass.ENUM,
     "options": list(hm.STATE_LABELS), "path": ("boiler", "state"), "icon": "mdi:fire"},
    {"key": "heating_buffer_energy", "unit": UnitOfEnergy.KILO_WATT_HOUR,
     "device_class": SensorDeviceClass.ENERGY_STORAGE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("buffer", "energy_kwh"), "icon": "mdi:storage-tank"},
    {"key": "heating_buffer_charge", "unit": PERCENTAGE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("buffer", "charge_pct"), "icon": "mdi:storage-tank-outline"},
    {"key": "heating_buffer_space", "unit": UnitOfEnergy.KILO_WATT_HOUR,
     "device_class": SensorDeviceClass.ENERGY_STORAGE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("buffer", "space_kwh"), "icon": "mdi:arrow-collapse-up"},
    {"key": "heating_wood_max", "unit": UnitOfMass.KILOGRAMS, "device_class": SensorDeviceClass.WEIGHT,
     "state_class": SensorStateClass.MEASUREMENT, "path": ("buffer", "wood_max_kg"), "icon": "mdi:pine-tree"},
    {"key": "heating_buffer_mean", "unit": UnitOfTemperature.CELSIUS,
     "device_class": SensorDeviceClass.TEMPERATURE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("buffer", "mean_c")},
    {"key": "heating_dhw_energy", "unit": UnitOfEnergy.KILO_WATT_HOUR,
     "device_class": SensorDeviceClass.ENERGY_STORAGE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("dhw", "energy_kwh"), "icon": "mdi:water-boiler"},
    {"key": "heating_house_mean", "unit": UnitOfTemperature.CELSIUS,
     "device_class": SensorDeviceClass.TEMPERATURE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("house_c",), "icon": "mdi:home-thermometer-outline"},
    {"key": "heating_buffer_runtime", "unit": UnitOfTime.HOURS, "device_class": SensorDeviceClass.DURATION,
     "path": ("runtime_h",), "icon": "mdi:timer-sand"},
    {"key": "heating_last_fire_energy", "unit": UnitOfEnergy.KILO_WATT_HOUR,
     "device_class": SensorDeviceClass.ENERGY_STORAGE, "path": ("last_session", "stored_kwh"),
     "icon": "mdi:campfire"},
    # control
    {"key": "heating_supply_target", "unit": UnitOfTemperature.CELSIUS,
     "device_class": SensorDeviceClass.TEMPERATURE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("control", "supply_target"), "icon": "mdi:thermometer-chevron-up"},
    {"key": "heating_valve_position", "unit": PERCENTAGE, "state_class": SensorStateClass.MEASUREMENT,
     "path": ("control", "valve", "position"), "icon": "mdi:valve"},
    # advisor (PRO — unknown without the license)
    {"key": "heating_empty_at", "device_class": SensorDeviceClass.TIMESTAMP, "conv": "ts",
     "path": ("advice", "empty_at"), "icon": "mdi:timer-sand-empty"},
    {"key": "heating_light_at", "device_class": SensorDeviceClass.TIMESTAMP, "conv": "ts",
     "path": ("advice", "light_at"), "icon": "mdi:fire-alert"},
    {"key": "heating_wood_recommended", "unit": UnitOfMass.KILOGRAMS, "device_class": SensorDeviceClass.WEIGHT,
     "state_class": SensorStateClass.MEASUREMENT, "path": ("advice", "wood_kg"), "icon": "mdi:pine-tree-box"},
    {"key": "heating_demand_24h", "unit": UnitOfEnergy.KILO_WATT_HOUR, "path": ("advice", "demand_24h_kwh"),
     "icon": "mdi:home-thermometer"},
    {"key": "heating_building_ua", "unit": "W/K", "state_class": SensorStateClass.MEASUREMENT,
     "path": ("advice", "building", "ua_w_k"), "icon": "mdi:home-export-outline"},
)

BINARY_SENSORS: tuple[dict[str, Any], ...] = (
    {"key": "heating_firing", "device_class": BinarySensorDeviceClass.RUNNING, "icon": "mdi:fire",
     "fn": lambda s: _get(s, "boiler", "firing")},
    {"key": "heating_overheat", "device_class": BinarySensorDeviceClass.HEAT,
     "fn": lambda s: any(a["id"] == "heating_overheat" for a in s.get("alarms") or [])},
    {"key": "heating_probe_fault", "device_class": BinarySensorDeviceClass.PROBLEM,
     "fn": lambda s: any(a["id"] == "heating_probe_fault" for a in s.get("alarms") or [])},
    {"key": "heating_buffer_full", "icon": "mdi:storage-tank",
     "fn": lambda s: any(a["id"] == "heating_buffer_full" for a in s.get("alarms") or [])},
    {"key": "heating_dump", "icon": "mdi:radiator", "fn": lambda s: _get(s, "control", "dump")},
    {"key": "heating_dhw_charging", "device_class": BinarySensorDeviceClass.RUNNING, "icon": "mdi:water-boiler",
     "fn": lambda s: _get(s, "control", "dhw_pump", "on") if _get(s, "control", "dhw_pump", "configured") else None},
)

NUMBERS: tuple[dict[str, Any], ...] = (
    {"key": "heating_room_target", "cfg": "room_target_c", "min": 10.0, "max": 28.0, "step": 0.5,
     "unit": UnitOfTemperature.CELSIUS, "icon": "mdi:home-thermometer"},
    {"key": "heating_room_night", "cfg": "room_night_c", "min": 10.0, "max": 28.0, "step": 0.5,
     "unit": UnitOfTemperature.CELSIUS, "icon": "mdi:home-thermometer-outline"},
    {"key": "heating_curve_shift", "cfg": "curve_shift", "min": -15.0, "max": 15.0, "step": 0.5,
     "unit": UnitOfTemperature.CELSIUS, "icon": "mdi:chart-bell-curve"},
)


class _HeatingEntity:
    _attr_has_entity_name = True
    _attr_should_poll = False

    def _setup(self, manager: HeatingManager, entry: Any, key: str) -> None:
        self._manager = manager
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_heating")},
            name="Ogrzewanie",
            manufacturer=MANUFACTURER,
            model="Kocioł na paliwo stałe + bufor ciepła",
            sw_version=VERSION,
            configuration_url=f"homeassistant://{HEATING_PANEL_URL}",
            **manager.parent_link(entry),
        )

    @property
    def available(self) -> bool:
        return self._manager.enabled and bool(self._manager.snapshot)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE, self.async_write_ha_state))


class HeatingSensor(_HeatingEntity, SensorEntity):
    def __init__(self, manager: HeatingManager, entry: Any, desc: dict[str, Any]) -> None:
        self._setup(manager, entry, desc["key"])
        self._path = desc["path"]
        self._conv = desc.get("conv")
        self._attr_native_unit_of_measurement = desc.get("unit")
        self._attr_device_class = desc.get("device_class")
        self._attr_state_class = desc.get("state_class")
        self._attr_icon = desc.get("icon")
        if desc.get("options"):
            self._attr_options = desc["options"]

    @property
    def native_value(self) -> Any:
        value = _get(self._manager.snapshot, *self._path)
        if self._conv == "ts":
            return dt_util.utc_from_timestamp(value) if isinstance(value, (int, float)) else None
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        snap = self._manager.snapshot
        if self._path[0] == "boiler":
            boiler = snap.get("boiler") or {}
            return {"temperature": boiler.get("temp_c"), "trend_k_min": boiler.get("trend_k_min"),
                    "label": boiler.get("label")}
        if self._path[0] == "last_session":
            last = snap.get("last_session") or {}
            return {k: last.get(k) for k in ("start", "end", "duration_h", "max_c", "wood_est_kg", "wood_kg",
                                             "heat_kwh", "house_kwh", "overheat")}
        if self._path == ("runtime_h",):
            return {"discharge_kw": snap.get("discharge_kw")}
        if self._path[0] == "control":
            ctl = snap.get("control") or {}
            return {k: ctl.get(k) for k in ("mode", "curve_c", "room_corr", "room_target", "t_out", "heating_needed",
                                            "dump", "limiter")}
        if self._path[0] == "advice":
            a = snap.get("advice") or {}
            return {"basis": a.get("basis"), "demand_kw": a.get("demand_kw"), "per_kg": a.get("per_kg"),
                    "locked": a.get("locked", False)}
        return None


class HeatingBinarySensor(_HeatingEntity, BinarySensorEntity):
    def __init__(self, manager: HeatingManager, entry: Any, desc: dict[str, Any]) -> None:
        self._setup(manager, entry, desc["key"])
        self._fn = desc["fn"]
        self._attr_device_class = desc.get("device_class")
        self._attr_icon = desc.get("icon")

    @property
    def is_on(self) -> bool | None:
        snap = self._manager.snapshot
        return bool(self._fn(snap)) if snap else None


class HeatingModeSelect(_HeatingEntity, SelectEntity):
    _attr_icon = "mdi:radiator"

    def __init__(self, manager: HeatingManager, entry: Any) -> None:
        self._setup(manager, entry, "heating_mode")
        self._attr_options = list(hm.MODES)

    @property
    def available(self) -> bool:
        return self._manager.enabled

    @property
    def current_option(self) -> str | None:
        return self._manager.cfg.get("mode")

    async def async_select_option(self, option: str) -> None:
        if option in hm.MODES:
            await self._manager.async_save({"mode": option})


class HeatingNumber(_HeatingEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: HeatingManager, entry: Any, desc: dict[str, Any]) -> None:
        self._setup(manager, entry, desc["key"])
        self._cfg_key = desc["cfg"]
        self._attr_native_min_value = desc["min"]
        self._attr_native_max_value = desc["max"]
        self._attr_native_step = desc["step"]
        self._attr_native_unit_of_measurement = desc["unit"]
        self._attr_icon = desc["icon"]

    @property
    def available(self) -> bool:
        return self._manager.enabled

    @property
    def native_value(self) -> float | None:
        return self._manager.cfg.get(self._cfg_key)

    async def async_set_native_value(self, value: float) -> None:
        await self._manager.async_save({self._cfg_key: value})


# ── WebSocket API ────────────────────────────────────────────────────────────

_WS_REGISTERED = "_heating_ws"


@callback
def async_register_ws(hass: HomeAssistant) -> None:
    if hass.data.setdefault(DOMAIN, {}).get(_WS_REGISTERED):
        return
    hass.data[DOMAIN][_WS_REGISTERED] = True
    websocket_api.async_register_command(hass, ws_heating_status)
    websocket_api.async_register_command(hass, ws_heating_save)
    websocket_api.async_register_command(hass, ws_heating_command)
    websocket_api.async_register_command(hass, ws_heating_guard)


# settings any HA user may change: the house temperatures (everyone at home lights the boiler and sets them)
HOUSEHOLD_KEYS = frozenset({"room_target_c", "room_night_c", "night_enabled", "night_from", "night_to", "setbacks",
                            "fan_off"})


def _household_command(cfg: dict[str, Any], msg: dict[str, Any]) -> bool:
    """What any HA user may do from the panel: book weighed wood, and turn a pump between AUTO and HAND (everyone
    at home runs the CH pump or charges the hot water by hand). The 0 position — the emergency lock, also set for
    maintenance or an empty installation — and taking a pump out of it stay with the administrator."""
    action = msg.get("action")
    if action in ("log_load", "undo_load"):
        return True
    if action == "set_switch" and msg.get("position") in ("auto", "on") and msg.get("element") in hm.FORCE_ELEMENTS:
        return not (cfg.get("lockout") or {}).get(msg["element"])
    return False


def _is_admin(connection: Any) -> bool:
    user = getattr(connection, "user", None)
    return bool(user and user.is_admin)


def _manager_or_error(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> HeatingManager | None:
    manager = get_manager(hass)
    if manager is None:
        connection.send_error(msg["id"], "not_ready", "Moduł ogrzewania nie jest uruchomiony")
    return manager


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/heating/status"})
@websocket_api.async_response
async def ws_heating_status(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    manager = _manager_or_error(hass, connection, msg)
    if manager is not None:
        connection.send_result(msg["id"], manager.ws_payload(_is_admin(connection)))


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/heating/save",
    vol.Required("config"): dict,
})
@websocket_api.async_response
async def ws_heating_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Partial update of settings "heating". The house temperatures (day, night, setbacks) — any HA user: everyone
    at home lights the boiler and sets the temperature; the rest (installation, control, alarm thresholds — safety
    settings) — the administrator only."""
    admin = _is_admin(connection)
    # the emergency switches change only through their own command (with its confirmation in the panel)
    updates = {k: v for k, v in msg["config"].items() if k not in ("lockout", "force_on")}
    if not admin and (not updates or not set(updates) <= HOUSEHOLD_KEYS):
        connection.send_error(msg["id"], "unauthorized", "Bez uprawnień administratora zmienisz tylko temperatury w domu "
                                                         "(dzień, noc, obniżenia); resztę ustawień zmienia administrator")
        return
    manager = _manager_or_error(hass, connection, msg)
    if manager is None:
        return
    await manager.async_save(updates)
    connection.send_result(msg["id"], manager.ws_payload(admin))


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/heating/command",
    vol.Required("action"): str,
    vol.Optional("seconds"): vol.Coerce(float),
    vol.Optional("kg"): vol.Coerce(float),
    vol.Optional("target"): vol.In(["auto", "current", "last", "next"]),
    vol.Optional("element"): vol.In(list(hm.LOCK_ELEMENTS)),
    vol.Optional("position"): vol.In(["auto", "off", "on"]),
    vol.Optional("percent"): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
})
@websocket_api.async_response
async def ws_heating_command(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    manager = _manager_or_error(hass, connection, msg)
    if manager is None:
        return
    if not _is_admin(connection) and not _household_command(manager.cfg, msg):
        connection.send_error(msg["id"], "unauthorized",
                              "Bez uprawnień administratora: drewno, a pompy tylko AUTO ↔ RĘKA (pozycję 0 i jej "
                              "zdjęcie, tryb pracy i ręczne sterowanie zmienia administrator)")
        return
    try:
        payload = await manager.async_command(msg["action"], msg)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {**payload, "admin": _is_admin(connection)})


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/heating/guard",
    vol.Required("action"): vol.In(["status", "install", "remove"]),
})
@websocket_api.async_response
async def ws_heating_guard(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """The local safety script on the CH pump's Shelly (heating_shelly.py)."""
    if msg["action"] != "status" and not _is_admin(connection):
        connection.send_error(msg["id"], "unauthorized", "Skrypt instaluje tylko administrator")
        return
    manager = _manager_or_error(hass, connection, msg)
    if manager is None:
        return
    fn = {"status": hs.guard_status, "install": hs.guard_install, "remove": hs.guard_remove}[msg["action"]]
    result = await fn(hass, manager.cfg)
    if msg["action"] != "status":
        manager._log("Skrypt bezpieczeństwa Shelly: " + (
            "zainstalowany" if msg["action"] == "install" and result.get("running") else
            "usunięty" if msg["action"] == "remove" and not result.get("installed") else
            f"błąd — {result.get('error') or result.get('reason')}"))
    connection.send_result(msg["id"], result)
