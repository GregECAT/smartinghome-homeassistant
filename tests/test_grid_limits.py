"""Export limit from the connection conditions and limits set by the DSO / an integrator."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_p = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome" / "grid_limits.py"
_spec = importlib.util.spec_from_file_location("grid_limits", _p)
gl = importlib.util.module_from_spec(_spec)
sys.modules["grid_limits"] = gl
_spec.loader.exec_module(gl)

DEFAULT = 16000


def test_config_defaults_and_parsing():
    assert gl.config(None) == {"export_cap_w": None, "export_baseline_w": None, "osd_priority": False, "external_clear": ""}
    c = gl.config({"export_cap_w": "7500", "export_baseline_w": -1, "osd_priority": 1, "external_clear": "x"})
    assert c["export_cap_w"] == 7500 and c["export_baseline_w"] is None and c["osd_priority"] and c["external_clear"] == "x"


def test_baseline_ignores_our_old_default():
    assert gl.baseline_from_observation(10000, DEFAULT) == 10000
    assert gl.baseline_from_observation(DEFAULT, DEFAULT) is None   # written by us earlier
    assert gl.baseline_from_observation(None, DEFAULT) is None


def test_max_export_priority():
    assert gl.max_export(5000, 10000, DEFAULT) == 5000          # owner's connection limit
    assert gl.max_export(None, 10000, DEFAULT) == 10000         # installer's setting
    assert gl.max_export(None, None, DEFAULT) == DEFAULT
    assert gl.max_export(0, 10000, DEFAULT) == 0                # zero export allowed


def test_external_change_waits_for_lagging_entity():
    assert not gl.external_change(3000, 8000, last_write_ts=100.0, now=150.0)   # our write still settling
    assert gl.external_change(3000, 8000, last_write_ts=100.0, now=400.0)
    assert not gl.external_change(8000, 8000, last_write_ts=0.0, now=400.0)
    assert not gl.external_change(None, 8000, 0.0, 400.0)
    assert not gl.external_change(3000, None, 0.0, 400.0)                       # we never wrote


def test_effective_limit_takes_the_most_restrictive():
    assert gl.effective_limit(16000, 8000, None, None) == 8000      # never above the connection limit
    assert gl.effective_limit(16000, 8000, 3000, None) == 3000      # DSO / integrator limit respected
    assert gl.effective_limit(16000, 8000, None, 2500) == 2500      # voltage guard cap
    assert gl.effective_limit(0, 8000, 3000, 2500) == 0             # zero export stays zero
    assert gl.effective_limit(5000, 8000, 6000, 7000) == 5000
