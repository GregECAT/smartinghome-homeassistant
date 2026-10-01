"""WebSocket API for the Smarting HOME panel (settings, AI providers & models)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .ai_providers import AI_TASKS, PROVIDERS
from .const import DOMAIN


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register commands once per HA run."""
    if hass.data.setdefault(DOMAIN, {}).get("_ws_registered"):
        return
    hass.data[DOMAIN]["_ws_registered"] = True
    websocket_api.async_register_command(hass, ws_ai_config)
    websocket_api.async_register_command(hass, ws_ai_models)
    websocket_api.async_register_command(hass, ws_ai_save)
    websocket_api.async_register_command(hass, ws_ai_test)
    websocket_api.async_register_command(hass, ws_settings_get)
    websocket_api.async_register_command(hass, ws_settings_update)
    websocket_api.async_register_command(hass, ws_energy_ledger)
    websocket_api.async_register_command(hass, ws_alerts)


def _advisor(hass: HomeAssistant):
    return hass.data.get(DOMAIN, {}).get("_ai_advisor")


def _not_ready(connection, msg) -> None:
    connection.send_error(msg["id"], "not_ready", "Smarting HOME AI nie jest jeszcze gotowe")


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/ai/config"})
@callback
def ws_ai_config(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    advisor = _advisor(hass)
    if advisor is None:
        _not_ready(connection, msg)
        return
    connection.send_result(msg["id"], advisor.get_config())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartinghome/ai/models",
        vol.Required("provider"): vol.In(PROVIDERS),
        vol.Optional("refresh", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_ai_models(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    advisor = _advisor(hass)
    if advisor is None:
        _not_ready(connection, msg)
        return
    provider = msg["provider"]
    result = await advisor.catalog.async_get(provider, advisor.key(provider), refresh=msg["refresh"])
    connection.send_result(msg["id"], {
        "provider": provider,
        "models": result.models,
        "error": result.error,
        "fetched_at": result.fetched_at,
    })


_ASSIGNMENT = vol.Schema({
    vol.Optional("provider", default=""): vol.Any("", vol.In(PROVIDERS)),
    vol.Optional("model", default=""): str,
})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartinghome/ai/save",
        vol.Optional("keys", default={}): {vol.In(PROVIDERS): str},
        vol.Optional("default"): _ASSIGNMENT,
        vol.Optional("tasks", default={}): {vol.In(list(AI_TASKS)): _ASSIGNMENT},
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ai_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    advisor = _advisor(hass)
    if advisor is None:
        _not_ready(connection, msg)
        return
    from .settings_io import read_async, write_async

    for provider, key in msg["keys"].items():
        key = key.strip()
        if key and "…" not in key and "***" not in key:
            await advisor.secrets.async_set(provider, key)

    settings = await read_async(hass)
    ai_config = dict(settings.get("ai_config") or {})
    updates: dict[str, Any] = {}
    if "default" in msg and msg["default"].get("provider"):
        default = msg["default"]
        ai_config["default"] = default
        # Legacy fields still read by older code paths / panel parts
        updates["default_ai_provider"] = default["provider"]
        if default.get("model"):
            updates[f"{default['provider']}_model"] = default["model"]
    tasks = dict(ai_config.get("tasks") or {})
    for task, assignment in msg["tasks"].items():
        if task == "default":
            continue
        if assignment.get("provider"):
            tasks[task] = assignment
        else:
            tasks.pop(task, None)  # "(domyślny)"
    ai_config["tasks"] = tasks
    updates["ai_config"] = ai_config
    await write_async(hass, updates)
    advisor.apply_settings(await read_async(hass))
    connection.send_result(msg["id"], advisor.get_config())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartinghome/ai/test",
        vol.Required("provider"): vol.In(PROVIDERS),
        vol.Optional("api_key", default=""): str,
        vol.Optional("model", default=""): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ai_test(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    advisor = _advisor(hass)
    if advisor is None:
        _not_ready(connection, msg)
        return
    key = msg["api_key"].strip()
    if "…" in key or "***" in key:
        key = ""
    result = await advisor.test_provider(msg["provider"], api_key=key or None, model=msg["model"] or None)
    if result["ok"] and key:
        await advisor.secrets.async_set(msg["provider"], key)
    connection.send_result(msg["id"], result)


# ── Panel settings (private store, replaces public /local/…/settings.json) ──
# Same access level as the panel itself (require_admin=False) and the
# save_panel_settings service: any authenticated HA user.


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartinghome/settings/get",
        vol.Optional("keys"): [str],
    }
)
@websocket_api.async_response
async def ws_settings_get(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .settings_io import SECRET_KEYS, read_async

    settings = await read_async(hass)
    if "keys" in msg:
        settings = {k: settings[k] for k in msg["keys"] if k in settings}
    for key in SECRET_KEYS:
        settings.pop(key, None)
    connection.send_result(msg["id"], settings)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartinghome/settings/update",
        vol.Required("settings"): dict,
    }
)
@websocket_api.async_response
async def ws_settings_update(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    from .settings_io import SECRET_KEYS, write_async

    updates = {k: v for k, v in msg["settings"].items() if k not in SECRET_KEYS}
    if updates:
        await write_async(hass, updates)
        if "arbitrage_params" in updates:
            # Re-plan on the next autopilot tick
            for entry_data in hass.data.get(DOMAIN, {}).values():
                ctrl = entry_data.get("strategy_controller") if isinstance(entry_data, dict) else None
                if ctrl is not None:
                    ctrl.invalidate_arbitrage_plan()
        advisor = _advisor(hass)
        if advisor is not None and "energy_provider" in updates:
            # Tariff labels/prices in AI prompts follow the panel's provider
            from .settings_io import read_async

            advisor.apply_settings(await read_async(hass))
    connection.send_result(msg["id"], {"updated": list(updates)})


# ── Energy ledger & alerts (backend is the single source of truth) ──


def _coordinator(hass: HomeAssistant):
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if isinstance(entry_data, dict) and entry_data.get("coordinator") is not None:
            return entry_data["coordinator"]
    return None


@websocket_api.websocket_command(
    {
        vol.Required("type"): "smartinghome/energy/ledger",
        vol.Required("start"): str,
        vol.Required("end"): str,
        vol.Optional("days", default=False): bool,
    }
)
@callback
def ws_energy_ledger(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Energy & money summary for [start, end] (YYYY-MM-DD, local days)."""
    from datetime import date, timedelta

    from .energy_ledger import EnergyLedger

    coordinator = _coordinator(hass)
    if coordinator is None:
        _not_ready(connection, msg)
        return
    try:
        start, end = date.fromisoformat(msg["start"]), date.fromisoformat(msg["end"])
    except ValueError:
        connection.send_error(msg["id"], "invalid_format", "Daty w formacie YYYY-MM-DD")
        return
    ledger = coordinator.ledger
    result: dict[str, Any] = {"summary": EnergyLedger.summarise(ledger.period(start, end))}
    if msg["days"]:
        days: dict[str, Any] = {}
        day = start
        while day <= end and len(days) < 400:
            if ledger.has_day(day):
                days[day.isoformat()] = EnergyLedger.summarise(ledger.day(day))
            day += timedelta(days=1)
        result["days"] = days
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/alerts"})
@callback
def ws_alerts(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Active alerts + recent history from the backend alert engine."""
    coordinator = _coordinator(hass)
    engine = getattr(coordinator, "alerts", None) if coordinator else None
    if engine is None:
        _not_ready(connection, msg)
        return
    connection.send_result(msg["id"], engine.snapshot())
