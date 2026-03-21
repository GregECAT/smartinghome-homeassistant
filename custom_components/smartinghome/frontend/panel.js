/**
 * Smarting HOME — Custom Panel for Home Assistant
 * Fullscreen energy management dashboard
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
    if (this.shadowRoot.querySelector(".panel-container")) {
      this._updateSensorValues();
    }
  }

  set panel(panel) {
    this._panel = panel;
  }

  set narrow(narrow) {
    this._narrow = narrow;
  }

  connectedCallback() {
    this._render();
    document.addEventListener("fullscreenchange", () => {
      this._isFullscreen = !!document.fullscreenElement;
      const btn = this.shadowRoot.querySelector(".fullscreen-btn");
      if (btn) btn.textContent = this._isFullscreen ? "⊡ Zamknij" : "⊞ Pełny ekran";
    });
  }

  _toggleFullscreen() {
    const container = this.shadowRoot.querySelector(".panel-container");
    if (!document.fullscreenElement) {
      container.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  }

  _switchTab(tab) {
    this._activeTab = tab;
    this.shadowRoot.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.tab === tab);
    });
    this.shadowRoot.querySelectorAll(".tab-content").forEach((content) => {
      content.classList.toggle("active", content.dataset.tab === tab);
    });
  }

  _getState(entityId) {
    if (!this._hass || !this._hass.states[entityId]) return "—";
    return this._hass.states[entityId].state;
  }

  _getStateFloat(entityId, decimals = 1) {
    const val = parseFloat(this._getState(entityId));
    return isNaN(val) ? "—" : val.toFixed(decimals);
  }

  _getUnit(entityId) {
    if (!this._hass || !this._hass.states[entityId]) return "";
    return this._hass.states[entityId].attributes.unit_of_measurement || "";
  }

  _callService(domain, service, data = {}) {
    if (this._hass) {
      this._hass.callService(domain, service, data);
    }
  }

  _updateSensorValues() {
    const updates = {
      "val-pv-power": () => `${this._getStateFloat("sensor.pv_power", 0)} W`,
      "val-grid-power": () => {
        const v = parseFloat(this._getState("sensor.meter_active_power_total"));
        if (isNaN(v)) return "— W";
        return v > 0 ? `↓ ${Math.abs(v).toFixed(0)} W` : `↑ ${Math.abs(v).toFixed(0)} W`;
      },
      "val-battery-power": () => {
        const v = parseFloat(this._getState("sensor.battery_power"));
        if (isNaN(v)) return "— W";
        return v > 0 ? `⚡ ${v.toFixed(0)} W` : `🔋 ${Math.abs(v).toFixed(0)} W`;
      },
      "val-load": () => `${this._getStateFloat("sensor.load", 0)} W`,
      "val-soc": () => `${this._getStateFloat("sensor.battery_state_of_charge", 0)}%`,
      "val-pv-today": () => `${this._getStateFloat("sensor.today_s_pv_generation")} kWh`,
      "val-g13-zone": () => this._getState("sensor.smartinghome_g13_current_zone"),
      "val-g13-price": () => `${this._getStateFloat("sensor.smartinghome_g13_buy_price", 2)} PLN`,
      "val-rce-price": () => `${this._getStateFloat("sensor.smartinghome_rce_sell_price", 2)} PLN`,
      "val-rce-trend": () => this._getState("sensor.smartinghome_rce_price_trend"),
      "val-autarky": () => `${this._getStateFloat("sensor.smartinghome_autarky_today", 0)}%`,
      "val-self-cons": () => `${this._getStateFloat("sensor.smartinghome_self_consumption_today", 0)}%`,
      "val-hems-rec": () => this._getState("sensor.smartinghome_hems_recommendation"),
      "val-system-status": () => this._getState("sensor.smartinghome_system_status"),
      "val-license": () => this._getState("sensor.smartinghome_license_tier"),
      "val-surplus": () => `${this._getStateFloat("sensor.smartinghome_pv_surplus_power", 0)} W`,
      "val-battery-energy": () => `${this._getStateFloat("sensor.smartinghome_battery_energy_available")} kWh`,
      "val-battery-runtime": () => this._getState("sensor.smartinghome_battery_runtime"),
      "val-forecast-today": () => `${this._getStateFloat("sensor.smartinghome_pv_forecast_today_total")} kWh`,
      "val-forecast-tomorrow": () => `${this._getStateFloat("sensor.smartinghome_pv_forecast_tomorrow_total")} kWh`,
      "val-rce-avg": () => `${this._getStateFloat("sensor.smartinghome_rce_average_today", 2)} PLN`,
      "val-rce-min": () => `${this._getStateFloat("sensor.smartinghome_rce_min_today", 2)} PLN`,
      "val-rce-max": () => `${this._getStateFloat("sensor.smartinghome_rce_max_today", 2)} PLN`,
      "val-net-grid": () => `${this._getStateFloat("sensor.smartinghome_net_grid_today")} kWh`,
      "val-voltage-l1": () => `${this._getStateFloat("sensor.on_grid_l1_voltage")} V`,
      "val-voltage-l2": () => `${this._getStateFloat("sensor.on_grid_l2_voltage")} V`,
      "val-voltage-l3": () => `${this._getStateFloat("sensor.on_grid_l3_voltage")} V`,
      "val-arbitrage": () => `${this._getStateFloat("sensor.smartinghome_battery_arbitrage_potential")} PLN`,
    };

    for (const [id, fn] of Object.entries(updates)) {
      const el = this.shadowRoot.getElementById(id);
      if (el) el.textContent = fn();
    }

    // Update SOC bar
    const socBar = this.shadowRoot.getElementById("soc-bar-fill");
    if (socBar) {
      const soc = parseFloat(this._getState("sensor.battery_state_of_charge")) || 0;
      socBar.style.width = `${Math.min(100, Math.max(0, soc))}%`;
      socBar.style.background = soc > 50 ? "#2ecc71" : soc > 20 ? "#f39c12" : "#e74c3c";
    }
  }

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

        /* ── Header ────────────────────────────── */
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 16px 24px;
          background: rgba(255,255,255,0.03);
          border-bottom: 1px solid rgba(255,255,255,0.06);
          backdrop-filter: blur(20px);
          position: sticky;
          top: 0;
          z-index: 100;
        }
        .header-left {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .logo { font-size: 28px; }
        .header h1 {
          font-size: 18px;
          font-weight: 600;
          background: linear-gradient(135deg, #00d4ff, #00e676);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .header-right {
          display: flex;
          align-items: center;
          gap: 12px;
        }
        .status-badge {
          padding: 4px 12px;
          border-radius: 20px;
          font-size: 11px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .status-badge.free { background: rgba(52,152,219,0.2); color: #3498db; border: 1px solid rgba(52,152,219,0.3); }
        .status-badge.pro { background: rgba(46,204,113,0.2); color: #2ecc71; border: 1px solid rgba(46,204,113,0.3); }
        .fullscreen-btn {
          padding: 6px 14px;
          border-radius: 8px;
          border: 1px solid rgba(255,255,255,0.15);
          background: rgba(255,255,255,0.05);
          color: #a0aec0;
          font-size: 12px;
          cursor: pointer;
          transition: all 0.2s;
        }
        .fullscreen-btn:hover { background: rgba(255,255,255,0.1); color: #fff; }

        /* ── Tabs ──────────────────────────────── */
        .tabs {
          display: flex;
          gap: 2px;
          padding: 8px 24px;
          background: rgba(0,0,0,0.2);
        }
        .tab-btn {
          padding: 10px 20px;
          border: none;
          background: transparent;
          color: #64748b;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          border-radius: 8px;
          transition: all 0.2s;
          white-space: nowrap;
        }
        .tab-btn:hover { background: rgba(255,255,255,0.05); color: #a0aec0; }
        .tab-btn.active {
          background: rgba(0,212,255,0.1);
          color: #00d4ff;
          box-shadow: 0 0 20px rgba(0,212,255,0.1);
        }

        /* ── Tab Content ──────────────────────── */
        .tab-content { display: none; padding: 20px 24px; }
        .tab-content.active { display: block; }

        /* ── Cards ─────────────────────────────── */
        .grid { display: grid; gap: 16px; }
        .grid-2 { grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }
        .grid-3 { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
        .grid-4 { grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }

        .card {
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 16px;
          padding: 20px;
          backdrop-filter: blur(10px);
          transition: all 0.3s;
        }
        .card:hover { border-color: rgba(0,212,255,0.2); box-shadow: 0 4px 30px rgba(0,212,255,0.05); }

        .card-title {
          font-size: 12px;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 1px;
          color: #64748b;
          margin-bottom: 12px;
        }

        /* ── Metric Cards ─────────────────────── */
        .metric { text-align: center; padding: 16px; }
        .metric .icon { font-size: 28px; margin-bottom: 8px; }
        .metric .value {
          font-size: 24px;
          font-weight: 700;
          color: #fff;
          margin-bottom: 4px;
        }
        .metric .label {
          font-size: 11px;
          color: #64748b;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }

        /* ── Data Rows ────────────────────────── */
        .data-row {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 10px 0;
          border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .data-row:last-child { border-bottom: none; }
        .data-row .label { color: #94a3b8; font-size: 13px; }
        .data-row .value { color: #fff; font-weight: 600; font-size: 14px; }

        /* ── SOC Bar ──────────────────────────── */
        .soc-bar {
          width: 100%;
          height: 8px;
          background: rgba(255,255,255,0.1);
          border-radius: 4px;
          overflow: hidden;
          margin-top: 8px;
        }
        .soc-bar-fill {
          height: 100%;
          border-radius: 4px;
          transition: width 1s ease, background 1s ease;
        }

        /* ── Buttons ──────────────────────────── */
        .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
        .action-btn {
          padding: 10px 18px;
          border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.05);
          color: #e0e6ed;
          font-size: 13px;
          cursor: pointer;
          transition: all 0.2s;
          flex: 1;
          min-width: 100px;
          text-align: center;
        }
        .action-btn:hover { background: rgba(0,212,255,0.15); border-color: rgba(0,212,255,0.3); }
        .action-btn.charge { border-color: rgba(46,204,113,0.3); }
        .action-btn.charge:hover { background: rgba(46,204,113,0.2); }
        .action-btn.discharge { border-color: rgba(231,76,60,0.3); }
        .action-btn.discharge:hover { background: rgba(231,76,60,0.2); }

        /* ── Recommendation Box ───────────────── */
        .recommendation {
          background: linear-gradient(135deg, rgba(0,212,255,0.08), rgba(0,230,118,0.08));
          border: 1px solid rgba(0,212,255,0.15);
          border-radius: 12px;
          padding: 16px;
          font-size: 14px;
          line-height: 1.6;
        }

        /* ── Responsive ───────────────────────── */
        @media (max-width: 768px) {
          .header { padding: 12px 16px; }
          .tabs { padding: 6px 16px; overflow-x: auto; }
          .tab-content { padding: 16px; }
          .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr 1fr; }
          .metric .value { font-size: 18px; }
        }
        @media (max-width: 480px) {
          .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
        }
      </style>

      <div class="panel-container">
        <!-- Header -->
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

        <!-- Tabs -->
        <div class="tabs">
          <button class="tab-btn active" data-tab="overview" onclick="this.getRootNode().host._switchTab('overview')">📊 Przegląd</button>
          <button class="tab-btn" data-tab="energy" onclick="this.getRootNode().host._switchTab('energy')">⚡ Energia</button>
          <button class="tab-btn" data-tab="tariff" onclick="this.getRootNode().host._switchTab('tariff')">💰 Taryfy & RCE</button>
          <button class="tab-btn" data-tab="battery" onclick="this.getRootNode().host._switchTab('battery')">🔋 Bateria</button>
          <button class="tab-btn" data-tab="hems" onclick="this.getRootNode().host._switchTab('hems')">🤖 HEMS</button>
        </div>

        <!-- ═══════ TAB: OVERVIEW ═══════ -->
        <div class="tab-content active" data-tab="overview">
          <!-- Live Power -->
          <div class="grid grid-4" style="margin-bottom:16px">
            <div class="card metric">
              <div class="icon">☀️</div>
              <div class="value" id="val-pv-power">— W</div>
              <div class="label">Produkcja PV</div>
            </div>
            <div class="card metric">
              <div class="icon">🏠</div>
              <div class="value" id="val-load">— W</div>
              <div class="label">Zużycie domu</div>
            </div>
            <div class="card metric">
              <div class="icon">🔋</div>
              <div class="value" id="val-battery-power">— W</div>
              <div class="label">Bateria</div>
            </div>
            <div class="card metric">
              <div class="icon">🔌</div>
              <div class="value" id="val-grid-power">— W</div>
              <div class="label">Sieć</div>
            </div>
          </div>

          <!-- SOC + Key metrics -->
          <div class="grid grid-2" style="margin-bottom:16px">
            <div class="card">
              <div class="card-title">🔋 Stan baterii</div>
              <div style="display:flex;align-items:baseline;gap:8px;margin-bottom:8px">
                <span style="font-size:36px;font-weight:800;color:#fff" id="val-soc">—%</span>
                <span style="color:#64748b;font-size:13px">SOC</span>
              </div>
              <div class="soc-bar"><div class="soc-bar-fill" id="soc-bar-fill" style="width:0%"></div></div>
              <div style="margin-top:12px">
                <div class="data-row">
                  <span class="label">Dostępna energia</span>
                  <span class="value" id="val-battery-energy">— kWh</span>
                </div>
                <div class="data-row">
                  <span class="label">Czas pracy</span>
                  <span class="value" id="val-battery-runtime">—</span>
                </div>
              </div>
            </div>

            <div class="card">
              <div class="card-title">📊 Dziś</div>
              <div class="data-row">
                <span class="label">☀️ Produkcja PV</span>
                <span class="value" id="val-pv-today">— kWh</span>
              </div>
              <div class="data-row">
                <span class="label">🛡️ Autarkia</span>
                <span class="value" id="val-autarky">—%</span>
              </div>
              <div class="data-row">
                <span class="label">♻️ Autokonsumpcja</span>
                <span class="value" id="val-self-cons">—%</span>
              </div>
              <div class="data-row">
                <span class="label">📈 Nadwyżka PV</span>
                <span class="value" id="val-surplus">— W</span>
              </div>
              <div class="data-row">
                <span class="label">⚖️ Saldo sieci</span>
                <span class="value" id="val-net-grid">— kWh</span>
              </div>
            </div>
          </div>

          <!-- HEMS Recommendation -->
          <div class="card" style="margin-bottom:16px">
            <div class="card-title">💡 Rekomendacja HEMS</div>
            <div class="recommendation" id="val-hems-rec">Ładowanie danych...</div>
          </div>
        </div>

        <!-- ═══════ TAB: ENERGY ═══════ -->
        <div class="tab-content" data-tab="energy">
          <div class="grid grid-3" style="margin-bottom:16px">
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
              <div style="text-align:center;margin:16px 0">
                <span style="font-size:48px;font-weight:800;color:#fff" id="val-soc-big">—%</span>
              </div>
              <div class="soc-bar" style="height:12px"><div class="soc-bar-fill" id="soc-bar-fill-2" style="width:0%"></div></div>
              <div style="margin-top:16px">
                <div class="data-row"><span class="label">Dostępna energia</span><span class="value" id="val-battery-energy-2">— kWh</span></div>
                <div class="data-row"><span class="label">Czas pracy</span><span class="value" id="val-battery-runtime-2">—</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">💰 Arbitraż</div>
              <div class="data-row"><span class="label">Potencjał arbitrażu</span><span class="value" id="val-arbitrage">— PLN</span></div>
              <div style="margin-top:12px;padding:12px;background:rgba(46,204,113,0.1);border-radius:8px;font-size:12px;color:#94a3b8">
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
              <div style="margin-top:16px">
                <div class="data-row"><span class="label">Status systemu</span><span class="value" id="val-system-status-2">—</span></div>
                <div class="data-row"><span class="label">Licencja</span><span class="value" id="val-license-2">—</span></div>
              </div>
            </div>
            <div class="card">
              <div class="card-title">💡 Rekomendacja AI</div>
              <div class="recommendation" id="val-hems-rec-2">Ładowanie danych...</div>
              <div class="actions" style="margin-top:12px">
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','ask_ai_advisor',{question:'Co powinienem teraz zoptymalizować?'})">🤖 Zapytaj AI</button>
                <button class="action-btn" onclick="this.getRootNode().host._callService('smartinghome','generate_report')">📊 Raport</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    // Initial update
    if (this._hass) this._updateSensorValues();

    // Auto-refresh every 5s
    setInterval(() => {
      if (this._hass) this._updateSensorValues();
    }, 5000);
  }
}

customElements.define("smartinghome-panel", SmartingHomePanel);
