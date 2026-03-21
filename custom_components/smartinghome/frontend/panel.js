/**
 * Smarting HOME — Custom Panel for Home Assistant
 * Animated Power Flow Dashboard
 * © 2026 Smarting HOME by GregECAT
 */

class SmartingHomePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._activeTab = "overview";
    this._isFullscreen = false;
    this._animFrame = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (this.shadowRoot.querySelector(".panel-container")) {
      this._updateAll();
    }
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

  disconnectedCallback() {
    if (this._animFrame) cancelAnimationFrame(this._animFrame);
  }

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

  /* ── Helpers ─────────────────────────────── */
  // Sensor map: read from diagnostic entity, fallback to defaults
  static DEFAULT_MAP = {
    pv_power: "sensor.pv_power",
    load_power: "sensor.load",
    grid_power: "sensor.meter_active_power_total",
    battery_power: "sensor.battery_power",
    battery_soc: "sensor.battery_state_of_charge",
    pv_today: "sensor.today_s_pv_generation",
    grid_import_today: "sensor.grid_import_daily",
    grid_export_today: "sensor.grid_export_daily",
    battery_charge_today: "sensor.today_battery_charge",
    battery_discharge_today: "sensor.today_battery_discharge",
    voltage_l1: "sensor.on_grid_l1_voltage",
    voltage_l2: "sensor.on_grid_l2_voltage",
    voltage_l3: "sensor.on_grid_l3_voltage",
  };
  _m(logicalName) {
    const map = this._hass?.states["sensor.smartinghome_sensor_map"]?.attributes;
    return map?.[logicalName] || SmartingHomePanel.DEFAULT_MAP[logicalName] || "";
  }
  _s(id) {
    if (!this._hass?.states[id]) return null;
    return this._hass.states[id].state;
  }
  _n(id, d = 1) {
    const v = parseFloat(this._s(id));
    return isNaN(v) ? null : v;
  }
  _f(id, d = 1) {
    const v = this._n(id);
    return v === null ? "—" : v.toFixed(d);
  }
  _callService(domain, service, data = {}) {
    if (this._hass) this._hass.callService(domain, service, data);
  }

  /* ── Update all values ──────────────────── */
  _updateAll() {
    this._updatePowerFlow();
    this._updateStats();
  }

  _updatePowerFlow() {
    const pv = this._n(this._m("pv_power")) || 0;
    const load = this._n(this._m("load_power")) || 0;
    const grid = this._n(this._m("grid_power")) || 0;
    const batt = this._n(this._m("battery_power")) || 0;
    const soc = this._n(this._m("battery_soc")) || 0;

    // Update power values
    this._setText("pf-pv-val", `${Math.round(pv)} W`);
    this._setText("pf-load-val", `${Math.round(load)} W`);
    this._setText("pf-grid-val", `${Math.abs(Math.round(grid))} W`);
    this._setText("pf-grid-dir", grid > 0 ? "Import ↓" : grid < 0 ? "Eksport ↑" : "");
    this._setText("pf-batt-val", `${Math.abs(Math.round(batt))} W`);
    this._setText("pf-batt-dir", batt > 0 ? "Ładowanie ↑" : batt < 0 ? "Rozładowanie ↓" : "");
    this._setText("pf-soc-val", `${Math.round(soc)}%`);

    // SOC color
    const socEl = this.shadowRoot.getElementById("pf-soc-val");
    if (socEl) socEl.style.color = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";

    // Animate flow lines
    this._animateFlow("flow-pv-home", pv > 10, pv);
    this._animateFlow("flow-pv-batt", batt > 10 && pv > 10, Math.min(pv, batt));
    this._animateFlow("flow-grid-home", grid > 10, grid, false);
    this._animateFlow("flow-home-grid", grid < -10, Math.abs(grid), true);
    this._animateFlow("flow-batt-home", batt < -10, Math.abs(batt), true);
    this._animateFlow("flow-home-batt", batt > 10, batt, false);
  }

  _animateFlow(id, active, power, reverse = false) {
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    if (active) {
      const speed = Math.max(0.8, 4 - (Math.min(power, 5000) / 5000) * 3.2);
      el.style.display = "block";
      el.style.animationDuration = `${speed}s`;
      el.style.animationDirection = reverse ? "reverse" : "normal";
    } else {
      el.style.display = "none";
    }
  }

  _updateStats() {
    const updates = {
      "val-pv-today": `${this._f(this._m("pv_today"))} kWh`,
      "val-g13-zone": this._s("sensor.smartinghome_g13_current_zone") || "—",
      "val-g13-price": `${this._f("sensor.smartinghome_g13_buy_price", 2)} PLN`,
      "val-rce-price": `${this._f("sensor.smartinghome_rce_sell_price", 2)} PLN`,
      "val-rce-trend": this._s("sensor.smartinghome_rce_price_trend") || "—",
      "val-rce-avg": `${this._f("sensor.smartinghome_rce_average_today", 2)} PLN`,
      "val-rce-min": `${this._f("sensor.smartinghome_rce_min_today", 2)} PLN`,
      "val-rce-max": `${this._f("sensor.smartinghome_rce_max_today", 2)} PLN`,
      "val-autarky": `${this._f("sensor.smartinghome_autarky_today", 0)}%`,
      "val-self-cons": `${this._f("sensor.smartinghome_self_consumption_today", 0)}%`,
      "val-surplus": `${this._f("sensor.smartinghome_pv_surplus_power", 0)} W`,
      "val-net-grid": `${this._f("sensor.smartinghome_net_grid_today")} kWh`,
      "val-hems-rec": this._s("sensor.smartinghome_hems_recommendation") || "Brak danych",
      "val-system-status": this._s("sensor.smartinghome_system_status") || "—",
      "val-license": this._s("sensor.smartinghome_license_tier") || "FREE",
      "val-battery-energy": `${this._f("sensor.smartinghome_battery_energy_available")} kWh`,
      "val-battery-runtime": this._s("sensor.smartinghome_battery_runtime") || "—",
      "val-forecast-today": `${this._f("sensor.smartinghome_pv_forecast_today_total")} kWh`,
      "val-forecast-tomorrow": `${this._f("sensor.smartinghome_pv_forecast_tomorrow_total")} kWh`,
      "val-voltage-l1": `${this._f(this._m("voltage_l1"))} V`,
      "val-voltage-l2": `${this._f(this._m("voltage_l2"))} V`,
      "val-voltage-l3": `${this._f(this._m("voltage_l3"))} V`,
      "val-arbitrage": `${this._f("sensor.smartinghome_battery_arbitrage_potential")} PLN`,
      "val-grid-import": `${this._f(this._m("grid_import_today"))} kWh`,
      "val-grid-export": `${this._f(this._m("grid_export_today"))} kWh`,
      "val-batt-charge": `${this._f(this._m("battery_charge_today"))} kWh`,
      "val-batt-discharge": `${this._f(this._m("battery_discharge_today"))} kWh`,
    };
    for (const [id, v] of Object.entries(updates)) this._setText(id, v);

    // SOC bars — use mapped battery_soc
    const soc = this._n(this._m("battery_soc")) || 0;
    ["soc-bar-fill", "soc-bar-fill-2"].forEach(id => {
      const el = this.shadowRoot.getElementById(id);
      if (el) {
        el.style.width = `${Math.min(100, Math.max(0, soc))}%`;
        el.style.background = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";
      }
    });

    // License badge
    const badge = this.shadowRoot.getElementById("val-license-badge");
    if (badge) {
      const tier = (this._s("sensor.smartinghome_license_tier") || "FREE").toUpperCase();
      badge.textContent = tier;
      badge.className = `status-badge ${tier === "PRO" ? "pro" : "free"}`;
    }
  }

  _setText(id, val) {
    const el = this.shadowRoot.getElementById(id);
    if (el) el.textContent = val;
  }

  /* ── Render ─────────────────────────────── */
  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        * { box-sizing: border-box; margin: 0; padding: 0; }

        .panel-container {
          min-height: 100vh;
          background: linear-gradient(135deg, #0a1628 0%, #1a2744 50%, #0d1f3c 100%);
          color: #e0e6ed;
          font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
          overflow-y: auto;
        }

        /* ── Header ─────────────────────── */
        .header {
          display: flex; align-items: center; justify-content: space-between;
          padding: 12px 24px;
          background: rgba(255,255,255,0.03);
          border-bottom: 1px solid rgba(255,255,255,0.06);
          backdrop-filter: blur(20px);
          position: sticky; top: 0; z-index: 100;
        }
        .header-left { display: flex; align-items: center; gap: 12px; }
        .logo { font-size: 24px; }
        .header h1 {
          font-size: 18px; font-weight: 600;
          background: linear-gradient(135deg, #00d4ff, #00e676);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .header-right { display: flex; align-items: center; gap: 10px; }
        .status-badge {
          padding: 3px 10px; border-radius: 20px; font-size: 10px;
          font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
        }
        .status-badge.free { background: rgba(52,152,219,0.2); color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
        .status-badge.pro { background: rgba(46,204,113,0.2); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
        .fullscreen-btn {
          padding: 5px 12px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.15);
          background: rgba(255,255,255,0.05); color: #a0aec0; font-size: 11px;
          cursor: pointer; transition: all 0.2s;
        }
        .fullscreen-btn:hover { background: rgba(255,255,255,0.1); color: #fff; }

        /* ── Tabs ───────────────────────── */
        .tabs { display: flex; gap: 2px; padding: 6px 24px; background: rgba(0,0,0,0.2); }
        .tab-btn {
          padding: 8px 16px; border: none; background: transparent;
          color: #64748b; font-size: 12px; font-weight: 500;
          cursor: pointer; border-radius: 8px; transition: all 0.2s; white-space: nowrap;
        }
        .tab-btn:hover { background: rgba(255,255,255,0.05); color: #a0aec0; }
        .tab-btn.active {
          background: rgba(0,212,255,0.1); color: #00d4ff;
          box-shadow: 0 0 20px rgba(0,212,255,0.1);
        }

        /* ── Tab Content ────────────────── */
        .tab-content { display: none; padding: 16px 24px; }
        .tab-content.active { display: block; }

        /* ── Cards ──────────────────────── */
        .grid { display: grid; gap: 14px; }
        .grid-2 { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
        .grid-3 { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
        .grid-4 { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
        .card {
          background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
          border-radius: 16px; padding: 18px; backdrop-filter: blur(10px); transition: all 0.3s;
        }
        .card:hover { border-color: rgba(0,212,255,0.2); box-shadow: 0 4px 30px rgba(0,212,255,0.05); }
        .card-title {
          font-size: 11px; font-weight: 600; text-transform: uppercase;
          letter-spacing: 1px; color: #64748b; margin-bottom: 10px;
        }
        .data-row {
          display: flex; justify-content: space-between; align-items: center;
          padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .data-row:last-child { border-bottom: none; }
        .data-row .label { color: #94a3b8; font-size: 13px; }
        .data-row .value { color: #fff; font-weight: 600; font-size: 13px; }
        .soc-bar { width: 100%; height: 6px; background: rgba(255,255,255,0.1); border-radius: 4px; overflow: hidden; margin-top: 6px; }
        .soc-bar-fill { height: 100%; border-radius: 4px; transition: width 1s ease, background 1s ease; }
        .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
        .action-btn {
          padding: 8px 14px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.05); color: #e0e6ed; font-size: 12px;
          cursor: pointer; transition: all 0.2s; flex: 1; min-width: 80px; text-align: center;
        }
        .action-btn:hover { background: rgba(0,212,255,0.15); border-color: rgba(0,212,255,0.3); }
        .action-btn.charge { border-color: rgba(46,204,113,0.3); }
        .action-btn.charge:hover { background: rgba(46,204,113,0.2); }
        .action-btn.discharge { border-color: rgba(231,76,60,0.3); }
        .action-btn.discharge:hover { background: rgba(231,76,60,0.2); }
        .recommendation {
          background: linear-gradient(135deg, rgba(0,212,255,0.08), rgba(0,230,118,0.08));
          border: 1px solid rgba(0,212,255,0.15); border-radius: 12px;
          padding: 14px; font-size: 13px; line-height: 1.6;
        }

        /* ═══ POWER FLOW DIAGRAM ═══ */
        .power-flow-container {
          position: relative;
          width: 100%;
          max-width: 680px;
          margin: 0 auto 16px;
          aspect-ratio: 4 / 3;
        }
        .pf-svg { width: 100%; height: 100%; }

        /* Flow line base */
        .flow-line {
          fill: none; stroke: rgba(255,255,255,0.08); stroke-width: 2.5;
          stroke-linecap: round;
        }

        /* Animated dots */
        .flow-dots {
          display: none;
          fill: none; stroke-width: 3; stroke-linecap: round;
          stroke-dasharray: 8 16;
          animation: flowDots 2s linear infinite;
        }
        .flow-dots.solar { stroke: #f7b731; filter: drop-shadow(0 0 4px rgba(247,183,49,0.6)); }
        .flow-dots.grid-in { stroke: #e74c3c; filter: drop-shadow(0 0 4px rgba(231,76,60,0.6)); }
        .flow-dots.grid-out { stroke: #2ecc71; filter: drop-shadow(0 0 4px rgba(46,204,113,0.6)); }
        .flow-dots.batt-in { stroke: #00d4ff; filter: drop-shadow(0 0 4px rgba(0,212,255,0.6)); }
        .flow-dots.batt-out { stroke: #3498db; filter: drop-shadow(0 0 4px rgba(52,152,219,0.6)); }

        @keyframes flowDots {
          0% { stroke-dashoffset: 24; }
          100% { stroke-dashoffset: 0; }
        }

        /* Power flow node boxes */
        .pf-node {
          text-anchor: middle;
        }
        .pf-node-bg {
          rx: 14; ry: 14;
          fill: rgba(255,255,255,0.04);
          stroke: rgba(255,255,255,0.1);
          stroke-width: 1.5;
          transition: stroke 0.3s;
        }
        .pf-node-bg.active-solar { stroke: rgba(247,183,49,0.5); }
        .pf-node-bg.active-grid-import { stroke: rgba(231,76,60,0.5); }
        .pf-node-bg.active-grid-export { stroke: rgba(46,204,113,0.5); }
        .pf-node-bg.active-batt-charge { stroke: rgba(0,212,255,0.5); }
        .pf-node-bg.active-batt-discharge { stroke: rgba(52,152,219,0.5); }
        .pf-icon { font-size: 28px; }
        .pf-power { fill: #fff; font-size: 18px; font-weight: 700; }
        .pf-label { fill: #64748b; font-size: 11px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
        .pf-sub { fill: #94a3b8; font-size: 11px; }

        /* ── Responsive ─────────────────── */
        @media (max-width: 768px) {
          .header { padding: 10px 16px; }
          .tabs { padding: 4px 16px; overflow-x: auto; }
          .tab-content { padding: 12px 16px; }
          .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr 1fr; }
          .power-flow-container { max-width: 100%; }
        }
        @media (max-width: 480px) {
          .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
        }
      </style>

      <div class="panel-container">
        <div class="header">
          <div class="header-left">
            <span class="logo">⚡</span>
            <h1>Smarting HOME</h1>
          </div>
          <div class="header-right">
            <span class="status-badge free" id="val-license-badge">FREE</span>
            <span class="status-badge" style="background:rgba(46,204,113,0.15);color:#2ecc71;border:1px solid rgba(46,204,113,0.2);" id="val-system-status">—</span>
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

          <!-- Animated Power Flow Diagram -->
          <div class="power-flow-container">
            <svg class="pf-svg" viewBox="0 0 680 480" xmlns="http://www.w3.org/2000/svg">

              <!-- Flow paths (background lines) -->
              <path class="flow-line" d="M340,110 L340,200" /><!-- PV → Home (vertical) -->
              <path class="flow-line" d="M340,110 L170,340" /><!-- PV → Battery -->
              <path class="flow-line" d="M130,230 L280,230" /><!-- Grid → Home -->
              <path class="flow-line" d="M400,230 L560,230" /><!-- Home → Grid (export) -->
              <path class="flow-line" d="M340,280 L170,370" /><!-- Home → Battery -->
              <path class="flow-line" d="M170,340 L280,240" /><!-- Battery → Home -->

              <!-- Animated flow dots -->
              <path id="flow-pv-home" class="flow-dots solar" d="M340,100 L340,195" />
              <path id="flow-pv-batt" class="flow-dots solar" d="M310,110 C280,200 210,280 170,340" />
              <path id="flow-grid-home" class="flow-dots grid-in" d="M130,230 L280,230" />
              <path id="flow-home-grid" class="flow-dots grid-out" d="M400,230 L550,230" />
              <path id="flow-batt-home" class="flow-dots batt-out" d="M210,350 C250,300 280,260 310,240" />
              <path id="flow-home-batt" class="flow-dots batt-in" d="M310,260 C280,300 240,330 200,360" />

              <!-- ☀️ SOLAR node (top center) -->
              <g class="pf-node" transform="translate(340, 55)">
                <rect class="pf-node-bg" id="pf-solar-bg" x="-70" y="-42" width="140" height="84" />
                <text class="pf-icon" y="-8">☀️</text>
                <text class="pf-power" id="pf-pv-val" y="18">0 W</text>
                <text class="pf-label" y="34">Produkcja PV</text>
              </g>

              <!-- 🏠 HOME node (center) -->
              <g class="pf-node" transform="translate(340, 230)">
                <rect class="pf-node-bg" x="-60" y="-40" width="120" height="80" />
                <text class="pf-icon" y="-10">🏠</text>
                <text class="pf-power" id="pf-load-val" y="14">0 W</text>
                <text class="pf-label" y="30">Zużycie</text>
              </g>

              <!-- 🔌 GRID node (left) -->
              <g class="pf-node" transform="translate(90, 230)">
                <rect class="pf-node-bg" id="pf-grid-bg" x="-70" y="-42" width="140" height="84" />
                <text class="pf-icon" y="-10">🔌</text>
                <text class="pf-power" id="pf-grid-val" y="14">0 W</text>
                <text class="pf-sub" id="pf-grid-dir" y="30"></text>
              </g>

              <!-- 🔋 BATTERY node (bottom center) -->
              <g class="pf-node" transform="translate(170, 400)">
                <rect class="pf-node-bg" id="pf-batt-bg" x="-80" y="-44" width="160" height="88" />
                <text class="pf-icon" y="-14">🔋</text>
                <text class="pf-power" id="pf-batt-val" y="10">0 W</text>
                <text class="pf-sub" id="pf-batt-dir" y="26"></text>
                <text id="pf-soc-val" y="40" style="fill:#2ecc71;font-size:14px;font-weight:700;">0%</text>
              </g>

              <!-- Daily stats (right side) -->
              <g transform="translate(530, 140)">
                <rect class="pf-node-bg" x="-80" y="-45" width="160" height="250" />
                <text class="pf-label" y="-24">📊 Dziś</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="0">☀️ PV</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="0" text-anchor="end" id="val-pv-today">—</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="28">↓ Import</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="28" text-anchor="end" id="val-grid-import">—</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="56">↑ Eksport</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="56" text-anchor="end" id="val-grid-export">—</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="84">🔋 Ładowanie</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="84" text-anchor="end" id="val-batt-charge">—</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="112">⚡ Rozładowanie</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="112" text-anchor="end" id="val-batt-discharge">—</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="148">🛡️ Autarkia</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="148" text-anchor="end" id="val-autarky">—</text>
                <text style="fill:#94a3b8;font-size:11px" x="-65" y="176">♻️ Autokonsumpcja</text>
                <text style="fill:#fff;font-size:12px;font-weight:600" x="65" y="176" text-anchor="end" id="val-self-cons">—</text>
              </g>
            </svg>
          </div>

          <!-- HEMS Recommendation -->
          <div class="card" style="margin-bottom:14px">
            <div class="card-title">💡 Rekomendacja HEMS</div>
            <div class="recommendation" id="val-hems-rec">Ładowanie danych...</div>
          </div>
        </div>

        <!-- ═══════ TAB: ENERGY ═══════ -->
        <div class="tab-content" data-tab="energy">
          <div class="grid grid-3" style="margin-bottom:14px">
            <div class="card">
              <div class="card-title">⚡ Napięcie sieci</div>
              <div class="data-row"><span class="label">L1</span><span class="value" id="val-voltage-l1">— V</span></div>
              <div class="data-row"><span class="label">L2</span><span class="value" id="val-voltage-l2">— V</span></div>
              <div class="data-row"><span class="label">L3</span><span class="value" id="val-voltage-l3">— V</span></div>
            </div>
            <div class="card">
              <div class="card-title">☀️ Prognoza PV</div>
              <div class="data-row"><span class="label">Dzisiaj</span><span class="value" id="val-forecast-today">— kWh</span></div>
              <div class="data-row"><span class="label">Jutro</span><span class="value" id="val-forecast-tomorrow">— kWh</span></div>
              <div class="data-row"><span class="label">Nadwyżka</span><span class="value" id="val-surplus">— W</span></div>
              <div class="data-row"><span class="label">Saldo sieci</span><span class="value" id="val-net-grid">— kWh</span></div>
            </div>
            <div class="card">
              <div class="card-title">🔧 Szybkie akcje</div>
              <div class="actions">
                <button class="action-btn charge" onclick="this.getRootNode().host._callService('smartinghome','force_charge')">🔋 Ładuj</button>
                <button class="action-btn discharge" onclick="this.getRootNode().host._callService('smartinghome','force_discharge')">⚡ Rozładuj</button>
              </div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: TARIFF & RCE ═══════ -->
        <div class="tab-content" data-tab="tariff">
          <div class="grid grid-2">
            <div class="card">
              <div class="card-title">⏰ Taryfa G13</div>
              <div class="data-row"><span class="label">Aktualna strefa</span><span class="value" id="val-g13-zone">—</span></div>
              <div class="data-row"><span class="label">Cena zakupu</span><span class="value" id="val-g13-price">— PLN</span></div>
            </div>
            <div class="card">
              <div class="card-title">📈 RCE PSE</div>
              <div class="data-row"><span class="label">Cena sprzedaży</span><span class="value" id="val-rce-price">— PLN</span></div>
              <div class="data-row"><span class="label">Trend</span><span class="value" id="val-rce-trend">—</span></div>
              <div class="data-row"><span class="label">Średnia dziś</span><span class="value" id="val-rce-avg">— PLN</span></div>
              <div class="data-row"><span class="label">Min dziś</span><span class="value" id="val-rce-min">— PLN</span></div>
              <div class="data-row"><span class="label">Max dziś</span><span class="value" id="val-rce-max">— PLN</span></div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: BATTERY ═══════ -->
        <div class="tab-content" data-tab="battery">
          <div class="grid grid-2">
            <div class="card">
              <div class="card-title">🔋 Stan baterii</div>
              <div style="text-align:center;margin:12px 0">
                <span style="font-size:42px;font-weight:800;color:#fff" id="pf-soc-big">—%</span>
              </div>
              <div class="soc-bar" style="height:10px"><div class="soc-bar-fill" id="soc-bar-fill" style="width:0%"></div></div>
              <div style="margin-top:12px">
                <div class="data-row"><span class="label">Dostępna energia</span><span class="value" id="val-battery-energy">— kWh</span></div>
                <div class="data-row"><span class="label">Czas pracy</span><span class="value" id="val-battery-runtime">—</span></div>
                <div class="data-row"><span class="label">Ładowanie dziś</span><span class="value" id="val-batt-charge-2">— kWh</span></div>
                <div class="data-row"><span class="label">Rozładowanie dziś</span><span class="value" id="val-batt-discharge-2">— kWh</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">💰 Arbitraż</div>
              <div class="data-row"><span class="label">Potencjał arbitrażu</span><span class="value" id="val-arbitrage">— PLN</span></div>
              <div style="margin-top:12px;padding:12px;background:rgba(46,204,113,0.1);border-radius:8px;font-size:11px;color:#94a3b8">
                <strong style="color:#2ecc71">Kalkulacja:</strong><br>
                Ładowanie nocne: 10.2 kWh × 0.63 PLN = 6.43 PLN<br>
                Sprzedaż w szczycie: 10.2 kWh × 1.50 PLN = 15.30 PLN<br>
                <strong style="color:#2ecc71">Zysk/cykl: ~8.87 PLN</strong>
              </div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: HEMS ═══════ -->
        <div class="tab-content" data-tab="hems">
          <div class="grid grid-2">
            <div class="card">
              <div class="card-title">🤖 Sterowanie HEMS</div>
              <div class="actions" style="margin-top:0">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'auto'})">🤖 Auto</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'sell'})">💰 Sprzedaj</button>
                <button class="action-btn charge" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'charge'})">🔋 Ładuj</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'peak_save'})">🏠 Szczyt</button>
              </div>
              <div style="margin-top:14px">
                <div class="data-row"><span class="label">Status systemu</span><span class="value" id="val-system-status-2">—</span></div>
                <div class="data-row"><span class="label">Licencja</span><span class="value" id="val-license">—</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">💡 Rekomendacja AI</div>
              <div class="recommendation" id="val-hems-rec-2">Ładowanie danych...</div>
              <div class="actions" style="margin-top:10px">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','ask_ai_advisor',{question:'Co powinienem teraz zoptymalizować?'})">🤖 Zapytaj AI</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','generate_report')">📊 Raport</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    if (this._hass) this._updateAll();
    setInterval(() => { if (this._hass) this._updateAll(); }, 5000);
  }
}

customElements.define("smartinghome-panel", SmartingHomePanel);
