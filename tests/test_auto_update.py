"""Automatic updates: which update entities get installed tonight."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome" / "auto_update.py"
_spec = importlib.util.spec_from_file_location("auto_update", _p)
au = importlib.util.module_from_spec(_spec)
sys.modules["auto_update"] = au
_spec.loader.exec_module(au)

H = 3600.0


def _item(eid, group, installed, latest, available=True, features=0):
    return {"entity_id": eid, "title": eid, "group": group, "installed": installed, "latest": latest,
            "available": available, "features": features}


def test_classify():
    assert au.classify("hacs", "1188124253") == "smartinghome"
    assert au.classify("hacs", "172733314") == "hacs"
    assert au.classify("hassio", "home_assistant_core_version_latest") == "core"
    assert au.classify("hassio", "home_assistant_os_version_latest") == "os"
    assert au.classify("hassio", "home_assistant_supervisor_version_latest") is None
    assert au.classify("hassio", "cb646a50_get_version_latest") == "addons"
    assert au.classify("esphome", "x") is None


def test_versions_and_window():
    assert au.is_patch_release("2026.10.1") and not au.is_patch_release("2026.10.0")
    assert au.is_prerelease("2026.11.0b3") and not au.is_prerelease("v1.69.10")
    assert au.in_window(3, [2, 5]) and not au.in_window(5, [2, 5])
    assert au.in_window(23, [22, 2]) and au.in_window(1, [22, 2]) and not au.in_window(12, [22, 2])


def test_select_respects_groups_age_and_core_patch():
    cfg = au.config({"enabled": True, "groups": {"core": {"enabled": True}, "hacs": {"enabled": True}}})
    now = 1_000_000.0
    items = [
        _item("update.sh", "smartinghome", "v1.69.9", "v1.69.10"),
        _item("update.goodwe", "hacs", "0.9.9.30", "0.9.9.31"),
        _item("update.core", "core", "2026.9.4", "2026.10.0", features=15),
        _item("update.addon", "addons", "1.0", "1.1", features=29),
        _item("update.hacs", "hacs", "2.0.5", "2.0.5", available=False),
    ]
    seen = {"update.sh": {"version": "v1.69.10", "ts": now - 1 * H},
            "update.goodwe": {"version": "0.9.9.31", "ts": now - 10 * H},
            "update.core": {"version": "2026.10.0", "ts": now - 200 * H}}
    res = {c["entity_id"]: c for c in au.select(items, cfg, seen, now)}
    assert res["update.sh"]["eligible"]                      # min age 0
    assert not res["update.goodwe"]["eligible"] and "za świeże" in res["update.goodwe"]["reason"]
    assert not res["update.core"]["eligible"] and ".1" in res["update.core"]["reason"]
    assert not res["update.addon"]["eligible"]               # group off by default
    assert res["update.hacs"]["reason"] == "aktualne"
    p = au.plan(list(res.values()))
    assert [c["entity_id"] for c in p["install"]] == ["update.sh"] and p["restart"] and p["final"] is None


def test_plan_core_restarts_itself_and_covers_integrations():
    cfg = au.config({"enabled": True, "groups": {"core": {"enabled": True, "min_age_h": 0}}})
    items = [_item("update.sh", "smartinghome", "1", "2"), _item("update.core", "core", "2026.9.4", "2026.10.1", features=15)]
    p = au.plan(au.select(items, cfg, {}, 0.0))
    assert p["final"]["entity_id"] == "update.core" and not p["restart"]
    assert [c["entity_id"] for c in p["install"]] == ["update.sh"]
