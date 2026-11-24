# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Shelly Gen2+ / Gen3 local RPC for the heating module.

- resolve(): which Shelly (host, port, auth) and which component (switch:0,
  cover:0, temperature:101 …) stands behind a Home Assistant entity of the
  Shelly integration — from the entity registry and the Shelly config entry,
- cover_pulse(): a mixing-valve pulse as Cover.Open / Cover.Close with a duration,
  so the Shelly itself ends the pulse (a Home Assistant restart in the middle of a
  pulse doesn't leave the valve running to its end stop),
- the boiler room guard: a script installed ON the Shelly with the CH pump relay
  (shelly/heating_guard.js) — layer 2 of the safety plan, it works without Home
  Assistant and without internet: too hot or a probe lost → pump on (+ valve open).

Home Assistant imports are lazy (inside the async functions): the pure helpers
(component_from_unique_id, guard_config, guard_code) are unit-tested without HA.
Devices with a password (auth) are not driven over RPC: the pulse falls back to
Home Assistant services and the guard script has to be installed by hand.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

GUARD_SCRIPT_NAME = "smartinghome_guard"
GUARD_TEMPLATE = Path(__file__).parent / "shelly" / "heating_guard.js"
_CONFIG_RE = re.compile(r"/\*@config\*/.*?/\*@end\*/", re.S)
_COMPONENT_RE = re.compile(r"(switch|cover|temperature|input):(\d+)")
PUT_CODE_CHUNK = 1000          # Script.PutCode: chunks small enough for every firmware
FAULT_S = 60                   # a probe without a reading this long → pump on
SCRIPT_KEYS = ("pump_id", "boiler_id", "buffer_id", "boiler_hot", "buffer_hot", "fault_s",
               "valve_host", "valve_id", "valve_open")


class ShellyRpcError(Exception):
    """HTTP / RPC error, timeout or a device that wants a password."""


@dataclass
class ShellyTarget:
    host: str
    port: int
    gen: int
    auth: bool
    component: str | None
    cid: int | None

    @property
    def address(self) -> str:
        return self.host if self.port in (80, 0) else f"{self.host}:{self.port}"


# ── pure helpers ─────────────────────────────────────────────────────────────

def component_from_unique_id(unique_id: str) -> tuple[str, int] | None:
    """("temperature", 101) from e.g. "aabbccddeeff-temperature:101-temperature_0"."""
    m = _COMPONENT_RE.search(unique_id or "")
    return (m.group(1), int(m.group(2))) if m else None


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _probe_id(probe: ShellyTarget | None, pump: ShellyTarget) -> int | None:
    """An Add-on probe the guard can read: temperature:N on the pump's own Shelly."""
    if probe is None or probe.component != "temperature" or probe.cid is None:
        return None
    if probe.host != pump.host or probe.port != pump.port:
        return None
    return probe.cid


def guard_config(cfg: dict[str, Any], pump: ShellyTarget | None, boiler: ShellyTarget | None,
                 buffer: ShellyTarget | None, valve: ShellyTarget | None) -> dict[str, Any]:
    """What the guard script on the pump's Shelly can do with this installation."""
    alarm_c = _num(cfg.get("alarm_c"), 92.0)
    full_c = _num(cfg.get("buffer_full_c"), 88.0)
    dump_top = cfg.get("dump_top_c")
    by_buffer = isinstance(dump_top, (int, float)) and not isinstance(dump_top, bool)
    out: dict[str, Any] = {
        "supported": False, "reason": "", "notes": [], "host": None,
        "pump_id": None, "boiler_id": None, "buffer_id": None,
        # the same rule as Home Assistant's (heating_control): with a dump by the buffer the boiler starts none
        # (999 °C: never — the script still treats a lost boiler probe as a fault)
        "boiler_hot": 999.0 if by_buffer else round(alarm_c - 2.0, 1),
        "buffer_hot": round(float(dump_top) if by_buffer else min(full_c + 4.0, 95.0), 1),
        "fault_s": FAULT_S,
        "valve_host": None, "valve_id": None, "valve_open": "Cover.Open",
    }
    ents = cfg.get("entities") if isinstance(cfg.get("entities"), dict) else {}
    if not ents.get("pump_switch"):
        out["reason"] = ("Nie wybrano przekaźnika pompy CO — skrypt działa na Shelly, "
                         "który włącza pompę.")
        return out
    if pump is None:
        out["reason"] = ("Pompa CO nie jest przełączana przez Shelly Gen2/Gen3 dodane do Home Assistant — "
                         "skrypt bezpieczeństwa musi działać na Shelly z przekaźnikiem pompy.")
        return out
    if pump.component != "switch" or pump.cid is None:
        out["reason"] = "Encja pompy CO nie jest przekaźnikiem Shelly (switch:N)."
        return out
    out["host"] = pump.address
    out["pump_id"] = pump.cid
    out["boiler_id"] = _probe_id(boiler, pump)
    out["buffer_id"] = _probe_id(buffer, pump)
    if out["boiler_id"] is None and out["buffer_id"] is None:
        out["reason"] = ("Sondy kotła i bufora muszą być podłączone do tego samego Shelly co pompa "
                         "(Plus Add-on) — inaczej skrypt nie ma czego pilnować.")
        return out
    if out["boiler_id"] is None:
        out["notes"].append("Sonda kotła nie jest na Shelly pompy — skrypt pilnuje tylko bufora.")
    if out["buffer_id"] is None:
        out["notes"].append("Sonda bufora nie jest na Shelly pompy — skrypt pilnuje tylko kotła.")

    if cfg.get("safety_dump") and cfg.get("valve_type") == "cover":
        if valve is None or valve.component != "cover" or valve.cid is None:
            out["notes"].append("Zawór mieszający nie jest roletą Shelly — przy przegrzaniu skrypt "
                                "włączy tylko pompę.")
        elif valve.auth:
            out["notes"].append("Shelly zaworu ma hasło — przy przegrzaniu skrypt włączy tylko pompę.")
        else:
            out["valve_host"] = valve.address
            out["valve_id"] = valve.cid
            out["valve_open"] = "Cover.Close" if cfg.get("valve_invert") else "Cover.Open"

    if pump.auth:
        out["reason"] = ("Shelly pompy ma włączone hasło — Smarting HOME nie wgra skryptu. "
                         "Wyłącz uwierzytelnianie w Shelly albo wgraj poniższy skrypt ręcznie.")
        return out
    out["supported"] = True
    return out


_template_cache: str | None = None


async def _template(hass: Any) -> str:
    """The script template, read once in the executor (no file I/O in the event loop)."""
    global _template_cache
    if _template_cache is None:
        _template_cache = await hass.async_add_executor_job(lambda: GUARD_TEMPLATE.read_text(encoding="utf-8"))
    return _template_cache


def guard_code(gcfg: dict[str, Any], template: str | None = None) -> str:
    """The guard script with this installation's configuration injected."""
    if template is None:
        template = GUARD_TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps({k: gcfg.get(k) for k in SCRIPT_KEYS}, separators=(",", ":"), ensure_ascii=True)
    code, count = _CONFIG_RE.subn(lambda _m: f"/*@config*/{payload}/*@end*/", template, count=1)
    if count != 1:
        raise ValueError("heating_guard.js: configuration marker not found")
    return code


# ── Home Assistant side ──────────────────────────────────────────────────────

async def resolve(hass: Any, entity_id: str | None) -> ShellyTarget | None:
    """The Gen2+ Shelly behind an entity of the Shelly integration (None for anything else)."""
    if not entity_id:
        return None
    from homeassistant.helpers import entity_registry as er

    ent = er.async_get(hass).async_get(entity_id)
    if ent is None or ent.platform != "shelly" or not ent.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(ent.config_entry_id)
    if entry is None or entry.domain != "shelly":
        return None
    data = entry.data or {}
    try:
        gen = int(data.get("gen") or 1)
        port = int(data.get("port") or 80)
    except (TypeError, ValueError):
        return None
    host = data.get("host")
    if gen < 2 or not host:
        return None
    comp = component_from_unique_id(ent.unique_id or "")
    return ShellyTarget(
        host=str(host), port=port, gen=gen, auth=bool(data.get("password")),
        component=comp[0] if comp else None, cid=comp[1] if comp else None,
    )


async def rpc(hass: Any, target: ShellyTarget, method: str, params: dict[str, Any] | None = None,
              timeout: float = 5.0) -> dict[str, Any]:
    """One JSON-RPC call: POST http://host:port/rpc. Raises ShellyRpcError."""
    if target.auth:
        raise ShellyRpcError("Shelly ma włączone hasło")
    import aiohttp
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    payload: dict[str, Any] = {"id": 1, "src": "smartinghome", "method": method}
    if params:
        payload["params"] = params
    url = f"http://{target.address}/rpc"
    try:
        async with async_get_clientsession(hass).post(
            url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status == 401:
                raise ShellyRpcError("Shelly wymaga hasła (401)")
            if resp.status != 200:
                raise ShellyRpcError(f"HTTP {resp.status}")
            body = await resp.json(content_type=None)
    except ShellyRpcError:
        raise
    except Exception as err:  # noqa: BLE001 — timeouts, connection errors, bad JSON
        raise ShellyRpcError(str(err) or type(err).__name__) from err
    if not isinstance(body, dict):
        raise ShellyRpcError("niepoprawna odpowiedź Shelly")
    if body.get("error"):
        err = body["error"] if isinstance(body["error"], dict) else {"message": body["error"]}
        raise ShellyRpcError(f"{err.get('code', '')} {err.get('message', '')}".strip())
    result = body.get("result")
    return result if isinstance(result, dict) else {}


async def _cover(hass: Any, cover_entity: str) -> ShellyTarget | None:
    target = await resolve(hass, cover_entity)
    if target is None or target.component != "cover" or target.cid is None or target.auth:
        return None
    return target


async def cover_target(hass: Any, cover_entity: str) -> ShellyTarget | None:
    """The Shelly cover that takes timed pulses (Gen2+, no auth), None for any other actuator."""
    return await _cover(hass, cover_entity)


async def cover_maxtime(hass: Any, target: ShellyTarget) -> float | None:
    """The cover's own run limit (Cover.GetConfig maxtime_open / maxtime_close): a duration can't exceed it."""
    try:
        res = await rpc(hass, target, "Cover.GetConfig", {"id": target.cid})
    except ShellyRpcError as err:
        _LOGGER.debug("Heating: Cover.GetConfig on %s failed: %s", target.address, err)
        return None
    times = [_num(res.get(k), 0.0) for k in ("maxtime_open", "maxtime_close")]
    times = [t for t in times if t > 0]
    return min(times) if times else None


async def cover_pulse(hass: Any, cover_entity: str, direction: str, seconds: float,
                      invert: bool = False) -> bool:
    """Valve pulse timed by the Shelly. False → not possible here (caller falls back to services)."""
    if direction not in ("open", "close"):
        raise ValueError(f"direction must be open/close, not {direction!r}")
    target = await _cover(hass, cover_entity)
    if target is None:
        return False
    opening = (direction == "open") != bool(invert)
    method = "Cover.Open" if opening else "Cover.Close"
    duration = round(max(float(seconds), 0.1), 1)
    try:
        await rpc(hass, target, method, {"id": target.cid, "duration": duration})
    except ShellyRpcError as err:
        _LOGGER.warning("Heating: %s %.1f s on %s failed: %s", method, duration, target.address, err)
        return False
    return True


async def cover_stop(hass: Any, cover_entity: str) -> bool:
    target = await _cover(hass, cover_entity)
    if target is None:
        return False
    try:
        await rpc(hass, target, "Cover.Stop", {"id": target.cid})
    except ShellyRpcError as err:
        _LOGGER.warning("Heating: Cover.Stop on %s failed: %s", target.address, err)
        return False
    return True


# ── guard script on the pump's Shelly ────────────────────────────────────────

async def _targets(hass: Any, cfg: dict[str, Any]) -> tuple[ShellyTarget | None, ...]:
    ents = cfg.get("entities") if isinstance(cfg.get("entities"), dict) else {}
    pump = await resolve(hass, ents.get("pump_switch"))
    boiler = await resolve(hass, ents.get("boiler_out"))
    buffer = await resolve(hass, ents.get("buffer_top"))
    valve = await resolve(hass, cfg.get("valve_cover")) if cfg.get("valve_type") == "cover" else None
    return pump, boiler, buffer, valve


async def _find_script(hass: Any, pump: ShellyTarget) -> int | None:
    res = await rpc(hass, pump, "Script.List")
    for script in res.get("scripts") or []:
        if isinstance(script, dict) and script.get("name") == GUARD_SCRIPT_NAME:
            try:
                return int(script.get("id"))
            except (TypeError, ValueError):
                return None
    return None


async def _get_code(hass: Any, pump: ShellyTarget, sid: int) -> str | None:
    """The script's source on the device (Script.GetCode in pieces), None when it can't be read."""
    parts: list[str] = []
    offset = 0
    for _ in range(40):
        try:
            res = await rpc(hass, pump, "Script.GetCode", {"id": sid, "offset": offset, "len": PUT_CODE_CHUNK})
        except ShellyRpcError:
            return None
        data = res.get("data")
        if not isinstance(data, str):
            return None
        parts.append(data)
        offset += len(data.encode("utf-8"))   # offsets are bytes (the header has non-ASCII)
        if not data or not res.get("left"):
            return "".join(parts)
    return None


def _status(gcfg: dict[str, Any], template: str | None = None) -> dict[str, Any]:
    can_write = gcfg["pump_id"] is not None and (gcfg["boiler_id"] is not None or gcfg["buffer_id"] is not None)
    return {
        "supported": gcfg["supported"], "reason": gcfg["reason"], "notes": list(gcfg.get("notes") or []),
        "host": gcfg.get("host"), "installed": False, "running": False, "id": None,
        "config": {k: gcfg.get(k) for k in SCRIPT_KEYS},
        "code": guard_code(gcfg, template) if can_write else "",
    }


async def guard_status(hass: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    pump, boiler, buffer, valve = await _targets(hass, cfg)
    gcfg = guard_config(cfg, pump, boiler, buffer, valve)
    out = _status(gcfg, await _template(hass))
    if not gcfg["supported"] or pump is None:
        return out
    try:
        sid = await _find_script(hass, pump)
        if sid is not None:
            st = await rpc(hass, pump, "Script.GetStatus", {"id": sid})
            out.update(installed=True, id=sid, running=bool(st.get("running")))
            # settings changed since the install (thresholds, probe ids, valve) → install again
            on_device = await _get_code(hass, pump, sid)
            out["outdated"] = None if on_device is None else on_device != out["code"]
    except ShellyRpcError as err:
        out["error"] = f"Shelly pompy nie odpowiada: {err}"
    return out


async def guard_install(hass: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    pump, boiler, buffer, valve = await _targets(hass, cfg)
    gcfg = guard_config(cfg, pump, boiler, buffer, valve)
    template = await _template(hass)
    if not gcfg["supported"] or pump is None:
        return _status(gcfg, template)
    code = guard_code(gcfg, template)
    try:
        sid = await _find_script(hass, pump)
        if sid is None:
            res = await rpc(hass, pump, "Script.Create", {"name": GUARD_SCRIPT_NAME})
            sid = int(res["id"])
        else:
            try:
                await rpc(hass, pump, "Script.Stop", {"id": sid})
            except ShellyRpcError:
                pass  # not running
        for i, start in enumerate(range(0, len(code), PUT_CODE_CHUNK)):
            await rpc(hass, pump, "Script.PutCode",
                      {"id": sid, "code": code[start:start + PUT_CODE_CHUNK], "append": i > 0}, timeout=10.0)
        locked = bool((cfg.get("lockout") or {}).get("pump"))
        # a CH pump locked in the panel: the script is installed but stays stopped until unlocked
        await rpc(hass, pump, "Script.SetConfig", {"id": sid, "config": {"enable": not locked}})
        if not locked:
            await rpc(hass, pump, "Script.Start", {"id": sid})
    except (ShellyRpcError, KeyError, TypeError, ValueError) as err:
        out = await guard_status(hass, cfg)
        out["error"] = f"Instalacja skryptu nie powiodła się: {err}"
        return out
    _LOGGER.info("Heating: guard script installed on %s (script %s)", pump.address, sid)
    return await guard_status(hass, cfg)


async def guard_set_enabled(hass: Any, cfg: dict[str, Any], enabled: bool) -> str:
    """Stop (and keep stopped across reboots) or restart an installed guard script.
    Returns "stopped" / "started", "none" without a script or Shelly pump, "error" when it failed."""
    pump, _boiler, _buffer, _valve = await _targets(hass, cfg)
    if pump is None or pump.component != "switch":
        return "none"
    try:
        sid = await _find_script(hass, pump)
        if sid is None:
            return "none"
        await rpc(hass, pump, "Script.SetConfig", {"id": sid, "config": {"enable": enabled}})
        try:
            await rpc(hass, pump, "Script.Start" if enabled else "Script.Stop", {"id": sid})
        except ShellyRpcError:
            pass  # already running / already stopped
    except ShellyRpcError as err:
        _LOGGER.warning("Heating guard: %s failed: %s", "start" if enabled else "stop", err)
        return "error"
    return "started" if enabled else "stopped"


async def guard_remove(hass: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    pump, boiler, buffer, valve = await _targets(hass, cfg)
    if pump is None or pump.component != "switch" or pump.auth:
        return await guard_status(hass, cfg)
    try:
        sid = await _find_script(hass, pump)
        if sid is not None:
            try:
                await rpc(hass, pump, "Script.Stop", {"id": sid})
            except ShellyRpcError:
                pass
            await rpc(hass, pump, "Script.Delete", {"id": sid})
            _LOGGER.info("Heating: guard script removed from %s", pump.address)
    except ShellyRpcError as err:
        out = await guard_status(hass, cfg)
        out["error"] = f"Usunięcie skryptu nie powiodło się: {err}"
        return out
    return await guard_status(hass, cfg)
