# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""Grid export limit from the connection conditions, and respect for limits set
by the DSO or an OSD integrator.

The inverter's export limit is normally set by the installer according to the
connection conditions. Smarting HOME must never raise it above that, and when
somebody else (the DSO's device, an integrator, the owner in the vendor app)
changes it, the most restrictive value wins and we stop fighting it.

Settings "grid": {
    "export_cap_w": int | None   — limit from the connection conditions (owner),
    "export_baseline_w": int | None — the inverter's limit seen before our first write,
    "osd_priority": bool          — external control has priority: never write the limit,
    "external_clear": str         — bumped by the panel to forget a detected external limit,
}
"""
from __future__ import annotations

from typing import Any

SETTLE_S = 180.0  # an entity can still show the old value this long after our write


def config(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}

    def _w(key: str) -> int | None:
        try:
            v = int(float(raw.get(key)))
        except (TypeError, ValueError):
            return None
        return v if v >= 0 else None

    return {
        "export_cap_w": _w("export_cap_w"),
        "export_baseline_w": _w("export_baseline_w"),
        "osd_priority": bool(raw.get("osd_priority", False)),
        "external_clear": str(raw.get("external_clear", "")),
    }


def baseline_from_observation(current_w: int | None, default_w: int) -> int | None:
    """The inverter limit seen before Smarting HOME wrote anything is the installer's
    setting. Our own old default (default_w) is not — then the baseline is unknown."""
    if current_w is None or current_w == default_w:
        return None
    return current_w


def max_export(cap_w: int | None, baseline_w: int | None, default_w: int) -> int:
    """Highest export limit Smarting HOME may set."""
    if cap_w is not None:
        return cap_w
    if baseline_w is not None:
        return baseline_w
    return default_w


def external_change(current_w: int | None, last_written_w: int | None,
                    last_write_ts: float, now: float, settle_s: float = SETTLE_S) -> bool:
    """The inverter reports a limit we did not write (and it is not just lagging)."""
    if current_w is None or last_written_w is None:
        return False
    if now - last_write_ts < settle_s:
        return False
    return current_w != last_written_w


def effective_limit(requested_w: int, max_w: int, external_w: int | None,
                    voltage_cap_w: int | None) -> int:
    """The limit to write: the request, never above the connection limit, a detected
    external limit or the voltage guard's cap."""
    limit = min(int(requested_w), int(max_w))
    for cap in (external_w, voltage_cap_w):
        if cap is not None:
            limit = min(limit, int(cap))
    return max(limit, 0)
