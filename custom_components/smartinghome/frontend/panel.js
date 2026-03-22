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
    ["fullscreenchange","webkitfullscreenchange","mozfullscreenchange","MSFullscreenChange"].forEach(ev => {
      document.addEventListener(ev, () => {
        this._isFullscreen = !!(document.fullscreenElement||document.webkitFullscreenElement||document.mozFullScreenElement||document.msFullscreenElement);
        const btn = this.shadowRoot.querySelector(".fullscreen-btn");
        if (btn) btn.textContent = this._isFullscreen ? "⊡ Zamknij" : "⊞ Pełny ekran";
      });
    });
  }
  disconnectedCallback() { if (this._interval) clearInterval(this._interval); }

  _toggleFullscreen() {
    const c = this.shadowRoot.querySelector(".panel-container");
    const isFS = document.fullscreenElement||document.webkitFullscreenElement||document.mozFullScreenElement||document.msFullscreenElement;
    if (!isFS) {
      const rfs = c.requestFullscreen||c.webkitRequestFullscreen||c.mozRequestFullScreen||c.msRequestFullscreen;
      if (rfs) rfs.call(c).catch(()=>{});
      else { c.classList.toggle("css-fullscreen", true); this._isFullscreen = true; const btn = this.shadowRoot.querySelector(".fullscreen-btn"); if(btn) btn.textContent="⊡ Zamknij"; }
    } else {
      const efs = document.exitFullscreen||document.webkitExitFullscreen||document.mozCancelFullScreen||document.msExitFullscreen;
      if (efs) efs.call(document).catch(()=>{});
      else { c.classList.remove("css-fullscreen"); this._isFullscreen = false; const btn = this.shadowRoot.querySelector(".fullscreen-btn"); if(btn) btn.textContent="⊞ Pełny ekran"; }
    }
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
    inverter_model:"sensor.inverter_model",
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

  _saveApiKeys() {
    const gemini = this.shadowRoot.getElementById("inp-gemini-key")?.value || "";
    const anthropic = this.shadowRoot.getElementById("inp-anthropic-key")?.value || "";
    if (this._hass) {
      this._hass.callService("smartinghome", "save_settings", {
        gemini_api_key: gemini,
        anthropic_api_key: anthropic,
      });
      const st = this.shadowRoot.getElementById("v-save-status");
      if (st) { st.textContent = "✅ Klucze zapisane pomyślnie!"; setTimeout(() => { st.textContent = ""; }, 4000); }
    }
  }

  async _uploadInverterImage(file) {
    if (!file) return;
    const st = this.shadowRoot.getElementById("v-upload-status");
    if (st) st.textContent = "⏳ Wgrywanie...";
    try {
      const reader = new FileReader();
      reader.onload = () => {
        if (this._hass) {
          this._hass.callService("smartinghome", "upload_inverter_image", {
            filename: file.name,
            data: reader.result.split(",")[1],
          });
          if (st) { st.textContent = "✅ Zdjęcie wgrane! Odśwież panel."; setTimeout(() => { st.textContent = ""; }, 6000); }
        }
      };
      reader.readAsDataURL(file);
    } catch (e) {
      if (st) st.textContent = "❌ Błąd wgrywania: " + e.message;
    }
  }

  _updateKeyStatus() {
    const geminiStatus = this._s("sensor.smartinghome_gemini_key_status");
    const anthropicStatus = this._s("sensor.smartinghome_anthropic_key_status");
    const geminiInd = this.shadowRoot.getElementById("key-status-gemini");
    const anthropicInd = this.shadowRoot.getElementById("key-status-anthropic");
    if (geminiInd) {
      if (geminiStatus === "valid") { geminiInd.textContent = "✅ Klucz zweryfikowany"; geminiInd.style.color = "#2ecc71"; }
      else if (geminiStatus === "invalid") { geminiInd.textContent = "❌ Klucz nieprawidłowy"; geminiInd.style.color = "#e74c3c"; }
      else if (geminiStatus === "saved") { geminiInd.textContent = "💾 Klucz zapisany (niesprawdzony)"; geminiInd.style.color = "#f39c12"; }
      else { geminiInd.textContent = "— Brak klucza"; geminiInd.style.color = "#64748b"; }
    }
    if (anthropicInd) {
      if (anthropicStatus === "valid") { anthropicInd.textContent = "✅ Klucz zweryfikowany"; anthropicInd.style.color = "#2ecc71"; }
      else if (anthropicStatus === "invalid") { anthropicInd.textContent = "❌ Klucz nieprawidłowy"; anthropicInd.style.color = "#e74c3c"; }
      else if (anthropicStatus === "saved") { anthropicInd.textContent = "💾 Klucz zapisany (niesprawdzony)"; anthropicInd.style.color = "#f39c12"; }
      else { anthropicInd.textContent = "— Brak klucza"; anthropicInd.style.color = "#64748b"; }
    }
  }

  _testApiKey(provider) {
    const btn = this.shadowRoot.getElementById(`test-btn-${provider}`);
    if (btn) { btn.textContent = "⏳ Testowanie..."; btn.disabled = true; }
    if (this._hass) {
      this._hass.callService("smartinghome", "test_api_key", { provider });
      setTimeout(() => {
        this._updateKeyStatus();
        if (btn) { btn.textContent = "🧪 Testuj"; btn.disabled = false; }
      }, 5000);
    }
  }

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
    this._flow("fl-inv-grid", grid < -10, Math.abs(grid));
    this._flow("fl-inv-batt", batt > 10, batt);
    this._flow("fl-batt-inv", batt < -10, Math.abs(batt));

    // Dynamic Inverter Image Logic
    let imgName = "goodwe"; // domyślnie GoodWe
    const invModel = (this._s(this._m("inverter_model")) || "").toLowerCase();
    const userChoice = (this._s("input_select.smartinghome_inverter_model") || "").toLowerCase();

    if (invModel.includes("deye") || userChoice.includes("deye")) {
      imgName = "deye";
    } else if (invModel.includes("goodwe") || userChoice.includes("goodwe")) {
      imgName = "goodwe";
    }

    const imgEl = this.shadowRoot.getElementById("v-inv-img");
    if (imgEl && imgEl.getAttribute("data-model") !== imgName) {
      // Re-enable image if it was hidden by error previously
      imgEl.style.display = "block";
      const iconEl = this.shadowRoot.getElementById("v-inv-icon");
      if (iconEl) iconEl.style.display = "none";
      
      imgEl.src = `/local/smartinghome/${imgName}.png`;
      imgEl.setAttribute("data-model", imgName);
    }
  }

  _flow(id, active, power) {
    const el = this.shadowRoot.getElementById(id);
    if (!el) return;
    if (active) {
      el.style.display = "block";
      const anim = el.querySelector("animateMotion");
      if (anim) {
        const speed = Math.max(0.8, 4 - (Math.min(power, 6000) / 6000) * 3.2);
        anim.setAttribute("dur", `${speed}s`);
      }
    } else {
      el.style.display = "none";
    }
  }

  _updateStats() {
    // HEMS recommendation
    this._setText("v-hems-rec", this._s("sensor.smartinghome_hems_recommendation") || "Brak danych");
    // License
    const badge = this.shadowRoot.getElementById("v-license");
    const tier = (this._s("sensor.smartinghome_license_tier") || "FREE").toUpperCase();
    if (badge) {
      badge.textContent = tier;
      badge.className = `badge ${tier === "PRO" ? "pro" : "free"}`;
    }
    // Settings: show upgrade prompt for FREE users
    const upgradeBox = this.shadowRoot.getElementById("settings-upgrade-box");
    if (upgradeBox) upgradeBox.style.display = (tier === "PRO" || tier === "ENTERPRISE") ? "none" : "block";
    // Settings: API key status
    this._updateKeyStatus();
    // RCE / Tariff
    const g13Zone = this._s("sensor.smartinghome_g13_current_zone") || "—";
    const g13Badge = this.shadowRoot.getElementById("v-g13-zone-badge");
    if (g13Badge) {
      g13Badge.textContent = g13Zone.toUpperCase();
      const isPeak = g13Zone.toLowerCase().includes("szczytow") && !g13Zone.toLowerCase().includes("poza");
      g13Badge.className = `status-badge ${isPeak ? 'peak' : 'offpeak'}`;
    }
    
    this._setText("v-g13-price-tab", `${this._f("sensor.smartinghome_g13_buy_price", 2)} PLN`);
    this._setText("v-rce-price-tab", `${this._f("sensor.smartinghome_rce_sell_price", 2)} PLN`);
    
    const rceTrend = this._s("sensor.smartinghome_rce_price_trend") || "—";
    this._setText("v-rce-trend-tab", rceTrend === "rosnie" ? "📈 Rośnie" : rceTrend === "spada" ? "📉 Spada" : "➖ Stabilny");
    
    this._setText("v-rce-avg-tab", `${this._f("sensor.smartinghome_rce_average_today", 2)} PLN`);
    
    const ftoday = this._f("sensor.smartinghome_pv_forecast_today_total");
    const ftomor = this._f("sensor.smartinghome_pv_forecast_tomorrow_total");
    this._setText("v-forecast-today", `${ftoday} kWh`);
    this._setText("v-forecast-tomorrow", `${ftomor} kWh`);
    this._setText("v-forecast-today-tab", `${ftoday} kWh`);
    this._setText("v-forecast-tomorrow-tab", `${ftomor} kWh`);
    
    const battEnergy = this._f("sensor.smartinghome_battery_energy_available");
    this._setText("v-battery-energy-tab", `${battEnergy} kWh`);
    
    const rt = (this._s("sensor.smartinghome_battery_runtime") || "—");
    this._setText("v-battery-runtime-tab", rt);
    
    const arbitrage = this._n("sensor.smartinghome_battery_arbitrage_potential") || 0;
    this._setText("v-arbitrage-tab", `${arbitrage.toFixed(2)} PLN`);
    const arbCard = this.shadowRoot.getElementById("arbitrage-card");
    if (arbCard) {
      if (arbitrage > 0) arbCard.classList.add("glow-card");
      else arbCard.classList.remove("glow-card");
    }
    
    const soc = this._nm("battery_soc") || 0;
    this._setText("v-soc-tab", `${Math.round(soc)}%`);
    const socBarTab = this.shadowRoot.getElementById("soc-fill-tab");
    if (socBarTab) {
      socBarTab.style.width = `${Math.min(100, Math.max(0, soc))}%`;
      socBarTab.style.background = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";
    }

    this._setText("v-surplus", `${this._f("sensor.smartinghome_pv_surplus_power", 0)} W`);
    this._setText("v-net-grid", `${this._f("sensor.smartinghome_net_grid_today")} kWh`);
    
    // AI Rec Tab
    this._setText("v-hems-rec-tab", this._s("sensor.smartinghome_hems_recommendation") || "Brak danych z asystenta AI.");
    
    // Voltage Bars update
    const v1 = this._fm("voltage_l1"); this._setText("v-e-v1", `${v1} V`);
    const v2 = this._fm("voltage_l2"); this._setText("v-e-v2", `${v2} V`);
    const v3 = this._fm("voltage_l3"); this._setText("v-e-v3", `${v3} V`);
    this._setText("v-e-freq", `${this._fm("grid_frequency", 2)} Hz`);
    [1, 2, 3].forEach(i => {
      const v = this._nm(`voltage_l${i}`);
      const bar = this.shadowRoot.getElementById(`vb-l${i}`);
      if (bar && v) {
        let pct = ((v - 200) / 60) * 100;
        bar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
        bar.style.background = v > 253 ? "#e74c3c" : v < 207 ? "#f39c12" : "#2ecc71";
      }
    });
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
        .panel-container.css-fullscreen {
          position: fixed; inset: 0; z-index: 99999; overflow-y: auto;
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
        /* ═══  POWER FLOW — ORTHOGONAL LAYOUT ═══ */
        /* ═══════════════════════════════════════ */
        .flow-wrapper {
          position: relative;
          width: 100%; max-width: 1100px; margin: 0 auto;
          min-height: 520px;
        }
        .flow-svg-bg {
          position: absolute; top: 0; left: 0; width: 100%; height: 100%;
          pointer-events: none; z-index: 0;
        }
        .flow-nodes {
          position: relative; z-index: 1;
          display: grid;
          grid-template-columns: 200px 1fr 200px;
          grid-template-rows: auto 1fr auto;
          gap: 0; min-height: 520px;
        }

        /* Node boxes */
        .node {
          background: rgba(20, 30, 48, 0.4);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border: 1px solid rgba(255,255,255,0.1);
          box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
          border-radius: 16px;
          padding: 18px;
          position: relative;
          transition: all 0.3s ease;
        }
        .node:hover {
          border-color: rgba(0,212,255,0.3);
          box-shadow: 0 12px 40px 0 rgba(0, 212, 255, 0.15);
        }
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
          width: 125px; height: 105px;
          background: rgba(20, 30, 48, 0.5);
          backdrop-filter: blur(12px);
          -webkit-backdrop-filter: blur(12px);
          border: 1px solid rgba(255,255,255,0.2);
          box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3), inset 0 0 20px rgba(0, 212, 255, 0.05);
          border-radius: 18px;
          display: flex; flex-direction: column;
          align-items: center; justify-content: center;
          position: relative;
          transition: all 0.3s ease;
        }
        .inv-box:hover {
          border-color: rgba(0, 212, 255, 0.5);
          box-shadow: 0 12px 40px 0 rgba(0, 212, 255, 0.25), inset 0 0 20px rgba(0, 212, 255, 0.1);
          transform: translateY(-2px);
        }
        .inv-box img {
          max-width: 80px; max-height: 60px;
          object-fit: contain; margin-bottom: 5px;
          filter: drop-shadow(0px 4px 10px rgba(0,0,0,0.5));
        }
        .inv-icon { font-size: 36px; }
        .inv-label { font-size: 9px; color: #a0aec0; text-transform: uppercase; letter-spacing: 1px; margin-top: 2px; }

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

        /* Flow SVG lines ─ orthogonal */
        .fl-line { fill: none; stroke: rgba(255,255,255,0.06); stroke-width: 2; }
        .fl-dot { display: none; filter: drop-shadow(0 0 4px currentColor); }
        .fl-dot.solar { fill: #f7b731; color: #f7b731; }
        .fl-dot.grid-in { fill: #e74c3c; color: #e74c3c; }
        .fl-dot.grid-out { fill: #2ecc71; color: #2ecc71; }
        .fl-dot.batt-charge { fill: #00d4ff; color: #00d4ff; }
        .fl-dot.batt-discharge { fill: #3498db; color: #3498db; }
        .fl-dot.load-flow { fill: #2ecc71; color: #2ecc71; }

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
        .grid-cards { display: grid; gap: 14px; }
        .gc-2 { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
        .gc-3 { grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
        .card {
          background: rgba(20, 30, 48, 0.4);
          backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
          border: 1px solid rgba(255,255,255,0.1);
          border-radius: 16px; padding: 18px;
          box-shadow: 0 4px 16px rgba(0,0,0,0.2);
          transition: all 0.3s ease;
        }
        .card:hover { border-color: rgba(0,212,255,0.3); box-shadow: 0 8px 32px rgba(0, 212, 255, 0.1); }
        .card.glow-card { border-color: rgba(46, 204, 113, 0.4); box-shadow: 0 0 20px rgba(46, 204, 113, 0.15); }
        
        .card-title {
          font-size: 11px; font-weight: 700; text-transform: uppercase;
          letter-spacing: 1px; color: #a0aec0; margin-bottom: 12px;
          display: flex; align-items: center; gap: 6px;
        }
        .dr { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.05); }
        .dr:last-child { border-bottom: none; }
        .dr .lb { color: #94a3b8; font-size: 12px; }
        .dr .vl { color: #fff; font-weight: 700; font-size: 13px; }

        /* Voltage bar */
        .v-bar-container { flex: 1; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; margin: 0 10px; overflow: hidden; }
        .v-bar-fill { height: 100%; border-radius: 3px; background: #2ecc71; transition: width 0.5s; width: 0%; }
        
        /* Chat bubble */
        .recommendation {
          background: rgba(46, 204, 113, 0.08);
          border-left: 3px solid #2ecc71;
          border-radius: 0 12px 12px 12px;
          padding: 14px; font-size: 13px; line-height: 1.6;
          color: #e2e8f0; margin-top: 5px; position: relative;
        }
        
        /* Dynamic badgets for Taryfa & RCE */
        .status-badge {
          display: inline-block; padding: 3px 8px; border-radius: 6px; font-size:11px; font-weight:700;
        }
        .status-badge.peak { background: rgba(231,76,60,0.2); color: #e74c3c; border: 1px solid rgba(231,76,60,0.3); }
        .status-badge.offpeak { background: rgba(46,204,113,0.2); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }

        .actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 12px; }
        .action-btn {
          padding: 10px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.05); color: #e0e6ed; font-size: 12px; font-weight: 600;
          cursor: pointer; text-align: center; transition: all 0.2s;
        }
        .action-btn:hover { background: rgba(0,212,255,0.15); border-color: rgba(0,212,255,0.3); transform: translateY(-1px); }

        /* Settings tab */
        .settings-field { margin-bottom: 14px; }
        .settings-field label { display: block; font-size: 11px; font-weight: 700; color: #a0aec0; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
        .settings-field input {
          width: 100%; padding: 10px 12px; border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.1); background: rgba(255,255,255,0.05);
          color: #e0e6ed; font-size: 13px; font-family: inherit;
        }
        .settings-field input:focus { outline: none; border-color: rgba(0,212,255,0.4); }
        .save-btn {
          padding: 10px 24px; border-radius: 10px; border: none;
          background: linear-gradient(135deg, #00d4ff, #00e676); color: #0a1628;
          font-weight: 700; font-size: 13px; cursor: pointer; transition: all 0.2s;
        }
        .save-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 16px rgba(0,212,255,0.3); }
        .test-btn {
          padding: 6px 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.12);
          background: rgba(255,255,255,0.04); color: #a0aec0; font-size: 11px;
          cursor: pointer; transition: all 0.2s; font-family: inherit;
        }
        .test-btn:hover { background: rgba(0,212,255,0.1); border-color: rgba(0,212,255,0.3); color: #00d4ff; }
        .test-btn:disabled { opacity: 0.5; cursor: wait; }
        .key-row { display: flex; gap: 8px; align-items: center; }
        .key-row input { flex: 1; }
        .key-status { font-size: 11px; margin-top: 3px; margin-bottom: 8px; }
        .gear-btn {
          background: none; border: none; font-size: 20px; cursor: pointer;
          padding: 4px 8px; border-radius: 6px; transition: all 0.2s;
          filter: drop-shadow(0 0 2px rgba(255,255,255,0.2));
        }
        .gear-btn:hover { background: rgba(255,255,255,0.08); transform: rotate(30deg); }
        .upload-zone {
          border: 2px dashed rgba(255,255,255,0.15); border-radius: 12px; padding: 20px;
          text-align: center; cursor: pointer; transition: all 0.2s;
          background: rgba(255,255,255,0.02);
        }
        .upload-zone:hover { border-color: rgba(0,212,255,0.3); background: rgba(0,212,255,0.03); }
        .upgrade-box {
          padding: 14px; border-radius: 10px;
          background: linear-gradient(135deg, rgba(247,183,49,0.08), rgba(0,212,255,0.08));
          border: 1px solid rgba(247,183,49,0.2);
        }

        @media (max-width: 800px) {
          .flow-nodes { grid-template-columns: 1fr 1fr; grid-template-rows: auto; }
          .pv-area { grid-column: 1; grid-row: 1; }
          .home-area { grid-column: 2; grid-row: 1; }
          .inv-area { grid-column: 1 / 3; grid-row: 2; flex-direction: row; gap: 12px; }
          .batt-area { grid-column: 1; grid-row: 3; }
          .grid-area { grid-column: 2; grid-row: 3; }
          .summary-area { grid-column: 1 / 3; }
          .flow-svg-bg { display: none; }
        }
        @media (max-width: 480px) {
          .flow-nodes { grid-template-columns: 1fr; }
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
            <button class="gear-btn" title="Ustawienia" onclick="this.getRootNode().host._switchTab('settings')">⚙️</button>
            <button class="fullscreen-btn" onclick="this.getRootNode().host._toggleFullscreen()">⊞ Pełny ekran</button>
          </div>
        </div>

        <div class="tabs">
          <button class="tab-btn active" data-tab="overview" onclick="this.getRootNode().host._switchTab('overview')">📊 Przegląd</button>
          <button class="tab-btn" data-tab="energy" onclick="this.getRootNode().host._switchTab('energy')">⚡ Energia</button>
          <button class="tab-btn" data-tab="tariff" onclick="this.getRootNode().host._switchTab('tariff')">💰 Taryfy & RCE</button>
          <button class="tab-btn" data-tab="battery" onclick="this.getRootNode().host._switchTab('battery')">🔋 Bateria</button>
          <button class="tab-btn" data-tab="hems" onclick="this.getRootNode().host._switchTab('hems')">🤖 HEMS</button>
          <button class="tab-btn" data-tab="settings" onclick="this.getRootNode().host._switchTab('settings')">⚙️ Ustawienia</button>
        </div>

        <!-- ═══════ TAB: OVERVIEW ═══════ -->
        <div class="tab-content active" data-tab="overview">
          <div class="flow-wrapper">
            <!-- ORTHOGONAL SVG OVERLAY -->
            <svg class="flow-svg-bg" viewBox="0 0 700 500" preserveAspectRatio="xMidYMid meet">
              <!-- PV → Inverter (horizontal) -->
              <path class="fl-line" d="M 130,70 H 350" />
              <g id="fl-pv-inv" class="fl-dot solar" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 130,70 H 350" />
              </g>
              <!-- Inverter → Home (horizontal) -->
              <path class="fl-line" d="M 350,70 H 570" />
              <g id="fl-inv-load" class="fl-dot load-flow" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 350,70 H 570" />
              </g>
              <!-- Inverter → Battery (down then left) -->
              <path class="fl-line" d="M 350,100 V 430 H 130" />
              <g id="fl-inv-batt" class="fl-dot batt-charge" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 350,100 V 430 H 130" />
              </g>
              <!-- Battery → Inverter (right then up) -->
              <g id="fl-batt-inv" class="fl-dot batt-discharge" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 130,430 H 350 V 100" />
              </g>
              <!-- Grid → Inverter (left then up) -->
              <path class="fl-line" d="M 570,430 H 350 V 100" />
              <g id="fl-grid-inv" class="fl-dot grid-in" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 570,430 H 350 V 100" />
              </g>
              <!-- Inverter → Grid (down then right) -->
              <g id="fl-inv-grid" class="fl-dot grid-out" style="display:none">
                <circle r="5" />
                <animateMotion dur="2s" repeatCount="indefinite" path="M 350,100 V 430 H 570" />
              </g>
            </svg>

            <!-- FLOW NODES GRID -->
            <div class="flow-nodes">
              <!-- ☀️ PV AREA -->
              <div class="pv-area">
                <div class="node" style="border-color: rgba(247,183,49,0.2); margin-bottom:8px">
                  <div class="node-title">☀️ Produkcja PV</div>
                  <div class="node-big" style="color:#f7b731" id="v-pv">— W</div>
                  <div class="node-sub" id="v-pv-today">— kWh dziś</div>
                </div>
                <div class="pv-strings">
                  <div class="pv-string" id="pv1-box"><div class="pv-name">PV1</div><div class="pv-val" id="v-pv1-p">—</div><div class="pv-detail"><span id="v-pv1-v">— V</span> · <span id="v-pv1-a">— A</span></div></div>
                  <div class="pv-string" id="pv2-box"><div class="pv-name">PV2</div><div class="pv-val" id="v-pv2-p">—</div><div class="pv-detail"><span id="v-pv2-v">— V</span> · <span id="v-pv2-a">— A</span></div></div>
                  <div class="pv-string" id="pv3-box" style="display:none"><div class="pv-name">PV3</div><div class="pv-val" id="v-pv3-p">—</div><div class="pv-detail"><span id="v-pv3-v">—</span></div></div>
                  <div class="pv-string" id="pv4-box" style="display:none"><div class="pv-name">PV4</div><div class="pv-val" id="v-pv4-p">—</div><div class="pv-detail"><span id="v-pv4-v">—</span></div></div>
                </div>
              </div>

              <!-- ⚡ INVERTER -->
              <div class="inv-area">
                <div class="inv-box">
                  <img id="v-inv-img" src="/local/smartinghome/goodwe.png" alt="Inverter" onerror="this.style.display='none'; this.nextElementSibling.style.display='block';" />
                  <div id="v-inv-icon" style="display:none; text-align:center">
                    <div class="inv-icon">⚡</div>
                    <div style="font-size:8px; color:#f39c12; line-height:1.2; margin-top:2px">Wgraj<br>goodwe.png lub deye.png<br>do /config/www/smartinghome/</div>
                  </div>
                  <div class="inv-label">Falownik</div>
                </div>
                <div style="text-align:center; margin-top:6px">
                  <div style="font-size:14px; font-weight:700; color:#fff" id="v-inv-p">— W</div>
                  <div style="font-size:10px; color:#94a3b8" id="v-inv-t">—°C</div>
                </div>
                <div style="display:flex; gap:14px; margin-top:12px">
                  <div class="summary-item"><div class="si-label">Autarkia</div><div class="si-val" id="v-autarky">—%</div></div>
                  <div class="summary-item"><div class="si-label">Autokonsumpcja</div><div class="si-val" id="v-selfcons">—%</div></div>
                </div>
              </div>

              <!-- 🏠 HOME -->
              <div class="home-area">
                <div class="node" style="border-color: rgba(46,204,113,0.2)">
                  <div class="node-title">🏠 Zużycie</div>
                  <div class="node-big" id="v-load">— W</div>
                  <div class="node-detail" style="margin-top:8px">
                    <div id="v-load-l1">L1: — W</div><div id="v-load-l2">L2: — W</div><div id="v-load-l3">L3: — W</div>
                  </div>
                </div>
              </div>

              <!-- 🔋 BATTERY -->
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
                    <div id="v-batt-charge">↑ — kWh</div><div id="v-batt-discharge">↓ — kWh</div>
                  </div>
                </div>
              </div>

              <!-- 🔌 GRID -->
              <div class="grid-area" style="margin-top:8px">
                <div class="node" style="border-color: rgba(231,76,60,0.15)">
                  <div class="node-title">🔌 Sieć</div>
                  <div class="node-big" id="v-grid">— W</div>
                  <div class="node-dir" id="v-grid-dir" style="color:#e74c3c"></div>
                  <div class="node-detail" style="margin-top:6px">
                    <div><span id="v-grid-v1">— V</span> · <span id="v-grid-v2">— V</span> · <span id="v-grid-v3">— V</span></div>
                    <div id="v-grid-freq">— Hz</div>
                    <div style="margin-top:4px"><span style="color:#e74c3c">↓</span> <span id="v-grid-import">— kWh</span>&nbsp;<span style="color:#2ecc71">↑</span> <span id="v-grid-export">— kWh</span></div>
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
              <div class="card-title">⚡ Napięcie sieci 3F</div>
              <div class="dr">
                <span class="lb">L1</span>
                <div class="v-bar-container"><div class="v-bar-fill" id="vb-l1"></div></div>
                <span class="vl" id="v-e-v1">— V</span>
              </div>
              <div class="dr">
                <span class="lb">L2</span>
                <div class="v-bar-container"><div class="v-bar-fill" id="vb-l2"></div></div>
                <span class="vl" id="v-e-v2">— V</span>
              </div>
              <div class="dr">
                <span class="lb">L3</span>
                <div class="v-bar-container"><div class="v-bar-fill" id="vb-l3"></div></div>
                <span class="vl" id="v-e-v3">— V</span>
              </div>
              <div class="dr" style="margin-top:4px"><span class="lb">Częstotliwość</span><span class="vl" id="v-e-freq">— Hz</span></div>
            </div>
            <div class="card">
              <div class="card-title">☀️ Prognoza PV</div>
              <div class="dr"><span class="lb">Dzisiaj</span><span class="vl" id="v-forecast-today-tab">— kWh</span></div>
              <div class="dr"><span class="lb">Jutro</span><span class="vl" id="v-forecast-tomorrow-tab">— kWh</span></div>
            </div>
            <div class="card">
              <div class="card-title">🔧 Szybkie akcje</div>
              <div class="actions">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','force_charge')">🔋 Wymuś Ład.</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','force_discharge')">⚡ Wymuś Rozład.</button>
              </div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: TARIFF & RCE ═══════ -->
        <div class="tab-content" data-tab="tariff">
          <div class="grid-cards gc-2">
            <div class="card">
              <div class="card-title">⏰ Taryfa G13</div>
              <div class="dr"><span class="lb">Trwająca strefa</span><span id="v-g13-zone-badge" class="status-badge">—</span></div>
              <div class="dr"><span class="lb">Cena zakupu</span><span class="vl" id="v-g13-price-tab">— PLN</span></div>
            </div>
            <div class="card">
              <div class="card-title">📈 Rynek RCE PSE</div>
              <div class="dr"><span class="lb">Cena sprzedaży</span><span class="vl" id="v-rce-price-tab">— PLN</span></div>
              <div class="dr"><span class="lb">Trend</span><span class="vl" id="v-rce-trend-tab">—</span></div>
              <div class="dr"><span class="lb">Średnia dzienna</span><span class="vl" id="v-rce-avg-tab">— PLN</span></div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: BATTERY ═══════ -->
        <div class="tab-content" data-tab="battery">
          <div class="grid-cards gc-2">
            <div class="card">
              <div class="card-title">🔋 Pojemność LiFePO4</div>
              <div style="display:flex; align-items:baseline; gap:8px; margin-bottom:8px;">
                 <div style="font-size:28px; font-weight:800; color:#fff" id="v-soc-tab">—%</div>
                 <div style="font-size:14px; color:#94a3b8" id="v-battery-energy-tab">— kWh</div>
              </div>
              <div class="soc-bar" style="height:12px; margin-bottom:12px"><div class="soc-fill" id="soc-fill-tab" style="width:0%"></div></div>
              <div class="dr" style="border-top:1px solid rgba(255,255,255,0.05)"><span class="lb">Czas pracy</span><span class="vl" id="v-battery-runtime-tab">—</span></div>
            </div>
            <div class="card" id="arbitrage-card">
              <div class="card-title">💰 Rozszerzony Arbitraż</div>
              <div class="dr"><span class="lb">Potencjał zarobku</span><span class="vl" id="v-arbitrage-tab" style="color:#2ecc71; font-size:22px">— PLN</span></div>
              <div style="font-size:11px; color:#8899aa; margin-top:10px;">Zarobek z cyklu G13 ładowania w Off-Peak i rozładowywania podczas popołudniowego szczytu.</div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: HEMS ═══════ -->
        <div class="tab-content" data-tab="hems">
          <div class="grid-cards gc-2">
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🤖 AI Energy Advisor</div>
              <div style="display:flex; gap:10px; align-items:flex-start;">
                <div style="font-size:28px; filter:drop-shadow(0 0 5px rgba(0,212,255,0.5));">🤖</div>
                <div class="recommendation" style="flex:1" id="v-hems-rec-tab">Ładowanie porady z asystenta...</div>
              </div>
            </div>
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">⚙️ Sterowanie Ręczne Trybem HEMS</div>
              <div class="actions">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'auto'})">🔄 Tryb Auto</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'sell'})">💰 Max Sprzedaż</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'charge'})">🔋 Zmuś Ładowanie</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','set_mode',{mode:'peak_save'})">🏠 Szczyt (Z domu)</button>
              </div>
            </div>
          </div>
        </div>

        <!-- ═══════ TAB: SETTINGS ═══════ -->
        <div class="tab-content" data-tab="settings">
          <div class="grid-cards gc-2">

            <!-- 🔑 API Keys -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🔑 Klucze API — AI Advisor</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Podaj klucze API aby włączyć AI Advisor — inteligentne porady dotyczące zarządzania energią.</div>
              <div class="settings-field">
                <label>Google Gemini API Key</label>
                <div class="key-row">
                  <input type="password" id="inp-gemini-key" placeholder="AIza..." />
                  <button class="test-btn" id="test-btn-gemini" onclick="this.getRootNode().host._testApiKey('gemini')">🧪 Testuj</button>
                </div>
                <div class="key-status" id="key-status-gemini">— Brak klucza</div>
              </div>
              <div class="settings-field">
                <label>Anthropic Claude API Key</label>
                <div class="key-row">
                  <input type="password" id="inp-anthropic-key" placeholder="sk-ant-..." />
                  <button class="test-btn" id="test-btn-anthropic" onclick="this.getRootNode().host._testApiKey('anthropic')">🧪 Testuj</button>
                </div>
                <div class="key-status" id="key-status-anthropic">— Brak klucza</div>
              </div>
              <button class="save-btn" onclick="this.getRootNode().host._saveApiKeys()">💾 Zapisz klucze API</button>
              <div id="v-save-status" style="font-size:11px; color:#2ecc71; margin-top:8px"></div>
            </div>

            <!-- 🖼️ Inverter Image Upload -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">🖼️ Zdjęcie falownika</div>
              <div style="font-size:11px; color:#94a3b8; margin-bottom:10px">Wgraj zdjęcie falownika, które będzie wyświetlane w panelu Przegląd.</div>
              <div class="upload-zone" onclick="this.nextElementSibling.click()">
                <div style="font-size:32px; margin-bottom:6px">📂</div>
                <div style="font-size:12px; color:#a0aec0">Kliknij aby wybrać plik PNG/JPG</div>
                <div style="font-size:10px; color:#64748b; margin-top:4px">Plik zostanie zapisany jako <code style="color:#00d4ff">/config/www/smartinghome/inverter.png</code></div>
              </div>
              <input type="file" accept="image/png,image/jpeg" style="display:none" onchange="this.getRootNode().host._uploadInverterImage(this.files[0])" />
              <div id="v-upload-status" style="font-size:11px; color:#2ecc71; margin-top:8px"></div>
              <div style="margin-top:10px; font-size:10px; color:#64748b">
                <strong>Alternatywnie:</strong> ręcznie skopiuj plik <code style="color:#f39c12">goodwe.png</code> lub <code style="color:#f39c12">deye.png</code> do katalogu <code style="color:#00d4ff">/config/www/smartinghome/</code> na serwerze HA.
              </div>
            </div>

            <!-- ⭐ Upgrade Prompt (FREE only) -->
            <div class="card" style="grid-column: 1 / -1" id="settings-upgrade-box">
              <div class="upgrade-box">
                <div style="font-size:15px; font-weight:700; color:#f7b731">⭐ Odblokuj pełną moc Smarting HOME</div>
                <div style="font-size:12px; color:#a0aec0; margin-top:6px; line-height:1.5">
                  Wersja <strong style="color:#00d4ff">PRO</strong> oferuje:<br>
                  🧠 Pełny silnik HEMS z 3-warstwową optymalizacją<br>
                  📈 Arbitraż nocny (zysk ~177 PLN/mies.)<br>
                  🛡️ Kaskada napięciowa i ochrona SOC<br>
                  📊 7 gotowych blueprintów automatyzacji
                </div>
                <a href="https://smartinghome.pl/buy" target="_blank" style="display:inline-block; margin-top:10px; padding:8px 20px; border-radius:8px; background:linear-gradient(135deg,#f7b731,#e67e22); color:#0a1628; font-weight:700; font-size:12px; text-decoration:none; transition:all 0.2s">🛒 Kup licencję PRO</a>
              </div>
            </div>

            <!-- ℹ️ Info -->
            <div class="card" style="grid-column: 1 / -1">
              <div class="card-title">ℹ️ Informacje</div>
              <div class="dr"><span class="lb">Wersja integracji</span><span class="vl">1.5.3</span></div>
              <div class="dr"><span class="lb">Ścieżka zdjęć</span><span class="vl" style="font-size:10px">/config/www/smartinghome/</span></div>
              <div class="dr"><span class="lb">Dokumentacja</span><span class="vl"><a href="https://smartinghome.pl/docs" target="_blank" style="color:#00d4ff">smartinghome.pl/docs</a></span></div>
              <div class="dr"><span class="lb">Wsparcie</span><span class="vl"><a href="https://github.com/GregECAT/smartinghome-homeassistant/issues" target="_blank" style="color:#00d4ff">GitHub Issues</a></span></div>
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
