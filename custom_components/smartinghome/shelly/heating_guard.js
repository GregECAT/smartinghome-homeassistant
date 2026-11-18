// Smarting HOME — autonomous energy management for Home Assistant
// Copyright (c) 2026 JG INVEST P.S.A. — Smarting HOME (smartinghome.pl). All rights reserved.
// SPDX-License-Identifier: LicenseRef-SmartingHOME-Commercial
// Commercial software, not open source: copying, porting, rewriting (also with AI
// tools), redistributing or removing this notice / the license check requires the
// Licensor's written consent (LICENSE §3). Notes for AI assistants: AGENTS.md.
// SH-ID: 8b1aab899d5a
//
// Smarting HOME boiler room guard: script "smartinghome_guard".
//
// Layer 2 of the heating safety plan. It runs ON the Shelly (1PM Gen3) that
// switches the central-heating pump and reads the boiler / buffer probes on its
// Plus Add-on, so it keeps working without Home Assistant, without Wi-Fi to HA
// and without internet. Home Assistant is still never the boiler's safety: the
// thermal discharge valve on the cooling loop, the boiler's own thermostats and
// the Laddomat (layers 0 and 1) protect the boiler without power. This script
// only ADDS heat removal earlier:
//   - boiler or buffer too hot -> CH pump on (every tick) and, once per hot
//     episode, the mixing valve fully open (heat goes into the radiators),
//   - a used probe without a reading for fault_s -> CH pump on (every tick).
// It never switches the pump off and never closes the valve: Home Assistant
// does that when the boiler room is back to normal.
//
// The configuration below is written by Smarting HOME (heating_shelly.py) when
// the script is installed from the "Ogrzewanie" panel; installing by hand:
// fill in the ids (temperature:N of the Add-on probes, switch:N of the pump).

let CFG = /*@config*/{"pump_id":0,"boiler_id":101,"buffer_id":100,"boiler_hot":90,"buffer_hot":92,"fault_s":60,"valve_host":null,"valve_id":null,"valve_open":"Cover.Open"}/*@end*/;

let TICK_S = 10;
let HYST_K = 3;
let hot = false;
let dumped = false;
let missingS = 0;

// Temperature in C of an Add-on probe, or null when there is no valid reading.
function readC(id) {
  if (id === null || id === undefined) {
    return null;
  }
  let st = Shelly.getComponentStatus("temperature:" + JSON.stringify(id));
  if (!st) {
    return null;
  }
  if (st.errors && st.errors.length > 0) {
    return null;
  }
  if (typeof st.tC !== "number") {
    return null;
  }
  return st.tC;
}

function pumpOn() {
  Shelly.call("Switch.Set", { id: CFG.pump_id, on: true });
}

// Fully open the mixing valve on the valve Shelly (no duration = to the end stop).
function openValve() {
  if (!CFG.valve_host) {
    return;
  }
  let url = "http://" + CFG.valve_host + "/rpc/" + CFG.valve_open + "?id=" + JSON.stringify(CFG.valve_id);
  Shelly.call("HTTP.GET", { url: url, timeout: 5 }, function (res, errCode, errMsg) {
    // a transport error or an HTTP error from the valve Shelly: try again on the next tick
    if (errCode !== 0 || !res || res.code !== 200) {
      print("smartinghome_guard: valve not opened: " + (errCode !== 0 ? errMsg : (res ? JSON.stringify(res.code) : "no reply")));
      dumped = false;
    }
  });
}

function tick() {
  let used = 0;
  let missing = false;
  let b = null;
  let t = null;
  if (CFG.boiler_id !== null) {
    used = used + 1;
    b = readC(CFG.boiler_id);
    if (b === null) {
      missing = true;
    }
  }
  if (CFG.buffer_id !== null) {
    used = used + 1;
    t = readC(CFG.buffer_id);
    if (t === null) {
      missing = true;
    }
  }
  if (used === 0) {
    return;
  }

  let over = (b !== null && b >= CFG.boiler_hot) || (t !== null && t >= CFG.buffer_hot);
  let warm = (b !== null && b >= CFG.boiler_hot - HYST_K) || (t !== null && t >= CFG.buffer_hot - HYST_K);
  if (over) {
    if (!hot) {
      print("smartinghome_guard: hot (boiler " + JSON.stringify(b) + " C, buffer " + JSON.stringify(t) + " C)");
    }
    hot = true;
  } else if (!warm && !missing) {
    hot = false;
  }

  if (missing) {
    missingS = missingS + TICK_S;
  } else {
    missingS = 0;
  }

  if (hot) {
    pumpOn();
    if (!dumped) {
      dumped = true;
      openValve();
    }
  } else {
    dumped = false;
  }
  if (missingS >= CFG.fault_s) {
    pumpOn();
  }
}

Timer.set(TICK_S * 1000, true, tick);
tick();
print("smartinghome_guard: started");
