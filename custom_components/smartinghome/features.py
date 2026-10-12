# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""FREE / PRO feature gating — pure Python (no Home Assistant imports).

LicenseManager.has_feature() delegates here; the strategy controller, schedule
manager and the config-entry migration use the helpers below as well.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .const import (
    CONF_FREE_LEGACY,
    CONFIG_ENTRY_MINOR_VERSION,
    FREE_FEATURES,
    LEGACY_FREE_FEATURES,
    STRATEGY_FALLBACK,
    STRATEGY_FEATURES,
    TIER_FEATURES,
)


def features_for(tier: str, free_legacy: bool = False) -> frozenset[str]:
    """Gated features enabled for a tier (+ grandfathered ones for old FREE installs)."""
    features = TIER_FEATURES.get(str(tier).lower(), FREE_FEATURES)
    if free_legacy:
        # Grandfathering only adds what pre-v1.71 FREE installs already had
        features = features | LEGACY_FREE_FEATURES
    return frozenset(features)


def has_feature(tier: str, feature: str, free_legacy: bool = False) -> bool:
    return feature in features_for(tier, free_legacy)


def strategy_feature(strategy: str) -> str | None:
    """The gated feature a strategy needs, or None when it is FREE."""
    return STRATEGY_FEATURES.get(str(strategy))


def permitted_strategy(strategy: str, check: Callable[[str], bool] | None) -> str:
    """The strategy the license allows: the requested one, or the FREE fallback.

    check — LicenseManager.has_feature (None = no license information → allow).
    """
    feature = strategy_feature(strategy)
    if feature is None or check is None or check(feature):
        return str(strategy)
    return STRATEGY_FALLBACK


def migrate_entry_data(data: dict[str, Any], version: int, minor_version: int) -> tuple[dict[str, Any], int]:
    """Config entry 1.1 → 1.2: installs that existed before the split keep their features.

    Returns (new data, new minor version). Entries created by v1.71+ start at
    minor version 2 and never pass through here, so they are not grandfathered.
    """
    if version != 1 or minor_version >= CONFIG_ENTRY_MINOR_VERSION:
        return data, minor_version
    new_data = dict(data)
    new_data[CONF_FREE_LEGACY] = True
    return new_data, CONFIG_ENTRY_MINOR_VERSION
