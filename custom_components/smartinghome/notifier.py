# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Notification delivery — HA Companion push, persistent notification, email/SMS webhook.

Policy (what/when to notify) lives in alert_engine.py; this module only formats
and delivers a message to the channels configured in the panel
(settings.notification_config).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

from .const import ALERT_WEBHOOK_URL

_LOGGER = logging.getLogger(__name__)

LEVEL_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵", "resolved": "✅", "report": "📊"}
# iOS interruption levels: time-sensitive breaks through Focus, passive is silent
_INTERRUPTION = {"critical": "time-sensitive", "warning": "active", "info": "passive",
                 "resolved": "passive", "report": "passive"}
PANEL_URL = "/smartinghome"


def push_targets(cfg: dict[str, Any]) -> list[str]:
    entities = [e for e in (cfg.get("ha_push_entities") or []) if e]
    if not entities and cfg.get("ha_push_entity"):
        entities = [cfg["ha_push_entity"]]
    return entities


async def async_deliver(
    hass: HomeAssistant,
    cfg: dict[str, Any],
    *,
    key: str,
    level: str,
    title: str,
    message: str,
    source: str = "System",
    email: bool = False,
) -> list[str]:
    """Send one message to the configured channels. Returns channels that succeeded.

    key   — stable id; the phone replaces an earlier notification with the same key
            (an alert's "resolved" message replaces the alert itself)
    level — critical / warning / info / resolved / report
    email — also send via the email/SMS webhook (reserved for critical + reports)
    """
    channels = cfg.get("channels") or {}
    icon = LEVEL_ICON.get(level, "ℹ️")
    full_title = f"{icon} {title}"
    sent: list[str] = []

    if channels.get("ha_push"):
        group = "smartinghome-report" if level == "report" else "smartinghome-alerts"
        payload = {
            "title": full_title,
            "message": message,
            "data": {
                "tag": f"smartinghome_{key}",
                "group": group,
                "subtitle": f"Smarting HOME · {source}",
                "url": PANEL_URL,           # iOS: open the panel
                "clickAction": PANEL_URL,   # Android: open the panel
                "channel": "smartinghome_alerts" if level in ("critical", "warning") else "smartinghome_info",
                "importance": "high" if level == "critical" else "default",
                "push": {"interruption-level": _INTERRUPTION.get(level, "active")},
            },
        }
        ok = False
        for entity in push_targets(cfg):
            try:
                await hass.services.async_call("notify", entity.replace("notify.", ""), payload)
                ok = True
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Push to %s failed: %s", entity, err)
        if ok:
            sent.append("ha_push")

    if channels.get("persistent") and level in ("critical", "warning", "resolved"):
        try:
            if level == "resolved":
                await hass.services.async_call(
                    "persistent_notification", "dismiss", {"notification_id": f"smartinghome_{key}"}
                )
            else:
                await hass.services.async_call(
                    "persistent_notification", "create",
                    {"title": full_title, "message": message, "notification_id": f"smartinghome_{key}"},
                )
            sent.append("persistent")
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Persistent notification failed: %s", err)

    if email:
        for channel, field_name in (("email", "email"), ("sms", "phone")):
            target = (cfg.get(field_name) or "").strip()
            if not channels.get(channel) or not target:
                continue
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    await session.post(
                        ALERT_WEBHOOK_URL,
                        json={
                            "channel": channel,
                            field_name: target,
                            "subject": full_title,
                            "message": message,
                            "level": level,
                            "alert_id": key,
                            "source": source,
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    )
                sent.append(channel)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Webhook %s failed: %s", channel, err)

    if sent:
        _LOGGER.info("Notification '%s' [%s] → %s", key, level, ", ".join(sent))
    return sent
