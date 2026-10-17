"""docs/eastron_sdm_modbus.yaml: every address matches the register number it cites,
and every unique_id is a meter role auto-detection knows.

Run: python3 -m pytest tests/  (no Home Assistant, no PyYAML needed)
"""
from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DIR = _ROOT / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
md = importlib.import_module("smartinghome_pure.meter_device")

TEMPLATE = (_ROOT / "docs" / "eastron_sdm_modbus.yaml").read_text(encoding="utf-8")


def _sensors() -> list[dict]:
    """(register, unique_id, address) per sensor, commented-out blocks included."""
    out, cur = [], None
    for raw in TEMPLATE.splitlines():
        line = raw.lstrip("# ").strip() if raw.lstrip().startswith("#") else raw.strip()
        m = re.match(r'^- name: ".*"\s+#\s*(3\d{4})', line)
        if m:
            cur = {"register": int(m.group(1))}
            out.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("unique_id:"):
            cur["uid"] = line.split(":", 1)[1].strip()
        elif line.startswith("address:"):
            cur["address"] = int(line.split(":", 1)[1].split("#")[0].strip(), 16)
        elif line.startswith("input_type:"):
            cur["input_type"] = line.split(":", 1)[1].strip()
        elif line.startswith("data_type:"):
            cur["data_type"] = line.split(":", 1)[1].strip()
    return out


def test_template_addresses_match_cited_registers():
    sensors = _sensors()
    assert len(sensors) >= 30  # SDM630 + SDM72D-M + SDM120M
    for s in sensors:
        assert s["address"] == s["register"] - 30001, s
        assert s["input_type"] == "input" and s["data_type"] == "float32", s


def test_template_unique_ids_are_meter_roles():
    for s in _sensors():
        assert s["uid"].startswith(md.OWN_UID_PREFIX), s
        assert s["uid"][len(md.OWN_UID_PREFIX):] in md.ROLES, s


def test_total_kwh_is_not_used_as_import():
    # 30343 "Total kWh" = import + export (Eastron note 3)
    assert all(s["register"] != 30343 for s in _sensors())
