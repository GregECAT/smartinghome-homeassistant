/**
 * Smarting HOME — Custom Panel for Home Assistant
 * Advanced Power Flow Dashboard (Sunsynk-style)
 * © 2026 Smarting HOME by GregECAT
 */

class SmartingHomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._activeTab = "overview";
    this._isFullscreen = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (this.shadowRoot.querySelector(".panel-container")) this._updateAll();
  }
  set panel(p) { this._panel = p; }
  set narrow(n) { this._narrow = n; }

  connectedCallback() {
    this._render();
    document.addEventListener("fullscreenchange", () => {
      this._isFullscreen = !!document.fullscreenElement;
      const btn = this.shadowRoot.querySelector(".fullscreen-btn");
      if (btn) btn.textContent = this._isFullscreen ? "⊡ Zamknij" : "⊞ Pełny ekran";
    });
  }
  disconnectedCallback() { if (this._interval) clearInterval(this._interval); }

  _toggleFullscreen() {
    const c = this.shadowRoot.querySelector(".panel-container");
    if (!document.fullscreenElement) c.requestFullscreen().catch(() => {});
    else document.exitFullscreen().catch(() => {});
  }
  _switchTab(tab) {
    this._activeTab = tab;
    this.shadowRoot.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    this.shadowRoot.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.dataset.tab === tab));
  }

  /* ── Sensor mapping ─────────────────────── */
  static DM = {
    pv_power:"sensor.pv_power", load_power:"sensor.load",
    grid_power:"sensor.meter_active_power_total", battery_power:"sensor.battery_power",
    battery_soc:"sensor.battery_state_of_charge",
    pv1_power:"sensor.pv1_power", pv1_voltage:"sensor.pv1_voltage", pv1_current:"sensor.pv1_current",
    pv2_power:"sensor.pv2_power", pv2_voltage:"sensor.pv2_voltage", pv2_current:"sensor.pv2_current",
    pv3_power:"", pv4_power:"",
    battery_voltage:"sensor.battery_voltage", battery_current:"sensor.battery_current",
    battery_temp:"sensor.battery_temperature", battery_capacity_kwh:"",
    voltage_l1:"sensor.on_grid_l1_voltage", voltage_l2:"sensor.on_grid_l2_voltage", voltage_l3:"sensor.on_grid_l3_voltage",
    current_l1:"sensor.on_grid_l1_current", current_l2:"sensor.on_grid_l2_current", current_l3:"sensor.on_grid_l3_current",
    power_l1:"sensor.on_grid_l1_power", power_l2:"sensor.on_grid_l2_power", power_l3:"sensor.on_grid_l3_power",
    grid_frequency:"sensor.on_grid_frequency",
    pv_today:"sensor.today_s_pv_generation",
    grid_import_today:"sensor.grid_import_daily", grid_export_today:"sensor.grid_export_daily",
    battery_charge_today:"sensor.today_battery_charge", battery_discharge_today:"sensor.today_battery_discharge",
    inverter_power:"sensor.active_power", inverter_temp:"sensor.inverter_temperature_air",
    weather_temp:"", weather_humidity:"", weather_cloud_cover:"",
  };
  _m(k) {
    const m = this._hass?.states["sensor.smartinghome_sensor_map"]?.attributes;
    return m?.[k] || SmartingHomePanel.DM[k] || "";
  }
  _s(id) { return id && this._hass?.states[id] ? this._hass.states[id].state : null; }
  _n(id) { const v = parseFloat(this._s(id)); return isNaN(v) ? null : v; }
  _f(id, d=1) { const v = this._n(id); return v === null ? "—" : v.toFixed(d); }
  _fm(k, d=1) { return this._f(this._m(k), d); }
  _nm(k) { return this._n(this._m(k)); }
  _setText(id, val) { const el = this.shadowRoot.getElementById(id); if (el) el.textContent = val; }
  _callService(domain, service, data = {}) { if (this._hass) this._hass.callService(domain, service, data); }

  // Smart unit formatting
  _pw(w) {
    if (w === null || isNaN(w)) return "—";
    return Math.abs(w) >= 1000 ? `${(w/1000).toFixed(1)} kW` : `${Math.round(w)} W`;
  }

  /* ── Update all ─────────────────────────── */
  _updateAll() { this._updateFlow(); this._updateStats(); }

  _updateFlow() {
    const pv = this._nm("pv_power") || 0;
    const load = this._nm("load_power") || 0;
    const grid = this._nm("grid_power") || 0;
    const batt = this._nm("battery_power") || 0;
    const soc = this._nm("battery_soc") || 0;

    // PV
    this._setText("v-pv", this._pw(pv));
    this._setText("v-pv-today", `${this._fm("pv_today")} kWh`);
    // PV Strings
    for (let i = 1; i <= 4; i++) {
      const p = this._nm(`pv${i}_power`);
      const box = this.shadowRoot.getElementById(`pv${i}-box`);
      if (box) box.style.display = p !== null && p > 0 ? "block" : (i <= 2 ? "block" : "none");
      this._setText(`v-pv${i}-p`, p !== null ? this._pw(p) : "—");
      this._setText(`v-pv${i}-v`, `${this._fm(`pv${i}_voltage`)} V`);
      this._setText(`v-pv${i}-a`, `${this._fm(`pv${i}_current`)} A`);
    }

    // Home / Load
    this._setText("v-load", this._pw(load));
    this._setText("v-load-l1", `L1: ${this._fm("power_l1", 0)} W`);
    this._setText("v-load-l2", `L2: ${this._fm("power_l2", 0)} W`);
    this._setText("v-load-l3", `L3: ${this._fm("power_l3", 0)} W`);

    // Grid
    this._setText("v-grid", this._pw(Math.abs(grid)));
    this._setText("v-grid-dir", grid > 0 ? "POBÓR Z SIECI" : grid < 0 ? "ODDAWANIE DO SIECI" : "");
    this._setText("v-grid-import", `${this._fm("grid_import_today")} kWh`);
    this._setText("v-grid-export", `${this._fm("grid_export_today")} kWh`);
    this._setText("v-grid-v1", `${this._fm("voltage_l1")} V`);
    this._setText("v-grid-v2", `${this._fm("voltage_l2")} V`);
    this._setText("v-grid-v3", `${this._fm("voltage_l3")} V`);
    this._setText("v-grid-freq", `${this._fm("grid_frequency", 2)} Hz`);

    // Battery
    this._setText("v-batt", this._pw(Math.abs(batt)));
    this._setText("v-batt-dir", batt > 0 ? "ŁADOWANIE ↑" : batt < 0 ? "ROZŁADOWANIE ↓" : "STANDBY");
    this._setText("v-soc", `${Math.round(soc)}%`);
    this._setText("v-batt-v", `${this._fm("battery_voltage")} V`);
    this._setText("v-batt-a", `${this._fm("battery_current")} A`);
    this._setText("v-batt-temp", `${this._fm("battery_temp")}°C`);
    this._setText("v-batt-charge", `↑ ${this._fm("battery_charge_today")} kWh`);
    this._setText("v-batt-discharge", `↓ ${this._fm("battery_discharge_today")} kWh`);

    // SOC bar
    const socBar = this.shadowRoot.getElementById("soc-fill");
    if (socBar) {
      socBar.style.width = `${Math.min(100, Math.max(0, soc))}%`;
      socBar.style.background = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";
    }
    const socEl = this.shadowRoot.getElementById("v-soc");
    if (socEl) socEl.style.color = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";

    // Inverter
    this._setText("v-inv-p", this._pw(this._nm("inverter_power")));
    this._setText("v-inv-t", `${this._fm("inverter_temp")}°C`);

    // Autarky / Self-consumption
    this._setText("v-autarky", `${this._f("sensor.smartinghome_autarky_today", 0)}%`);
    this._setText("v-selfcons", `${this._f("sensor.smartinghome_self_consumption_today", 0)}%`);

    // Weather
    const wt = this._nm("weather_temp");
    this._setText("v-weather", wt !== null ? `${wt.toFixed(0)}°C` : "");
    this._setText("v-clouds", this._fm("weather_cloud_cover", 0) + "%");

    // Animated flows
    this._flow("fl-pv-inv", pv > 10, pv);
    this._flow("fl-inv-load", load > 10, load);
    this._flow("fl-grid-inv", grid > 10, grid);
    this._flow("fl-inv-grid", grid < -10, Math.abs(grid), true);
    this._flow("fl-inv-batt", batt > 10, batt);
    this._flow("fl-batt-inv", batt < -10, Math.abs(batt), true);
  }

  _flow(id, active, power, reverse = false) {
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    if (active) {
      const speed = Math.max(0.6, 3.5 - (Math.min(power, 6000) / 6000) * 2.9);
      el.style.display = "block";
      el.style.animationDuration = `${speed}s`;
      el.style.animationDirection = reverse ? "reverse" : "normal";
    } else {
      el.style.display = "none";
    }
  }

  _updateStats() {
    // HEMS recommendation
    this._setText("v-hems-rec", this._s("sensor.smartinghome_hems_recommendation") || "Brak danych");
    // License
    const badge = this.shadowRoot.getElementById("v-license");
    if (badge) {
      const tier = (this._s("sensor.smartinghome_license_tier") || "FREE").toUpperCase();
      badge.textContent = tier;
      badge.className = `badge ${tier === "PRO" ? "pro" : "free"}`;
    }
    // RCE / Tariff
    this._setText("v-g13-zone", this._s("sensor.smartinghome_g13_current_zone") || "—");
    this._setText("v-g13-price", `${this._f("sensor.smartinghome_g13_buy_price", 2)} PLN`);
    this._setText("v-rce-price", `${this._f("sensor.smartinghome_rce_sell_price", 2)} PLN`);
    this._setText("v-rce-trend", this._s("sensor.smartinghome_rce_price_trend") || "—");
    this._setText("v-rce-avg", `${this._f("sensor.smartinghome_rce_average_today", 2)} PLN`);
    this._setText("v-forecast-today", `${this._f("sensor.smartinghome_pv_forecast_today_total")} kWh`);
    this._setText("v-forecast-tomorrow", `${this._f("sensor.smartinghome_pv_forecast_tomorrow_total")} kWh`);
    this._setText("v-battery-energy", `${this._f("sensor.smartinghome_battery_energy_available")} kWh`);
    this._setText("v-battery-runtime", this._s("sensor.smartinghome_battery_runtime") || "—");
    this._setText("v-arbitrage", `${this._f("sensor.smartinghome_battery_arbitrage_potential")} PLN`);
    this._setText("v-surplus", `${this._f("sensor.smartinghome_pv_surplus_power", 0)} W`);
    this._setText("v-net-grid", `${this._f("sensor.smartinghome_net_grid_today")} kWh`);
  }

  /* ── Render ─────────────────────────────── */
  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        * { box-sizing: border-box; margin: 0; padding: 0; }

        .panel-container {
          min-height: 100vh;
          background: linear-gradient(135deg, #0a1628 0%, #111d35 50%, #0d1f3c 100%);
          color: #e0e6ed;
          font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
          overflow-y: auto;
        }

        /* ── Header ── */
        .header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 10px 20px;
          background: rgba(255,255,255,0.03);
          border-bottom: 1px solid rgba(255,255,255,0.06);
          position: sticky; top: 0; z-index: 100;
        }
        .header-left { display: flex; align-items: center; gap: 10px; }
        .header h1 {
          font-size: 17px; font-weight: 700;
          background: linear-gradient(135deg, #00d4ff, #00e676);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .header-right { display: flex; align-items: center; gap: 8px; }
        .badge {
          padding: 2px 8px; border-radius: 20px; font-size: 10px;
          font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .badge.free { background: rgba(52,152,219,0.2); color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
        .badge.pro { background: rgba(46,204,113,0.2); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
        .fullscreen-btn {
          padding: 4px 10px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.12);
          background: rgba(255,255,255,0.04); color: #8899aa; font-size: 11px; cursor: pointer;
        }

        /* ── Tabs ── */
        .tabs { display: flex; gap: 2px; padding: 4px 20px; background: rgba(0,0,0,0.25); }
        .tab-btn {
          padding: 7px 14px; border: none; background: transparent;
          color: #64748b; font-size: 12px; font-weight: 500; cursor: pointer;
          border-radius: 7px; transition: all 0.2s; white-space: nowrap;
        }
        .tab-btn:hover { background: rgba(255,255,255,0.05); color: #a0aec0; }
        .tab-btn.active { background: rgba(0,212,255,0.1); color: #00d4ff; }

        /* ── Content ── */
        .tab-content { display: none; padding: 12px 16px; }
        .tab-content.active { display: block; }

        /* ═══════════════════════════════════════ */
        /* ═══  POWER FLOW — SUNSYNK STYLE  ═══ */
        /* ═══════════════════════════════════════ */
        .flow-grid {
          display: grid;
          grid-template-columns: 200px 1fr 200px;
          grid-template-rows: auto 1fr auto;
          gap: 0;
          max-width: 1100px;
          margin: 0 auto;
          min-height: 540px;
        }

        /* Node boxes */
        .node {
          background: rgba(255,255,255,0.03);
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 14px;
          padding: 14px;
          position: relative;
        }
        .node:hover { border-color: rgba(0,212,255,0.2); }
        .node-title {
          font-size: 10px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 1px; color: #64748b; margin-bottom: 8px;
        }
        .node-big {
          font-size: 26px; font-weight: 800; color: #fff; line-height: 1.1;
        }
        .node-sub { font-size: 11px; color: #8899aa; margin-top: 3px; }
        .node-detail { font-size: 11px; color: #94a3b8; line-height: 1.7; }
        .node-dir {
          font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
          text-transform: uppercase; margin-top: 2px;
        }

        /* Layout positions */
        .pv-area { grid-column: 1 / 2; grid-row: 1 / 2; }
        .inv-area { grid-column: 2 / 3; grid-row: 1 / 3; display: flex; flex-direction: column; align-items: center; justify-content: center; position: relative; }
        .home-area { grid-column: 3 / 4; grid-row: 1 / 2; }
        .batt-area { grid-column: 1 / 2; grid-row: 2 / 4; }
        .grid-area { grid-column: 3 / 4; grid-row: 2 / 4; }
        .summary-area { grid-column: 1 / 4; grid-row: 4 / 5; }

        /* PV string boxes */
        .pv-strings { display: flex; flex-direction: column; gap: 6px; }
        .pv-string {
          background: rgba(247,183,49,0.06);
          border: 1px solid rgba(247,183,49,0.15);
          border-radius: 10px; padding: 8px 10px;
        }
        .pv-string .pv-name { font-size: 10px; color: #f7b731; font-weight: 700; }
        .pv-string .pv-val { font-size: 15px; color: #fff; font-weight: 700; }
        .pv-string .pv-detail { font-size: 10px; color: #94a3b8; }

        /* Inverter center */
        .inv-box {
          width: 120px; height: 100px;
          background: rgba(255,255,255,0.06);
          border: 1px solid rgba(255,255,255,0.15);
          border-radius: 14px;
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          position: relative;
        }
        .inv-icon { font-size: 36px; }
        .inv-label { font-size: 9px; color: #8899aa; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; }

        /* Animated flow connectors */
        .flow-connector {
          position: absolute;
          display: none;
          background: repeating-linear-gradient(90deg, transparent, transparent 4px, currentColor 4px, currentColor 8px);
          background-size: 12px 100%;
          animation: flowAnim 1.5s linear infinite;
          opacity: 0.8;
          height: 3px;
          border-radius: 2px;
        }
        .flow-connector.vertical {
          width: 3px; height: auto;
          background: repeating-linear-gradient(0deg, transparent, transparent 4px, currentColor 4px, currentColor 8px);
          background-size: 100% 12px;
          animation: flowAnimV 1.5s linear infinite;
        }
        @keyframes flowAnim { 0% { background-position: 0 0; } 100% { background-position: 12px 0; } }
        @keyframes flowAnimV { 0% { background-position: 0 0; } 100% { background-position: 0 12px; } }

        .flow-solar { color: #f7b731; }
        .flow-grid-in { color: #e74c3c; }
        .flow-grid-out { color: #2ecc71; }
        .flow-batt-in { color: #00d4ff; }
        .flow-batt-out { color: #3498db; }
        .flow-load { color: #2ecc71; }

        /* Flow lines using SVG overlay */
        .flow-svg {
          position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          pointer-events: none; z-index: 1;
        }
        .fl-line { fill: none; stroke: rgba(255,255,255,0.06); stroke-width: 2; }
        .fl-dots {
          display: none; fill: none; stroke-width: 2.5; stroke-linecap: round;
          stroke-dasharray: 6 12;
          animation: flDots 2s linear infinite;
        }
        .fl-dots.solar { stroke: #f7b731; filter: drop-shadow(0 0 3px rgba(247,183,49,0.5)); }
        .fl-dots.grid-in { stroke: #e74c3c; filter: drop-shadow(0 0 3px rgba(231,76,60,0.5)); }
        .fl-dots.grid-out { stroke: #2ecc71; filter: drop-shadow(0 0 3px rgba(46,204,113,0.5)); }
        .fl-dots.batt-charge { stroke: #00d4ff; filter: drop-shadow(0 0 3px rgba(0,212,255,0.5)); }
        .fl-dots.batt-discharge { stroke: #3498db; filter: drop-shadow(0 0 3px rgba(52,152,219,0.5)); }
        .fl-dots.load-flow { stroke: #2ecc71; filter: drop-shadow(0 0 3px rgba(46,204,113,0.5)); }
        @keyframes flDots { 0% { stroke-dashoffset: 18; } 100% { stroke-dashoffset: 0; } }

        /* SOC bar */
        .soc-bar { width: 100%; height: 8px; background: rgba(255,255,255,0.08); border-radius: 4px; overflow: hidden; margin: 6px 0; }
        .soc-fill { height: 100%; border-radius: 4px; transition: width 1s, background 1s; }

        /* Summary bar */
        .summary-bar {
          display: flex; gap: 16px; flex-wrap: wrap; align-items: center; justify-content: center;
          padding: 12px 16px; margin-top: 8px;
          background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 12px;
        }
        .summary-item { text-align: center; }
        .summary-item .si-label { font-size: 9px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
        .summary-item .si-val { font-size: 16px; font-weight: 700; color: #fff; }

        /* ── Cards (for other tabs) ── */
        .grid-cards { display: grid; gap: 12px; }
        .gc-2 { grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
        .gc-3 { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }
        .card {
          background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 14px; padding: 16px;
        }
        .card:hover { border-color: rgba(0,212,255,0.15); }
        .card-title {
          font-size: 11px; font-weight: 600; text-transform: uppercase;
          letter-spacing: 1px; color: #64748b; margin-bottom: 8px;
        }
        .dr { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
        .dr:last-child { border-bottom: none; }
        .dr .lb { color: #94a3b8; font-size: 12px; }
        .dr .vl { color: #fff; font-weight: 600; font-size: 12px; }

        .recommendation {
          background: linear-gradient(135deg, rgba(0,212,255,0.08), rgba(0,230,118,0.08));
          border: 1px solid rgba(0,212,255,0.15); border-radius: 10px;
          padding: 12px; font-size: 13px; line-height: 1.5;
        }
        .actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
        .action-btn {
          padding: 7px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.04); color: #e0e6ed; font-size: 11px;
          cursor: pointer; flex: 1; min-width: 70px; text-align: center;
        }
        .action-btn:hover { background: rgba(0,212,255,0.12); border-color: rgba(0,212,255,0.25); }

        @media (max-width: 800px) {
          .flow-grid { grid-template-columns: 1fr 1fr; grid-template-rows: auto; }
          .pv-area { grid-column: 1; grid-row: 1; }
          .home-area { grid-column: 2; grid-row: 1; }
          .inv-area { grid-column: 1 / 3; grid-row: 2; flex-direction: row; gap: 12px; }
          .batt-area { grid-column: 1; grid-row: 3; }
          .grid-area { grid-column: 2; grid-row: 3; }
          .summary-area { grid-column: 1 / 3; }
          .flow-svg { display: none; }
        }
        @media (max-width: 480px) {
          .flow-grid { grid-template-columns: 1fr; }
          .pv-area, .home-area, .inv-area, .batt-area, .grid-area, .summary-area { grid-column: 1; }
        }
      </style>

      <div class="panel-container">
        <div class="header">
          <div class="header-left">
            <span style="font-size:22px">⚡</span>
            <h1>Smarting HOME</h1>
          </div>
          <div class="header-right">
            <span class="badge free" id="v-license">FREE</span>
            <button class="fullscreen-btn" onclick="this.getRootNode().host._toggleFullscreen()">⊞ Pełny ekran</button>
          </div>
        </div>

        <div class="tabs">
          <button class="tab-btn active" data-tab="overview" onclick="this.getRootNode().host._switchTab('overview')">📊 Przegląd</button>
          <button class="tab-btn" data-tab="energy" onclick="this.getRootNode().host._switchTab('energy')">⚡ Energia</button>
          <button class="tab-btn" data-tab="tariff" onclick="this.getRootNode().host._switchTab('tariff')">💰 Taryfy & RCE</button>
          <button class="tab-btn" data-tab="battery" onclick="this.getRootNode().host._switchTab('battery')">🔋 Bateria</button>
          <button class="tab-btn" data-tab="hems" onclick="this.getRootNode().host._switchTab('hems')">🤖 HEMS</button>
        </div>

        <!-- ═══════ TAB: OVERVIEW ═══════ -->
        <div class="tab-content active" data-tab="overview">
          <div class="flow-grid">

            <!-- ☀️ PV AREA (top-left) -->
            <div class="pv-area">
              <div class="node" style="border-color: rgba(247,183,49,0.2); margin-bottom:8px">
                <div class="node-title">☀️ Produkcja PV</div>
                <div class="node-big" style="color:#f7b731" id="v-pv">— W</div>
                <div class="node-sub" id="v-pv-today">— kWh dziś</div>
              </div>
              <div class="pv-strings">
                <div class="pv-string" id="pv1-box">
                  <div class="pv-name">PV1</div>
                  <div class="pv-val" id="v-pv1-p">—</div>
                  <div class="pv-detail"><span id="v-pv1-v">— V</span> · <span id="v-pv1-a">— A</span></div>
                </div>
                <div class="pv-string" id="pv2-box">
                  <div class="pv-name">PV2</div>
                  <div class="pv-val" id="v-pv2-p">—</div>
                  <div class="pv-detail"><span id="v-pv2-v">— V</span> · <span id="v-pv2-a">— A</span></div>
                </div>
                <div class="pv-string" id="pv3-box" style="display:none">
                  <div class="pv-name">PV3</div>
                  <div class="pv-val" id="v-pv3-p">—</div>
                  <div class="pv-detail"><span id="v-pv3-v">—</span></div>
                </div>
                <div class="pv-string" id="pv4-box" style="display:none">
                  <div class="pv-name">PV4</div>
                  <div class="pv-val" id="v-pv4-p">—</div>
                  <div class="pv-detail"><span id="v-pv4-v">—</span></div>
                </div>
              </div>
            </div>

            <!-- ⚡ INVERTER (center) -->
            <div class="inv-area">
              <svg class="flow-svg" viewBox="0 0 400 500" preserveAspectRatio="none">
                <!-- PV → Inverter -->
                <path class="fl-line" d="M50,80 L200,200" />
                <path id="fl-pv-inv" class="fl-dots solar" d="M50,80 L200,200" />
                <!-- Inverter → Home -->
                <path class="fl-line" d="M200,200 L350,80" />
                <path id="fl-inv-load" class="fl-dots load-flow" d="M200,200 L350,80" />
                <!-- Grid → Inverter -->
                <path class="fl-line" d="M350,350 L200,240" />
                <path id="fl-grid-inv" class="fl-dots grid-in" d="M350,350 L200,240" />
                <!-- Inverter → Grid (export) -->
                <path id="fl-inv-grid" class="fl-dots grid-out" d="M200,240 L350,350" />
                <!-- Inverter → Battery -->
                <path class="fl-line" d="M200,280 L60,400" />
                <path id="fl-inv-batt" class="fl-dots batt-charge" d="M200,280 L60,400" />
                <!-- Battery → Inverter -->
                <path id="fl-batt-inv" class="fl-dots batt-discharge" d="M60,400 L200,280" />
              </svg>

              <div class="inv-box">
                <div class="inv-icon">⚡</div>
                <div class="inv-label">Falownik</div>
              </div>
              <div style="text-align:center; margin-top:6px">
                <div style="font-size:14px; font-weight:700; color:#fff" id="v-inv-p">— W</div>
                <div style="font-size:10px; color:#94a3b8" id="v-inv-t">—°C</div>
              </div>

              <div style="display:flex; gap:14px; margin-top:12px">
                <div class="summary-item">
                  <div class="si-label">Autarkia</div>
                  <div class="si-val" id="v-autarky">—%</div>
                </div>
                <div class="summary-item">
                  <div class="si-label">Autokonsumpcja</div>
                  <div class="si-val" id="v-selfcons">—%</div>
                </div>
              </div>
            </div>

            <!-- 🏠 HOME (top-right) -->
            <div class="home-area">
              <div class="node" style="border-color: rgba(46,204,113,0.2)">
                <div class="node-title">🏠 Zużycie</div>
                <div class="node-big" id="v-load">— W</div>
                <div class="node-detail" style="margin-top:8px">
                  <div id="v-load-l1">L1: — W</div>
                  <div id="v-load-l2">L2: — W</div>
                  <div id="v-load-l3">L3: — W</div>
                </div>
              </div>
            </div>

            <!-- 🔋 BATTERY (bottom-left) -->
            <div class="batt-area" style="margin-top:8px">
              <div class="node" style="border-color: rgba(0,212,255,0.2)">
                <div class="node-title">🔋 Bateria</div>
                <div style="display:flex; align-items:baseline; gap:8px">
                  <div class="node-big" id="v-soc" style="color:#2ecc71">—%</div>
                  <div style="font-size:16px; font-weight:700; color:#fff" id="v-batt">— W</div>
                </div>
                <div class="node-dir" id="v-batt-dir" style="color:#00d4ff">STANDBY</div>
                <div class="soc-bar"><div class="soc-fill" id="soc-fill" style="width:0%"></div></div>
                <div class="node-detail">
                  <div><span id="v-batt-v">— V</span> · <span id="v-batt-a">— A</span> · <span id="v-batt-temp">—°C</span></div>
                  <div id="v-batt-charge">↑ — kWh</div>
                  <div id="v-batt-discharge">↓ — kWh</div>
                </div>
              </div>
            </div>

            <!-- 🔌 GRID (bottom-right) -->
            <div class="grid-area" style="margin-top:8px">
              <div class="node" style="border-color: rgba(231,76,60,0.15)">
                <div class="node-title">🔌 Sieć</div>
                <div class="node-big" id="v-grid">— W</div>
                <div class="node-dir" id="v-grid-dir" style="color:#e74c3c"></div>
                <div class="node-detail" style="margin-top:6px">
                  <div><span id="v-grid-v1">— V</span> · <span id="v-grid-v2">— V</span> · <span id="v-grid-v3">— V</span></div>
                  <div id="v-grid-freq">— Hz</div>
                  <div style="margin-top:4px">
                    <span style="color:#e74c3c">↓</span> <span id="v-grid-import">— kWh</span>
                    &nbsp;
                    <span style="color:#2ecc71">↑</span> <span id="v-grid-export">— kWh</span>
                  </div>
                </div>
              </div>
            </div>

          </div>

          <!-- Summary bar -->
          <div class="summary-bar">
            <div class="summary-item">
              <div class="si-label">🌤️ Pogoda</div>
              <div class="si-val" id="v-weather">—</div>
            </div>
            <div class="summary-item">
              <div class="si-label">☁️ Zachmurzenie</div>
              <div class="si-val" id="v-clouds">—%</div>
            </div>
            <div class="summary-item">
              <div class="si-label">⚡ Nadwyżka PV</div>
              <div class="si-val" id="v-surplus">— W</div>
            </div>
            <div class="summary-item">
              <div class="si-label">📊 Saldo sieci</div>
              <div class="si-val" id="v-net-grid">— kWh</div>
            </div>
          </div>

          <!-- HEMS Recommendation -->
          <div class="card" style="margin-top:10px">
            <div class="card-title">💡 Rekomendacja HEMS</div>
            <div class="recommendation" id="v-hems-rec">Ładowanie danych...</div>
          </div>
        </div>

        <!-- ═══════ TAB: ENERGY ═══════ -->
        <div class="tab-content" data-tab="energy">
          <div class="grid-cards gc-3">
            <div class="card">
              <div class="card-title">⚡ Napięcie sieci</div>
              <div class="dr"><span class="lb">L1</span><span class="vl" id="v-e-v1">— V</span></div>
              <div class="dr"><span class="lb">L2</span><span class="vl" id="v-e-v2">— V</span></div>
              <div class="dr"><span class="lb">L3</span><span class="vl" id="v-e-v3">— V</span></div>
              <div class="dr"><span class="lb">Częstotliwość</span><span class="vl" id="v-e-freq">— Hz</span></div>
            </div>
            <div class="card">
              <div class="card-title">☀️ Prognoza PV</div>
              <div class="dr"><span class="lb">Dzisiaj</span><span class="vl" id="v-forecast-today">— kWh</span></div>
              <div class="dr"><span class="lb">Jutro</span><span class="vl" id="v-forecast-tomorrow">— kWh</span></div>
            </div>
            <div class="card">
              <div class="card-title">🔧 Szybkie akcje</div>
              <div class="actions" style="margin-top:0">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','force_charge')">🔋 Ładuj</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','force_discharge')">⚡ Rozładuj</button>
              </div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: TARIFF & RCE ═══════ -->
        <div class="tab-content" data-tab="tariff">
          <div class="grid-cards gc-2">
            <div class="card">
              <div class="card-title">⏰ Taryfa G13</div>
              <div class="dr"><span class="lb">Strefa</span><span class="vl" id="v-g13-zone">—</span></div>
              <div class="dr"><span class="lb">Cena zakupu</span><span class="vl" id="v-g13-price">— PLN</span></div>
            </div>
            <div class="card">
              <div class="card-title">📈 RCE PSE</div>
              <div class="dr"><span class="lb">Cena sprzedaży</span><span class="vl" id="v-rce-price">— PLN</span></div>
              <div class="dr"><span class="lb">Trend</span><span class="vl" id="v-rce-trend">—</span></div>
              <div class="dr"><span class="lb">Średnia dziś</span><span class="vl" id="v-rce-avg">— PLN</span></div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: BATTERY ═══════ -->
        <div class="tab-content" data-tab="battery">
          <div class="grid-cards gc-2">
            <div class="card">
              <div class="card-title">🔋 Stan baterii</div>
              <div class="dr"><span class="lb">Dostępna energia</span><span class="vl" id="v-battery-energy">— kWh</span></div>
              <div class="dr"><span class="lb">Czas pracy</span><span class="vl" id="v-battery-runtime">—</span></div>
            </div>
            <div class="card">
              <div class="card-title">💰 Arbitraż</div>
              <div class="dr"><span class="lb">Potencjał</span><span class="vl" id="v-arbitrage">— PLN</span></div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: HEMS ═══════ -->
        <div class="tab-content" data-tab="hems">
          <div class="grid-cards gc-2">
            <div class="card">
              <div class="card-title">🤖 Sterowanie HEMS</div>
              <div class="actions" style="margin-top:0">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'auto'})">🤖 Auto</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'sell'})">💰 Sprzedaj</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'charge'})">🔋 Ładuj</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'peak_save'})">🏠 Szczyt</button>
              </div>
            </div>
            <div class="card">
              <div class="card-title">💡 Rekomendacja AI</div>
              <div class="recommendation" id="v-hems-rec-2">Ładowanie danych...</div>
            </div>
          </div>
        </div>
      </div>
    `;

    if (this._hass) this._updateAll();
    this._interval = setInterval(() => { if (this._hass) this._updateAll(); }, 5000);
  }
}

customElements.define("smartinghome-panel", SmartingHomePanel);
