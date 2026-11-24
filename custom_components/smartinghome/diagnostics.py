# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Diagnostics download (Settings → Devices & services → Smarting HOME → ⋮).

Deliberately small: the version, the inverter brand, the resolved sensor map and the
inverter-control status — never the config entry data (it holds the license and AI
keys). For Deye it adds the read-only inverter check from deye_ha.async_diagnostics.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_INVERTER_BRAND, DOMAIN, INVERTER_BRAND_DEYE, INVERTER_BRAND_GOODWE, VERSION


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    brand = entry.data.get(CONF_INVERTER_BRAND, INVERTER_BRAND_GOODWE)
    stored = (hass.data.get(DOMAIN) or {}).get(entry.entry_id) or {}
    coordinator = stored.get("coordinator")
    controller = getattr(coordinator, "_strategy_controller", None)
    energy_manager = getattr(controller, "energy_manager", None)
    data: dict[str, Any] = {
        "version": VERSION,
        "inverter_brand": brand,
        "sensor_map": dict(getattr(coordinator, "_sensor_map", {}) or {}),
        "inverter_control": energy_manager.control_status() if energy_manager else None,
    }
    if brand == INVERTER_BRAND_DEYE:
        from .deye_ha import async_diagnostics

        try:
            data["deye"] = await async_diagnostics(hass)
        except Exception as err:  # noqa: BLE001 — the download must still work
            data["deye"] = {"error": str(err)}
    return data
