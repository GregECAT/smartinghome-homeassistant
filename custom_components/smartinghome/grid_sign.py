# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Grid power sign convention — one place, no Home Assistant imports (unit-testable).

Canonical meter value (SENSOR_GRID_POWER_TOTAL, the panel and the ledger):
    + export / − import   (GoodWe meter_active_power_total / active_power_total)

The AI prompts and services use the opposite (+ import / − export) and negate
the meter value themselves — never store the inverted value under the canonical key.
"""

from __future__ import annotations


def split_grid_power(meter_w: float) -> tuple[float, float]:
    """Return (import_w, export_w), both ≥ 0, from a + export / − import meter value."""
    return max(-meter_w, 0.0), max(meter_w, 0.0)


def pcc_to_meter(pcc_w: float, is_sofar: bool) -> float:
    """Synthetic grid power (Σ V×I per phase) → canonical + export / − import.

    Sofar PCC current is positive on import, so it is negated; other brands'
    phase sensors already follow the meter convention.
    """
    return -pcc_w if is_sofar else pcc_w
