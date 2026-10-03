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
    websocket_api.async_register_command(hass, ws_tariffs)
    websocket_api.async_register_command(hass, ws_energy_monthly)
    websocket_api.async_register_command(hass, ws_forecast_status)
    websocket_api.async_register_command(hass, ws_wind_today)
    websocket_api.async_register_command(hass, ws_wind_calendar)
    websocket_api.async_register_command(hass, ws_deposit_status)
    websocket_api.async_register_command(hass, ws_voltage_report)
    websocket_api.async_register_command(hass, ws_ledger_rebuild)
    websocket_api.async_register_command(hass, ws_battery_full_log)

    from .meter_ws import async_register as _register_meter_ws
    _register_meter_ws(hass)


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


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/wind/today"})
@callback
def ws_wind_today(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Today's wind-turbine estimate (recorder hours + live hour) and the turbine used."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        cal = entry_data.get("wind_calendar") if isinstance(entry_data, dict) else None
        if cal is not None:
            connection.send_result(msg["id"], {"today": cal.get_today_status(), "turbine": cal._get_turbine_params()})
            return
    _not_ready(connection, msg)


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/wind/calendar",
    vol.Optional("start_date"): vol.Any(str, None),
    vol.Optional("end_date"): vol.Any(str, None),
})
@callback
def ws_wind_calendar(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Wind calendar days for a date range — answered directly, not as a bus event
    (the payload is too large for the recorder's event table)."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        cal = entry_data.get("wind_calendar") if isinstance(entry_data, dict) else None
        if cal is not None:
            connection.send_result(msg["id"], cal.get_calendar_data(
                msg.get("start_date") or None, msg.get("end_date") or None))
            return
    _not_ready(connection, msg)


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/deposit/status",
    vol.Optional("force", default=False): bool,
})
@websocket_api.async_response
async def ws_deposit_status(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Prosumer deposit: monthly account, balance, expiry, export value for the autopilot."""
    from .deposit_service import get_tracker

    try:
        connection.send_result(msg["id"], await get_tracker(hass).async_status(force=msg["force"]))
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "deposit_failed", f"{type(err).__name__}: {err}")


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/voltage/report",
    vol.Optional("days", default=30): vol.All(int, vol.Range(min=1, max=400)),
})
@websocket_api.async_response
async def ws_voltage_report(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Grid voltage report (recorder statistics) + the guard's state and exceedance log."""
    from datetime import timedelta as _td

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period
    from homeassistant.util import dt as dt_util

    from .const import (
        SENSOR_GRID_POWER_TOTAL, SENSOR_GRID_VOLTAGE_L1, SENSOR_GRID_VOLTAGE_L2, SENSOR_GRID_VOLTAGE_L3,
    )
    from .voltage_report import build_report, window_start

    phases = {"L1": SENSOR_GRID_VOLTAGE_L1, "L2": SENSOR_GRID_VOLTAGE_L2, "L3": SENSOR_GRID_VOLTAGE_L3}
    now = dt_util.now()
    start = window_start(now, msg["days"])
    inst = get_instance(hass)

    def local(ts):
        t = dt_util.utc_from_timestamp(ts) if isinstance(ts, (int, float)) else ts
        return dt_util.as_local(t).replace(tzinfo=None)

    # Older history may sit under another integration (GoodWe SEMS cloud: grid_N_ac_voltage)
    import re as _re

    from homeassistant.components.recorder.statistics import list_statistic_ids

    known = await inst.async_add_executor_job(list_statistic_ids, hass, None, "mean")
    extra: dict[str, list[str]] = {}
    for item in known:
        m = _re.match(r"^sensor\..*_grid_([123])_ac_voltage$", item["statistic_id"])
        if m:
            extra.setdefault(f"L{m.group(1)}", []).append(item["statistic_id"])
    ids = set(phases.values()) | {SENSOR_GRID_POWER_TOTAL} | {i for v in extra.values() for i in v}
    hourly_raw = await inst.async_add_executor_job(
        statistics_during_period, hass, dt_util.as_utc(start), None, ids, "hour", None, {"mean", "max", "min"})
    five_raw = await inst.async_add_executor_job(
        statistics_during_period, hass, dt_util.as_utc(max(start, now - _td(days=10))), None,
        set(phases.values()), "5minute", None, {"mean"})
    def valid(x):
        return x if x is not None and 150 < x < 300 else None  # 0 V = inverter offline

    def live(r) -> bool:
        # A real voltage moves within an hour; a flat line is a stuck (cloud) sensor
        mx, mn = valid(r.get("max")), valid(r.get("min"))
        return mx is not None and mn is not None and mx - mn >= 0.1

    hourly = {}
    for p, sid in phases.items():
        rows: dict = {}
        for alt in extra.get(p, []):  # fallback first, the inverter's own sensor wins
            for r in hourly_raw.get(alt, []):
                if live(r):
                    rows[local(r["start"])] = (valid(r.get("mean")), valid(r.get("max")))
        for r in hourly_raw.get(sid, []):
            if live(r):
                rows[local(r["start"])] = (valid(r.get("mean")), valid(r.get("max")))
        hourly[p] = [(t, mean, mx) for t, (mean, mx) in sorted(rows.items())]
    five = {p: [(local(r["start"]), r["mean"]) for r in five_raw.get(sid, []) if valid(r.get("mean")) is not None]
            for p, sid in phases.items()}
    grid = {local(r["start"]): r["mean"] for r in hourly_raw.get(SENSOR_GRID_POWER_TOTAL, [])
            if r.get("mean") is not None and (r.get("max") or 0) - (r.get("min") or 0) > 1}

    ctrl = getattr(_coordinator(hass), "_strategy_controller", None)
    guard = getattr(ctrl, "_vguard", None)
    limit = float(guard.cfg.get("limit_v", 253.0)) if guard else 253.0
    report = await hass.async_add_executor_job(build_report, hourly, five, grid, limit)
    events = []
    if ctrl is not None:
        cutoff = start.timestamp()
        events = [e for e in await ctrl._voltage_events() if e.get("start", 0) >= cutoff]
    connection.send_result(msg["id"], {
        **report,
        "days_requested": msg["days"],
        "guard": guard.status() if guard else None,
        "events": events[-200:],
        "generated": now.isoformat(timespec="minutes"),
    })


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/energy/rebuild_day",
    vol.Required("date"): str,
})
@websocket_api.async_response
async def ws_ledger_rebuild(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Rebuild one day of the energy ledger from recorder history + lifetime counters."""
    from datetime import date as _date

    coordinator = _coordinator(hass)
    if coordinator is None:
        _not_ready(connection, msg)
        return
    try:
        day = _date.fromisoformat(msg["date"][:10])
    except ValueError:
        connection.send_error(msg["id"], "invalid_date", "YYYY-MM-DD")
        return
    connection.send_result(msg["id"], await coordinator.async_rebuild_ledger_day(day))


@websocket_api.websocket_command({
    vol.Required("type"): "smartinghome/battery/full_log",
    vol.Optional("days", default=21): vol.All(int, vol.Range(min=1, max=120)),
})
@callback
def ws_battery_full_log(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """When the battery got full from the sun each day vs the morning plan and forecast."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        _not_ready(connection, msg)
        return
    connection.send_result(msg["id"], {"days": coordinator.ledger.full_log(msg["days"])})


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/forecast/status"})
@callback
def ws_forecast_status(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """PV forecast (source, per-MPPT calibration), load model and the W5 peak load guard."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        _not_ready(connection, msg)
        return
    data = coordinator.data or {}
    ctrl = getattr(coordinator, "_strategy_controller", None)
    guard = getattr(ctrl, "_load_guard", None)
    plan = getattr(ctrl, "_arb_status", None) or {}
    # Hourly forecasts around now (charts): {"YYYY-MM-DDTHH": kW mean}
    from datetime import timedelta as _td

    from homeassistant.util import dt as dt_util

    now = dt_util.now().replace(tzinfo=None, minute=0, second=0, microsecond=0)
    pvf = getattr(coordinator, "pv_forecaster", None)
    lf = getattr(coordinator, "load_forecaster", None)
    temps = pvf.daily_temps() if pvf is not None else None
    pv_hourly: dict[str, float] = {}
    load_hourly: dict[str, float] = {}
    for i in range(-13, 26):
        t = now + _td(hours=i)
        key = t.strftime("%Y-%m-%dT%H")
        if pvf is not None and pvf.available and data.get("pv_forecast_source") == "open_meteo":
            pv_hourly[key] = round(pvf.hour_kwh(t), 3)
        if lf is not None and lf.model.days >= 3:
            kw = lf.kw(t, temps)
            if kw is not None:
                load_hourly[key] = round(kw, 3)
    connection.send_result(msg["id"], {
        "station": {
            "radiation": data.get("ecowitt_solar_radiation"),
            "temp": data.get("ecowitt_temp"),
            "nowcast": data.get("radiation_nowcast"),
            "scale": data.get("radiation_sensor_scale"),
            "pv_expected_w": data.get("pv_expected_now_w"),
        },
        "pv_hourly": pv_hourly,
        "load_hourly": load_hourly,
        "pv": data.get("pv_forecast_status") or {},
        "pv_forecast_solar": {
            "today": data.get("pv_forecast_today_total_fs"),
            "tomorrow": data.get("pv_forecast_tomorrow_total_fs"),
        },
        "load": data.get("load_forecast_status") or {},
        "plan": {k: plan.get(k) for k in ("pv_source", "load_source", "load_ratio", "pv_factor", "updated")},
        "guard": guard.status() if guard is not None else None,
        "boiler": ctrl._boiler.status() if ctrl is not None and hasattr(ctrl, "_boiler") else None,
        "voltage": ctrl._vguard.status() if ctrl is not None and hasattr(ctrl, "_vguard") else None,
    })


# ── Tariff prices (single source for panel, autopilot and AI) ──

_PANEL_TARIFF_KEY = {"g11": "G11", "g12": "G12", "g12w": "G12w", "g12n": "G12n", "g13": "G13"}


@websocket_api.websocket_command({vol.Required("type"): "smartinghome/tariffs"})
@callback
def ws_tariffs(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Per-kWh prices per provider/tariff (brutto, all variable components) + season."""
    from homeassistant.util import dt as dt_util

    from .const import (
        PROVIDER_TARIFF_PRICES,
        TARIFF_PRICES_CHECKED,
        TARIFF_PRICES_SOURCE,
        TARIFF_PRICES_VALID_FROM,
        WINTER_MONTHS,
    )

    prices: dict[str, Any] = {}
    for provider, tariffs in PROVIDER_TARIFF_PRICES.items():
        prices[str(provider)] = {
            _PANEL_TARIFF_KEY.get(str(t), str(t).upper()): dict(v) for t, v in tariffs.items()
        }
    now = dt_util.now()
    winter = now.month in WINTER_MONTHS
    connection.send_result(msg["id"], {
        "prices": prices,
        "valid_from": TARIFF_PRICES_VALID_FROM,
        "checked": TARIFF_PRICES_CHECKED,
        "source": TARIFF_PRICES_SOURCE,
        "season": "winter" if winter else "summer",
        "g13_afternoon_peak": "16:00–21:00" if winter else "19:00–22:00",
    })


# ── Monthly history from the recorder (Zima na plusie) ──

_LOAD_CANDIDATES = ("sensor.total_load", "sensor.goodwe_total_load")
_PV_CANDIDATES = ("sensor.total_pv_generation", "sensor.goodwe_total_pv_generation")


@websocket_api.websocket_command(
    {vol.Required("type"): "smartinghome/energy/monthly", vol.Optional("months", default=13): int}
)
@websocket_api.async_response
async def ws_energy_monthly(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Per month: house load and PV (inverter lifetime counters) and grid import/export
    (utility meter import e.g. Tauron eLicznik, else grid-meter counters), with the
    number of days that actually had data."""
    from datetime import timedelta

    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import (
        list_statistic_ids,
        statistics_during_period,
    )
    from homeassistant.util import dt as dt_util

    coordinator = _coordinator(hass)
    smap = getattr(coordinator, "_sensor_map", {}) if coordinator else {}
    months = max(1, min(25, msg["months"]))
    now = dt_util.now()
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for _ in range(months - 1):
        first = (first - timedelta(days=1)).replace(day=1)

    instance = get_instance(hass)
    stat_ids = await instance.async_add_executor_job(list_statistic_ids, hass)
    known = {s["statistic_id"] for s in stat_ids if s.get("has_sum")}

    def pick(mapped: str, candidates: tuple[str, ...]) -> str:
        for eid in (mapped, *candidates):
            if eid and eid in known:
                return eid
        return ""

    load_id = pick(smap.get("total_load_consumption", ""), _LOAD_CANDIDATES)
    pv_id = pick(smap.get("total_production", ""), _PV_CANDIDATES)
    # Utility meter import (billing truth, full history): prefer hourly-balanced values
    ext = sorted(k for k in known if ":" in k)
    imp_id = next((k for k in ext if k.endswith("_balanced_consumption")), "") or \
        next((k for k in ext if k.endswith("_consumption")), "")
    exp_id = next((k for k in ext if k.endswith("_balanced_generation")), "") or \
        next((k for k in ext if k.endswith("_generation")), "")
    raw_imp_id = next((k for k in ext if k.endswith("_consumption") and "balanced" not in k), "")
    raw_exp_id = next((k for k in ext if k.endswith("_generation") and "balanced" not in k), "")
    if not imp_id:
        imp_id = pick(smap.get("total_energy_import", ""), ())
        exp_id = pick(smap.get("total_energy_export", ""), ())
    ids = [i for i in {load_id, pv_id, imp_id, exp_id, raw_imp_id, raw_exp_id} if i]

    stats = await instance.async_add_executor_job(
        statistics_during_period, hass, dt_util.as_utc(first), None, set(ids), "day", None, {"change"}
    ) if ids else {}

    def monthly(stat_id: str, cap: float) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for row in stats.get(stat_id, []) if stat_id else []:
            change = row.get("change")
            start = row.get("start")
            if change is None or start is None:
                continue
            ts = dt_util.as_local(dt_util.utc_from_timestamp(start) if isinstance(start, (int, float)) else start)
            key = ts.strftime("%Y-%m")
            m = out.setdefault(key, {"kwh": 0.0, "days": 0})
            if 0 < change < cap:  # counter resets / gaps produce negative or huge jumps
                m["kwh"] += change
                m["days"] += 1
        return out

    load_m, pv_m = monthly(load_id, 300), monthly(pv_id, 200)
    imp_m, exp_m = monthly(imp_id, 300), monthly(exp_id, 300)
    rimp_m, rexp_m = monthly(raw_imp_id, 300), monthly(raw_exp_id, 300)

    result = []
    cursor = first
    while cursor <= now:
        key = cursor.strftime("%Y-%m")
        nxt = (cursor + timedelta(days=32)).replace(day=1)
        days_in = (nxt - cursor).days if nxt <= now else now.day
        def g(src, k=key):
            v = src.get(k)
            return (round(v["kwh"], 1), v["days"]) if v else (None, 0)
        load, load_d = g(load_m)
        pv, pv_d = g(pv_m)
        imp, imp_d = g(imp_m)
        exp, exp_d = g(exp_m)
        rimp, _ = g(rimp_m)
        rexp, _ = g(rexp_m)
        result.append({
            "month": key, "days": days_in, "complete": nxt <= now,
            "load_kwh": load, "load_days": load_d,
            "pv_kwh": pv, "pv_days": pv_d,
            "import_kwh": imp, "import_days": imp_d,
            "export_kwh": exp, "export_days": exp_d,
            "import_raw_kwh": rimp, "export_raw_kwh": rexp,
        })
        cursor = nxt
    connection.send_result(msg["id"], {
        "months": result,
        "sources": {"load": load_id, "pv": pv_id, "import": imp_id, "export": exp_id,
                    "import_raw": raw_imp_id, "export_raw": raw_exp_id},
    })
