# Smarting HOME — autonomous energy management for Home Assistant
# Copyright (c) 2026 Smarting HOME — GregECAT (smartinghome.pl). All rights reserved.
# SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
# Commercial software, not open source: copying, porting, rewriting (also with AI
# tools), redistributing or removing this notice / the license check requires the
# Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
# SH-ID: 8b1aab899d5a
"""FREE / PRO split (v1.71): feature matrix, grandfathering migration, strategy fallback.

Run: python3 -m pytest tests/  (no Home Assistant needed — the few HA names the
license manager, notifier and wind calendar import are stubbed below)
"""
from __future__ import annotations

import asyncio
import importlib
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"


def _ensure(name: str, **attrs) -> None:
    """A real module when importable, else a stub; missing attributes are added."""
    mod = sys.modules.get(name)
    if mod is None:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            mod = sys.modules[name] = types.ModuleType(name)
    for key, value in attrs.items():
        if not hasattr(mod, key):
            setattr(mod, key, value)


def _stub_homeassistant() -> None:
    """Minimal homeassistant.* names (other tests may have stubbed some already)."""
    _ensure("homeassistant")
    _ensure("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    _ensure("homeassistant.const", __version__="2026.10.0")


_stub_homeassistant()

# Package with the real modules; api.py (network) is replaced by a stub
_pkg = types.ModuleType("smartinghome_lic")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_lic", _pkg)
_api = types.ModuleType("smartinghome_lic.api")


@dataclass
class _LicenseInfo:
    valid: bool
    tier: object
    expires: object = None
    email: object = None
    max_installations: int = 1
    features: list | None = None
    message: str = ""


class _APIError(Exception):
    pass


_api.SmartingHomeAPI = object
_api.LicenseInfo = _LicenseInfo
_api.AuthenticationError = _APIError
_api.ConnectionError = _APIError
sys.modules.setdefault("smartinghome_lic.api", _api)

const = importlib.import_module("smartinghome_lic.const")
features = importlib.import_module("smartinghome_lic.features")
license_mod = importlib.import_module("smartinghome_lic.license")
notifier = importlib.import_module("smartinghome_lic.notifier")
wind = importlib.import_module("smartinghome_lic.wind_calendar")
vr = importlib.import_module("smartinghome_lic.voltage_report")

LicenseTier = const.LicenseTier
LEGACY = const.LEGACY_FREE_FEATURES
ALL_GATED = const.PRO_FEATURES


def _mgr(tier: LicenseTier, free_legacy: bool = False):
    mode = const.LICENSE_MODE_FREE if tier == LicenseTier.FREE else const.LICENSE_MODE_PRO
    mgr = license_mod.LicenseManager(object(), object(), license_mode=mode, free_legacy=free_legacy)
    if tier in (LicenseTier.PRO, LicenseTier.ENTERPRISE):
        mgr._cached_valid = True
        mgr._cached_tier = tier
    elif tier == LicenseTier.DEMO:
        mgr._cached_valid = False  # expired / unreachable license server
    assert mgr.tier == tier
    return mgr


# ── feature matrix ───────────────────────────────────────────────────────────

@pytest.mark.parametrize(("tier", "legacy", "expected"), [
    (LicenseTier.FREE, False, frozenset()),
    (LicenseTier.FREE, True, LEGACY),
    (LicenseTier.DEMO, False, frozenset()),
    (LicenseTier.DEMO, True, LEGACY),
    (LicenseTier.PRO, False, ALL_GATED),
    (LicenseTier.PRO, True, ALL_GATED),
    (LicenseTier.ENTERPRISE, False, ALL_GATED),
])
def test_feature_matrix(tier, legacy, expected):
    mgr = _mgr(tier, legacy)
    assert mgr.features == expected
    for feature in ALL_GATED:
        assert mgr.has_feature(feature) is (feature in expected), feature
    assert features.features_for(tier.value, legacy) == expected


def test_legacy_never_grants_ai_weather_or_more_strings():
    mgr = _mgr(LicenseTier.FREE, free_legacy=True)
    for feature in (const.FEATURE_AI, const.FEATURE_WEATHER_STRATEGY, const.FEATURE_PV_STRINGS_10):
        assert not mgr.has_feature(feature)
    for feature in (const.FEATURE_ADVANCED_ALERTS, const.FEATURE_DAILY_REPORT, const.FEATURE_AUTO_UPDATE,
                    const.FEATURE_VOLTAGE_REPORT_EXPORT, const.FEATURE_INVOICE_COMPARE,
                    const.FEATURE_PV_CALIBRATION, const.FEATURE_MULTI_TURBINE):
        assert mgr.has_feature(feature)


def test_unknown_feature_and_tier():
    assert not _mgr(LicenseTier.PRO).has_feature("time_travel")
    assert features.features_for("platinum") == frozenset()


def test_state_attributes_expose_features():
    attrs = _mgr(LicenseTier.FREE, free_legacy=True).state_attributes
    assert attrs["features"] == sorted(LEGACY)
    assert attrs["free_legacy"] is True


# ── grandfathering migration ─────────────────────────────────────────────────

def test_migration_marks_existing_entries():
    data = {"license_mode": "free", "tariff": "g13"}
    new, minor = features.migrate_entry_data(data, 1, 1)
    assert minor == const.CONFIG_ENTRY_MINOR_VERSION == 2
    assert new == {**data, const.CONF_FREE_LEGACY: True}
    assert const.CONF_FREE_LEGACY not in data  # input not mutated


def test_migration_skips_new_entries():
    data = {"license_mode": "free"}
    assert features.migrate_entry_data(data, 1, 2) == (data, 2)
    assert features.migrate_entry_data(data, 2, 1) == (data, 1)  # unknown major: untouched


def test_new_entries_start_at_the_migrated_minor_version():
    # A fresh entry is created with the flow's MINOR_VERSION, so it is never migrated
    src = (_DIR / "config_flow.py").read_text(encoding="utf-8")
    m = re.search(r"^\s+MINOR_VERSION = (\d+)", src, re.M)
    assert m and int(m.group(1)) == const.CONFIG_ENTRY_MINOR_VERSION


def test_setup_passes_free_legacy_from_entry_data():
    src = (_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "async def async_migrate_entry" in src
    assert "free_legacy=bool(entry.data.get(CONF_FREE_LEGACY, False))" in src


# ── strategy gating ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(("tier", "legacy"), [
    (LicenseTier.FREE, False), (LicenseTier.FREE, True), (LicenseTier.DEMO, True),
])
def test_pro_strategies_fall_back_on_free(tier, legacy):
    check = _mgr(tier, legacy).has_feature
    for strategy in ("weather_adaptive", "ai_full_autonomy"):
        assert features.permitted_strategy(strategy, check) == "max_self_consumption"
    for strategy in ("max_self_consumption", "max_profit", "battery_protection", "zero_export"):
        assert features.permitted_strategy(strategy, check) == strategy


def test_pro_strategies_allowed_on_pro():
    for tier in (LicenseTier.PRO, LicenseTier.ENTERPRISE):
        check = _mgr(tier).has_feature
        for strategy in const.AutopilotStrategy:
            assert features.permitted_strategy(strategy.value, check) == strategy.value


def test_strategy_without_license_info_is_allowed():
    assert features.permitted_strategy("ai_full_autonomy", None) == "ai_full_autonomy"


def test_every_gated_strategy_maps_to_a_pro_feature():
    assert set(const.STRATEGY_FEATURES.values()) <= ALL_GATED
    assert const.STRATEGY_FALLBACK not in const.STRATEGY_FEATURES


# ── alert channels ───────────────────────────────────────────────────────────

class _Services:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data):
        self.calls.append((domain, service))


def _deliver(advanced: bool):
    hass = types.SimpleNamespace(services=_Services())
    cfg = {
        "channels": {"ha_push": True, "persistent": True, "email": True, "sms": True},
        "ha_push_entities": ["notify.mobile_app_a", "notify.mobile_app_b"],
        "email": "x@example.com", "phone": "+48600000000",
    }
    if advanced:
        # Webhook channels would hit the network — test them without targets
        cfg["email"] = cfg["phone"] = ""
    sent = asyncio.run(notifier.async_deliver(
        hass, cfg, key="T", level="critical", title="t", message="m", email=True, advanced=advanced))
    return hass.services.calls, sent


def test_free_alerts_one_push_device_and_no_webhook():
    calls, sent = _deliver(advanced=False)
    assert calls == [("notify", "mobile_app_a"), ("persistent_notification", "create")]
    assert sent == ["ha_push", "persistent"]


def test_pro_alerts_all_push_devices():
    calls, _ = _deliver(advanced=True)
    assert ("notify", "mobile_app_b") in calls


# ── wind turbines ────────────────────────────────────────────────────────────

def test_turbine_count_limited_on_free():
    assert wind.sanitize_turbine({"count": 4}, max_count=const.FREE_MAX_TURBINES)["count"] == 1
    assert wind.sanitize_turbine({"count": 4}, max_count=const.PRO_MAX_TURBINES)["count"] == 4
    assert wind.sanitize_turbine({"count": 40})["count"] == 20


# ── voltage report export ────────────────────────────────────────────────────

def _report():
    return {
        "limit_v": 253.0,
        "summary": {"days": 1, "days_over": 1, "max_v": 256.4, "hours_max_over": 2, "hours_mean_over": 1,
                    "ten_min_over": 3, "hours_high_no_export": 1, "first_day": "2026-10-01"},
        "days": [{"date": "2026-10-01", "max_v": 256.4, "max_hour_mean": 254.0, "hours_max_over": 2,
                  "ten_min_over": 3, "hours_high_no_export": 1}],
        "weeks": [],
        "worst_10min": [{"start": "2026-10-01T12:10", "phase": "L1", "mean_v": 254.2, "grid_w": 4200}],
        "high_without_export": [{"hour": "2026-10-01T06:00", "phase": "L2", "mean_v": 251.0, "grid_w": -300}],
        "phases": {"days": [], "weeks": [], "top_hours": [{"hour": "x"}], "summary": {"hours": 0}},
    }


def test_voltage_basic_view_drops_export_details():
    basic = vr.basic_view(_report())
    assert "worst_10min" not in basic and "high_without_export" not in basic
    assert "top_hours" not in basic["phases"]
    assert basic["summary"]["max_v"] == 256.4 and basic["days"]


def test_voltage_export_csv_and_complaint():
    csv = vr.export_csv(_report())
    assert csv.splitlines()[1] == "2026-10-01;256,4;254;2;3;1"
    assert "2026-10-01T12:10;L1;254,2;4200" in csv
    text = vr.complaint_text(_report(), "2026-10-12")
    assert "2026-10-01 – 2026-10-12" in text
    assert "najwyższe zmierzone napięcie: 256,4 V" in text
    assert "(253 V)" in text


# ── strategy controller: fallback, restore, license regained ─────────────────

class _AnyHA:
    """Stand-in for any Home Assistant class / function the controller imports."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _AnyHA()

    def __getattr__(self, name):
        return _AnyHA()


class _HAStubModule(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _AnyHA


class _HAFinder:
    """Import hook: every homeassistant.* module becomes a permissive stub."""

    def find_spec(self, name, path, target=None):
        if name == "homeassistant" or name.startswith("homeassistant."):
            import importlib.machinery

            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        mod = _HAStubModule(spec.name)
        mod.__path__ = []
        return mod

    def exec_module(self, module):
        pass


@pytest.fixture(scope="module")
def sc_mod():
    """strategy_controller imported against stubs, then the other tests' modules restored."""
    def is_ha(name):
        return name == "homeassistant" or name.startswith("homeassistant.")

    saved = {k: sys.modules.pop(k) for k in [k for k in sys.modules if is_ha(k)]}
    finder = _HAFinder()
    sys.meta_path.insert(0, finder)
    pkg = types.ModuleType("smartinghome_sc")
    pkg.__path__ = [str(_DIR)]
    sys.modules.setdefault("smartinghome_sc", pkg)
    try:
        return importlib.import_module("smartinghome_sc.strategy_controller")
    finally:
        sys.meta_path.remove(finder)
        for k in [k for k in sys.modules if is_ha(k)]:
            del sys.modules[k]
        sys.modules.update(saved)


class _License:
    def __init__(self, pro: bool):
        self.pro = pro

    def has_feature(self, feature):
        return self.pro and feature in ALL_GATED


def _controller(sc, lic, settings=None):
    S = sc.AutopilotStrategy
    c = sc.StrategyController.__new__(sc.StrategyController)
    c._feature_check = lic.has_feature
    c._strategy_denied_logged = set()
    c._license_fallback_from = None
    c._enabled = False
    c._active_strategy = S.MAX_SELF_CONSUMPTION
    c._last_action = {}
    c._disabled_automations = set()
    c.persisted = 0

    async def _noop(*args, **kwargs):
        return None

    async def _persist():
        c.persisted += 1

    async def _executor(func, *args):
        return dict(settings or {})

    c._disable_conflicting_automations = _noop
    c._persist_autopilot_state = _persist
    c._update_action_statuses = lambda: None
    c._log_decision = lambda *args, **kwargs: None
    c.hass = types.SimpleNamespace(
        bus=types.SimpleNamespace(async_fire=lambda *a, **k: None),
        async_add_executor_job=_executor,
    )
    return c, S


def test_controller_free_falls_back_and_regains(sc_mod):
    lic = _License(pro=False)
    c, S = _controller(sc_mod, lic)
    asyncio.run(c.activate_strategy(S.WEATHER_ADAPTIVE))
    assert c.active_strategy == S.MAX_SELF_CONSUMPTION and c.enabled
    assert not c.is_strategy_allowed(S.AI_FULL_AUTONOMY) and c.is_strategy_allowed(S.MAX_PROFIT)
    lic.pro = True  # license validated later (e.g. server reachable again)
    asyncio.run(c.enforce_license())
    assert c.active_strategy == S.WEATHER_ADAPTIVE


def test_controller_expired_pro_leaves_pro_strategy(sc_mod):
    lic = _License(pro=True)
    c, S = _controller(sc_mod, lic)
    asyncio.run(c.activate_strategy(S.AI_FULL_AUTONOMY))
    assert c.active_strategy == S.AI_FULL_AUTONOMY
    lic.pro = False
    asyncio.run(c.enforce_license())
    assert c.active_strategy == S.MAX_SELF_CONSUMPTION
    # A strategy the user picks afterwards is not overridden when PRO returns
    asyncio.run(c.activate_strategy(S.MAX_PROFIT))
    lic.pro = True
    asyncio.run(c.enforce_license())
    assert c.active_strategy == S.MAX_PROFIT


def test_controller_restore_pro_strategy_on_free(sc_mod):
    settings = {"autopilot_active_strategy": "weather_adaptive", "autopilot_enabled": True}
    c, S = _controller(sc_mod, _License(pro=False), settings)
    asyncio.run(c.restore_state())
    assert c.active_strategy == S.MAX_SELF_CONSUMPTION and c.enabled
    assert c.persisted == 0  # settings keep the PRO strategy for when the license allows it
    c2, _ = _controller(sc_mod, _License(pro=True), settings)
    asyncio.run(c2.restore_state())
    assert c2.active_strategy == S.WEATHER_ADAPTIVE


def test_controller_restore_after_runtime_fallback(sc_mod):
    settings = {"autopilot_active_strategy": "max_self_consumption", "autopilot_enabled": True,
                "autopilot_license_fallback_from": "ai_full_autonomy"}
    c, S = _controller(sc_mod, _License(pro=True), settings)
    asyncio.run(c.restore_state())
    assert c.active_strategy == S.AI_FULL_AUTONOMY
    c2, _ = _controller(sc_mod, _License(pro=False), settings)
    asyncio.run(c2.restore_state())
    assert c2.active_strategy == S.MAX_SELF_CONSUMPTION
