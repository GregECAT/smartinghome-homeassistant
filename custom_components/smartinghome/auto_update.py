# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Automatic updates — Smarting HOME, other custom integrations, add-ons, HA Core / OS.

Once a night, inside a time window, installs the updates Home Assistant already
offers (update.* entities from HACS / the Marketplace and the Supervisor) that are
old enough for their group, backs up first, checks the configuration and restarts
Home Assistant when an integration was updated. After the restart it verifies the
installed versions and reports the result (panel history + notification).

Home Assistant 2026.11 replaced the custom HACS integration with the core
"marketplace" integration. It adopts the HACS update entities (same unique ids —
the GitHub repository ids), but refuses update.install until someone opened the
Marketplace and accepted its warning; such updates are held and reported.

The selection is pure (`select`, `plan`); `AutoUpdater` is the Home Assistant side.
Settings "auto_update": see DEFAULTS.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import timedelta
from typing import Any

_LOGGER = logging.getLogger(__name__)

SH_REPO_ID = "1188124253"   # GitHub repository id of Smarting HOME (HACS and Marketplace)
# Platforms of the update entities of custom integrations: HACS (HA < 2026.11) and the
# core Marketplace that replaced it (HA 2026.11+). The group key stays "hacs" so the
# saved settings keep working.
STORE_PLATFORMS = ("hacs", "marketplace")
MSG_WARNING_NOT_ACCEPTED = "Otwórz Marketplace w HA i zaakceptuj ostrzeżenie"
GROUP_ORDER = ("smartinghome", "hacs", "addons", "core", "os")
GROUP_LABELS = {
    "smartinghome": "Smarting HOME",
    "hacs": "Integracje HACS",
    "addons": "Dodatki (add-ons)",
    "core": "Home Assistant Core",
    "os": "Home Assistant OS",
}
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "window": [2, 5],          # local hours [from, to)
    "backup": True,
    "exclude": [],             # update.* entity ids never installed automatically
    "groups": {
        "smartinghome": {"enabled": True, "min_age_h": 0},
        "hacs": {"enabled": False, "min_age_h": 72},
        "addons": {"enabled": False, "min_age_h": 72},
        # x.y.0 releases often break custom integrations — wait for the first patch
        "core": {"enabled": False, "min_age_h": 168, "patch_only": True},
        "os": {"enabled": False, "min_age_h": 168},
    },
}
FEATURE_BACKUP = 8
# HACS looks at custom repositories (Smarting HOME is one) only every 48 h, the
# Marketplace never without a connected GitHub account, so a release could wait
# nights before its update entity turns on — refresh ours.
HACS_REFRESH_S = 6 * 3600
HACS_REFRESH_IN_WINDOW_S = 3600
_PRERELEASE = re.compile(r"(b|beta|rc|dev|alpha)\d*", re.IGNORECASE)


def config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Settings merged over the defaults (groups merged one level deep)."""
    raw = raw or {}
    cfg = {**DEFAULTS, **{k: v for k, v in raw.items() if k != "groups"}}
    groups = {}
    for g, base in DEFAULTS["groups"].items():
        groups[g] = {**base, **((raw.get("groups") or {}).get(g) or {})}
    cfg["groups"] = groups
    return cfg


def labels(store: str | None) -> dict[str, str]:
    """Group names for the panel; on HA 2026.11+ custom integrations come from the Marketplace."""
    if store == "marketplace":
        return {**GROUP_LABELS, "hacs": "Integracje (Marketplace)"}
    return dict(GROUP_LABELS)


def classify(platform: str | None, unique_id: str | None) -> str | None:
    """Update entity → group; None = not handled (firmware of devices, Supervisor)."""
    uid = str(unique_id or "")
    if platform in STORE_PLATFORMS:
        return "smartinghome" if uid == SH_REPO_ID else "hacs"
    if platform == "hassio":
        if uid.startswith("home_assistant_core"):
            return "core"
        if uid.startswith("home_assistant_os"):
            return "os"
        if uid.startswith("home_assistant_supervisor"):
            return None  # the Supervisor updates itself
        return "addons"
    return None


def is_prerelease(version: str | None) -> bool:
    v = str(version or "").lstrip("v")
    return bool(_PRERELEASE.search(v.split(".")[-1])) if v else False


def is_patch_release(version: str | None) -> bool:
    """2026.10.1 → True, 2026.10.0 → False (Home Assistant Core numbering)."""
    parts = str(version or "").lstrip("v").split(".")
    try:
        return len(parts) >= 3 and int(re.match(r"\d+", parts[2]).group()) >= 1
    except (AttributeError, ValueError):
        return False


def in_window(hour: int, window: list[int] | tuple[int, int]) -> bool:
    start, end = int(window[0]), int(window[1])
    return start <= hour < end if start <= end else (hour >= start or hour < end)


def select(items: list[dict[str, Any]], cfg: dict[str, Any], seen: dict[str, Any],
           now_ts: float) -> list[dict[str, Any]]:
    """Annotate every available update with eligibility; ordered by group.

    items: {entity_id, group, installed, latest, available(bool), features,
            blocked(str, optional — why the store refuses installs, e.g. the Marketplace warning)}
    seen:  {entity_id: {"version", "ts"}} — when this latest version was first seen
    An update that passes every check but is blocked is "held": not installed, reported.
    """
    out = []
    for it in items:
        g = it.get("group")
        if g is None:
            continue
        gcfg = cfg["groups"].get(g) or {}
        first = (seen.get(it["entity_id"]) or {})
        age_h = (now_ts - first["ts"]) / 3600 if first.get("version") == it.get("latest") else 0.0
        reason = ""
        if not it.get("available"):
            reason = "aktualne"
        elif it.get("in_progress"):
            reason = "instalacja w toku"
        elif it.get("self_update"):
            reason = "aktualizuje Supervisor (auto)"
        elif it["entity_id"] in (cfg.get("exclude") or []):
            reason = "wykluczone"
        elif not gcfg.get("enabled"):
            reason = "grupa wyłączona"
        elif is_prerelease(it.get("latest")):
            reason = "wersja testowa"
        elif g == "core" and gcfg.get("patch_only") and not is_patch_release(it.get("latest")):
            reason = "czekam na wersję poprawkową (.1)"
        elif age_h < float(gcfg.get("min_age_h", 0)):
            reason = f"za świeże ({age_h:.0f}/{float(gcfg.get('min_age_h', 0)):.0f} h)"
        held = reason == "" and bool(it.get("blocked"))
        if held:
            reason = str(it["blocked"])
        out.append({**it, "age_h": round(age_h, 1), "eligible": reason == "", "held": held,
                    "reason": reason})
    out.sort(key=lambda x: (GROUP_ORDER.index(x["group"]), x["entity_id"]))
    return out


def plan(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """What to do tonight: integrations + add-ons first, then at most one of Core / OS
    (they restart by themselves); a plain restart only when an integration changed.
    "held": due updates the store refuses for now (reported, not installed)."""
    ok = [c for c in candidates if c["eligible"]]
    install = [c for c in ok if c["group"] in ("smartinghome", "hacs", "addons")]
    final = next((c for c in ok if c["group"] == "core"), None) or next(
        (c for c in ok if c["group"] == "os"), None)
    integrations = any(c["group"] in ("smartinghome", "hacs") for c in install)
    return {"install": install, "final": final, "restart": integrations and final is None,
            "held": [c for c in candidates if c.get("held")]}


def held_news(held: list[dict[str, Any]], notified: dict[str, Any]) -> list[dict[str, Any]]:
    """Held updates not reported yet (one report per entity and version)."""
    return [c for c in held if notified.get(c["entity_id"]) != c.get("latest")]


def install_error(err: BaseException) -> str:
    """A refused update.install in words for the panel history and the notification."""
    if getattr(err, "translation_key", None) == "warning_not_accepted":
        return MSG_WARNING_NOT_ACCEPTED
    return (str(err) or type(err).__name__)[:200]


class AutoUpdater:
    """Home Assistant side: tick every 10 min, act once a night inside the window."""

    TICK = timedelta(minutes=10)

    def __init__(self, hass) -> None:
        from homeassistant.helpers.storage import Store

        from .const import DOMAIN

        self.hass = hass
        self._store = Store(hass, 1, f"{DOMAIN}.auto_update")
        self._data: dict[str, Any] = {"seen": {}, "history": [], "pending": None, "last_night": ""}
        self._busy = False
        self._unsub = None

    async def async_start(self) -> None:
        from homeassistant.helpers.event import async_call_later, async_track_time_interval

        self._data.update(await self._store.async_load() or {})
        self._unsub = async_track_time_interval(self.hass, self._tick, self.TICK)
        if self._data.get("pending"):
            # update entities appear a while after the start
            async_call_later(self.hass, 120, self._verify_pending)

    def stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    def allowed(self) -> bool:
        """PRO feature (kept by installs from before v1.71 — const.LEGACY_FREE_FEATURES)."""
        from .const import FEATURE_AUTO_UPDATE
        from .license import any_entry_has_feature

        return any_entry_has_feature(self.hass, FEATURE_AUTO_UPDATE)

    async def _cfg(self) -> dict[str, Any]:
        from .settings_io import read_async

        return config((await read_async(self.hass)).get("auto_update"))

    def _source(self) -> tuple[str | None, Any]:
        """The store behind the custom integrations: ("marketplace", its manager) on
        HA 2026.11+, ("hacs", HACS) before, (None, None) without either."""
        from homeassistant.config_entries import ConfigEntryState

        for entry in self.hass.config_entries.async_entries("marketplace"):
            mgr = getattr(entry, "runtime_data", None)
            if entry.state is ConfigEntryState.LOADED and mgr is not None:
                return "marketplace", mgr
        hacs = self.hass.data.get("hacs")
        return ("hacs", hacs) if hacs is not None else (None, None)

    def _warning_accepted(self) -> bool | None:
        """Marketplace only: has anyone accepted its first-run warning? Automations
        (and this updater) install on the word of whoever accepted. None = not known."""
        store, mgr = self._source()
        if store != "marketplace":
            return None
        try:
            acceptances = getattr(mgr, "warning_acceptances", None)
        except Exception:  # noqa: BLE001 — Marketplace internals
            return None
        return None if acceptances is None else bool(acceptances)

    def _items(self) -> list[dict[str, Any]]:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(self.hass)
        accepted = self._warning_accepted()
        out = []
        for st in self.hass.states.async_all("update"):
            ent = reg.async_get(st.entity_id)
            platform = ent.platform if ent else None
            group = classify(platform, ent.unique_id if ent else None)
            if group is None:
                continue
            a = st.attributes
            out.append({
                "entity_id": st.entity_id,
                "platform": platform,
                "title": a.get("title") or a.get("friendly_name") or st.entity_id,
                "group": group,
                "installed": a.get("installed_version"),
                "latest": a.get("latest_version"),
                "available": st.state == "on",
                "features": int(a.get("supported_features") or 0),
                "in_progress": bool(a.get("in_progress")),
                "self_update": bool(a.get("auto_update")),  # Supervisor updates it itself
                **({"blocked": MSG_WARNING_NOT_ACCEPTED}
                   if platform == "marketplace" and accepted is False else {}),
            })
        return out

    def _remember(self, items: list[dict[str, Any]], now_ts: float) -> None:
        seen = self._data.setdefault("seen", {})
        for it in items:
            if it["available"] and (seen.get(it["entity_id"]) or {}).get("version") != it["latest"]:
                seen[it["entity_id"]] = {"version": it["latest"], "ts": now_ts}

    async def _tick(self, _now=None) -> None:
        from homeassistant.util import dt as dt_util

        if self._busy:
            return
        now = dt_util.now()
        cfg = await self._cfg()
        window = in_window(now.hour, cfg["window"])
        if cfg["enabled"] and self.allowed():
            await self._refresh_store(cfg, HACS_REFRESH_IN_WINDOW_S if window else HACS_REFRESH_S)
        items = self._items()
        self._remember(items, time.time())
        await self._store.async_save(self._data)
        if not cfg["enabled"] or not window:
            return
        if not self.allowed():
            return
        if self._data.get("last_night") == now.date().isoformat() or self._data.get("pending"):
            return
        p = plan(select(items, cfg, self._data["seen"], time.time()))
        if p["held"]:
            await self._report_held(p["held"])
        if not (p["install"] or p["final"]):
            return
        self._data["last_night"] = now.date().isoformat()
        await self.async_run(p, cfg)

    async def _refresh_store(self, cfg: dict[str, Any], max_age_s: float) -> None:
        """Ask HACS / the Marketplace to re-read the repositories behind the update
        entities we install (what their "Refresh" does, plus telling the update entities)
        — at most every `max_age_s`.

        The Marketplace refreshes installed custom repositories only with a connected
        GitHub account, and homeassistant.update_entity does nothing for its entities, so
        without this a new Smarting HOME release would never show up. Without an account
        GitHub allows ~60 anonymous requests an hour (≈5 per repository): Smarting HOME
        goes first, the catalog keeps the default repositories current by itself.
        """
        from homeassistant.helpers import entity_registry as er

        now_ts = time.time()
        if now_ts - float(self._data.get("hacs_refreshed") or 0) < max_age_s:
            return
        self._data["hacs_refreshed"] = now_ts
        store, mgr = self._source()
        repos = getattr(mgr, "repositories", None)
        if repos is None:
            return
        reg = er.async_get(self.hass)
        targets = []
        for st in self.hass.states.async_all("update"):
            ent = reg.async_get(st.entity_id)
            if ent is None or ent.platform != store:
                continue
            group = classify(ent.platform, ent.unique_id)
            if group not in ("smartinghome", "hacs") or not cfg["groups"][group]["enabled"]:
                continue
            targets.append((group != "smartinghome", st.entity_id, str(ent.unique_id)))
        categories = set()
        for _, entity_id, uid in sorted(targets):
            try:
                repo = repos.get_by_id(uid)
                if repo is None:
                    continue
                is_default = getattr(repos, "is_default", None)
                if store == "marketplace" and uid != SH_REPO_ID and is_default and is_default(uid):
                    continue  # the catalog keeps default repositories current
                await repo.update_repository(ignore_issues=True, force=True)
                categories.add(repo.data.category)
            except Exception as err:  # noqa: BLE001 — store internals, GitHub limits
                if type(err).__name__ == "GitHubAnonymousRateLimitError":
                    _LOGGER.info("Auto-update: GitHub rate limit (no GitHub account in the "
                                 "Marketplace) — refresh stopped at %s", entity_id)
                    break
                _LOGGER.debug("Auto-update: %s refresh of %s failed: %s", store, entity_id, err)
        if not categories:
            return
        try:
            await mgr.data.async_write()
            for category in categories:
                if coordinator := (getattr(mgr, "coordinators", None) or {}).get(category):
                    coordinator.async_update_listeners()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Auto-update: %s state write failed: %s", store, err)

    async def _report_held(self, held: list[dict[str, Any]]) -> None:
        """Due updates the Marketplace refuses until its warning is accepted: one panel
        history entry + notification per entity and version, not one every night."""
        from homeassistant.util import dt as dt_util

        notified = self._data.setdefault("held_notified", {})
        new = held_news(held, notified)
        if not new:
            return
        for c in new:
            notified[c["entity_id"]] = c.get("latest")
        entry = {
            "ts": dt_util.now().isoformat(timespec="minutes"),
            "items": [{"entity_id": c["entity_id"], "title": c["title"], "from": c["installed"],
                       "to": c["latest"], "ok": False, "error": c["reason"]} for c in new],
            "result": "wstrzymane — Marketplace",
        }
        await self._save_history(entry)
        await self._notify(
            "warning", "Aktualizacja wstrzymana — Marketplace",
            self._summary(entry) + "\nUstawienia → Marketplace. Home Assistant 2026.11+ instaluje "
            "integracje przez Marketplace i do czasu akceptacji odmawia ich aktualizacji. "
            "Po akceptacji aktualizacja zainstaluje się w najbliższym oknie.")

    async def async_run_now(self) -> dict[str, Any]:
        """Panel button: install what is eligible now (window and night limit ignored)."""
        if not self.allowed():
            return {"started": False, "reason": "automatyczne aktualizacje — funkcja PRO"}
        cfg = await self._cfg()
        await self._refresh_store(cfg, 0)
        items = self._items()
        self._remember(items, time.time())
        p = plan(select(items, cfg, self._data["seen"], time.time()))
        if not (p["install"] or p["final"]):
            if p["held"]:
                return {"started": False, "reason": MSG_WARNING_NOT_ACCEPTED}
            return {"started": False, "reason": "brak aktualizacji spełniających warunki"}
        self.hass.async_create_task(self.async_run(p, cfg))
        return {"started": True, "items": [c["entity_id"] for c in p["install"]]
                + ([p["final"]["entity_id"]] if p["final"] else [])}

    async def async_run(self, p: dict[str, Any], cfg: dict[str, Any]) -> None:
        from homeassistant.util import dt as dt_util

        self._busy = True
        try:
            done, failed, errors = [], [], {}
            needs_backup = cfg["backup"] and any(not (c["features"] & FEATURE_BACKUP) for c in p["install"])
            if needs_backup and (p["install"] or p["final"]):
                await self._backup()
            for c in p["install"]:
                err = await self._install(c, cfg["backup"])
                (done if err is None else failed).append(c)
                if err is not None:
                    errors[c["entity_id"]] = err
            entry = {
                "ts": dt_util.now().isoformat(timespec="minutes"),
                "items": [{"entity_id": c["entity_id"], "title": c["title"], "from": c["installed"],
                           "to": c["latest"], "ok": c in done,
                           **({"error": errors[c["entity_id"]]} if c["entity_id"] in errors else {})}
                          for c in p["install"]],
                "result": "",
            }
            final = p["final"]
            if final:
                entry["items"].append({"entity_id": final["entity_id"], "title": final["title"],
                                       "from": final["installed"], "to": final["latest"], "ok": None})
                entry["result"] = "restart (aktualizacja systemu)"
                self._data["pending"] = entry
                await self._save_history(entry, keep_pending=True)
                await self._notify("info", "Aktualizacja Home Assistant",
                                   self._summary(entry) + "\nHome Assistant uruchomi się ponownie.")
                # the Supervisor restarts Core/the host — the call may not come back
                self.hass.async_create_task(self.hass.services.async_call(
                    "update", "install",
                    {"entity_id": final["entity_id"],
                     **({"backup": True} if cfg["backup"] and final["features"] & FEATURE_BACKUP else {})},
                    blocking=False,
                ))
                return
            if p["restart"] and any(c["group"] in ("smartinghome", "hacs") for c in done):
                errors = await self._check_config()
                if errors:
                    entry["result"] = "błąd konfiguracji — bez restartu"
                    await self._save_history(entry)
                    await self._notify("warning", "Aktualizacja wstrzymana",
                                       self._summary(entry) + f"\nKonfiguracja HA ma błędy: {errors[:300]}")
                    return
                entry["result"] = "restart"
                self._data["pending"] = entry
                await self._save_history(entry, keep_pending=True)
                await self._notify("info", "Aktualizacja — restart Home Assistant", self._summary(entry))
                await self.hass.services.async_call("homeassistant", "restart", {}, blocking=False)
                return
            entry["result"] = "zainstalowano" if not failed else "częściowo"
            await self._save_history(entry)
            await self._notify("warning" if failed else "info", "Aktualizacje zainstalowane", self._summary(entry))
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception("Auto update failed: %s", err)
            await self._notify("warning", "Automatyczna aktualizacja nie powiodła się", str(err)[:300])
        finally:
            self._busy = False

    async def _install(self, c: dict[str, Any], backup: bool) -> str | None:
        """update.install; None when it worked, else why not (for history / notification)."""
        data: dict[str, Any] = {"entity_id": c["entity_id"]}
        if backup and c["features"] & FEATURE_BACKUP:
            data["backup"] = True
        try:
            await self.hass.services.async_call("update", "install", data, blocking=True)
            return None
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Auto update %s failed: %s", c["entity_id"], err)
            return install_error(err)

    async def _backup(self) -> None:
        """A Home Assistant backup before integrations without their own backup step."""
        for domain, service in (("backup", "create_automatic"), ("hassio", "backup_partial")):
            if self.hass.services.has_service(domain, service):
                data = {} if domain == "backup" else {
                    "homeassistant": True, "name": "Smarting HOME — przed aktualizacją"}
                try:
                    await self.hass.services.async_call(domain, service, data, blocking=True)
                    return
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Backup before update (%s.%s) failed: %s", domain, service, err)
        _LOGGER.info("Auto update: no backup service available — continuing without a backup")

    async def _check_config(self) -> str:
        try:
            from homeassistant.helpers.check_config import async_check_ha_config_file

            res = await async_check_ha_config_file(self.hass)
            return "; ".join(str(e) for e in res.errors) if res.errors else ""
        except Exception as err:  # noqa: BLE001
            return f"sprawdzenie nie powiodło się: {err}"

    async def _verify_pending(self, _now=None) -> None:
        entry = self._data.get("pending")
        if not entry:
            return
        states = {it["entity_id"]: it for it in self._items()}
        bad = []
        for it in entry["items"]:
            cur = (states.get(it["entity_id"]) or {}).get("installed")
            it["ok"] = cur == it["to"] if cur else it.get("ok")
            if it["ok"] is False:
                bad.append(f"{it['title']}: {cur or '?'} zamiast {it['to']}")
        entry["result"] = "OK po restarcie" if not bad else "niezgodne wersje po restarcie"
        self._data["pending"] = None
        await self._save_history(entry, replace=True)
        await self._notify("warning" if bad else "info",
                           "Aktualizacja zakończona" if not bad else "Aktualizacja — sprawdź wersje",
                           self._summary(entry) + ("\n" + "\n".join(bad) if bad else ""))

    async def _save_history(self, entry: dict[str, Any], keep_pending: bool = False,
                            replace: bool = False) -> None:
        hist = self._data.setdefault("history", [])
        if replace and hist and hist[-1].get("ts") == entry.get("ts"):
            hist[-1] = entry
        else:
            hist.append(entry)
        del hist[:-30]
        if not keep_pending and not replace:
            self._data["pending"] = None
        await self._store.async_save(self._data)

    @staticmethod
    def _summary(entry: dict[str, Any]) -> str:
        return "\n".join(
            f"{'✅' if it['ok'] else ('⏳' if it['ok'] is None else '❌')} {it['title']}: "
            f"{it['from']} → {it['to']}" + (f" — {it['error']}" if it.get("error") else "")
            for it in entry["items"]) or "—"

    async def _notify(self, level: str, title: str, message: str) -> None:
        try:
            from .notifier import async_deliver
            from .settings_io import read_async

            cfg = (await read_async(self.hass)).get("notification_config") or {}
            await async_deliver(self.hass, cfg, key="auto_update", level=level, title=title,
                                message=message, source="Aktualizacje")
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Auto update notification failed: %s", err)

    async def async_status(self) -> dict[str, Any]:
        cfg = await self._cfg()
        items = self._items()
        self._remember(items, time.time())
        cands = select(items, cfg, self._data.get("seen", {}), time.time())
        store, _ = self._source()
        accepted = self._warning_accepted()
        return {
            "allowed": self.allowed(),
            "config": cfg,
            "labels": labels(store),
            "store": {"kind": store, "warning_accepted": accepted,
                      "message": MSG_WARNING_NOT_ACCEPTED if accepted is False else None},
            "updates": [c for c in cands if c["available"]],
            "tracked": len(cands),
            "plan": {k: ([c["entity_id"] for c in v] if isinstance(v, list) else (v or {}).get("entity_id")
                         if k != "restart" else v) for k, v in plan(cands).items()},
            "history": list(reversed(self._data.get("history", [])))[:15],
            "pending": self._data.get("pending"),
            "last_night": self._data.get("last_night"),
            "busy": self._busy,
        }


def get_updater(hass) -> AutoUpdater | None:
    from .const import DOMAIN

    return (hass.data.get(DOMAIN) or {}).get("_auto_updater")
