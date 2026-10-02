"""Backend alert engine — one source of truth for alerts and notifications.

Runs every coordinator cycle (not in the browser), so an incident produces one
notification no matter how many panels are open. Each alert:

  * must hold for a minimum time before it becomes active (no blips),
  * notifies once per incident (critical: reminder every 2 h while active),
  * sends a "resolved" message that replaces the alert on the phone,
  * respects quiet hours — warnings that start at night are sent in the morning
    if still active, critical ones go out immediately.

Checks know what the autopilot is doing: exporting from the battery at night is
arbitrage, not a CT fault; a planned grid charge that doesn't run is a fault.

Sign convention (project canonical): grid + export / − import,
battery + discharge / − charge.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SENSOR_BATTERY_POWER,
    SENSOR_BATTERY_SOC,
    SENSOR_BATTERY_TEMPERATURE,
    SENSOR_GRID_FREQUENCY_L1,
    SENSOR_GRID_POWER_TOTAL,
    SENSOR_GRID_VOLTAGE_L1,
    SENSOR_GRID_VOLTAGE_L2,
    SENSOR_GRID_VOLTAGE_L3,
    SENSOR_INVERTER_TEMP,
    SENSOR_PV_POWER,
)
from .notifier import async_deliver

_LOGGER = logging.getLogger(__name__)

HISTORY_LEN = 100
CRITICAL_REMINDER_S = 2 * 3600
CLEAR_AFTER_S = 120           # condition must be gone this long to resolve
SETTINGS_TTL_S = 60

# PN-EN 50160 / NC RfG: 230 V +10 % = 253 V as a 10-minute average — above it
# the inverter must disconnect. Warn a little earlier so there's time to react.
VOLT_WARN = 250.0
VOLT_TRIP = 253.0


@dataclass
class Alert:
    """A detected condition (rendered in the panel and in notifications)."""

    id: str
    level: str                 # critical / warning / info
    source: str                # Falownik / Bateria / Sieć / PV / Autopilot / Pomiar
    title: str
    message: str               # one or two lines for the phone
    action: str = ""           # what to do
    hold_s: int = 0            # condition must persist this long
    detected: str = ""
    causes: list[str] = field(default_factory=list)
    resolved_text: str = ""

    def as_panel(self) -> dict[str, Any]:
        return {
            "id": self.id, "level": self.level, "source": self.source,
            "title": self.title, "desc": self.message,
            "diag": {
                "detected": self.detected or self.message,
                "reason": self.message,
                "causes": self.causes,
                "action": self.action,
                "severity": {"critical": "Krytyczne", "warning": "Ostrzeżenie", "info": "Informacja"}.get(
                    self.level, self.level),
            },
        }


@dataclass
class _Track:
    first_seen: float
    active: bool = False
    since_wall: str = ""
    last_seen: float = 0.0
    notified_at: float = 0.0
    pending_notify: bool = False
    notified_level: str = ""
    alert: Alert | None = None


def _num(data: dict[str, Any], key: str) -> float | None:
    value = data.get(key)
    try:
        return float(value) if value not in (None, "", "unknown", "unavailable") else None
    except (TypeError, ValueError):
        return None


def _pl(value: float, digits: int = 1) -> str:
    """Polish decimal comma."""
    return f"{value:.{digits}f}".replace(".", ",")


def _fmt_w(watts: float) -> str:
    return f"{_pl(watts / 1000)} kW" if abs(watts) >= 1000 else f"{watts:.0f} W"


def _in_quiet(cfg: dict[str, Any], now: datetime) -> bool:
    start, end = cfg.get("quiet_start") or "", cfg.get("quiet_end") or ""
    if not start or not end:
        return False
    try:
        qs, qe = int(start.replace(":", "")), int(end.replace(":", ""))
    except ValueError:
        return False
    hm = now.hour * 100 + now.minute
    return (hm >= qs or hm < qe) if qs > qe else (qs <= hm < qe)


class AlertEngine:
    """Evaluates checks, tracks incidents, sends notifications, keeps history."""

    def __init__(self, hass: HomeAssistant, entry_id: str, settings_reader: Callable) -> None:
        self.hass = hass
        self._read_settings = settings_reader
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry_id}.alerts")
        self._loaded = False
        self._tracks: dict[str, _Track] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_LEN)
        self._sent: deque[dict[str, Any]] = deque(maxlen=50)
        self._volt: dict[str, deque[tuple[float, float]]] = {}
        self._settings: dict[str, Any] = {}
        self._settings_ts = 0.0
        self._started = time.monotonic()
        self._report_sent_for = ""
        self._dirty = False
        self._last_save = 0.0
        self.active: list[Alert] = []

    # ── persistence ──────────────────────────────────────────────

    async def _async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load() or {}
        self._history.extend(data.get("history") or [])
        self._sent.extend(data.get("sent") or [])
        self._report_sent_for = data.get("report_sent_for", "")

    async def _async_save(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (not self._dirty or now - self._last_save < 60):
            return
        await self._store.async_save({
            "history": list(self._history),
            "sent": list(self._sent),
            "report_sent_for": self._report_sent_for,
        })
        self._dirty = False
        self._last_save = now

    async def _cfg(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._settings_ts > SETTINGS_TTL_S:
            try:
                self._settings = await self._read_settings(self.hass)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Alert engine: settings read failed: %s", err)
            self._settings_ts = now
        return self._settings.get("notification_config") or {}

    # ── public ───────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": [a.as_panel() | {"since": self._tracks[a.id].since_wall} for a in self.active
                       if a.id in self._tracks],
            "history": list(self._history)[-50:],
            "sent": list(self._sent)[-20:],
        }

    def record_event(self, alert: Alert) -> None:
        """One-off event (e.g. watchdog healed the inverter) — history + optional notify."""
        self._history.append(self._hist_entry(alert, "event"))
        self._dirty = True
        self.hass.async_create_task(self._notify_alert(alert, kind="event"))

    async def async_evaluate(self, data: dict[str, Any], context: dict[str, Any]) -> list[Alert]:
        """Run all checks for this cycle; returns currently active alerts."""
        await self._async_load()
        cfg = await self._cfg()
        now_m = time.monotonic()
        now = dt_util.now()

        found: dict[str, Alert] = {}
        checks = (
            self._check_inverter_offline, self._check_control_lost, self._check_voltage,
            self._check_grid_outage, self._check_frequency, self._check_inverter_temp,
            self._check_battery_temp, self._check_export_balance, self._check_pv_dead, self._check_pv_low,
            self._check_peak_import, self._check_planned_charge, self._check_autopilot_error,
        )
        if context.get("grid_only"):
            # No inverter, PV or battery — only grid-quality checks apply
            checks = (self._check_voltage, self._check_grid_outage, self._check_frequency)
        for check in checks:
            try:
                alert = check(data, context, now_m)
            except Exception as err:  # noqa: BLE001 — one bad check must not stop the rest
                _LOGGER.debug("Alert check %s failed: %s", check.__name__, err)
                continue
            if alert is not None:
                found[alert.id] = alert

        # Track incidents
        for alert_id, alert in found.items():
            track = self._tracks.get(alert_id)
            if track is None:
                track = self._tracks[alert_id] = _Track(first_seen=now_m)
            track.last_seen = now_m
            track.alert = alert
            if not track.active and now_m - track.first_seen >= alert.hold_s:
                track.active = True
                track.since_wall = now.strftime("%H:%M")
                self._history.append(self._hist_entry(alert, "start"))
                self._dirty = True
                await self._maybe_notify(track, cfg, now, first=True)
            elif track.active:
                await self._maybe_notify(track, cfg, now, first=False)

        # Resolve incidents whose condition is gone
        for alert_id in list(self._tracks):
            if alert_id in found:
                continue
            track = self._tracks[alert_id]
            if now_m - track.last_seen < CLEAR_AFTER_S:
                continue
            del self._tracks[alert_id]
            if track.active and track.alert:
                self._history.append(self._hist_entry(track.alert, "end"))
                self._dirty = True
                if track.notified_at and track.alert.level in ("critical", "warning"):
                    await self._send(cfg, track.alert, resolved=True)

        await self._maybe_daily_report(cfg, now, context)
        await self._async_save()
        self.active = [t.alert for t in self._tracks.values() if t.active and t.alert]
        order = {"critical": 0, "warning": 1, "info": 2}
        self.active.sort(key=lambda a: order.get(a.level, 3))
        return self.active

    # ── notification policy ──────────────────────────────────────

    async def _maybe_notify(self, track: _Track, cfg: dict[str, Any], now: datetime, first: bool) -> None:
        alert = track.alert
        if alert is None or not cfg.get("enabled"):
            return
        if alert.level not in (cfg.get("levels") or ["critical", "warning"]):
            return
        quiet = _in_quiet(cfg, now)
        if first:
            if quiet and alert.level != "critical":
                track.pending_notify = True   # deliver in the morning if still active
                return
            await self._send(cfg, alert)
            track.notified_at = time.monotonic()
            track.notified_level = alert.level
            return
        escalated = track.notified_level == "warning" and alert.level == "critical"
        if (track.pending_notify and not quiet) or escalated:
            track.pending_notify = False
            await self._send(cfg, alert)
            track.notified_at = time.monotonic()
            track.notified_level = alert.level
        elif (alert.level == "critical" and track.notified_at
              and time.monotonic() - track.notified_at > CRITICAL_REMINDER_S):
            await self._send(cfg, alert, reminder=True)
            track.notified_at = time.monotonic()

    async def _notify_alert(self, alert: Alert, kind: str) -> None:
        cfg = await self._cfg()
        if cfg.get("enabled") and alert.level in (cfg.get("levels") or ["critical", "warning"]):
            if not (_in_quiet(cfg, dt_util.now()) and alert.level != "critical"):
                await self._send(cfg, alert)

    async def _send(self, cfg: dict[str, Any], alert: Alert, resolved: bool = False,
                    reminder: bool = False) -> None:
        if resolved:
            title = alert.resolved_text or f"{alert.title.split(':')[0]} — wróciło do normy"
            message = f"Problem ustąpił ({dt_util.now().strftime('%H:%M')})."
            level = "resolved"
        else:
            title = ("Nadal: " if reminder else "") + alert.title
            message = alert.message + (f"\n→ {alert.action}" if alert.action else "")
            level = alert.level
        sent = await async_deliver(
            self.hass, cfg, key=alert.id, level=level, title=title, message=message,
            source=alert.source, email=(alert.level == "critical" and not resolved),
        )
        if sent:
            self._sent.append({
                "ts": dt_util.now().isoformat(timespec="seconds"), "alert_id": alert.id,
                "level": level, "title": title, "channels": sent,
            })
            self._dirty = True

    def _hist_entry(self, alert: Alert, phase: str) -> dict[str, Any]:
        return {
            "ts": dt_util.now().isoformat(timespec="seconds"), "phase": phase,
            "id": alert.id, "level": alert.level, "source": alert.source, "title": alert.title,
            "desc": alert.message,
        }

    # ── daily report ─────────────────────────────────────────────

    async def _maybe_daily_report(self, cfg: dict[str, Any], now: datetime, context: dict[str, Any]) -> None:
        if not cfg.get("enabled") or cfg.get("daily_report", True) is False:
            return
        today = now.date().isoformat()
        if self._report_sent_for == today:
            return
        report_at = context.get("report_time")  # datetime (after the evening peak)
        if not report_at or now < report_at or now - report_at > timedelta(hours=2):
            return
        builder = context.get("report_builder")
        if builder is None:
            return
        title, message = builder()
        self._report_sent_for = today
        self._dirty = True
        sent = await async_deliver(
            self.hass, cfg, key=f"report_{today}", level="report", title=title, message=message,
            source="Raport dnia", email=bool(cfg.get("daily_report_email", False)),
        )
        if sent:
            self._sent.append({"ts": now.isoformat(timespec="seconds"), "alert_id": "DAILY_REPORT",
                               "level": "report", "title": title, "channels": sent})
        await self._async_save(force=True)

    # ── checks ───────────────────────────────────────────────────

    def _startup(self, now_m: float) -> bool:
        return now_m - self._started < 300

    def _check_inverter_offline(self, d, ctx, now_m) -> Alert | None:
        if self._startup(now_m):
            return None
        core = [_num(d, k) for k in (SENSOR_PV_POWER, SENSOR_BATTERY_SOC, SENSOR_GRID_POWER_TOTAL)]
        if any(v is not None for v in core):
            return None
        return Alert(
            id="INV_OFFLINE", level="critical", source="Falownik", hold_s=600,
            title="Brak łączności z falownikiem",
            message="Od 10 min brak danych z falownika — HEMS nie może sterować baterią.",
            action="Sprawdź zasilanie i sieć falownika (LAN/Wi-Fi). Watchdog próbuje przeładować integrację.",
            causes=["Falownik bez zasilania", "Brak sieci LAN/Wi-Fi", "Zawieszona integracja GoodWe"],
            resolved_text="Łączność z falownikiem przywrócona",
        )

    def _check_control_lost(self, d, ctx, now_m) -> Alert | None:
        since = ctx.get("control_unavailable_since") or 0.0
        if not since or time.monotonic() - since < 600 or self._startup(now_m):
            return None
        return Alert(
            id="INV_CONTROL_LOST", level="critical", source="Falownik",
            title="Utracono sterowanie falownikiem",
            message="Tryb pracy falownika (EMS) niedostępny od ponad 10 min — autopilot nie wykonuje planu.",
            action="Watchdog przeładowuje integrację GoodWe; jeśli to nie pomoże, zrestartuj falownik.",
            causes=["Zawieszony moduł komunikacji falownika", "Restart HA w trakcie zapisu"],
            resolved_text="Sterowanie falownikiem przywrócone",
        )

    def _check_voltage(self, d, ctx, now_m) -> Alert | None:
        worst_phase, worst_avg, worst_now = "", 0.0, 0.0
        for phase, key in (("L1", SENSOR_GRID_VOLTAGE_L1), ("L2", SENSOR_GRID_VOLTAGE_L2),
                           ("L3", SENSOR_GRID_VOLTAGE_L3)):
            v = _num(d, key)
            buf = self._volt.setdefault(phase, deque())
            if v is not None and v > 100:
                buf.append((now_m, v))
            while buf and now_m - buf[0][0] > 600:
                buf.popleft()
            # 10-minute average needs most of the window filled
            if len(buf) < 5 or now_m - buf[0][0] < 480:
                continue
            avg = sum(x for _, x in buf) / len(buf)
            if avg > worst_avg:
                worst_phase, worst_avg, worst_now = phase, avg, buf[-1][1]
        if worst_avg < VOLT_WARN:
            return None
        grid = _num(d, SENSOR_GRID_POWER_TOTAL) or 0.0
        exporting = grid > 300
        critical = worst_avg >= VOLT_TRIP
        msg = (f"{worst_phase}: {_pl(worst_avg)} V (średnia 10 min, teraz {worst_now:.0f} V). "
               f"Próg wyłączenia falownika: {VOLT_TRIP:.0f} V.")
        if exporting:
            msg += f" Eksport {_fmt_w(grid)}."
        return Alert(
            id="GRID_VOLTAGE", level="critical" if critical else "warning", source="Sieć",
            title=f"{'Napięcie sieci przekracza normę' if critical else 'Wysokie napięcie sieci'}: {worst_avg:.0f} V",
            message=msg,
            action=("Ogranicz eksport lub ładuj baterię zamiast oddawać do sieci; "
                    "jeśli się powtarza — zgłoś do operatora (Tauron)") if exporting else
                   "Napięcie po stronie operatora — jeśli się powtarza, zgłoś do Taurona",
            causes=["Duży eksport PV w okolicy", "Słaby transformator / długa linia", "Mały pobór w sieci"],
            detected=f"Średnia 10-min {worst_phase} = {_pl(worst_avg)} V (norma ≤ {VOLT_TRIP:.0f} V)",
            resolved_text="Napięcie sieci wróciło do normy",
        )

    def _check_grid_outage(self, d, ctx, now_m) -> Alert | None:
        volts = [_num(d, k) for k in (SENSOR_GRID_VOLTAGE_L1, SENSOR_GRID_VOLTAGE_L2, SENSOR_GRID_VOLTAGE_L3)]
        known = [v for v in volts if v is not None]
        if not known or max(known) > 50 or _num(d, SENSOR_BATTERY_SOC) is None:
            return None
        soc = _num(d, SENSOR_BATTERY_SOC) or 0
        return Alert(
            id="GRID_OUTAGE", level="critical", source="Sieć", hold_s=30,
            title="Brak zasilania z sieci",
            message=f"Napięcie sieci 0 V — dom na zasilaniu awaryjnym z baterii (SOC {soc:.0f}%).",
            action="Ogranicz duże odbiorniki do czasu powrotu zasilania",
            resolved_text="Zasilanie z sieci wróciło",
        )

    def _check_frequency(self, d, ctx, now_m) -> Alert | None:
        f = _num(d, SENSOR_GRID_FREQUENCY_L1)
        if f is None or f < 40 or 49.8 <= f <= 50.2:
            return None
        return Alert(
            id="GRID_FREQUENCY", level="warning", source="Sieć", hold_s=60,
            title=f"Częstotliwość sieci {_pl(f, 2)} Hz",
            message="Częstotliwość poza zakresem 49,8–50,2 Hz od ponad minuty — niestabilność sieci.",
            action="Obserwuj; falownik może się chwilowo odłączyć",
        )

    def _check_inverter_temp(self, d, ctx, now_m) -> Alert | None:
        temps = [t for t in (_num(d, SENSOR_INVERTER_TEMP), _num(d, "inverter_temp_radiator")) if t]
        if not temps:
            return None
        t = max(temps)
        if t < 65:
            return None
        crit = t >= 75
        return Alert(
            id="INV_TEMP", level="critical" if crit else "warning", source="Falownik", hold_s=300,
            title=f"{'Przegrzanie' if crit else 'Wysoka temperatura'} falownika: {t:.0f}°C",
            message="Falownik ogranicza moc powyżej ~75°C." if not crit else
                    "Temperatura krytyczna — falownik może się wyłączyć.",
            action="Zapewnij przepływ powietrza wokół falownika",
            resolved_text="Temperatura falownika w normie",
        )

    def _check_battery_temp(self, d, ctx, now_m) -> Alert | None:
        t = _num(d, SENSOR_BATTERY_TEMPERATURE)
        if t is None or 2 <= t <= 45:
            return None
        hot = t > 45
        return Alert(
            id="BAT_TEMP", level="critical" if (t > 55 or t < -5) else "warning", source="Bateria", hold_s=300,
            title=f"{'Wysoka' if hot else 'Niska'} temperatura baterii: {t:.0f}°C",
            message=("BMS ograniczy moc ładowania/rozładowania." if hot else
                     "Poniżej ~2°C BMS blokuje ładowanie — plan ładowania może się nie wykonać."),
            action="Sprawdź warunki w pomieszczeniu z baterią",
            resolved_text="Temperatura baterii w normie",
        )

    def _check_export_balance(self, d, ctx, now_m) -> Alert | None:
        """Export larger than PV + battery discharge can supply → measurement (CT) fault."""
        pv, bat, grid = (_num(d, SENSOR_PV_POWER), _num(d, SENSOR_BATTERY_POWER),
                         _num(d, SENSOR_GRID_POWER_TOTAL))
        if pv is None or bat is None or grid is None:
            return None
        sources = max(0.0, pv) + max(0.0, bat)
        if grid <= sources + 500:
            return None
        return Alert(
            id="METER_BALANCE", level="warning", source="Pomiar", hold_s=600,
            title="Niespójny pomiar licznika",
            message=(f"Eksport {_fmt_w(grid)} większy niż PV {_fmt_w(pv)} + bateria {_fmt_w(max(0, bat))} "
                     "— możliwy błąd przekładników CT."),
            action="Sprawdź kierunek i fazy przekładników CT licznika",
            causes=["Odwrócony przekładnik CT", "Zamienione fazy CT", "Błąd odczytu licznika"],
            resolved_text="Pomiar licznika znów spójny",
        )

    def _check_pv_low(self, d, ctx, now_m) -> Alert | None:
        """PV far below what the local station's irradiance says it should give."""
        pv = _num(d, SENSOR_PV_POWER)
        expected = _num(d, "pv_expected_now_w")
        if pv is None or expected is None or expected < 1500 or pv <= 50 or pv >= 0.45 * expected:
            return None  # pv ≤ 50 W in daylight is PV_ZERO's case
        volts = [_num(d, k) for k in (SENSOR_GRID_VOLTAGE_L1, SENSOR_GRID_VOLTAGE_L2, SENSOR_GRID_VOLTAGE_L3)]
        if max((v for v in volts if v is not None), default=0) >= 252:
            return None  # the inverter derates at high grid voltage (P(U)) — not a PV fault
        temp = _num(d, "ecowitt_temp")
        snow = temp is not None and temp <= 2
        return Alert(
            id="PV_LOW", level="warning", source="PV", hold_s=1800,
            title="Produkcja PV poniżej nasłonecznienia",
            message=(f"PV {_fmt_w(pv)}, a przy obecnym nasłonecznieniu (stacja) powinno być ok. {_fmt_w(expected)} "
                     f"— od 30 min ≤ 45 %." + (f" Temperatura {_pl(temp)} °C — możliwy śnieg lub szron na panelach." if snow else "")),
            action="Sprawdź panele (zacienienie, śnieg, zabrudzenie) i stringi w zakładce Energia",
            causes=(["Śnieg/szron na panelach"] if snow else []) + [
                "Zacienienie lub zabrudzenie paneli", "Wyłączony string / rozłącznik DC", "Ograniczenie mocy falownika (temperatura)"],
            resolved_text="Produkcja PV znów zgodna z nasłonecznieniem",
        )

    def _check_pv_dead(self, d, ctx, now_m) -> Alert | None:
        pv = _num(d, SENSOR_PV_POWER)
        expected = _num(d, "pv_forecast_power_now_total") or 0
        sun = self.hass.states.get("sun.sun")
        elevation = float(sun.attributes.get("elevation", 0)) if sun else 0
        if pv is None or pv > 50 or elevation < 20 or expected < 1000:
            return None
        return Alert(
            id="PV_ZERO", level="critical", source="PV", hold_s=1800,
            title="Brak produkcji PV w dzień",
            message=f"PV 0 W od 30 min przy prognozie {_fmt_w(expected)} i słońcu {elevation:.0f}° nad horyzontem.",
            action="Sprawdź rozłącznik DC i błędy falownika",
            causes=["Wyłączony rozłącznik DC", "Błąd izolacji", "Awaria stringu/falownika"],
            resolved_text="Produkcja PV wróciła",
        )

    def _check_peak_import(self, d, ctx, now_m) -> Alert | None:
        """Expensive zone: house should run on battery, not the grid."""
        if not ctx.get("is_peak") or not ctx.get("autopilot_active") or ctx.get("manual_hold"):
            return None
        soc = _num(d, SENSOR_BATTERY_SOC)
        grid = _num(d, SENSOR_GRID_POWER_TOTAL)
        floor = ctx.get("peak_floor_soc", 5)
        if soc is None or grid is None or soc <= floor + 3 or grid > -500:
            return None
        if ctx.get("intent") == "charge_grid":
            return None  # deliberate (rare) — reported by the plan check instead
        bat = _num(d, SENSOR_BATTERY_POWER)
        if bat is not None and bat >= 0.85 * ctx.get("battery_max_w", 3700):
            return None  # the house is above the battery's max power — nothing to fix
        return Alert(
            id="PEAK_GRID_IMPORT", level="warning", source="Autopilot", hold_s=900,
            title="Szczyt: dom pobiera z sieci",
            message=(f"Strefa droga, import {_fmt_w(-grid)} od 15 min, a bateria ma {soc:.0f}% "
                     f"(próg {floor:.0f}%)."),
            action="Autopilot powinien zasilać dom z baterii — sprawdź tryb falownika (EMS)",
            causes=["Falownik w trybie standby/hold", "Limit mocy rozładowania", "Ręczne sterowanie"],
            resolved_text="Szczyt: dom znów na baterii",
        )

    def _check_planned_charge(self, d, ctx, now_m) -> Alert | None:
        if ctx.get("plan_action") != "charge_grid" or not ctx.get("autopilot_active") or ctx.get("manual_hold"):
            return None
        bat, soc = _num(d, SENSOR_BATTERY_POWER), _num(d, SENSOR_BATTERY_SOC)
        if bat is None or soc is None or soc >= 97 or bat < -300:
            return None
        return Alert(
            id="PLAN_CHARGE_IDLE", level="warning", source="Autopilot", hold_s=900,
            title="Plan: ładowanie z sieci nie ruszyło",
            message=f"Wg planu bateria ma się ładować przed szczytem, a moc baterii to {_fmt_w(-bat)} (SOC {soc:.0f}%).",
            action="Sprawdź tryb EMS falownika (charge_battery) i limit mocy",
            resolved_text="Ładowanie z sieci ruszyło",
        )

    def _check_autopilot_error(self, d, ctx, now_m) -> Alert | None:
        err = ctx.get("autopilot_error")
        if not err:
            return None
        return Alert(
            id="AUTOPILOT_ERROR", level="warning", source="Autopilot", hold_s=600,
            title="Autopilot zgłasza błąd",
            message=str(err)[:180],
            action="Sprawdź logi Smarting HOME",
            resolved_text="Autopilot działa poprawnie",
        )
