# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""RCE prices from the RCE PSE integration, brought to one convention.

Smarting HOME works with the MARKET price in PLN/MWh (net) and applies the
prosumer coefficient (× 1.23) itself. The RCE PSE integration (Lewa-Reka/ha-rce-pse)
can publish gross prices (option "use_gross_prices": every price and the
"prices" attribute × 1.23) and PLN/kWh instead of PLN/MWh. Read with its
defaults (net, PLN/MWh) the scale is 1; on bobrek (gross) every price was
multiplied by 1.23 twice — the panel showed 1.23 zł/kWh for an RCE of 0.81.

The prosumer selling price sensor is always max(RCE, 0) × 1.23 in the chosen
unit, so it only needs the unit scale.
"""
from __future__ import annotations

from typing import Any

GROSS_FACTOR = 1.23
RCE_DOMAIN = "rce_pse"


def _options(hass: Any) -> dict[str, Any] | None:
    try:
        entries = hass.config_entries.async_entries(RCE_DOMAIN)
    except Exception:  # noqa: BLE001
        return None
    if not entries:
        return None
    entry = entries[0]
    opts: dict[str, Any] = {}
    for src in (entry.data or {}, entry.options or {}):
        for key, value in src.items():
            if isinstance(value, dict):  # options flow sections ("pricing": {...})
                opts.update(value)
            else:
                opts[key] = value
    return opts


def unit_scale(hass: Any) -> float:
    """Value in the integration's unit → PLN/MWh."""
    opts = _options(hass) or {}
    return 1000.0 if str(opts.get("price_unit", "PLN/MWh")) == "PLN/kWh" else 1.0


def market_scale(hass: Any) -> float:
    """Price as published by RCE PSE → market price, PLN/MWh, net."""
    opts = _options(hass) or {}
    gross = bool(opts.get("use_gross_prices", False))
    return unit_scale(hass) / (GROSS_FACTOR if gross else 1.0)


def scale_prices(prices: list[dict[str, Any]] | None, scale: float) -> list[dict[str, Any]] | None:
    """A copy of the "prices" attribute with rce_pln in market PLN/MWh."""
    if not prices or scale == 1.0:
        return prices
    out = []
    for entry in prices:
        try:
            out.append({**entry, "rce_pln": float(entry.get("rce_pln")) * scale})
        except (TypeError, ValueError):
            out.append(entry)
    return out
