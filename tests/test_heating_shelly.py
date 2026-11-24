"""Shelly helpers of the heating module: component ids, guard script config and code.

Run: python3 -m pytest tests/  (no Home Assistant needed)
"""
from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "smartinghome"
_pkg = types.ModuleType("smartinghome_pure")
_pkg.__path__ = [str(_DIR)]
sys.modules.setdefault("smartinghome_pure", _pkg)
hs = importlib.import_module("smartinghome_pure.heating_shelly")

T = hs.ShellyTarget
PUMP = T(host="192.168.1.50", port=80, gen=3, auth=False, component="switch", cid=0)
BOILER = T(host="192.168.1.50", port=80, gen=3, auth=False, component="temperature", cid=101)
BUFFER = T(host="192.168.1.50", port=80, gen=3, auth=False, component="temperature", cid=100)
VALVE = T(host="192.168.1.51", port=80, gen=3, auth=False, component="cover", cid=0)
CFG = {
    "entities": {"pump_switch": "switch.pompa_co", "boiler_out": "sensor.kociol", "buffer_top": "sensor.bufor_gora"},
    "alarm_c": 92.0, "buffer_full_c": 88.0,
    "valve_type": "cover", "valve_cover": "cover.zawor", "valve_invert": False, "safety_dump": True,
}


# ── component ids ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("uid, expected", [
    ("aabbccddeeff-temperature:101-temperature_0", ("temperature", 101)),
    ("aabbccddeeff-switch:0", ("switch", 0)),
    ("x-cover:0", ("cover", 0)),
    ("aabbccddeeff-input:2-input", ("input", 2)),
    ("aabbccddeeff-sys-uptime", None),
    ("", None),
])
def test_component_from_unique_id(uid, expected):
    assert hs.component_from_unique_id(uid) == expected


# ── guard config ─────────────────────────────────────────────────────────────

def test_guard_full_setup():
    g = hs.guard_config(CFG, PUMP, BOILER, BUFFER, VALVE)
    assert g["supported"] and not g["reason"]
    assert (g["pump_id"], g["boiler_id"], g["buffer_id"]) == (0, 101, 100)
    assert g["boiler_hot"] == 90.0 and g["buffer_hot"] == 92.0 and g["fault_s"] == 60
    assert (g["valve_host"], g["valve_id"], g["valve_open"]) == ("192.168.1.51", 0, "Cover.Open")
    assert g["host"] == "192.168.1.50"


def test_buffer_threshold_capped_at_95():
    g = hs.guard_config({**CFG, "buffer_full_c": 93.0}, PUMP, BOILER, BUFFER, VALVE)
    assert g["buffer_hot"] == 95.0


def test_no_pump_selected_or_not_a_shelly():
    g = hs.guard_config({**CFG, "entities": {**CFG["entities"], "pump_switch": ""}}, None, BOILER, BUFFER, VALVE)
    assert not g["supported"] and "Nie wybrano" in g["reason"]
    g = hs.guard_config(CFG, None, BOILER, BUFFER, VALVE)
    assert not g["supported"] and "Shelly" in g["reason"]


def test_pump_entity_is_not_a_relay():
    g = hs.guard_config(CFG, T("192.168.1.50", 80, 3, False, "cover", 0), BOILER, BUFFER, VALVE)
    assert not g["supported"] and "switch" in g["reason"]


def test_pump_with_password_not_installable_but_code_for_manual_install():
    g = hs.guard_config(CFG, T("192.168.1.50", 80, 3, True, "switch", 0), BOILER, BUFFER, VALVE)
    assert not g["supported"] and "hasło" in g["reason"]
    assert g["pump_id"] == 0 and g["boiler_id"] == 101       # still known: the code can be pasted by hand
    st = hs._status(g)
    assert st["code"] and '"pump_id":0' in st["code"]


def test_probes_on_another_shelly_are_skipped():
    other = T("192.168.1.60", 80, 3, False, "temperature", 100)
    g = hs.guard_config(CFG, PUMP, BOILER, other, VALVE)
    assert g["supported"] and g["boiler_id"] == 101 and g["buffer_id"] is None
    assert any("bufora" in n for n in g["notes"])
    g = hs.guard_config(CFG, PUMP, T("192.168.1.60", 80, 3, False, "temperature", 101), other, VALVE)
    assert not g["supported"] and "tego samego Shelly" in g["reason"]


def test_no_probes_at_all():
    g = hs.guard_config(CFG, PUMP, None, None, VALVE)
    assert not g["supported"] and g["boiler_id"] is None and g["buffer_id"] is None


def test_same_host_other_port_is_another_device():
    g = hs.guard_config(CFG, PUMP, T("192.168.1.50", 8080, 3, False, "temperature", 101), BUFFER, VALVE)
    assert g["boiler_id"] is None and g["buffer_id"] == 100


def test_valve_dump_inverted_and_port_in_host():
    g = hs.guard_config({**CFG, "valve_invert": True}, PUMP, BOILER, BUFFER,
                        T("192.168.1.51", 8080, 3, False, "cover", 1))
    assert (g["valve_host"], g["valve_id"], g["valve_open"]) == ("192.168.1.51:8080", 1, "Cover.Close")


@pytest.mark.parametrize("cfg, valve", [
    ({**CFG, "safety_dump": False}, VALVE),                         # floor heating: no dump
    ({**CFG, "valve_type": "switches"}, VALVE),                     # valve not driven by a cover
    (CFG, None),                                                    # valve not a Shelly
    (CFG, T("192.168.1.51", 80, 3, True, "cover", 0)),              # valve Shelly with a password
])
def test_no_valve_dump(cfg, valve):
    g = hs.guard_config(cfg, PUMP, BOILER, BUFFER, valve)
    assert g["supported"] and g["valve_host"] is None and g["valve_id"] is None


# ── guard code ───────────────────────────────────────────────────────────────

def _strip_strings_and_comments(code: str) -> str:
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            i = code.find("\n", i)
            i = n if i < 0 else i
        elif code.startswith("/*", i):
            end = code.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif c in "\"'":
            j = i + 1
            while j < n and code[j] != c:
                j += 2 if code[j] == "\\" else 1
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def test_guard_code_injects_config_and_is_plain_mjs():
    g = hs.guard_config(CFG, PUMP, BOILER, BUFFER, VALVE)
    code = hs.guard_code(g)
    injected = json.dumps({k: g[k] for k in hs.SCRIPT_KEYS}, separators=(",", ":"))
    assert f"/*@config*/{injected}/*@end*/" in code
    assert code.count("/*@config*/") == 1
    assert "=>" not in code and "`" not in code and "class " not in code
    bare = _strip_strings_and_comments(code)
    for o, c in ("{}", "()", "[]"):
        assert bare.count(o) == bare.count(c), (o, c)
    assert "SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial" in code
    # the rest of the template is untouched
    template = hs.GUARD_TEMPLATE.read_text(encoding="utf-8")
    assert re.sub(r"/\*@config\*/.*?/\*@end\*/", "", code, flags=re.S) == \
        re.sub(r"/\*@config\*/.*?/\*@end\*/", "", template, flags=re.S)


def test_guard_code_without_valve_uses_null():
    g = hs.guard_config({**CFG, "safety_dump": False}, PUMP, BOILER, None, None)
    code = hs.guard_code(g)
    assert '"valve_host":null' in code and '"buffer_id":null' in code


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_guard_code_parses_as_javascript(tmp_path):
    code = hs.guard_code(hs.guard_config(CFG, PUMP, BOILER, BUFFER, VALVE))
    f = tmp_path / "guard.js"
    f.write_text(code, encoding="utf-8")
    res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_guard_script_behaviour_in_a_fake_shelly(tmp_path):
    """Run the script in node with a fake Shelly API: hot → pump on + one valve open; probe lost → pump on."""
    code = hs.guard_code(hs.guard_config(CFG, PUMP, BOILER, BUFFER, VALVE))
    harness = """
const calls = []; let temps = {100: 60, 101: 60}; let timer = null;
globalThis.print = () => {};
globalThis.Shelly = {
  getComponentStatus: (k) => { const id = Number(k.split(':')[1]); const v = temps[id];
    return v === undefined ? null : (v === null ? {id, tC: null, errors: ['read']} : {id, tC: v}); },
  call: (m, p) => calls.push([m, p]),
};
globalThis.Timer = { set: (ms, rep, fn) => { timer = fn; } };
eval(require('fs').readFileSync(process.argv[2], 'utf8').replace(/^let /gm, 'var '));
const run = (n) => { for (let i = 0; i < n; i++) timer(); };
const out = {};
run(3); out.idle = calls.length;
temps[101] = 91; run(3);
out.hotPump = calls.filter(c => c[0] === 'Switch.Set' && c[1].on === true && c[1].id === 0).length;
out.valve = calls.filter(c => c[0] === 'HTTP.GET').map(c => c[1].url);
temps[101] = 88.5; calls.length = 0; run(2); out.hysteresisPump = calls.length;
temps[101] = 80; calls.length = 0; run(2); out.cooled = calls.length;
temps[100] = null; calls.length = 0; run(5); out.faultEarly = calls.length; run(1); out.faultPump = calls.length;
console.log(JSON.stringify(out));
"""
    (tmp_path / "guard.js").write_text(code, encoding="utf-8")
    (tmp_path / "harness.js").write_text(harness, encoding="utf-8")
    res = subprocess.run(["node", str(tmp_path / "harness.js"), str(tmp_path / "guard.js")],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout.strip().splitlines()[-1])
    assert out["idle"] == 0                      # normal temperatures: the script does nothing
    assert out["hotPump"] == 3                   # pump on every tick while hot
    assert out["valve"] == ["http://192.168.1.51/rpc/Cover.Open?id=0"]   # valve opened once
    assert out["hysteresisPump"] == 2            # 88.5 °C: still hot (released below 87)
    assert out["cooled"] == 0                    # back to normal: hands off, HA switches the pump off
    assert out["faultEarly"] == 0                # probe lost for 50 s: wait
    assert out["faultPump"] == 1                 # 60 s: pump on


def test_guard_follows_a_dump_by_the_buffer_top():
    g = hs.guard_config({**CFG, "dump_top_c": 93.0}, PUMP, BOILER, BUFFER, VALVE)
    assert g["boiler_hot"] == 999.0 and g["buffer_hot"] == 93.0
