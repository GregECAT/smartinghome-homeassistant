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
